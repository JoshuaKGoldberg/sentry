from sentry.services.hybrid_cloud.usersocialauth.service import usersocialauth_service
from sentry.shared_integrations.client import BaseApiClient, BaseInternalApiClient
from sentry.shared_integrations.exceptions import ApiUnauthorized


class ApiClient(BaseApiClient):
    integration_type = "plugin"

    metrics_prefix = "sentry-plugins"

    log_path = "sentry.plugins.client"

    plugin_name = "undefined"


class AuthApiClient(ApiClient):
    auth = None

    def __init__(self, auth=None, *args, **kwargs):
        self.auth = auth
        super().__init__(*args, **kwargs)

    def has_auth(self):
        return self.auth and "access_token" in self.auth.tokens

    def exception_means_unauthorized(self, exc):
        return isinstance(exc, ApiUnauthorized)

    def ensure_auth(self, **kwargs):
        headers = kwargs["headers"]
        if "Authorization" not in headers and self.has_auth() and "auth" not in kwargs:
            kwargs = self.bind_auth(**kwargs)
        return kwargs

    def bind_auth(self, **kwargs):
        token = self.auth.tokens["access_token"]
        kwargs["headers"]["Authorization"] = f"Bearer {token}"
        return kwargs

    def _request(self, method, path, **kwargs):
        headers = kwargs.setdefault("headers", {})
        headers.setdefault("Accept", "application/json, application/xml")

        # TODO(dcramer): we could proactively refresh the token if we knew
        # about expires
        kwargs = self.ensure_auth(**kwargs)

        try:
            return ApiClient._request(self, method, path, **kwargs)
        except Exception as exc:
            if not self.exception_means_unauthorized(exc):
                raise
            if not self.auth:
                raise

        # refresh token
        self.logger.info(
            "token.refresh", extra={"auth_id": self.auth.id, "provider": self.auth.provider}
        )
        usersocialauth_service.refresh_token(filter={"id": self.auth.id})
        kwargs = self.bind_auth(**kwargs)
        return ApiClient._request(self, method, path, **kwargs)


class InternalApiClient(BaseInternalApiClient):
    integration_type = "plugin"

    metrics_prefix = "sentry-plugins"

    log_path = "sentry.plugins.client"

    plugin_name = "undefined"
