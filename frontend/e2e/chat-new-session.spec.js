import { expect, test } from '@playwright/test'

async function mockChat(page, createStatus = 200) {
  await page.addInitScript(() => {
    localStorage.setItem('access_token', 'test-token')
  })

  await page.route('**/api/**', async (route) => {
    const request = route.request()
    const path = new URL(request.url()).pathname
    if (!path.startsWith('/api/')) {
      await route.continue()
      return
    }

    if (path === '/api/lens/sessions/' && request.method() === 'POST') {
      await route.fulfill({
        status: createStatus,
        contentType: 'application/json',
        body: JSON.stringify({
          data:
            createStatus === 200
              ? { uuid: 'session-2', title: '', status: 'active' }
              : { detail: 'Create failed' }
        })
      })
      return
    }

    const payloads = {
      '/api/v1/auth/user': {
        username: 'tester',
        features: ['workspace'],
        permissions: []
      },
      '/api/lens/assistants/': [
        {
          uuid: 'assistant-1',
          slug: 'draft-test',
          name: 'Draft Test',
          status: 'active',
          selected_task: 'knowledge_qa'
        }
      ],
      '/api/lens/shares/': [],
      '/api/lens/sessions/': [
        { uuid: 'session-1', title: 'Existing', status: 'active' }
      ],
      '/api/lens/sessions/session-1/messages/': []
    }

    await route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify({ data: payloads[path] ?? [] })
    })
  })
}

test('starting a new session clears the composer draft', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 })
  await mockChat(page)
  await page.goto('/lens/assistants/draft-test/chat')

  const composer = page.locator('.composer-input')
  await expect(composer).toBeVisible()
  const initialHeight = await composer.evaluate(
    (element) => element.getBoundingClientRect().height
  )
  const draft = 'Unsent draft\nwith several\nlines\nfrom the old session'
  await composer.fill(draft)
  await expect
    .poll(() =>
      composer.evaluate((element) => element.getBoundingClientRect().height)
    )
    .toBeGreaterThan(initialHeight)

  await page.locator('.new-chat-btn').click()

  await expect(page).toHaveURL(/session=session-2/)
  await expect(composer).toHaveValue('')
  await expect
    .poll(() =>
      composer.evaluate((element) => element.getBoundingClientRect().height)
    )
    .toBe(initialHeight)
})

test('a failed session creation preserves the composer draft', async ({
  page
}) => {
  await page.setViewportSize({ width: 1280, height: 800 })
  await mockChat(page, 500)
  await page.goto('/lens/assistants/draft-test/chat')

  const composer = page.locator('.composer-input')
  await expect(composer).toBeVisible()
  await composer.fill('Keep this draft if creation fails')

  const responsePromise = page.waitForResponse(
    (response) =>
      new URL(response.url()).pathname === '/api/lens/sessions/' &&
      response.request().method() === 'POST'
  )
  await page.locator('.new-chat-btn').click()
  await responsePromise

  await expect(page).toHaveURL(/session=session-1/)
  await expect(composer).toHaveValue('Keep this draft if creation fails')
})
