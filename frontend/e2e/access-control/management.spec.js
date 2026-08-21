/**
 * Admin management console: group CRUD + membership, user disable-only
 * (never delete), and the cascade that revokes a group's assistant grants
 * when the group is deleted.
 */
import { expect, test } from '@playwright/test'

import { asRole, authHeader, fixtures } from './helpers.js'

const f = fixtures()
const ADMIN = authHeader('admin')
const USER_ID = f.user_ids.user
const PRIVATE_UUID = f.assistants.private.uuid

function data(body) {
  return body?.data ?? body
}

async function createGroup(request, userIds = []) {
  const res = await request.post('/api/v1/management/groups/', {
    headers: ADMIN,
    data: {
      name: `e2e_mgmt_${Date.now()}_${Math.floor(Math.random() * 1e6)}`,
      user_ids: userIds
    }
  })
  expect(res.ok()).toBeTruthy()
  return data(await res.json())
}

async function listGroups(request) {
  const res = await request.get('/api/v1/management/groups/?page_size=1000', {
    headers: ADMIN
  })
  const d = data(await res.json())
  return Array.isArray(d) ? d : (d.results ?? [])
}

function deleteGroup(request, id) {
  return request.delete(`/api/v1/management/groups/${id}/`, { headers: ADMIN })
}

async function privateGrants(request) {
  const res = await request.get(`/api/lens/assistants/${PRIVATE_UUID}/`, {
    headers: ADMIN
  })
  return data(await res.json()).access_grants
}

test.describe('Management: groups', () => {
  test('create and delete a group', async ({ request }) => {
    const group = await createGroup(request)
    expect((await listGroups(request)).some((g) => g.id === group.id)).toBe(
      true
    )
    const del = await deleteGroup(request, group.id)
    expect(del.status()).toBe(204)
    expect((await listGroups(request)).some((g) => g.id === group.id)).toBe(
      false
    )
  })

  test('add and remove a member', async ({ request }) => {
    const group = await createGroup(request)
    try {
      const added = await request.patch(
        `/api/v1/management/groups/${group.id}/`,
        { headers: ADMIN, data: { user_ids: [USER_ID] } }
      )
      expect(data(await added.json()).user_count).toBe(1)

      const removed = await request.patch(
        `/api/v1/management/groups/${group.id}/`,
        { headers: ADMIN, data: { user_ids: [] } }
      )
      expect(data(await removed.json()).user_count).toBe(0)
    } finally {
      await deleteGroup(request, group.id)
    }
  })
})

test.describe('Management: users are disabled, never deleted', () => {
  test('user delete endpoint is not allowed (405)', async ({ request }) => {
    const res = await request.delete(`/api/v1/management/users/${USER_ID}/`, {
      headers: ADMIN
    })
    expect(res.status()).toBe(405)
  })

  test('disable then re-enable a user', async ({ request }) => {
    try {
      const off = await request.patch(`/api/v1/management/users/${USER_ID}/`, {
        headers: ADMIN,
        data: { is_active: false }
      })
      expect(off.ok()).toBeTruthy()

      const list = await request.get(
        '/api/v1/management/users/?page_size=1000',
        { headers: ADMIN }
      )
      const users = data(await list.json()).results ?? data(await list.json())
      expect(users.find((u) => u.id === USER_ID).is_active).toBe(false)
    } finally {
      await request.patch(`/api/v1/management/users/${USER_ID}/`, {
        headers: ADMIN,
        data: { is_active: true }
      })
    }
  })
})

