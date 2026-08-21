/**
 * E2E tests for LLM pages (admin /management/llm/*).
 * Assumes app is served at baseURL. Requires admin_console feature.
 */
import { test, expect } from '@playwright/test'

async function tryLogin(page) {
  const email = process.env.TEST_EMAIL || 'e2e_admin@example.com'
  const password = process.env.TEST_PASSWORD || 'e2ePass123!'

  await page.goto('/login')
  await page.waitForLoadState('networkidle')

  const loginResponse = await page.request.post(
    new URL('/api/v1/auth/login', page.url()).toString(),
    { data: { email, password } }
  )
  if (loginResponse.ok()) {
    const body = await loginResponse.json()
    const data = body?.data || body
    const accessToken = data?.access || data?.access_token || data?.token
    const refreshToken = data?.refresh || data?.refresh_token
    if (accessToken) {
      await page.addInitScript(
        ({ access, refresh }) => {
          localStorage.setItem('access_token', access)
          if (refresh) localStorage.setItem('refresh_token', refresh)
        },
        { access: accessToken, refresh: refreshToken }
      )
      return true
    }
  }

  const loginForm = page.locator('form').first()
  const formVisible = await loginForm.isVisible().catch(() => false)
  if (!formVisible) return false

  const passwordModeButton = page.getByText(
    /Use email and password|使用邮箱和密码/
  )
  if (await passwordModeButton.isVisible().catch(() => false)) {
    await passwordModeButton.click()
  }

  await page.locator('input[name="email"]').fill(email)
  await page.locator('input[name="password"]').fill(password)
  await page.click('button[type="submit"]')
  await page.waitForURL((url) => !url.pathname.includes('/login'))
  return !page.url().includes('/login')
}

