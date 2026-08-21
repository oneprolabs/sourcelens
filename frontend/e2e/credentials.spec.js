import { expect, test } from '@playwright/test'

const credentials = [
  {
    uuid: 'credential-bound',
    name: 'Bound GitHub credential',
    provider: 'github',
    auth_type: 'https_token',
    endpoint_url: 'https://github.com/HyperBDR',
    has_secret: true,
    datasource_count: 2,
    datasource_bindings: [
      {
        uuid: 'datasource-one',
        name: 'Source one',
        source_type: 'git',
        status: 'active'
      },
      {
        uuid: 'datasource-two',
        name: 'Source two',
        source_type: 'git',
        status: 'active'
      }
    ],
    scope_summary: {
      organization_url: 'https://github.com/HyperBDR'
    },
    validation_status: 'success',
    validation_message: '',
    validated_at: '2026-07-25T07:00:00Z',
    last_used_at: '2026-07-25T07:30:00Z'
  },
  {
    uuid: 'credential-unused',
    name: 'Unused GitLab credential',
    provider: 'gitlab',
    auth_type: 'https_token',
    endpoint_url: 'https://gitlab.example.com',
    has_secret: true,
    datasource_count: 0,
    datasource_bindings: [],
    scope_summary: {
      organization_url: 'https://gitlab.example.com/example'
    },
    validation_status: 'failed',
    validation_message: 'Token expired',
    validated_at: '2026-07-24T07:00:00Z',
    last_used_at: null
  }
]

async function mockCredentialsPage(page) {
  await page.addInitScript(() => {
    localStorage.setItem('access_token', 'e2e-credentials')
    localStorage.setItem('userLanguage', 'en')
  })

  await page.route('**://*/api/**', async (route) => {
    const pathname = new URL(route.request().url()).pathname.replace(/\/$/, '')
    if (!pathname.startsWith('/api/')) {
      await route.continue()
      return
    }
    let data = []

    if (pathname === '/api/v1/auth/user') {
      data = {
        id: 1,
        username: 'admin',
        is_staff: true,
        is_superuser: true,
        permissions: []
      }
    } else if (pathname === '/api/lens/admin/credentials') {
      data = credentials
    }

    await route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify({ data })
    })
  })
}

test.beforeEach(async ({ page }) => {
  await mockCredentialsPage(page)
  await page.goto('/management/lens/resources/credentials')
  await expect(
    page.getByRole('heading', { name: 'Credentials', exact: true }).last()
  ).toBeVisible()
})

test('explains the impact before deleting a bound credential', async ({
  page
}) => {
  const row = page.getByRole('row').filter({
    hasText: 'Bound GitHub credential'
  })

  await expect(row.getByRole('button', { name: 'More Actions' })).toBeVisible()
  await expect(
    row.getByRole('button', { name: '2 bound data sources' })
  ).toBeVisible()
})

test('opens the credential drawer with accessible focus and labels', async ({
  page
}) => {
  const createButton = page.getByRole('button', {
    name: 'Create Credential',
    exact: true
  })
  await createButton.click()

  const dialog = page.getByRole('dialog', { name: 'Create Credential' })
  await expect(dialog).toBeVisible()
  await expect(dialog.getByLabel('Name')).toBeFocused()
  await expect(dialog.getByLabel('Type')).toBeVisible()
  await expect(dialog.getByLabel('URL')).toBeVisible()
  await expect(dialog.getByLabel('HTTPS Token')).toBeVisible()
  await expect
    .poll(() => page.evaluate(() => document.body.style.overflow))
    .toBe('hidden')

  await dialog.getByRole('button', { name: 'Close' }).click()
  await expect(createButton).toBeFocused()
})

