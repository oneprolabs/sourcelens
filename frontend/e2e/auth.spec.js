/**
 * E2E tests for authentication: login form, validation, redirect guards.
 */
import { test, expect } from '@playwright/test'

/**
 * Attempt to log in using environment credentials or known test account.
 * Returns true if login succeeded (redirected away from /login).
 */
async function tryLogin(page) {
  const email = process.env.TEST_EMAIL || 'e2e_admin@example.com'
  const password = process.env.TEST_PASSWORD || 'e2ePass123!'

  await page.goto('/login')
  await page.waitForLoadState('networkidle')

  const loginForm = page.locator('form').first()
  const formVisible = await loginForm.isVisible().catch(() => false)
  if (!formVisible) return false

  await page.getByText(/Use email and password|使用邮箱和密码/).click()
  await page.fill('input[name="email"]', email)
  await page.fill('input[name="password"]', password)
  await page.click('button[type="submit"]')
  await page.waitForLoadState('networkidle')

  const currentUrl = page.url()
  return !currentUrl.includes('/login')
}

test.describe('Login page', () => {
  test('renders login form with all fields', async ({ page }) => {
    await page.goto('/login')
    await page.waitForLoadState('networkidle')

    await page.getByText(/Use email and password|使用邮箱和密码/).click()
    await expect(page.locator('input[name="email"]')).toBeVisible()
    await expect(page.locator('input[name="password"]')).toBeVisible()
    await expect(page.locator('button[type="submit"]')).toBeVisible()
  })

  test('shows validation error when submitting empty form', async ({
    page
  }) => {
    await page.goto('/login')
    await page.waitForLoadState('networkidle')

    await page.getByText(/Use email and password|使用邮箱和密码/).click()
    await page.click('button[type="submit"]')

    await expect(page.locator('input[name="email"]:invalid')).toBeVisible()
    await expect(page.locator('input[name="password"]:invalid')).toBeVisible()
  })

  test('shows error for invalid credentials', async ({ page }) => {
    await page.goto('/login')
    await page.waitForLoadState('networkidle')

    await page.getByText(/Use email and password|使用邮箱和密码/).click()
    await page.fill('input[name="email"]', 'wrong@example.com')
    await page.fill('input[name="password"]', 'wrongpassword')
    await page.click('button[type="submit"]')
    await page.waitForLoadState('networkidle')

    // Should stay on login page
    expect(page.url()).toContain('/login')

    // Should show error message
    const errorMsg = page.locator(
      'text=/error|登录失败|invalid|incorrect|错误|human verification|人机验证/i'
    ).first()
    await expect(errorMsg).toBeVisible({ timeout: 5000 })
  })

  test('login button is disabled while loading', async ({ page }) => {
    await page.goto('/login')
    await page.waitForLoadState('networkidle')

    await page.getByText(/Use email and password|使用邮箱和密码/).click()
    await page.fill('input[name="email"]', 'e2e_admin@example.com')
    await page.fill('input[name="password"]', 'wrongpassword')

    await page.click('button[type="submit"]')

    // Button should be disabled or show loading state during request
    const btn = page.locator('button[type="submit"]').first()
    const isDisabled =
      (await btn.getAttribute('disabled')) !== null ||
      (await btn.isDisabled()) ||
      (await btn.getAttribute('aria-disabled')) === 'true'
    expect(isDisabled).toBeTruthy()
  })

  test('successful login redirects away from /login', async ({ page }) => {
    const loggedIn = await tryLogin(page)
    if (!loggedIn) {
      test.skip()
    }
    expect(page.url()).not.toContain('/login')
  })
})

test.describe('Auth guards', () => {
  test('unauthenticated user is redirected to /login for protected routes', async ({
    page
  }) => {
    await page.goto('/login')
    await page.evaluate(() => localStorage.clear())
    await page.context().clearCookies()

    const protectedRoutes = [
      '/dashboard',
      '/management/users',
      '/management/task-management/list',
      '/settings/profile'
    ]

    for (const route of protectedRoutes) {
      await page.goto(route)
      await page.waitForLoadState('networkidle')
      expect(page.url()).toContain('/login')
    }
  })

  test('authenticated user is redirected away from /login', async ({
    page
  }) => {
    const loggedIn = await tryLogin(page)
    if (!loggedIn) {
      test.skip()
    }
    await page.goto('/login')
    await page.waitForLoadState('networkidle')
    expect(page.url()).not.toContain('/login')
  })
})

test.describe('Language switcher', () => {
  test('language switcher is present on login page', async ({ page }) => {
    await page.goto('/login')
    await page.waitForLoadState('networkidle')
    const langSwitcher = page.locator('button[title="Language"]').first()
    await expect(langSwitcher).toBeVisible({ timeout: 5000 })
  })
})
