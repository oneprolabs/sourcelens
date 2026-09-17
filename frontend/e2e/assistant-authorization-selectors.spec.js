import { expect, test } from '@playwright/test'

const assistant = {
  uuid: 'private-assistant',
  name: 'Private Assistant',
  slug: 'private-assistant',
  status: 'active',
  visibility: 'private',
  lensnode: 'test-lensnode',
  selected_task: 'general_chat',
  agent_model_ref: 'test-model',
  skill_bindings: [{ skill_uuid: 'test-skill', enabled: true }],
  access_grants: [
    { type: 'group', id: 201, name: 'Assigned Outside Group Page' },
    {
      type: 'user',
      id: 101,
      name: 'assigned-outside-user',
      username: 'assigned-outside-user',
      email: 'assigned@example.com'
    }
  ]
}

async function mockAssistantsPage(page) {
  await page.setExtraHTTPHeaders({ 'Cache-Control': 'no-cache' })
  await page.addInitScript(() => {
    localStorage.setItem('access_token', 'test-token')
    localStorage.setItem('userLanguage', 'en')
  })

  await page.route('**/api/**', async (route) => {
    const url = new URL(route.request().url())
    const path = url.pathname
    if (!path.startsWith('/api/')) {
      await route.continue()
      return
    }
    let payload = []

    if (path === '/api/v1/auth/user') {
      payload = { username: 'admin', is_staff: true }
    } else if (path === '/api/lens/assistants/') {
      payload = [assistant]
    } else if (path === '/api/lens/admin/lensnodes/') {
      payload = [
        {
          uuid: 'test-lensnode',
          name: 'Test LensNode',
          tasks: [{ name: 'general_chat', title: 'General Chat' }]
        }
      ]
    } else if (path === '/api/lens/admin/skills/') {
      payload = [{ uuid: 'test-skill', name: 'Test Skill', slug: 'test-skill' }]
    } else if (path === '/api/v1/admin/llm-config/all/') {
      payload = [
        {
          uuid: 'test-model',
          provider: 'test',
          config: { model: 'test-model' }
        }
      ]
    } else if (path === '/api/v1/management/groups/') {
      const requestedPage = Number(url.searchParams.get('page'))
      const search = url.searchParams.get('search')
      payload = {
        count: search ? 1 : 21,
        page: requestedPage,
        page_size: 20,
        results: search
          ? [{ id: 401, name: 'Searched Group' }]
          : requestedPage === 2
            ? [{ id: 301, name: 'Group From Page Two' }]
            : Array.from({ length: 20 }, (_, index) => ({
                id: index + 1,
                name: `Default Group ${index + 1}`
              }))
      }
    } else if (path === '/api/v1/management/users/') {
      const search = url.searchParams.get('search')
      const requestedPage = Number(url.searchParams.get('page'))
      payload = {
        count: search ? 1 : 21,
        page: requestedPage,
        page_size: 20,
        results: search
          ? [
              {
                id: 8,
                username: 'search-target',
                display_name: 'search-target',
                email: 'target@example.com'
              }
            ]
          : requestedPage === 2
            ? [
                {
                  id: 301,
                  username: 'user-from-page-two',
                  display_name: 'user-from-page-two',
                  email: 'page-two@example.com'
                }
              ]
            : Array.from({ length: 20 }, (_, index) => ({
                id: index + 1,
                username: `default-user-${index + 1}`,
                display_name: `default-user-${index + 1}`,
                email: `default-${index + 1}@example.com`
              }))
      }
    }

    await route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify({ data: payload })
    })
  })
}

async function openAccessStep(page) {
  await page.goto('/management/lens/assistants')
  await page.getByRole('button', { name: 'Edit' }).click()
  const drawer = page.getByRole('dialog', { name: 'Edit Assistant' })
  await drawer.getByRole('button', { name: 'Next' }).click()
  await drawer.getByRole('button', { name: 'Next' }).click()
  await drawer.getByRole('button', { name: 'Next' }).click()
  return drawer
}

