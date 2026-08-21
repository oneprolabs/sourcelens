import { expect, test } from '@playwright/test'

const assistants = [
  {
    uuid: 'knowledge-assistant',
    name: 'Knowledge Assistant',
    slug: 'knowledge-assistant',
    selected_task: 'knowledge_qa'
  },
  {
    uuid: 'legacy-assistant',
    name: 'Legacy Assistant',
    slug: 'legacy-assistant',
    selected_task: 'qa'
  },
  {
    uuid: 'code-assistant',
    name: 'Code Assistant',
    slug: 'code-assistant',
    selected_task: 'code_analysis'
  },
  {
    uuid: 'chat-assistant',
    name: 'Chat Assistant',
    slug: 'chat-assistant',
    selected_task: 'general_chat'
  },
  {
    uuid: 'future-assistant',
    name: 'Future Assistant',
    slug: 'future-assistant',
    selected_task: 'future_assistant'
  },
  {
    uuid: 'empty-assistant',
    name: 'Empty Assistant',
    slug: 'empty-assistant',
    selected_task: ''
  }
].map((assistant) => ({
  ...assistant,
  status: 'inactive',
  visibility: 'public'
}))

const lensnodes = [
  {
    uuid: 'test-lensnode',
    name: 'Test LensNode',
    tasks: [
      { name: 'knowledge_qa', title: 'Knowledge Q&A' },
      { name: 'code_analysis', title: 'Code Analysis' },
      { name: 'general_chat', title: 'General Chat' }
    ]
  }
]

async function mockAssistantsPage(page) {
  await page.setExtraHTTPHeaders({ 'Cache-Control': 'no-cache' })
  await page.addInitScript(() => {
    localStorage.setItem('access_token', 'test-token')
    localStorage.setItem('userLanguage', 'en')
  })

  await page.route('**/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname
    if (!path.startsWith('/api/')) {
      await route.continue()
      return
    }

    const payloads = {
      '/api/v1/auth/user': { username: 'admin', is_staff: true },
      '/api/lens/assistants/': assistants,
      '/api/lens/admin/lensnodes/': lensnodes,
      '/api/v1/admin/llm-config/all/': [
        {
          uuid: 'test-model',
          provider: 'test',
          config: { model: 'test-model' }
        }
      ]
    }

    await route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify({ data: payloads[path] ?? [] })
    })
  })
}

function assistantTypeCell(page, assistantName) {
  return page
    .getByRole('row')
    .filter({ hasText: assistantName })
    .getByRole('cell')
    .nth(2)
}

test('localizes assistant types with readable fallback values', async ({
  page
}) => {
  await mockAssistantsPage(page)
  await page.goto('/management/lens/assistants')

  await expect(page.getByRole('columnheader', { name: 'Type' })).toBeVisible()
  await expect(assistantTypeCell(page, 'Knowledge Assistant')).toHaveText(
    'Knowledge Q&A'
  )
  await expect(assistantTypeCell(page, 'Legacy Assistant')).toHaveText(
    'Knowledge Q&A'
  )
  await expect(assistantTypeCell(page, 'Code Assistant')).toHaveText(
    'Code Analysis'
  )
  await expect(assistantTypeCell(page, 'Chat Assistant')).toHaveText(
    'General Chat'
  )
  await expect(assistantTypeCell(page, 'Future Assistant')).toHaveText(
    'future assistant'
  )
  await expect(assistantTypeCell(page, 'Empty Assistant')).toHaveText('—')

  await page.locator('button[title="Language"]').click()
  await page.getByRole('button', { name: /简体中文/ }).click()

  await expect(page.getByRole('columnheader', { name: '类型' })).toBeVisible()
  await expect(assistantTypeCell(page, 'Knowledge Assistant')).toHaveText(
    '知识问答'
  )
  await expect(assistantTypeCell(page, 'Knowledge Assistant')).toHaveCSS(
    'white-space',
    'nowrap'
  )
  await expect(assistantTypeCell(page, 'Legacy Assistant')).toHaveText(
    '知识问答'
  )
  await expect(assistantTypeCell(page, 'Code Assistant')).toHaveText('代码分析')
  await expect(assistantTypeCell(page, 'Chat Assistant')).toHaveText('通用对话')
  await expect(assistantTypeCell(page, 'Future Assistant')).toHaveText(
    'future assistant'
  )
  await expect(assistantTypeCell(page, 'Empty Assistant')).toHaveText('—')

  await page.getByRole('button', { name: '新建 Assistant' }).click()
  const drawer = page.getByRole('dialog', { name: '新建 Assistant' })
  const textInputs = drawer.locator('input.form-input:not([type="number"])')
  await textInputs.nth(0).fill('Localized Assistant')
  await textInputs.nth(1).fill('localized-assistant')
  await drawer.getByRole('combobox').first().click()
  await page.getByRole('option', { name: 'test-model' }).click()
  await drawer.getByRole('button', { name: '下一步' }).click()

  const executionSelects = drawer.locator('select[aria-hidden="true"]')
  await drawer.getByRole('combobox').first().click()
  await page.getByRole('option', { name: 'Test LensNode' }).click()
  await expect(drawer.getByText('类型', { exact: true })).toBeVisible()
  await expect(executionSelects.nth(1).locator('option')).toHaveText([
    '请选择类型',
    '知识问答',
    '代码分析',
    '通用对话'
  ])
})