test.describe('Management: exact user filters', () => {
  test.beforeEach(async ({ page }) => {
    await asRole(page, 'admin')
    await page.goto('/management/users')
    await expect(page.getByTestId('username-filter-input')).toBeVisible()
    await expect(page.getByTestId('email-filter-input')).toBeVisible()
  })

  test('filters exactly, preserves refresh, and resets', async ({ page }) => {
    const username = f.users.user
    const input = page.getByTestId('username-filter-input')

    await input.fill(username)
    await page.getByTestId('user-filter-submit').click()

    await expect(page.locator('tbody tr')).toHaveCount(1)
    await expect(page.locator('tbody tr')).toContainText(username)

    const refreshResponse = page.waitForResponse((response) => {
      const url = new URL(response.url())
      return (
        url.pathname === '/api/v1/management/users/' &&
        url.searchParams.get('username') === username
      )
    })
    await page.getByRole('button', { name: 'Refresh' }).click()
    await refreshResponse
    await expect(page.locator('tbody tr')).toHaveCount(1)

    await input.fill(username.toUpperCase())
    await input.press('Enter')
    await expect(page.locator('tbody tr')).toHaveCount(0)
    await expect(page.getByText('No data')).toBeVisible()

    await page.getByTestId('user-filter-reset').click()
    await expect(page.locator('tbody tr').first()).toBeVisible()
    await expect(input).toHaveValue('')
  })

  test('filters by exact email and combines both fields', async ({
    page,
    request
  }) => {
    const username = f.users.user
    const email = 'e2e_user@example.com'
    const emailInput = page.getByTestId('email-filter-input')

    const update = await request.patch(`/api/v1/management/users/${USER_ID}/`, {
      headers: ADMIN,
      data: { email }
    })
    expect(update.ok()).toBeTruthy()

    await emailInput.fill(email)
    await page.getByTestId('user-filter-submit').click()
    await expect(page.locator('tbody tr')).toHaveCount(1)
    await expect(page.locator('tbody tr')).toContainText(email)

    await emailInput.fill(email.toUpperCase())
    await emailInput.press('Enter')
    await expect(page.locator('tbody tr')).toHaveCount(0)

    await page.getByTestId('username-filter-input').fill(username)
    await emailInput.fill(email)
    await emailInput.press('Enter')
    await expect(page.locator('tbody tr')).toHaveCount(1)
    await expect(page.locator('tbody tr')).toContainText(username)

    await emailInput.fill('other@example.com')
    await emailInput.press('Enter')
    await expect(page.locator('tbody tr')).toHaveCount(0)

    await page.getByTestId('user-filter-reset').click()
    await expect(page.locator('tbody tr').first()).toBeVisible()
    await expect(page.getByTestId('username-filter-input')).toHaveValue('')
    await expect(emailInput).toHaveValue('')
  })
})

