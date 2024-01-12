from __future__ import annotations

import logging
from typing import Dict, List, Mapping, Optional, TypedDict

from rest_framework.request import Request
from rest_framework.response import Response
from sentry_sdk import Scope, configure_scope

from sentry import analytics
from sentry.api.api_owners import ApiOwner
from sentry.api.api_publish_status import ApiPublishStatus
from sentry.api.base import region_silo_endpoint
from sentry.api.bases.project import ProjectEndpoint
from sentry.api.serializers import IntegrationSerializer, serialize
from sentry.api.utils import Timer
from sentry.integrations import IntegrationFeatures
from sentry.integrations.mixins import RepositoryMixin
from sentry.integrations.utils.code_mapping import get_sorted_code_mapping_configs
from sentry.integrations.utils.codecov import codecov_enabled, fetch_codecov_data
from sentry.models.integrations.repository_project_path_config import RepositoryProjectPathConfig
from sentry.models.project import Project
from sentry.services.hybrid_cloud.integration import integration_service
from sentry.shared_integrations.exceptions import ApiError
from sentry.utils.event_frames import munged_filename_and_frames

logger = logging.getLogger(__name__)


class ReposityLinkOutcome(TypedDict):
    sourceUrl: str | None
    error: str | None
    attemptedUrl: str | None
    sourcePath: str | None


def get_link(
    config: RepositoryProjectPathConfig,
    filepath: str,
    version: Optional[str] = None,
    group_id: Optional[str] = None,
    frame_abs_path: Optional[str] = None,
) -> ReposityLinkOutcome:
    result: ReposityLinkOutcome = {}

    integration = integration_service.get_integration(
        organization_integration_id=config.organization_integration_id
    )
    install = integration.get_installation(organization_id=config.project.organization_id)

    formatted_path = filepath.replace(config.stack_root, config.source_root, 1)

    link = None
    try:
        if isinstance(install, RepositoryMixin):
            with Timer() as t:
                link = install.get_stacktrace_link(
                    config.repository, formatted_path, config.default_branch, version
                )
                analytics.record(
                    "function_timer.timed",
                    function_name="get_stacktrace_link",
                    duration=t.duration,
                    organization_id=config.project.organization_id,
                    project_id=config.project_id,
                    group_id=group_id,
                    frame_abs_path=frame_abs_path,
                )

    except ApiError as e:
        if e.code != 403:
            raise
        result["error"] = "integration_link_forbidden"

    # If the link was not found, attach the URL that we attempted.
    if link:
        result["sourceUrl"] = link
    else:
        result["error"] = result.get("error") or "file_not_found"
        assert isinstance(install, RepositoryMixin)
        result["attemptedUrl"] = install.format_source_url(
            config.repository, formatted_path, config.default_branch
        )
    result["sourcePath"] = formatted_path

    return result


def generate_context(parameters: Dict[str, Optional[str]]) -> Dict[str, Optional[str]]:
    return {
        "file": parameters.get("file"),
        # XXX: Temp change to support try_path_munging until refactored
        "filename": parameters.get("file"),
        "commit_id": parameters.get("commitId"),
        "platform": parameters.get("platform"),
        "sdk_name": parameters.get("sdkName"),
        "abs_path": parameters.get("absPath"),
        "module": parameters.get("module"),
        "package": parameters.get("package"),
        "line_no": parameters.get("lineNo"),
        "group_id": parameters.get("groupId"),
    }


def set_top_tags(
    scope: Scope,
    project: Project,
    ctx: Mapping[str, Optional[str]],
    has_code_mappings: bool,
) -> None:
    try:
        scope.set_tag("project.slug", project.slug)
        scope.set_tag("organization.slug", project.organization.slug)
        scope.set_tag(
            "organization.early_adopter", bool(project.organization.flags.early_adopter.is_set)
        )
        scope.set_tag("stacktrace_link.platform", ctx["platform"])
        scope.set_tag("stacktrace_link.code_mappings", has_code_mappings)
        scope.set_tag("stacktrace_link.file", ctx["file"])
        # Add tag if filepath is Windows
        if ctx["file"] and ctx["file"].find(":\\") > -1:
            scope.set_tag("stacktrace_link.windows", True)
        scope.set_tag("stacktrace_link.abs_path", ctx["abs_path"])
        if ctx["platform"] == "python":
            # This allows detecting a file that belongs to Python's 3rd party modules
            scope.set_tag("stacktrace_link.in_app", "site-packages" not in str(ctx["abs_path"]))
    except Exception:
        # If errors arises we can still proceed
        logger.exception("We failed to set a tag.")