test('keeps assignments visible during incremental loading and search', async ({
  page
}) => {
  await mockAssistantsPage(page)
  const userRequest = page.waitForRequest((request) => {
    const url = new URL(request.url())
    return (
      url.pathname === '/api/v1/management/users/' &&
      url.searchParams.get('compact') === 'true'
    )
  })
  const drawer = await openAccessStep(page)
  await userRequest

  const groupSelector = drawer.getByTestId('authorized-groups-selector')
  const userSelector = drawer.getByTestId('authorized-users-selector')
  await expect(
    groupSelector.getByTestId('authorized-group-option').first()
  ).toContainText('Assigned Outside Group Page')
  await expect(
    groupSelector.getByTestId('authorized-group-option')
  ).toHaveCount(21)
  await expect(
    userSelector.getByTestId('authorized-user-option').first()
  ).toContainText('assigned-outside-user')
  await expect(userSelector.getByTestId('authorized-user-option')).toHaveCount(
    21
  )
  await expect(
    groupSelector.getByRole('button', { name: 'Next', exact: true })
  ).toHaveCount(0)
  await expect(
    userSelector.getByRole('button', { name: 'Next', exact: true })
  ).toHaveCount(0)

  const groupNextRequest = page.waitForRequest((request) => {
    const url = new URL(request.url())
    return (
      url.pathname === '/api/v1/management/groups/' &&
      url.searchParams.get('page') === '2'
    )
  })
  await groupSelector
    .locator('.overflow-y-auto')
    .evaluate((element) => element.scrollTo(0, element.scrollHeight))
  await groupNextRequest
  await expect(
    groupSelector.getByTestId('authorized-group-option').last()
  ).toContainText('Group From Page Two')

  const groupSearchRequest = page.waitForRequest((request) => {
    const url = new URL(request.url())
    return (
      url.pathname === '/api/v1/management/groups/' &&
      url.searchParams.get('search') === 'searched' &&
      url.searchParams.get('compact') === 'true'
    )
  })
  await drawer.getByTestId('authorized-group-search').fill('searched')
  await groupSearchRequest
  await expect(
    groupSelector.getByTestId('authorized-group-option').last()
  ).toContainText('Searched Group')

  const userNextRequest = page.waitForRequest((request) => {
    const url = new URL(request.url())
    return (
      url.pathname === '/api/v1/management/users/' &&
      url.searchParams.get('page') === '2' &&
      !url.searchParams.has('search')
    )
  })
  await userSelector
    .locator('.overflow-y-auto')
    .evaluate((element) => element.scrollTo(0, element.scrollHeight))
  await userNextRequest
  await expect(
    userSelector.getByTestId('authorized-user-option').last()
  ).toContainText('user-from-page-two')

  await expect(
    userSelector.getByTestId('authorized-user-option').first()
  ).toContainText('assigned-outside-user')

  await userSelector
    .getByTestId('authorized-user-option')
    .first()
    .getByRole('checkbox')
    .uncheck()
  await expect(drawer.getByTestId('authorized-users-count')).toHaveText('0')

  const searchRequest = page.waitForRequest((request) => {
    const url = new URL(request.url())
    return (
      url.searchParams.get('search') === 'target' &&
      url.searchParams.get('compact') === 'true'
    )
  })
  await drawer.getByTestId('authorized-user-search').fill('target')
  await searchRequest
  const searchResult = userSelector
    .getByTestId('authorized-user-option')
    .filter({ hasText: 'search-target' })
  await searchResult.getByRole('checkbox').check()
  await expect(drawer.getByTestId('authorized-users-count')).toHaveText('1')

  const defaultRequest = page.waitForRequest((request) => {
    const url = new URL(request.url())
    return (
      url.pathname === '/api/v1/management/users/' &&
      !url.searchParams.has('search')
    )
  })
  await drawer.getByTestId('authorized-user-search').fill('')
  await defaultRequest
  await expect(
    userSelector.getByTestId('authorized-user-option').first()
  ).toContainText(/search-target.*target@example.com/s)
  await expect(userSelector.getByTestId('authorized-user-option')).toHaveCount(
    20
  )
  await expect(drawer.getByTestId('authorized-users-count')).toHaveText('1')
})
