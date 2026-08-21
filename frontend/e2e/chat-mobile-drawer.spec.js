import { expect, test } from '@playwright/test'

async function mockChat(
  page,
  messageDelays = {},
  language = 'en',
  assistantList = null,
  initialSessions = null
) {
  await page.addInitScript((selectedLanguage) => {
    localStorage.setItem('access_token', 'test-token')
    localStorage.setItem('userLanguage', selectedLanguage)
  }, language)

  const chatSessions = initialSessions || [
    {
      uuid: 'session-1',
      title: 'First session',
      status: 'active',
      pinned_at: null,
      has_shareable_answer: false
    },
    {
      uuid: 'session-2',
      title: 'Second session',
      status: 'active',
      pinned_at: null,
      has_shareable_answer: true
    },
    {
      uuid: 'session-3',
      title: 'Third session',
      status: 'active',
      pinned_at: null,
      has_shareable_answer: false
    }
  ]
  let nextSessionNumber = chatSessions.length + 1

  await page.route('**/api/**', async (route) => {
    const url = new URL(route.request().url())
    const path = url.pathname
    if (!path.startsWith('/api/')) {
      await route.continue()
      return
    }

    const payloads = {
      '/api/v1/auth/user': {
        username: 'tester',
        features: ['workspace'],
        permissions: []
      },
      '/api/lens/assistants/': assistantList || [
        {
          uuid: 'assistant-1',
          slug: 'drawer-test',
          name: 'Drawer Test',
          status: 'active',
          multimodal_model_ref: 'vision-model',
          selected_task: 'knowledge_qa',
          can_process_images: true
        }
      ],
      '/api/lens/shares/': [],
      '/api/lens/sessions/session-1/messages/': [],
      '/api/lens/sessions/session-2/messages/': [
        {
          uuid: 'message-2-user',
          role: 'user',
          content: 'Original second question',
          run: 'run-2',
          created_at: '2026-07-24T07:59:00Z'
        },
        {
          uuid: 'message-2',
          role: 'assistant',
          content: 'Second session response',
          run: 'run-2',
          feedback: 'positive',
          output_files: [
            {
              uuid: 'file-1',
              filename: 'report.pdf',
              byte_size: 1024,
              url: '/api/lens/output-files/file-1/content/'
            }
          ],
          created_at: '2026-07-24T08:00:00Z'
        }
      ],
      '/api/lens/sessions/session-3/messages/': [
        {
          uuid: 'message-3',
          role: 'assistant',
          content: 'Third session response',
          created_at: '2026-07-24T08:01:00Z'
        }
      ]
    }

    if (messageDelays[path]) {
      await new Promise((resolve) => setTimeout(resolve, messageDelays[path]))
    }

    if (path.endsWith('/attachments/')) {
      await route.fulfill({
        contentType: 'application/json',
        body: JSON.stringify({ data: { uuid: 'attachment-1' } })
      })
      return
    }

    if (path === '/api/lens/sessions/' && route.request().method() === 'GET') {
      const archived = url.searchParams.get('archived') === 'true'
      await route.fulfill({
        json: {
          data: chatSessions.filter(
            (session) => session.status === (archived ? 'archived' : 'active')
          )
        }
      })
      return
    }

    if (path === '/api/lens/sessions/' && route.request().method() === 'POST') {
      const session = {
        uuid: `session-${nextSessionNumber}`,
        title: '',
        status: 'active',
        pinned_at: null,
        has_shareable_answer: false,
        created_at: new Date().toISOString()
      }
      nextSessionNumber += 1
      chatSessions.push(session)
      await route.fulfill({ json: { data: session } })
      return
    }

    const sessionDelete = path.match(/^\/api\/lens\/sessions\/(session-\d+)\/$/)
    if (sessionDelete && route.request().method() === 'DELETE') {
      const index = chatSessions.findIndex(
        (item) => item.uuid === sessionDelete[1]
      )
      if (index !== -1) chatSessions.splice(index, 1)
      await route.fulfill({ status: 204, body: '' })
      return
    }

    const sessionAction = path.match(
      /^\/api\/lens\/sessions\/(session-\d+)\/(pin|unpin|archive|restore)\/$/
    )
    if (sessionAction) {
      const session = chatSessions.find(
        (item) => item.uuid === sessionAction[1]
      )
      const action = sessionAction[2]
      if (action === 'pin') session.pinned_at = new Date().toISOString()
      if (action === 'unpin') session.pinned_at = null
      if (action === 'archive') {
        session.status = 'archived'
        session.pinned_at = null
      }
      if (action === 'restore') session.status = 'active'
      await route.fulfill({ json: { data: session } })
      return
    }

    if (path === '/api/lens/runs/run-2/share/') {
      await route.fulfill({
        contentType: 'application/json',
        body: JSON.stringify({
          data: {
            uuid: 'share-1',
            token: 'shared-token',
            title: 'Second session'
          }
        })
      })
      return
    }

    await route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify({ data: payloads[path] ?? [] })
    })
  })
}

