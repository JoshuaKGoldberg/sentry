import {GroupsFixture} from 'sentry-fixture/groups';
import {OrganizationFixture} from 'sentry-fixture/organization';
import {ProjectFixture} from 'sentry-fixture/project';
import {RouterContextFixture} from 'sentry-fixture/routerContextFixture';
import {RouterFixture} from 'sentry-fixture/routerFixture';

import {
  render,
  renderGlobalModal,
  screen,
  userEvent,
  waitFor,
} from 'sentry-test/reactTestingLibrary';

import * as analytics from 'sentry/utils/analytics';
import GroupSimilarIssues from 'sentry/views/issueDetails/groupSimilarIssues';

const MockNavigate = jest.fn();
jest.mock('sentry/utils/useNavigate', () => ({
  useNavigate: () => MockNavigate,
}));

describe('Issues Similar View', function () {
  let mock;

  const project = ProjectFixture({
    features: ['similarity-view'],
  });

  const routerContext = RouterContextFixture([
    {
      router: {
        ...RouterFixture(),
        params: {orgId: 'org-slug', projectId: 'project-slug', groupId: '1000000'},
      },
    },
  ]);

  const scores = [
    {'exception:stacktrace:pairs': 0.375},
    {'exception:stacktrace:pairs': 0.01264},
    {'exception:stacktrace:pairs': 0.875},
    {'exception:stacktrace:pairs': 0.001488},
  ];

  const mockData = {
    similar: GroupsFixture().map((issue, i) => [issue, scores[i]]),
  };

  const router = RouterFixture();

  const analyticsSpy = jest.spyOn(analytics, 'trackAnalytics');

  beforeEach(function () {
    mock = MockApiClient.addMockResponse({
      url: '/organizations/org-slug/issues/1000000/similar/?limit=50',
      body: mockData.similar,
    });
  });

  afterEach(() => {
    MockApiClient.clearMockResponses();
    jest.clearAllMocks();
  });

  const selectNthSimilarItem = async (index: number) => {
    const items = await screen.findAllByTestId('similar-item-row');

    const item = items.at(index);

    expect(item).toBeDefined();

    await userEvent.click(item!);
  };

  it('renders with mocked data', async function () {
    render(
      <GroupSimilarIssues
        project={project}
        params={{orgId: 'org-slug', groupId: '1000000'}}
        location={router.location}
        router={router}
        routeParams={router.params}
        routes={router.routes}
        route={{}}
      />,
      {context: routerContext}
    );

    expect(screen.getByTestId('loading-indicator')).toBeInTheDocument();

    await waitFor(() => expect(mock).toHaveBeenCalled());

    expect(screen.getByText('Show 3 issues below threshold')).toBeInTheDocument();
  });

  it('can merge and redirect to new parent', async function () {
    const merge = MockApiClient.addMockResponse({
      method: 'PUT',
      url: '/projects/org-slug/project-slug/issues/',
      body: {
        merge: {children: ['123'], parent: '321'},
      },
    });

    render(
      <GroupSimilarIssues
        project={project}
        params={{orgId: 'org-slug', groupId: '1000000'}}
        location={router.location}
        router={router}
        routeParams={router.params}
        routes={router.routes}
        route={{}}
      />,
      {context: routerContext}
    );
    renderGlobalModal();

    await selectNthSimilarItem(0);
    await userEvent.click(await screen.findByRole('button', {name: 'Merge (1)'}));
    await userEvent.click(screen.getByRole('button', {name: 'Confirm'}));

    await waitFor(() => {
      expect(merge).toHaveBeenCalledWith(
        '/projects/org-slug/project-slug/issues/',
        expect.objectContaining({
          data: {merge: 1},
        })
      );
    });

    expect(MockNavigate).toHaveBeenCalledWith(
      '/organizations/org-slug/issues/321/similar/'
    );
  });

  it('renders all filtered issues with issues-similarity-embeddings flag', async function () {
    const features = ['issues-similarity-embeddings'];

    render(
      <GroupSimilarIssues
        project={project}
        params={{orgId: 'org-slug', groupId: '1000000'}}
        location={router.location}
        router={router}
        routeParams={router.params}
        routes={router.routes}
        route={{}}
      />,
      {context: routerContext, organization: OrganizationFixture({features})}
    );

    expect(screen.getByTestId('loading-indicator')).toBeInTheDocument();

    await waitFor(() => expect(mock).toHaveBeenCalled());

    expect(screen.queryByText('Show 3 issues below threshold')).not.toBeInTheDocument();
  });

  it('sends issue similarity embeddings agree analytics', async function () {
    const features = ['issues-similarity-embeddings'];

    render(
      <GroupSimilarIssues
        project={project}
        params={{orgId: 'org-slug', groupId: '1000000'}}
        location={router.location}
        router={router}
        routeParams={router.params}
        routes={router.routes}
        route={{}}
      />,
      {context: routerContext, organization: OrganizationFixture({features})}
    );
    renderGlobalModal();

    await selectNthSimilarItem(0);
    await userEvent.click(await screen.findByRole('button', {name: 'Agree (1)'}));
    expect(analyticsSpy).toHaveBeenCalledTimes(1);
    expect(analyticsSpy).toHaveBeenCalledWith(
      'issue_details.similar_issues.similarity_embeddings_feedback_recieved',
      expect.objectContaining({
        parentGroupId: 1000000,
        value: 'Yes',
      })
    );
  });
});