def try_path_munging(
    config: RepositoryProjectPathConfig,
    filepath: str,
    ctx: Mapping[str, Optional[str]],
    current_iteration_count: int,
) -> tuple[Dict[str, str], int]:
    result: Dict[str, str] = {}
    munged_frames = munged_filename_and_frames(
        str(ctx["platform"]), [ctx], "munged_filename", sdk_name=str(ctx["sdk_name"])
    )
    if munged_frames:
        munged_frame: Mapping[str, Mapping[str, str]] = munged_frames[1][0]
        munged_filename = str(munged_frame.get("munged_filename"))
        if munged_filename:
            if not filepath.startswith(config.stack_root) and not munged_filename.startswith(
                config.stack_root
            ):
                result = {"error": "stack_root_mismatch"}
            else:
                result = get_link(
                    config,
                    munged_filename,
                    ctx.get("commit_id"),
                    ctx.get("group_id"),
                    ctx.get("abs_path"),
                )

                current_iteration_count += 1

    return result, current_iteration_count


def set_tags(scope: Scope, result: StacktraceLinkOutcome, integrations: List[None]) -> None:
    scope.set_tag("stacktrace_link.found", result["source_url"] is not None)
    scope.set_tag("stacktrace_link.source_url", result.get("source_url"))
    scope.set_tag("stacktrace_link.error", result.get("error"))
    scope.set_tag("stacktrace_link.tried_url", result.get("attemptedUrl"))
    if result["current_config"]:
        scope.set_tag(
            "stacktrace_link.empty_root",
            result["current_config"]["config"].automatically_generated == "",
        )
        scope.set_tag(
            "stacktrace_link.auto_derived",
            result["current_config"]["config"].automatically_generated is True,
        )
    scope.set_tag("stacktrace_link.has_integration", len(integrations) > 0)


class StacktraceLinkConfig(TypedDict):
    config: RepositoryProjectPathConfig
    outcome: ReposityLinkOutcome
    repository: str


class StacktraceLinkOutcome(TypedDict):
    source_url: str | None
    error: str | None
    current_config: StacktraceLinkConfig | None
    iteration_count: int
    is_munged: bool


def get_stacktrace_config(
    configs: List[RepositoryProjectPathConfig],
    ctx: Dict[str, Optional[str]],
) -> StacktraceLinkOutcome:
    filepath = ctx.get("file")
    result: StacktraceLinkOutcome = {
        "source_url": None,
        "error": None,
        "current_config": None,
        "iteration_count": 0,
        "is_munged": False,
    }
    for config in configs:
        outcome = {}
        munging_outcome = {}

        # Munging is required for get_link to work with mobile platforms
        if ctx["platform"] in ["java", "cocoa", "other"]:
            munging_outcome, next_iteration_count = try_path_munging(
                config, filepath, ctx, result["iteration_count"]
            )
            result["iteration_count"] = next_iteration_count
            if munging_outcome.get("error") == "stack_root_mismatch":
                result["error"] = "stack_root_mismatch"
                continue

        if not munging_outcome:
            if not filepath.startswith(config.stack_root):
                # This may be overwritten if a valid code mapping is found
                result["error"] = "stack_root_mismatch"
                continue

            outcome = get_link(
                config,
                filepath,
                ctx.get("commit_id"),
                ctx.get("group_id"),
                ctx.get("abs_path"),
            )
            result["iteration_count"] += 1
            # XXX: I want to remove this whole block logic as I believe it is wrong
            # In some cases the stack root matches and it can either be that we have
            # an invalid code mapping or that munging is expect it to work
            if not outcome.get("sourceUrl"):
                munging_outcome, next_iteration_count = try_path_munging(
                    config, filepath, ctx, result["iteration_count"]
                )
                result["iteration_count"] = next_iteration_count
                if munging_outcome:
                    # Report errors to Sentry for investigation
                    logger.error("We should never be able to reach this code.")

        # Keep the original outcome if munging failed
        if munging_outcome:
            outcome = munging_outcome
            result["is_munged"] = True

        result["current_config"] = {
            "config": config,
            "outcome": outcome,
            "repository": config.repository,
        }

        # Stop processing if a match is found
        if outcome.get("sourceUrl") and outcome["sourceUrl"]:
            result["source_url"] = outcome["sourceUrl"]
            return result

    return result