async function sidebarBox(page) {
  return page.locator('.sidebar').evaluate((element) => {
    const rect = element.getBoundingClientRect()
    return { x: rect.x, width: rect.width }
  })
}

async function expectMinimumTouchTarget(locator) {
  const box = await locator.boundingBox()
  expect(box).not.toBeNull()
  expect(box.width).toBeGreaterThanOrEqual(44)
  expect(box.height).toBeGreaterThanOrEqual(44)
}

test('mobile session drawer opens, selects sessions, and closes', async ({
  page
}) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await mockChat(page)
  await page.goto('/lens/assistants/drawer-test/chat')

  await expect.poll(() => sidebarBox(page)).toMatchObject({ x: -320 })

  const composer = page.locator('.composer-input')
  const initialComposerHeight = await composer.evaluate(
    (element) => element.getBoundingClientRect().height
  )
  await composer.fill('Session A draft\nsecond line\nthird line')
  await expect
    .poll(() =>
      composer.evaluate((element) => element.getBoundingClientRect().height)
    )
    .toBeGreaterThan(initialComposerHeight)

  await page.getByRole('button', { name: 'Recent' }).click()
  await expect(page.locator('.sidebar')).toHaveClass(/sidebar-open/)
  await expect.poll(() => sidebarBox(page)).toMatchObject({ x: 0, width: 320 })

  const secondSession = page
    .locator('.session-item')
    .filter({ hasText: 'Second session' })
  await expect(secondSession).toBeVisible()
  await secondSession.click()
  await expect(page).toHaveURL(/session=session-2/)
  await expect(page.getByText('Second session response')).toBeAttached()
  await expect(composer).toHaveValue('')
  await expect
    .poll(() =>
      composer.evaluate((element) => element.getBoundingClientRect().height)
    )
    .toBe(initialComposerHeight)

  await page.locator('.sidebar').getByRole('button', { name: 'Close' }).click()
  await expect(page.locator('.sidebar')).not.toHaveClass(/sidebar-open/)
  await expect.poll(() => sidebarBox(page)).toMatchObject({ x: -320 })

  await page.getByRole('button', { name: 'Recent' }).click()
  await expect.poll(() => sidebarBox(page)).toMatchObject({ x: 0 })
  await page
    .locator('div.fixed.inset-0.z-20')
    .click({ position: { x: 380, y: 400 } })
  await expect(page.locator('.sidebar')).not.toHaveClass(/sidebar-open/)
  await expect.poll(() => sidebarBox(page)).toMatchObject({ x: -320 })
})

test('mobile header switches assistants without overflowing', async ({
  page
}) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await mockChat(page, {}, 'en', [
    {
      uuid: 'assistant-1',
      slug: 'drawer-test',
      name: 'Drawer Test',
      status: 'active'
    },
    {
      uuid: 'assistant-2',
      slug: 'mobile-switch',
      name: 'Mobile Switch',
      status: 'active'
    }
  ])
  await page.goto('/lens/assistants/drawer-test/chat')

  const switcher = page.getByRole('button', { name: 'Switch assistant' })
  await expect(switcher).toBeVisible()
  await expectMinimumTouchTarget(switcher)
  await switcher.click()

  const panel = page.locator('.assistant-switcher-panel')
  await expect(panel).toBeVisible()
  const panelBox = await panel.boundingBox()
  expect(panelBox).not.toBeNull()
  expect(panelBox.x).toBeGreaterThanOrEqual(0)
  expect(panelBox.x + panelBox.width).toBeLessThanOrEqual(390)

  await panel.getByRole('button', { name: /Mobile Switch/ }).click()
  await expect(page).toHaveURL('/lens/assistants/mobile-switch/chat')
  await expect(switcher).toContainText('Mobile Switch')
})

