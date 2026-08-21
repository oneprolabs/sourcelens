import { expect, test } from '@playwright/test'

const RUN_UUID = '11111111-1111-4111-8111-111111111111'
const FILE_UUID = '22222222-2222-4222-8222-222222222222'

function runDetail(outputFiles) {
  return {
    uuid: RUN_UUID,
    status: 'done',
    username: 'run-owner',
    assistant_name: 'Report assistant',
    assistant_slug: 'report-assistant',
    question: 'Generated report request',
    answer: 'The report is ready.',
    started_at: '2026-07-27T02:29:59Z',
    finished_at: '2026-07-27T02:30:00Z',
    created_at: '2026-07-27T02:29:58Z',
    duration_seconds: 1,
    lensnode_name: 'Test LensNode',
    event_count: 0,
    subagent_count: 0,
    llm_calls: 0,
    total_tokens: 0,
    prompt_tokens: 0,
    completion_tokens: 0,
    total_cost: null,
    agent_rounds: 10,
    attachments: [],
    steps: [],
    execution: null,
    error: '',
    output_files: outputFiles
  }
}

async function mockAdminRunApis(page, outputFiles) {
  await page.addInitScript(() => {
    localStorage.setItem('access_token', 'e2e-admin-run-files')
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
    } else if (pathname === '/api/lens/assistants/') {
      data = []
    } else if (pathname === '/api/lens/admin/runs/') {
      data = {
        results: [runDetail(outputFiles)],
        total: 1,
        page: 1,
        page_size: 20
      }
    } else if (pathname === `/api/lens/admin/runs/${RUN_UUID}/`) {
      data = runDetail(outputFiles)
    } else if (pathname === `/api/lens/output-files/${FILE_UUID}/`) {
      await route.fulfill({
        contentType: 'text/plain',
        body: 'generated report'
      })
      return
    }

    await route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify({ data })
    })
  })
}

test('admin run detail previews and downloads generated files', async ({
  page
}) => {
  await mockAdminRunApis(page, [
    {
      uuid: FILE_UUID,
      url: `/api/lens/output-files/${FILE_UUID}/`,
      filename: 'report.txt',
      content_type: 'text/plain',
      byte_size: 1536,
      created_at: '2026-07-27T02:30:00Z'
    }
  ])

  await page.goto('/management/lens/runs')
  await page.getByRole('cell', { name: 'Generated report request' }).click()
  await page.getByTestId('run-files-tab').click()

  await expect(page.getByText('report.txt')).toBeVisible()
  await expect(page.getByText('text/plain')).toBeVisible()
  await expect(page.getByText('1.5 KB')).toBeVisible()
  await expect(page.getByTestId('output-file-created')).toHaveText(/07-27/)

  await page.getByTestId('preview-output-file').click()
  await expect(page.locator('.preview-title')).toHaveText('report.txt')
  await expect(page.locator('.preview-text')).toHaveText('generated report')
  await page.getByRole('button', { name: 'Close', exact: true }).click()

  const downloadPromise = page.waitForEvent('download')
  await page.getByTestId('download-output-file').click()
  const download = await downloadPromise
  expect(download.suggestedFilename()).toBe('report.txt')
})

test('admin run detail shows an empty generated-files state', async ({
  page
}) => {
  await mockAdminRunApis(page, [])

  await page.goto('/management/lens/runs')
  await page.getByRole('cell', { name: 'Generated report request' }).click()
  await page.getByTestId('run-files-tab').click()

  await expect(page.getByTestId('run-files-empty')).toBeVisible()
})
