import { expect, test } from '@playwright/test'

const assistants = [
  { uuid: 'assistant-alpha', slug: 'alpha', name: 'Alpha', status: 'active' },
  { uuid: 'assistant-beta', slug: 'beta', name: 'Beta', status: 'active' },
  { uuid: 'assistant-gamma', slug: 'gamma', name: 'Gamma', status: 'active' }
]

async function mockAdminChat(
  page,
  { deleteSessionOnAdminNavigation = false } = {}
) {
  let sessionDeleted = false

  await page.addInitScript(() => {
    localStorage.setItem('access_token', 'test-token')
  })

  await page.route('**/api/**', async (route) => {
    const request = route.request()
    const path = new URL(request.url()).pathname.replace(/\/$/, '')
    if (!path.startsWith('/api/')) {
      await route.continue()
      return
    }
    if (
      deleteSessionOnAdminNavigation &&
      path === '/api/v1/management/users'
    ) {
      sessionDeleted = true
    }
    if (path === '/api/lens/sessions' && request.method() === 'POST') {
      await route.fulfill({
        status: 201,
        contentType: 'application/json',
        body: JSON.stringify({
          data: {
            uuid: 'replacement-session',
            title: '',
            assistant_slug: 'alpha',
            status: 'active'
          }
        })
      })
      return
    }

    const payloads = {
      '/api/v1/auth/user': {
        username: 'admin',
        is_staff: true,
        features: ['workspace'],
        permissions: []
      },
      '/api/lens/assistants/': assistants,
      '/api/lens/shares/': [],
      '/api/lens/sessions/': sessionDeleted
        ? []
        : [{ uuid: 'alpha-session', title: 'Alpha history' }],
      '/api/lens/sessions/alpha-session/messages/': [
        { uuid: 'message-1', role: 'user', content: 'Keep this history' }
      ],
      '/api/lens/sessions/replacement-session/messages/': [],
      '/api/v1/management/users/': []
    }

    const payload = payloads[path] ?? payloads[`${path}/`] ?? []
    await route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify({ data: payload })
    })
  })
}

test('returning from admin restores the current assistant and session', async ({
  page
}) => {
  await mockAdminChat(page)
  await page.goto('/lens/assistants/alpha/chat?session=alpha-session')

  const switchAssistant = page.getByRole('button', {
    name: /切换助手|Switch assistant/
  })
  await switchAssistant.click()
  await page.getByRole('button', { name: 'Beta' }).click()
  await expect(page).toHaveURL(/\/lens\/assistants\/beta\/chat/)
  await switchAssistant.click()
  await page.getByRole('button', { name: 'Gamma' }).click()
  await expect(page).toHaveURL(/\/lens\/assistants\/gamma\/chat/)
  await switchAssistant.click()
  await page.locator('.assistant-switcher-item', { hasText: 'Alpha' }).click()
  await expect(page).toHaveURL(/\/lens\/assistants\/alpha\/chat/)
  await expect(page.getByText('Keep this history')).toBeVisible()
  await page.locator('.dock-trigger').click()
  await page.getByText('Admin Console', { exact: true }).click()
  await expect(page).toHaveURL(/\/management\/users/)

  await page.locator('a[aria-label="Back to Assistant"]').click()
  await expect(page).toHaveURL(
    /\/lens\/assistants\/alpha\/chat\?session=alpha-session/
  )
  await expect(page.getByText('Keep this history')).toBeVisible()
})

test('a deleted session is replaced after returning from admin', async ({
  page
}) => {
  await mockAdminChat(page, { deleteSessionOnAdminNavigation: true })
  await page.goto('/lens/assistants/alpha/chat?session=alpha-session')
  await expect(page.getByText('Keep this history')).toBeVisible()
  await page.locator('.dock-trigger').click()
  await page.getByText('Admin Console', { exact: true }).click()
  await expect(page).toHaveURL(/\/management\/users/)

  await page.locator('a[aria-label="Back to Assistant"]').click()

  await expect(page).toHaveURL(
    /\/lens\/assistants\/alpha\/chat\?session=replacement-session/
  )
  await expect(page).not.toHaveURL(/\/lens\/assistants\/beta|gamma/)
})