@region_silo_endpoint
class ProjectStacktraceLinkEndpoint(ProjectEndpoint):
    publish_status = {
        "GET": ApiPublishStatus.PRIVATE,
    }
    """
    Returns valid links for source code providers so that
    users can go from the file in the stack trace to the
    provider of their choice.

    `file`: The file path from the stack trace
    `commitId` (optional): The commit_id for the last commit of the
                           release associated to the stack trace's event
    `sdkName` (optional): The sdk.name associated with the event
    `absPath` (optional): The abs_path field value of the relevant stack frame
    `module`   (optional): The module field value of the relevant stack frame
    `package`  (optional): The package field value of the relevant stack frame
    `groupId`   (optional): The Issue's id.
    """

    owner = ApiOwner.ISSUES

    def get(self, request: Request, project: Project) -> Response:
        ctx = generate_context(request.GET)
        filepath = ctx.get("file")
        if not filepath:
            return Response({"detail": "Filepath is required"}, status=400)

        integrations = integration_service.get_integrations(organization_id=project.organization_id)
        # TODO(meredith): should use get_provider.has_feature() instead once this is
        # no longer feature gated and is added as an IntegrationFeature
        serializer = IntegrationSerializer()
        serialized_integrations = [
            serialize(i, request.user, serializer)
            for i in integrations
            if i.has_feature(IntegrationFeatures.STACKTRACE_LINK)
        ]

        configs = get_sorted_code_mapping_configs(project)
        if not configs:
            return Response(
                {
                    "config": None,
                    "sourceUrl": None,
                    "integrations": serialized_integrations,
                }
            )

        attempted_url = None
        error = None
        codecov_data = None
        serialized_config = None

        with configure_scope() as scope:
            set_top_tags(scope, project, ctx, len(configs) > 0)
            result = get_stacktrace_config(configs, ctx)
            error = result["error"]

            # Post-processing before exiting scope context
            if result["current_config"]:
                # Use the provider key to split up stacktrace-link metrics by integration type
                serialized_config = serialize(result["current_config"]["config"], request.user)
                provider = serialized_config["provider"]["key"]
                scope.set_tag("integration_provider", provider)  # e.g. github
                scope.set_tag("stacktrace_link.munged", result["is_munged"])

                if not result["source_url"]:
                    error = result["current_config"]["outcome"].get("error")
                    # When no code mapping have been matched we have not attempted a URL
                    if result["current_config"]["outcome"].get("attemptedUrl"):
                        attempted_url = result["current_config"]["outcome"]["attemptedUrl"]

                should_get_coverage = codecov_enabled(project.organization)
                scope.set_tag("codecov.enabled", should_get_coverage)
                if should_get_coverage:
                    with Timer() as t:
                        codecov_data = fetch_codecov_data(
                            config={
                                "repository": result["current_config"]["repository"],
                                "config": serialized_config,
                                "outcome": result["current_config"]["outcome"],
                            }
                        )
                        analytics.record(
                            "function_timer.timed",
                            function_name="fetch_codecov_data",
                            duration=t.duration,
                            organization_id=project.organization_id,
                            project_id=project.id,
                            group_id=ctx.get("group_id"),
                            frame_abs_path=ctx.get("abs_path"),
                        )
            try:
                set_tags(scope, result, serialized_integrations)
            except Exception:
                logger.exception("Failed to set tags.")

        if result["current_config"] and serialized_config:
            analytics.record(
                "integration.stacktrace.linked",
                provider=serialized_config["provider"]["key"],
                config_id=serialized_config["id"],
                project_id=project.id,
                organization_id=project.organization_id,
                filepath=filepath,
                status=error or "success",
                link_fetch_iterations=result["iteration_count"],
            )
            return Response(
                {
                    # TODO(scttcper): Remove error in success case
                    "error": error,
                    "config": serialized_config,
                    "sourceUrl": result["source_url"],
                    "attemptedUrl": attempted_url,
                    "integrations": serialized_integrations,
                    "codecov": codecov_data,
                }
            )

        return Response(
            {
                "error": error,
                "config": serialized_config,
                "sourceUrl": None,
                "attemptedUrl": attempted_url,
                "integrations": serialized_integrations,
                "codecov": codecov_data,
            }
        )
