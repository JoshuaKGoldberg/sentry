import {useEffect, useRef} from 'react';
import {RouteComponentProps} from 'react-router';

import {switchOrganization} from 'sentry/actionCreators/organizations';
import useRouteAnalyticsHookSetup from 'sentry/utils/routeAnalytics/useRouteAnalyticsHookSetup';
import OrganizationLayout from 'sentry/views/organizationLayout';

import Body from './body';

interface Props extends RouteComponentProps<{orgId?: string}, {}> {
  children: React.ReactNode;
}

function OrganizationDetails({children, ...props}: Props) {
  // Switch organizations when the orgId changes
  const orgId = useRef(props.params.orgId);
  useRouteAnalyticsHookSetup();
  useEffect(() => {
    if (props.params.orgId && orgId.current !== props.params.orgId) {
      // Only switch on: org1 -> org2
      // Not on: undefined -> org1
      // Also avoid: org1 -> undefined -> org1
      if (orgId.current) {
        switchOrganization();
      }

      orgId.current = props.params.orgId;
    }
  }, [props.params.orgId]);

  return (
    <OrganizationLayout includeSidebar>
      <Body>{children}</Body>
    </OrganizationLayout>
  );
}

export default OrganizationDetails;