test('desktop sidebar still expands and collapses', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 })
  await mockChat(page)
  await page.goto('/lens/assistants/drawer-test/chat')

  await expect.poll(() => sidebarBox(page)).toMatchObject({ x: 0, width: 264 })
  await page.getByRole('button', { name: 'Collapse' }).click()
  await expect.poll(() => sidebarBox(page)).toMatchObject({ x: 0, width: 64 })
  await page.locator('.sidebar-collapse-btn[aria-label="Expand"]').click()
  await expect.poll(() => sidebarBox(page)).toMatchObject({ x: 0, width: 264 })
})

test.describe('touch input accessibility', () => {
  test.use({ hasTouch: true })

  test('chat actions stay visible, accessible, and touchable', async ({
    page
  }) => {
    await page.setViewportSize({ width: 390, height: 844 })
    await mockChat(page)
    await page.goto('/lens/assistants/drawer-test/chat')

    const recent = page.getByRole('button', { name: 'Recent' })
    await expectMinimumTouchTarget(recent)
    await recent.click()
    const firstSession = page
      .locator('.session-item')
      .filter({ hasText: 'First session' })
    const overflow = firstSession.getByRole('button', {
      name: 'Actions for First session'
    })

    await expect(overflow).toBeVisible()
    await expect(firstSession.locator('.session-overflow')).toHaveCSS(
      'opacity',
      '1'
    )
    await expectMinimumTouchTarget(overflow)

    await overflow.click()
    const remove = page.getByRole('menuitem', { name: 'Delete session' })
    await expectMinimumTouchTarget(remove)
    await remove.click()
    const cancelDelete = page.getByRole('button', { name: 'Cancel' })
    await expectMinimumTouchTarget(cancelDelete)
    await cancelDelete.click()

    await page
      .locator('.session-item')
      .filter({ hasText: 'Second session' })
      .click()
    await page
      .locator('.sidebar')
      .getByRole('button', { name: 'Close' })
      .click()

    const copy = page.getByRole('button', { name: 'Copy' })
    const share = page.getByRole('button', { name: 'Share' })
    const retry = page.getByRole('button', { name: 'Retry' })
    const upload = page.getByRole('button', { name: 'Upload image' })
    const submit = page.getByRole('button', { name: 'Submit' })
    const preview = page.getByRole('button', { name: 'Preview' })
    const download = page.getByRole('button', { name: 'Download' })

    for (const action of [
      copy,
      share,
      retry,
      upload,
      submit,
      preview,
      download
    ]) {
      await expect(action).toBeVisible()
      await expectMinimumTouchTarget(action)
    }
    await expect(page.locator('.message-feedback-status')).toHaveCount(0)
    await expect(copy).toHaveAttribute('title', 'Copy')
    await expect(share).toHaveAttribute('title', 'Share')
    await expect(retry).toHaveAttribute('title', 'Retry')

    await page.locator('input[type="file"]').setInputFiles({
      name: 'mobile-test.png',
      mimeType: 'image/png',
      buffer: Buffer.from(
        'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk'
        + '+A8AAQUBAScY42YAAAAASUVORK5CYII=',
        'base64'
      )
    })
    const removeImage = page.getByRole('button', {
      name: /Remove attachment|Remove image/
    })
    const imagePreview = page.locator('.composer-thumb img')
    await expect(removeImage).toBeVisible()
    await expect(imagePreview).toHaveAttribute('src', /^blob:/)
    await expect(removeImage).toHaveAttribute(
      'title',
      /Remove attachment|Remove image/
    )
    await expectMinimumTouchTarget(removeImage)

    await share.click()
    const close = page.locator('.modal-close-btn')
    const createLink = page.getByRole('button', { name: 'Create link' })
    await expectMinimumTouchTarget(close)
    await expectMinimumTouchTarget(createLink)
    await createLink.click()
    await expectMinimumTouchTarget(
      page.getByRole('button', { name: 'Copy link' })
    )
    await expectMinimumTouchTarget(
      page.getByRole('button', { name: 'Stop sharing' })
    )
    await expectMinimumTouchTarget(page.getByRole('button', { name: 'Done' }))
  })

  test('icon action names are localized in Simplified Chinese', async ({
    page
  }) => {
    await page.setViewportSize({ width: 390, height: 844 })
    await mockChat(page, {}, 'zh-CN')
    await page.goto('/lens/assistants/drawer-test/chat')

    await page.getByRole('button', { name: '最近' }).click()
    await page
      .locator('.session-item')
      .filter({ hasText: 'Second session' })
      .click()

    const localizedActions = [
      ['复制', '复制'],
      ['分享', '分享'],
      ['重试', '重试'],
      ['上传图片或文档', '上传图片或文档'],
      ['预览', '预览'],
      ['下载', '下载']
    ]
    for (const [name, title] of localizedActions) {
      await expect(page.getByRole('button', { name })).toHaveAttribute(
        'title',
        title
      )
    }

    await page.locator('input[type="file"]').setInputFiles({
      name: 'mobile-test.png',
      mimeType: 'image/png',
      buffer: Buffer.from(
        'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk'
        + '+A8AAQUBAScY42YAAAAASUVORK5CYII=',
        'base64'
      )
    })
    await expect(
      page.getByRole('button', { name: /移除附件|移除图片/ })
    ).toHaveAttribute('title', /移除附件|移除图片/)
  })
})

