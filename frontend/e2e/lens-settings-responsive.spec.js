import { expect, test } from '@playwright/test'

const MOBILE_VIEWPORTS = [
  { width: 320, height: 568 },
  { width: 360, height: 800 },
  { width: 375, height: 667 },
  { width: 390, height: 844 },
  { width: 412, height: 915 },
  { width: 430, height: 932 }
]

const DESKTOP_VIEWPORTS = [
  { width: 768, height: 800 },
  { width: 1280, height: 800 }
]

const RESOURCE_SETTINGS_PATH = '/management/lens/resources/settings'
const SETTINGS_PATH = '/management/lens/settings'

function setting(key, value) {
  return { key, value, description: key }
}

async function mockSettingsApis(page) {
  const state = {
    globalSettingsRequests: 0,
    settingUpdates: [],
    taskUpdates: []
  }
  const settings = [
    setting('public_base_url', 'https://lens.example.com'),
    setting('lensnode.image', 'oneprolabs/lensnode:test'),
    setting('lensnode.defaults.timeout', 600),
    setting('retention.run_days', 90),
    setting('lensnode.health.offline_threshold_s', 120),
    setting('lensnode_cleanup.interval_seconds', 3600),
    setting('lensnode_health.interval_seconds', 60),
    setting('run_retention.interval_seconds', 86400),
    setting('lens.skills.generator_model_ref', 'model-1'),
    setting('lens.datasource_sync.timeout_s', 360),
    setting('lens.datasource_sync.workers', 4),
    setting('lens.datasource_conversion.vision_model_ref', 'model-1'),
    setting('lens.datasource_conversion.document_model_ref', 'model-1')
  ]
  const tasks = [
    {
      task_type: 'lensnode_cleanup',
      enabled: true,
      last_run_at: '2026-07-27T08:00:00Z',
      last_status: 'success'
    },
    {
      task_type: 'lensnode_health',
      enabled: true,
      last_run_at: '2026-07-27T08:01:00Z',
      last_status: 'success'
    },
    {
      task_type: 'run_retention',
      enabled: false,
      last_run_at: null,
      last_status: null
    }
  ]

  await page.addInitScript(() => {
    localStorage.setItem('access_token', 'e2e-lens-settings')
    localStorage.setItem('userLanguage', 'en')
  })

  await page.route('**://*/api/**', async (route) => {
    const request = route.request()
    const { pathname } = new URL(request.url())
    const method = request.method()
    let data = []

    if (pathname === '/api/v1/auth/user') {
      data = {
        id: 1,
        username: 'admin',
        is_staff: true,
        is_superuser: true,
        permissions: []
      }
    } else if (pathname === '/api/lens/admin/global-settings/') {
      state.globalSettingsRequests += 1
      data = settings
    } else if (pathname === '/api/lens/admin/global-settings/system-health/') {
      if (method === 'PATCH') {
        const payload = request.postDataJSON()
        state.taskUpdates.push(payload)
        const task = tasks.find((item) => item.task_type === payload.task_type)
        task.enabled = payload.enabled
        data = task
      } else {
        data = tasks
      }
    } else if (
      pathname.startsWith('/api/lens/admin/global-settings/') &&
      method === 'PATCH'
    ) {
      const payload = request.postDataJSON()
      const key = decodeURIComponent(pathname.split('/').at(-2))
      state.settingUpdates.push({ key, ...payload })
      const current = settings.find((item) => item.key === key)
      current.value = payload.value
      data = current
    } else if (pathname === '/api/v1/admin/llm-config/all/') {
      data = [
        { uuid: 'model-1', name: 'Primary model', model: 'gpt-5' },
        { uuid: 'model-2', name: 'Backup model', model: 'gpt-4.1' }
      ]
    }

    await route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify({ data })
    })
  })

  return state
}

async function openSettingsPage(page, path) {
  await page.goto(path)
  await expect(page.locator('.settings-table').first()).toBeVisible()
  await expect(page.locator('.settings-input').first()).toBeVisible()
}

async function expectMobileLayout(page, includeTasks) {
  const layout = await page.evaluate((hasTasks) => {
    const root = document.documentElement
    const main = document.querySelector('.layout-admin main')
    const insideViewport = (element) => {
      const rect = element.getBoundingClientRect()
      return rect.left >= 0 && rect.right <= root.clientWidth + 0.5
    }
    const fitsOwnWidth = (element) =>
      element.scrollWidth <= element.clientWidth + 1
    const controls = [...document.querySelectorAll('.settings-input')]
    const keys = [...document.querySelectorAll('.setting-key')]
    const units = [...document.querySelectorAll('.settings-unit')].filter(
      (element) => element.textContent.trim()
    )
    const tables = [...document.querySelectorAll('.settings-table')]
    const firstRow = document.querySelector('.settings-table tbody tr')
    const firstCells = firstRow.querySelectorAll('.table-cell')
    const taskDetails = hasTasks
      ? [...document.querySelectorAll('.task-detail')]
      : []

    return {
      documentFits: root.scrollWidth === root.clientWidth,
      mainFits: main.scrollWidth <= main.clientWidth + 1,
      tablesFit: tables.every(fitsOwnWidth),
      controlsFit: controls.every(insideViewport),
      keysFit: keys.every(fitsOwnWidth),
      unitsFit: units.every(insideViewport),
      taskDetailsFit: taskDetails.every(insideViewport),
      tableDisplay: getComputedStyle(tables[0]).display,
      rowDisplay: getComputedStyle(firstRow).display,
      cellDisplays: [...firstCells].map(
        (cell) => getComputedStyle(cell).display
      )
    }
  }, includeTasks)

  expect(layout).toMatchObject({
    documentFits: true,
    mainFits: true,
    tablesFit: true,
    controlsFit: true,
    keysFit: true,
    unitsFit: true,
    taskDetailsFit: true,
    tableDisplay: 'block',
    rowDisplay: 'block',
    cellDisplays: ['block', 'block']
  })

  await expect(page.getByRole('button', { name: 'Refresh' })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Reset' })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Save Changes' })).toBeVisible()
}