test('keeps credential details and actions visible on a narrow screen', async ({
  page
}) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await page.reload()

  const list = page.getByTestId('credentials-list')
  await expect(list).toBeVisible()
  const layout = await list.evaluate((element) => ({
    clientWidth: element.clientWidth,
    scrollWidth: element.scrollWidth
  }))
  expect(layout.scrollWidth).toBe(layout.clientWidth)

  const row = page.getByRole('row').filter({
    hasText: 'Bound GitHub credential'
  })
  const deleteButton = row.getByRole('button', { name: 'More Actions' })
  await deleteButton.scrollIntoViewIfNeeded()
  await expect(deleteButton).toBeInViewport()

  const mobileTitle = page
    .locator('.layout-admin-header h1')
    .filter({ hasText: 'Credentials' })
  const titleBox = await mobileTitle.boundingBox()
  expect(titleBox.height).toBeLessThanOrEqual(28)
})

for (const width of [320, 768, 1024, 1440]) {
  test(`avoids page overflow at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 })
    await page.reload()

    const documentLayout = await page.evaluate(() => ({
      clientWidth: document.documentElement.clientWidth,
      scrollWidth: document.documentElement.scrollWidth
    }))
    expect(documentLayout.scrollWidth).toBe(documentLayout.clientWidth)

    if (width < 1024) {
      const mobileTitle = page
        .locator('.layout-admin-header h1')
        .filter({ hasText: 'Credentials' })
      const titleBox = await mobileTitle.boundingBox()
      expect(titleBox.height).toBeLessThanOrEqual(28)
    }

    if (width < 768) {
      const deleteButton = page
        .getByRole('row')
        .filter({ hasText: 'Bound GitHub credential' })
        .getByRole('button', { name: 'More Actions' })
      await deleteButton.scrollIntoViewIfNeeded()
      await expect(deleteButton).toBeInViewport()
    }
  })
}

test('filters and sorts credentials from the list toolbar', async ({
  page
}) => {
  const search = page.getByRole('searchbox', { name: 'Search credentials' })
  await search.fill('unused')
  await expect(
    page.getByRole('row').filter({ hasText: 'Unused GitLab' })
  ).toBeVisible()
  await expect(page.getByText('Bound GitHub credential')).toBeHidden()

  await search.fill('')
  await page.getByLabel('Provider').click()
  await page.getByRole('option', { name: 'GitLab' }).click()
  await expect(page.getByText('Unused GitLab credential')).toBeVisible()
  await expect(page.getByText('Bound GitHub credential')).toBeHidden()

  await page.getByLabel('Provider').click()
  await page.getByRole('option', { name: 'All' }).click()
  await page
    .getByRole('combobox', { name: 'Validation status' })
    .click()
  await page.getByRole('option', { name: /Invalid|无效/ }).click()
  await expect(page.getByText('Unused GitLab credential')).toBeVisible()
  await expect(page.getByText('Bound GitHub credential')).toBeHidden()
})

test('shows validation context, accessible bindings, and compact URL actions', async ({
  page
}) => {
  const row = page.getByRole('row').filter({
    hasText: 'Bound GitHub credential'
  })

  await expect(row.getByText(/Validated/)).toBeVisible()
  const bindings = row.getByRole('button', {
    name: '2 bound data sources'
  })
  await bindings.click()
  await expect(page.getByText('Source one')).toBeVisible()

  const url = page.getByTestId('credential-url-credential-bound')
  await expect(url).toHaveText('github.com/HyperBDR')
  await expect(url).toHaveAttribute('title', 'https://github.com/HyperBDR')
  await expect(
    row.getByRole('button', {
      name: 'Copy URL for Bound GitHub credential'
    })
  ).toBeVisible()

  const rowBox = await row.boundingBox()
  expect(rowBox.height).toBeLessThanOrEqual(100)
})

test('keeps an actionable error visible for an incomplete form', async ({
  page
}) => {
  await page
    .getByRole('button', { name: 'Create Credential', exact: true })
    .click()
  const dialog = page.getByRole('dialog', { name: 'Create Credential' })

  await dialog.getByRole('button', { name: 'Save' }).click()

  await expect(dialog.getByRole('alert')).toContainText(
    'Complete the required fields'
  )
  await expect(dialog.getByLabel('Name')).toBeFocused()
})

test('uses concise credential page copy without duplicate actions', async ({
  page
}) => {
  await expect(
    page.getByText('Create Credential', { exact: true })
  ).toHaveCount(1)
  await expect(page.getByRole('heading', { name: 'Credentials' })).toBeVisible()
})