test('desktop session actions use one ordered compact overflow menu', async ({
  page
}) => {
  await page.setViewportSize({ width: 1280, height: 800 })
  await mockChat(page)
  await page.goto('/lens/assistants/drawer-test/chat')

  const thirdSession = page
    .locator('.session-item')
    .filter({ hasText: 'Third session' })
  const overflowContainer = thirdSession.locator('.session-overflow')
  const overflow = thirdSession.getByRole('button', {
    name: 'Actions for Third session'
  })

  await expect(overflowContainer).toHaveCSS('opacity', '0')
  const overflowBox = await overflow.boundingBox()
  expect(overflowBox).toMatchObject({ width: 32, height: 32 })

  await thirdSession.hover()
  await expect(overflowContainer).toHaveCSS('opacity', '1')
  await overflow.click()
  await expect(page.getByRole('menuitem')).toHaveText([
    'Share',
    'Rename',
    'Pin chat',
    'Archive',
    'Delete session'
  ])
  await expect(page.getByRole('menuitem', { name: /Share\./ })).toHaveAttribute(
    'aria-disabled',
    'true'
  )
  await page.keyboard.press('Escape')
  await expect(page.getByRole('menu')).toHaveCount(0)
  await expect(overflow).toBeFocused()

  await overflow.click()
  await page
    .locator('div.fixed.inset-0.z-40')
    .click({ position: { x: 5, y: 5 } })
  await expect(page.getByRole('menu')).toHaveCount(0)
  await expect(overflow).toBeFocused()
})

test('sessions can be pinned, archived, and restored from the menu', async ({
  page
}) => {
  await page.setViewportSize({ width: 1280, height: 800 })
  await mockChat(page)
  await page.goto('/lens/assistants/drawer-test/chat')

  const secondSession = page
    .locator('.session-item')
    .filter({ hasText: 'Second session' })
  const openSecondMenu = () =>
    secondSession.getByRole('button', {
      name: 'Actions for Second session'
    })

  await openSecondMenu().click()
  await page.getByRole('menuitem', { name: 'Pin chat' }).click()
  await expect(secondSession.getByLabel('Pinned')).toBeVisible()

  await page.getByRole('button', { name: 'New session' }).click()
  await expect(page.locator('.session-item').first()).toContainText(
    'Second session'
  )
  await expect(page.locator('.session-item').nth(1)).toContainText(
    'Untitled session'
  )

  await openSecondMenu().click()
  await page.getByRole('menuitem', { name: 'Archive' }).click()
  await expect(secondSession).toHaveCount(0)

  await page.getByRole('button', { name: 'Archived' }).click()
  const archivedSession = page
    .locator('.session-item')
    .filter({ hasText: 'Second session' })
  await expect(archivedSession).toBeVisible()
  await archivedSession
    .getByRole('button', { name: 'Actions for Second session' })
    .click()
  await expect(page.getByRole('menuitem')).toHaveText([
    'Share',
    'Rename',
    'Restore',
    'Delete session'
  ])
  await page.getByRole('menuitem', { name: 'Restore' }).click()
  await expect(archivedSession).toHaveCount(0)

  await page.getByRole('button', { name: 'Recent' }).click()
  await expect(
    page.locator('.session-item').filter({ hasText: 'Second session' })
  ).toBeVisible()
})

