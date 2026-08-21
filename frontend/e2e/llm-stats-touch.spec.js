import { expect, test } from '@playwright/test'

async function mockStats(page) {
  await page.addInitScript(() => {
    localStorage.setItem('access_token', 'e2e-llm-stats-touch')
    localStorage.setItem('userLanguage', 'en')
  })

  await page.route('**://*/api/**', async (route) => {
    const { pathname } = new URL(route.request().url())
    let data = []

    if (pathname === '/api/v1/auth/user') {
      data = {
        id: 1,
        username: 'admin',
        is_staff: true,
        is_superuser: true,
        permissions: []
      }
    } else if (pathname === '/api/v1/admin/users/') {
      data = [{ id: 1, username: 'admin' }]
    } else if (pathname === '/api/v1/admin/token-stats/') {
      data = {
        total_tokens: 0,
        total_calls: 0,
        total_cost_usd: 0,
        by_model: [],
        by_provider: [],
        series: { items: [] }
      }
    }

    await route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify({ data })
    })
  })
}

async function expectMinimumTouchTarget(locator, minimum = 44) {
  const box = await locator.boundingBox()
  expect(box).not.toBeNull()
  expect(box.width).toBeGreaterThanOrEqual(minimum)
  expect(box.height).toBeGreaterThanOrEqual(minimum)
}

test.describe('LLM Stats touch targets', () => {
  test.use({ hasTouch: true })

  test('filters and ranges remain touchable at the tablet breakpoint', async ({
    page
  }) => {
    await page.setViewportSize({ width: 768, height: 1024 })
    await mockStats(page)
    await page.goto('/management/llm/stats')

    await expect(
      page.getByRole('main').getByRole('heading', {
        name: 'Stats',
        exact: true
      })
    ).toBeVisible()

    const ranges = page.locator('.stats-granularity-btn')
    await expect(ranges).toHaveCount(3)
    for (const range of await ranges.all()) {
      await expectMinimumTouchTarget(range)
    }

    await expectMinimumTouchTarget(page.getByRole('combobox').first(), 38)
    const userSelectBox = await page.getByRole('combobox').first().boundingBox()
    const filterSectionBox = await page
      .locator('section[aria-label="Filters"]')
      .boundingBox()
    expect(userSelectBox).not.toBeNull()
    expect(filterSectionBox).not.toBeNull()
    expect(userSelectBox.width).toBeLessThanOrEqual(filterSectionBox.width)
    await expectMinimumTouchTarget(page.locator('input[type="date"]'), 36)
    await expectMinimumTouchTarget(
      page.getByRole('button', { name: 'Refresh' })
    )

    await ranges.nth(1).click()
    await expectMinimumTouchTarget(page.locator('input[type="month"]'), 36)

    await ranges.nth(2).click()
    await expectMinimumTouchTarget(page.getByRole('combobox').nth(1), 26)
  })
})

test('LLM Stats keeps compact controls for a desktop fine pointer', async ({
  page
}) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  await mockStats(page)
  await page.goto('/management/llm/stats')

  const rangeBox = await page
    .locator('.stats-granularity-btn')
    .first()
    .boundingBox()
  const refreshBox = await page
    .getByRole('button', { name: 'Refresh' })
    .boundingBox()

  expect(rangeBox).not.toBeNull()
  expect(refreshBox).not.toBeNull()
  expect(rangeBox.height).toBeLessThan(44)
  expect(refreshBox.height).toBeLessThan(44)
})
