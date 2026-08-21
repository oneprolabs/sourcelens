/**
 * E2E smoke tests for Lens pages.
 */
import { test, expect } from '@playwright/test'

async function tryLogin(page) {
  const email = process.env.TEST_EMAIL || 'e2e_admin@example.com'
  const password = process.env.TEST_PASSWORD || 'e2ePass123!'

  await page.goto('/login')
  await page.waitForLoadState('networkidle')

  const response = await page.request.post('/api/v1/auth/login', {
    data: { email, password }
  })
  if (response.ok()) {
    const body = await response.json()
    const access = body?.data?.access
    if (access) {
      await page.evaluate((token) => {
        localStorage.setItem('access_token', token)
      }, access)
      return true
    }
  }

  const loginForm = page.locator('form').first()
  const formVisible = await loginForm.isVisible().catch(() => false)
  if (!formVisible) return false

  const passwordMode = page.getByText(/Use email and password|使用邮箱和密码/)
  if (await passwordMode.isVisible().catch(() => false)) {
    await passwordMode.click()
  }
  await page.fill('input[name="email"]', email)
  await page.fill('input[name="password"]', password)
  await page.click('button[type="submit"]')
  await page.waitForLoadState('networkidle')
  return !page.url().includes('/login')
}

test.describe('Lens pages', () => {
  test.beforeEach(async ({ page }) => {
    const loggedIn = await tryLogin(page)
    if (!loggedIn) test.skip()
  })

  test('assistants page renders', async ({ page }) => {
    await page.goto('/lens/assistants')
    await page.waitForLoadState('networkidle')

    await expect(page).toHaveURL(/\/lens\/assistants/)
    await expect(page.getByRole('main')).toBeVisible({
      timeout: 10000
    })
  })

  test('admin resources page renders', async ({ page }) => {
    await page.goto('/lens/admin/resources')
    await page.waitForLoadState('networkidle')

    await expect(page).toHaveURL(/\/management\/lens\/resources/)
    await expect(page.getByRole('main')).toBeVisible({
      timeout: 10000
    })
  })
})