test('deleting the final session leaves the recent list empty', async ({
  page
}) => {
  await page.setViewportSize({ width: 1280, height: 800 })
  await mockChat(page, {}, 'en', null, [
    {
      uuid: 'session-1',
      title: 'Only session',
      status: 'active',
      pinned_at: null,
      has_shareable_answer: false
    }
  ])
  await page.goto('/lens/assistants/drawer-test/chat')

  const session = page.locator('.session-item')
  await session
    .getByRole('button', { name: 'Actions for Only session' })
    .click()
  await page.getByRole('menuitem', { name: 'Delete session' }).click()
  await page.getByRole('dialog').getByRole('button', { name: 'Delete' }).click()

  await expect(session).toHaveCount(0)
  await expect(page.getByText('No recent sessions')).toBeVisible()
  await expect(page).not.toHaveURL(/session=/)
  await expect(page.getByRole('button', { name: 'Submit' })).toBeDisabled()

  await page.reload()
  await expect(page.locator('.session-item')).toHaveCount(0)
  await expect(page.getByText('No recent sessions')).toBeVisible()
})

test('new sessions clear retry relationships from the previous session', async ({
  page
}) => {
  await page.setViewportSize({ width: 1280, height: 800 })
  await mockChat(page)
  await page.goto('/lens/assistants/drawer-test/chat')

  await page
    .locator('.session-item')
    .filter({ hasText: 'Second session' })
    .click()
  await page.getByRole('button', { name: 'Retry' }).click()

  const composer = page.locator('.composer-input')
  await expect(composer).toHaveValue('Original second question')
  await page.getByRole('button', { name: 'New session' }).click()
  await expect(composer).toHaveValue('')
  await composer.fill('Original second question')

  const runRequest = page.waitForRequest(
    (request) =>
      request.method() === 'POST' &&
      request.url().includes('/sessions/session-4/runs/')
  )
  await page.getByRole('button', { name: 'Submit' }).click()
  const payload = (await runRequest).postDataJSON()

  expect(payload).not.toHaveProperty('retry_of_run_uuid')
})

test('stale session responses do not replace a newer session draft', async ({
  page
}) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await mockChat(page, {
    '/api/lens/sessions/session-2/messages/': 1000
  })
  await page.goto('/lens/assistants/drawer-test/chat')

  await page.getByRole('button', { name: 'Recent' }).click()
  const slowResponse = page.waitForResponse((response) =>
    response.url().includes('/sessions/session-2/messages/')
  )
  await page
    .locator('.session-item')
    .filter({ hasText: 'Second session' })
    .click()
  await page
    .locator('.session-item')
    .filter({ hasText: 'Third session' })
    .click()

  await expect(page).toHaveURL(/session=session-3/)
  await expect(page.getByText('Third session response')).toBeAttached()
  const composer = page.locator('.composer-input')
  await composer.fill('Session C draft\nsecond line\nthird line')
  const draftHeight = await composer.evaluate(
    (element) => element.getBoundingClientRect().height
  )

  const response = await slowResponse
  await response.finished()
  await page.evaluate(
    () =>
      new Promise((resolve) =>
        requestAnimationFrame(() => requestAnimationFrame(resolve))
      )
  )
  await expect(page).toHaveURL(/session=session-3/)
  await expect(page.getByText('Second session response')).not.toBeAttached()
  await expect(page.getByText('Third session response')).toBeAttached()
  await expect(composer).toHaveValue('Session C draft\nsecond line\nthird line')
  await expect
    .poll(() =>
      composer.evaluate((element) => element.getBoundingClientRect().height)
    )
    .toBe(draftHeight)
})