for (const viewport of MOBILE_VIEWPORTS) {
  test(`settings stay reachable at ${viewport.width}x${viewport.height}`, async ({
    page
  }) => {
    await page.setViewportSize(viewport)
    await mockSettingsApis(page)

    await openSettingsPage(page, RESOURCE_SETTINGS_PATH)
    await expectMobileLayout(page, false)

    await openSettingsPage(page, SETTINGS_PATH)
    await expectMobileLayout(page, true)
    await expect(
      page.locator('.scheduled-tasks input[type="checkbox"]')
    ).toHaveCount(3)
  })
}

test('resource settings controls edit, reset, save, and refresh on mobile', async ({
  page
}) => {
  await page.setViewportSize({ width: 320, height: 568 })
  const state = await mockSettingsApis(page)
  await openSettingsPage(page, RESOURCE_SETTINGS_PATH)

  const timeout = page.locator('.settings-input[type="number"]').first()
  const model = page.locator('.settings-table select').first()
  await timeout.fill('12')
  await model.locator('xpath=..').getByRole('combobox').click()
  await page.getByRole('option', { name: 'Backup model' }).click()
  await page.getByRole('button', { name: 'Reset' }).click()
  await expect(timeout).toHaveValue('6')
  await expect(model).toHaveValue('model-1')

  await timeout.fill('10')
  await model.locator('xpath=..').getByRole('combobox').click()
  await page.getByRole('option', { name: 'Backup model' }).click()
  await page.getByRole('button', { name: 'Save Changes' }).click()
  await expect.poll(() => state.settingUpdates.length).toBe(4)
  expect(
    state.settingUpdates.find(
      (update) => update.key === 'lens.datasource_sync.timeout_s'
    )?.value
  ).toBe(600)

  const requestsBeforeRefresh = state.globalSettingsRequests
  await page.getByRole('button', { name: 'Refresh' }).click()
  await expect
    .poll(() => state.globalSettingsRequests)
    .toBeGreaterThan(requestsBeforeRefresh)
})

test('global settings and scheduled tasks remain operable on mobile', async ({
  page
}) => {
  await page.setViewportSize({ width: 320, height: 568 })
  const state = await mockSettingsApis(page)
  await openSettingsPage(page, SETTINGS_PATH)

  const publicUrl = page.locator('.settings-input[type="text"]').first()
  const model = page.locator('.settings-table select').first()
  await publicUrl.fill('https://changed.example.com')
  await model.locator('xpath=..').getByRole('combobox').click()
  await page.getByRole('option', { name: 'Backup model' }).click()
  await page.getByRole('button', { name: 'Reset' }).click()
  await expect(publicUrl).toHaveValue('https://lens.example.com')
  await expect(model).toHaveValue('model-1')

  await publicUrl.fill('https://saved.example.com')
  await page.getByRole('button', { name: 'Save Changes' }).click()
  await expect.poll(() => state.settingUpdates.length).toBe(9)
  expect(
    state.settingUpdates.find((update) => update.key === 'public_base_url')
      ?.value
  ).toBe('https://saved.example.com')

  const cleanupToggle = page
    .locator('.scheduled-tasks input[type="checkbox"]')
    .first()
  await expect(cleanupToggle).toBeChecked()
  await cleanupToggle.click()
  await expect
    .poll(() => state.taskUpdates)
    .toEqual([{ task_type: 'lensnode_cleanup', enabled: false }])
  await expect(cleanupToggle).not.toBeChecked()
})

for (const viewport of DESKTOP_VIEWPORTS) {
  test(`desktop tables remain unchanged at ${viewport.width}px`, async ({
    page
  }) => {
    await page.setViewportSize(viewport)
    await mockSettingsApis(page)

    for (const path of [RESOURCE_SETTINGS_PATH, SETTINGS_PATH]) {
      await openSettingsPage(page, path)
      const layout = await page
        .locator('.settings-table')
        .first()
        .evaluate((table) => {
          const row = table.querySelector('tbody tr')
          const cells = row.querySelectorAll('.table-cell')
          const tableWidth = table.getBoundingClientRect().width
          return {
            tableDisplay: getComputedStyle(table).display,
            rowDisplay: getComputedStyle(row).display,
            cellDisplays: [...cells].map(
              (cell) => getComputedStyle(cell).display
            ),
            firstColumnRatio:
              cells[0].getBoundingClientRect().width / tableWidth
          }
        })

      expect(layout.tableDisplay).toBe('table')
      expect(layout.rowDisplay).toBe('table-row')
      expect(layout.cellDisplays).toEqual(['table-cell', 'table-cell'])
      expect(layout.firstColumnRatio).toBeGreaterThan(0.45)
      expect(layout.firstColumnRatio).toBeLessThan(0.56)
    }
  })
}