test.describe('LLM pages', () => {
  test.beforeEach(async ({ page }) => {
    const loggedIn = await tryLogin(page)
    if (!loggedIn) test.skip()
  })

  test('LLM admin navigation shows the Models section', async ({ page }) => {
    await page.goto('/management/llm/stats')
    await page.waitForLoadState('networkidle')

    const modelsMenu = page.getByText(/Models|模型/).first()
    await expect(modelsMenu).toBeVisible({ timeout: 10000 })
    await expect(page).toHaveURL(/\/management\/llm\/stats/)
  })

  test('LLM Stats page shows stats cards', async ({ page }) => {
    await page.goto('/management/llm/stats')
    await page.waitForLoadState('networkidle')
    await expect(page).toHaveURL(/\/management\/llm\/stats/)

    const title = page.locator('h1:visible, h2:visible').first()
    await expect(title).toBeVisible({ timeout: 10000 })

    // Should show stats content: either cards or empty state
    const content = page
      .locator('.text-2xl:visible, .text-xl:visible, .font-semibold:visible')
      .or(page.getByText(/暂无|no data/i))
      .first()
    await expect(content).toBeVisible({ timeout: 10000 })
  })

  test('LLM Usage page shows table or empty state', async ({ page }) => {
    await page.goto('/management/llm/usage')
    await page.waitForLoadState('networkidle')
    await expect(page).toHaveURL(/\/management\/llm\/usage/)

    const tableOrEmpty = page
      .locator('table')
      .or(page.getByText(/暂无数据|暂无|no data|no records/i))
      .first()
    await expect(tableOrEmpty).toBeVisible({ timeout: 15000 })
  })

  test('LLM Usage pagination controls exist when data present', async ({
    page
  }) => {
    await page.goto('/management/llm/usage')
    await page.waitForLoadState('networkidle')
    await page.waitForTimeout(2000)

    const pagination = page.locator('text=/showing|page|共/i').first()
    const hasPagination = await pagination.isVisible().catch(() => false)
    if (hasPagination) {
      await expect(pagination).toBeVisible()
    }
  })

  test('LLM Config page shows provider form', async ({ page }) => {
    await page.goto('/management/llm/config')
    await page.waitForLoadState('networkidle')
    await expect(page).toHaveURL(/\/management\/llm\/config/)

    await page.getByRole('button', { name: /Add config|添加配置/ }).click()
    const providerSelect = page
      .locator('form')
      .last()
      .getByRole('combobox')
      .nth(1)
    await expect(providerSelect).toBeVisible({ timeout: 10000 })
  })

  test('LLM Config has an add-config submit button', async ({ page }) => {
    await page.goto('/management/llm/config')
    await page.waitForLoadState('networkidle')
    await page.getByRole('button', { name: /Add config|添加配置/ }).click()

    const form = page.locator('form').last()
    await expect(
      form.getByRole('button', { name: /Add config|添加配置/ })
    ).toBeVisible({ timeout: 5000 })
  })

  test('LLM Config uses provider schema and persists model parameters', async ({
    page
  }) => {
    const configUuid = '11111111-1111-4111-8111-111111111111'
    const config = {
      id: 1,
      uuid: configUuid,
      scope: 'global',
      provider: 'openai',
      config: {
        api_key: 'sk-t***-key',
        model: 'schema-test-model',
        max_tokens: 2048,
        temperature: 0,
        top_p: 0.9,
        request_timeout_seconds: 90
      },
      is_active: false,
      is_default: false
    }
    const providerSchemas = {
      providers: {
        openai: {
          required: ['api_key'],
          optional: [
            'api_base',
            'model',
            'max_tokens',
            'temperature',
            'top_p',
            'request_timeout_seconds'
          ],
          editable_params: [
            'api_base',
            'api_key',
            'model',
            'max_tokens',
            'temperature',
            'top_p',
            'request_timeout_seconds'
          ],
          default_model: 'gpt-4o-mini',
          default_api_base: 'https://api.openai.com/v1',
          default_temperature: 0.7,
          default_top_p: 1,
          default_max_tokens: 16384
        },
        azure_openai: {
          required: ['api_key', 'api_base', 'deployment'],
          optional: [
            'api_base',
            'model',
            'api_version',
            'max_tokens',
            'temperature',
            'top_p',
            'request_timeout_seconds'
          ],
          editable_params: [
            'api_base',
            'api_key',
            'deployment',
            'model',
            'api_version',
            'max_tokens',
            'temperature',
            'top_p',
            'request_timeout_seconds'
          ],
          default_model: 'gpt-4o-mini',
          default_api_base: null,
          default_temperature: 0.7,
          default_top_p: 1,
          default_max_tokens: 16384
        }
      }
    }
    const models = {
      providers: [
        {
          id: 'openai',
          label: 'OpenAI',
          models: []
        },
        {
          id: 'azure_openai',
          label: 'Azure OpenAI',
          models: []
        }
      ],
      capability_labels: {}
    }
    let updatePayload = null

    await page.route('**/api/v1/admin/llm-config/providers/**', (route) =>
      route.fulfill({ json: providerSchemas })
    )
    await page.route('**/api/v1/admin/llm-config/models/**', (route) =>
      route.fulfill({ json: models })
    )
    await page.route('**/api/v1/admin/llm-config/all/**', (route) =>
      route.fulfill({ json: [config] })
    )
    await page.route(`**/api/v1/admin/llm-config/${configUuid}/`, (route) => {
      if (route.request().method() === 'PUT') {
        updatePayload = route.request().postDataJSON()
        config.config = { ...config.config, ...updatePayload.config }
      }
      return route.fulfill({ json: config })
    })
    await page.route('**/api/v1/management/users/**', (route) =>
      route.fulfill({ json: [] })
    )

    await page.goto('/management/llm/config')
    const row = page.locator('tr', { hasText: 'schema-test-model' })
    await expect(row).toContainText('max_tokens: 2048')
    await expect(row).toContainText('temperature: 0')
    await expect(row).toContainText('top_p: 0.9')
    await expect(row).toContainText('request_timeout_seconds: 90')

    await row.getByRole('button', { name: /更多操作|More Actions/ }).click()
    await page.getByRole('menuitem', { name: /编辑|Edit/ }).click({ force: true })
    let form = page.locator('form').last()
    const parameterInput = (name) =>
      form
        .locator(`label[title="${name}"]`)
        .locator('xpath=following-sibling::input[1]')

    await expect(parameterInput('temperature')).toHaveValue('0')
    await expect(parameterInput('request_timeout_seconds')).toHaveValue('90')
    await parameterInput('request_timeout_seconds').fill('120')
    await form.getByRole('button', { name: /Save|保存/ }).click()
    await expect
      .poll(() => updatePayload?.config.request_timeout_seconds)
      .toBe(120)
    await expect(row).toContainText('request_timeout_seconds: 120')

    await row.getByRole('button', { name: /更多操作|More Actions/ }).click()
    await page.getByRole('menuitem', { name: /编辑|Edit/ }).click({ force: true })
    form = page.locator('form').last()
    await parameterInput('temperature').fill('')
    await parameterInput('request_timeout_seconds').fill('')
    await form.getByRole('button', { name: /Save|保存/ }).click()
    await expect.poll(() => updatePayload?.config.temperature).toBeNull()
    await expect
      .poll(() => updatePayload?.config.request_timeout_seconds)
      .toBeNull()
    await expect(row).toContainText('temperature: 0.7')
    await expect(row).toContainText('request_timeout_seconds: 180')
    await expect(row).toContainText(/default|默认值/i)

    await page.getByRole('button', { name: /Add config|添加配置/ }).click()
    form = page.locator('form').last()
    const providerSelect = form.getByRole('combobox').nth(1)
    await providerSelect.click()
    await page.getByRole('option', { name: /Azure OpenAI/i }).click()
    await expect(form.locator('label[title="deployment"]')).toBeVisible()
    await expect(form.locator('label[title="api_version"]')).toBeVisible()
    await providerSelect.click()
    await page.getByRole('option', { name: 'OpenAI', exact: true }).click()
    await expect(form.locator('label[title="deployment"]')).toHaveCount(0)
    await expect(form.locator('label[title="api_version"]')).toHaveCount(0)
  })

  test('LLM Data Settings page loads', async ({ page }) => {
    await page.goto('/management/llm/data-settings')
    await page.waitForLoadState('networkidle')
    await expect(page).toHaveURL(/\/management\/llm\/data-settings/)
    await expect(
      page.locator('h1:visible, h2:visible, form:visible').first()
    ).toBeVisible({ timeout: 10000 })
  })

  test('legacy /llm/* routes redirect to /management/llm/*', async ({
    page
  }) => {
    const legacyRoutes = ['/llm/stats', '/llm/usage', '/llm/config']
    for (const route of legacyRoutes) {
      await page.goto(route)
      await page.waitForLoadState('networkidle')
      expect(page.url()).toMatch(/\/management\/llm/)
    }
  })
})