test.describe('Management: create drawers', () => {
  const drawers = [
    {
      path: '/management/users',
      title: 'Create User',
      errors: ['Username is required', 'Password is required'],
      fieldLabel: 'Username',
      postPath: '/api/v1/management/users/'
    },
    {
      path: '/management/groups',
      title: 'Create Group',
      errors: ['Group name is required'],
      fieldLabel: 'Group name',
      postPath: '/api/v1/management/groups/'
    }
  ]

  test.beforeEach(async ({ page }) => {
    await asRole(page, 'admin')
  })

  for (const drawer of drawers) {
    test(`${drawer.title} supports every dismissal action`, async ({
      page
    }) => {
      await page.goto(drawer.path)

      await page.getByRole('button', { name: drawer.title }).click()
      const dialog = page.getByRole('dialog', { name: drawer.title })
      await expect(dialog).toBeVisible()
      await dialog.getByRole('button', { name: 'Cancel' }).click()
      await expect(
        page.getByRole('dialog', { name: drawer.title })
      ).toBeHidden()

      await page.getByRole('button', { name: drawer.title }).click()
      await dialog.getByRole('button', { name: 'Close' }).click()
      await expect(
        page.getByRole('dialog', { name: drawer.title })
      ).toBeHidden()

      await page.getByRole('button', { name: drawer.title }).click()
      await page.keyboard.press('Escape')
      await expect(
        page.getByRole('dialog', { name: drawer.title })
      ).toBeHidden()
    })

    test(`${drawer.title} shows field errors without posting`, async ({
      page
    }) => {
      const posts = []
      page.on('request', (request) => {
        const path = new URL(request.url()).pathname
        if (request.method() === 'POST' && path === drawer.postPath) {
          posts.push(request.url())
        }
      })

      await page.setViewportSize({ width: 900, height: 700 })
      await page.goto(drawer.path)
      await page.getByRole('button', { name: drawer.title }).click()
      const dialog = page.getByRole('dialog', { name: drawer.title })
      const confirmButton = dialog.getByRole('button', { name: 'Confirm' })
      await confirmButton.click()

      for (const error of drawer.errors) {
        const message = page.getByText(error, { exact: true })
        await expect(message).toBeVisible()
        await expect(message).toBeInViewport()
      }
      await expect(confirmButton).toBeInViewport()
      expect(posts).toEqual([])

      await dialog.getByLabel(drawer.fieldLabel).fill('fixed value')
      await expect(
        page.getByText(drawer.errors[0], { exact: true })
      ).toBeHidden()
    })
  }

  test('group member rows stay contained on compact phones', async ({
    page,
    request
  }) => {
    const usersResponse = await request.get(
      '/api/v1/management/users/?page_size=1000',
      { headers: ADMIN }
    )
    const originalUser = data(await usersResponse.json()).results.find(
      (user) => user.id === USER_ID
    )
    const longUsername = `compact_member_${Date.now()}_${'x'.repeat(58)}`
    const longEmail = `compact.${Date.now()}.${'long'.repeat(18)}@example.com`
    let group

    try {
      const update = await request.patch(
        `/api/v1/management/users/${USER_ID}/`,
        {
          headers: ADMIN,
          data: { username: longUsername, email: longEmail }
        }
      )
      expect(update.ok()).toBeTruthy()
      group = await createGroup(request, [USER_ID])

      await page.setViewportSize({ width: 320, height: 568 })
      await page.goto('/management/groups')
      await page.getByRole('button', { name: 'Create Group' }).click()

      const createDialog = page.getByRole('dialog', { name: 'Create Group' })
      const createSelector = createDialog.getByTestId('group-member-selector')
      const createRow = createSelector.locator('label').filter({
        hasText: longEmail
      })
      const checkbox = createRow.getByRole('checkbox')

      await expect(createRow).toContainText(longUsername)
      await expect(createRow).toContainText(longEmail)
      await expect(createRow).toBeVisible()
      await expect(createSelector).toBeVisible()
      expect(
        await createSelector.evaluate(
          (element) => element.scrollWidth <= element.clientWidth
        )
      ).toBe(true)

      await checkbox.focus()
      await page.keyboard.press('Space')
      await expect(checkbox).toBeChecked()
      await page.keyboard.press('Space')
      await expect(checkbox).not.toBeChecked()

      const checkboxes = createSelector.getByRole('checkbox')
      await checkbox.check()
      await checkboxes.nth(1).check()
      await expect(checkbox).toBeChecked()
      await expect(checkboxes.nth(1)).toBeChecked()

      await createDialog.getByRole('button', { name: 'Cancel' }).click()
      const groupRow = page.locator('tbody tr').filter({ hasText: group.name })
      await groupRow.click()
      await page
        .getByRole('dialog', { name: 'Group Details' })
        .getByRole('button', { name: 'Edit' })
        .click()

      const editDialog = page.getByRole('dialog', { name: 'Edit Group' })
      const editSelector = editDialog.getByTestId('group-member-selector')
      const editRow = editSelector.locator('label').filter({
        hasText: longEmail
      })

      await expect(editRow.getByRole('checkbox')).toBeChecked()
      expect(
        await editSelector.evaluate(
          (element) => element.scrollWidth <= element.clientWidth
        )
      ).toBe(true)
    } finally {
      if (group) await deleteGroup(request, group.id)
      if (originalUser) {
        await request.patch(`/api/v1/management/users/${USER_ID}/`, {
          headers: ADMIN,
          data: {
            username: originalUser.username,
            email: originalUser.email
          }
        })
      }
    }
  })

  test('shared drawer supports every dismissal action', async ({ page }) => {
    await page.goto('/management/lens/assistants')

    const createButton = page.getByRole('button', {
      name: 'Create Assistant'
    })
    const dialog = page.getByRole('dialog', { name: 'Create Assistant' })

    await createButton.click()
    await expect(dialog).toBeVisible()
    await dialog.getByRole('button', { name: 'Cancel' }).click()
    await expect(dialog).toBeHidden()

    await createButton.click()
    await dialog.getByRole('button', { name: 'Close' }).click()
    await expect(dialog).toBeHidden()

    await createButton.click()
    await page.keyboard.press('Escape')
    await expect(dialog).toBeHidden()
  })
})

test.describe('Management: deleting a group revokes its assistant grants', () => {
  test('group deletion cascades AssistantAccess', async ({ request }) => {
    const baseline = (await privateGrants(request)).map((g) => ({
      type: g.type,
      id: g.id
    }))
    const group = await createGroup(request)
    let deleted = false
    try {
      const patch = await request.patch(
        `/api/lens/assistants/${PRIVATE_UUID}/`,
        {
          headers: ADMIN,
          data: {
            access_grants: [...baseline, { type: 'group', id: group.id }]
          }
        }
      )
      expect(patch.ok()).toBeTruthy()

      const withGrant = (await listGroups(request)).find(
        (g) => g.id === group.id
      )
      expect(withGrant.assistant_grant_count).toBe(1)

      const del = await deleteGroup(request, group.id)
      expect(del.status()).toBe(204)
      deleted = true

      const grants = await privateGrants(request)
      expect(grants.some((g) => g.type === 'group' && g.id === group.id)).toBe(
        false
      )
      expect(grants.length).toBe(baseline.length)
    } finally {
      if (!deleted) {
        await deleteGroup(request, group.id)
        await request.patch(`/api/lens/assistants/${PRIVATE_UUID}/`, {
          headers: ADMIN,
          data: { access_grants: baseline }
        })
      }
    }
  })
})
