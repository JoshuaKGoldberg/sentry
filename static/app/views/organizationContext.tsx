import {Component, createContext, useCallback, useEffect, useRef} from 'react';
import * as Sentry from '@sentry/react';

import {fetchOrganizationDetails} from 'sentry/actionCreators/organization';
import {openSudo} from 'sentry/actionCreators/sudoModal';
import {DEPLOY_PREVIEW_CONFIG} from 'sentry/constants';
import {SentryPropTypeValidators} from 'sentry/sentryPropTypeValidators';
import ConfigStore from 'sentry/stores/configStore';
import OrganizationsStore from 'sentry/stores/organizationsStore';
import OrganizationStore from 'sentry/stores/organizationStore';
import {useLegacyStore} from 'sentry/stores/useLegacyStore';
import {Organization} from 'sentry/types';
import {metric} from 'sentry/utils/analytics';
import getRouteStringFromRoutes from 'sentry/utils/getRouteStringFromRoutes';
import useApi from 'sentry/utils/useApi';
import {useParams} from 'sentry/utils/useParams';
import {useRoutes} from 'sentry/utils/useRoutes';

export const OrganizationContext = createContext<Organization | null>(null);

interface Props {
  children: React.ReactNode;
}

/**
 * There are still a number of places where we consume the lgacy organization
 * context. So for now we still need a component that provides this.
 */
class LegacyOrganizationContextProvider extends Component<{value: Organization | null}> {
  static childContextTypes = {
    organization: SentryPropTypeValidators.isOrganization,
  };

  getChildContext() {
    return {organization: this.props.value};
  }

  render() {
    return this.props.children;
  }
}
/**
 * Context provider responsible for loading the organization into the
 * OrganizationStore if it is not already present.
 */
export function OrganizationContextProvider({children}: Props) {
  const api = useApi();
  const configStore = useLegacyStore(ConfigStore);

  const {organizations} = useLegacyStore(OrganizationsStore);
  const {organization, error} = useLegacyStore(OrganizationStore);

  const hasMadeFirstFetch = useRef(false);

  const lastOrganization: string | null =
    configStore.lastOrganization ?? organizations[0]?.slug ?? null;

  const routes = useRoutes();
  const params = useParams<{orgId?: string}>();

  // XXX(epurkhiser): When running in deploy preview mode customer domains are
  // not supported correctly. Do NOT use the customer domain from the params.
  const orgSlug = DEPLOY_PREVIEW_CONFIG
    ? lastOrganization
    : params.orgId || lastOrganization;

  const handleLoad = useCallback(() => {
    if (!orgSlug) {
      return;
    }

    metric.mark({name: 'organization-details-fetch-start'});
    fetchOrganizationDetails(api, orgSlug, false, hasMadeFirstFetch.current);
    hasMadeFirstFetch.current = true;
  }, [api, orgSlug]);

  // If the organization slug differs from what we have in the organization
  // store reload the store
  useEffect(() => {
    // Nothing to do if we already have the organization loaded
    if (organization && organization.slug === orgSlug) {
      return;
    }

    handleLoad();
  }, [orgSlug, organization, handleLoad]);

  // Take a measurement for when organization details are done loading and the
  // new state is applied
  useEffect(
    () => {
      if (organization === null) {
        return;
      }

      metric.measure({
        name: 'app.component.perf',
        start: 'organization-details-fetch-start',
        data: {
          name: 'org-details',
          route: getRouteStringFromRoutes(routes),
          organization_id: parseInt(organization.id, 10),
        },
      });
    },
    // Ignore the `routes` dependency for the metrics measurement
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [organization]
  );

  // Configure sentry SDK scope to have organization tag
  useEffect(() => {
    if (organization === null || error) {
      return;
    }

    Sentry.configureScope(scope => {
      // XXX(dcramer): this is duplicated in sdk.py on the backend
      scope.setTag('organization', organization.id);
      scope.setTag('organization.slug', organization.slug);
      scope.setContext('organization', {id: organization.id, slug: organization.slug});
    });
  }, [organization, error]);

  const {user} = configStore;

  // If we've had an error it may be possible for the user to use the sudo
  // modal to load the organization.
  useEffect(() => {
    if (!error) {
      return;
    }

    if (user.isSuperuser && error.status === 403) {
      openSudo({isSuperuser: true, needsReload: true});
    }

    // This `catch` can swallow up errors in development (and tests)
    // So let's log them. This may create some noise, especially the test case where
    // we specifically test this branch
    console.error(error); // eslint-disable-line no-console
  }, [user, error]);

  return (
    <OrganizationContext.Provider value={organization}>
      <LegacyOrganizationContextProvider value={organization}>
        {children}
      </LegacyOrganizationContextProvider>
    </OrganizationContext.Provider>
  );
}
