import { expect, test } from '@playwright/test'

import { asRole, authHeader, fixtures } from './helpers.js'

test.beforeEach(async ({ page }) => {
  await asRole(page, 'admin')
})

test('opens a user detail and deep-links exact history filters', async ({
  page
}) => {
  const f = fixtures()
  await page.goto('/management/users')
  await page
    .getByTestId('user-detail-row')
    .filter({ hasText: f.users.authuser })
    .click()

  const drawer = page.getByRole('dialog', { name: 'User Details' })
  await expect(drawer.getByTestId('user-detail-content')).toContainText(
    f.users.authuser
  )
  await drawer.getByRole('button', { name: 'Assistants & activity' }).click()
  await drawer.getByRole('button', { name: 'E2E Private' }).click()

  await expect(page).toHaveURL(new RegExp(`user_id=${f.user_ids.authuser}`))
  await expect(page).toHaveURL(/assistant=e2e-private/)
  await expect(page.getByPlaceholder('Username')).toHaveValue(f.users.authuser)
})

test('opens a group detail and lists its members', async ({
  page,
  request
}) => {
  const f = fixtures()
  const response = await request.get(
    '/api/v1/management/groups/?page_size=1000',
    { headers: authHeader('admin') }
  )
  const body = await response.json()
  const groups = body?.data?.results ?? body?.data ?? body?.results ?? body
  const group = groups.find((item) => item.id === f.group_id)

  await page.goto('/management/groups')
  await page
    .getByTestId('group-detail-row')
    .filter({ hasText: group.name })
    .click()

  const drawer = page.getByRole('dialog', { name: 'Group Details' })
  await expect(drawer.getByTestId('group-detail-content')).toContainText(
    group.name
  )
  await drawer.getByRole('button', { name: 'Members' }).click()
  await expect(drawer).toContainText(f.users.groupuser)
})

test('keeps row edit actions independent from detail drawers', async ({
  page,
  request
}) => {
  const f = fixtures()
  await page.goto('/management/users')
  const userRow = page
    .getByTestId('user-detail-row')
    .filter({ hasText: f.users.authuser })
  await userRow.click()
  await page.getByRole('dialog', { name: 'User Details' })
    .getByRole('button', { name: 'Edit' })
    .click()

  await expect(page.getByRole('dialog', { name: 'Edit User' })).toBeVisible()
  await expect(
    page.getByRole('dialog', { name: 'User Details' })
  ).not.toBeVisible()

  await page
    .getByRole('dialog', { name: 'Edit User' })
    .getByRole('button', { name: 'Cancel' })
    .click()

  const response = await request.get(
    '/api/v1/management/groups/?page_size=1000',
    { headers: authHeader('admin') }
  )
  const body = await response.json()
  const groups = body?.data?.results ?? body?.data ?? body?.results ?? body
  const group = groups.find((item) => item.id === f.group_id)

  await page.goto('/management/groups')
  const groupRow = page
    .getByTestId('group-detail-row')
    .filter({ hasText: group.name })
  await groupRow.click()
  await page.getByRole('dialog', { name: 'Group Details' })
    .getByRole('button', { name: 'Edit' })
    .click()

  await expect(page.getByRole('dialog', { name: 'Edit Group' })).toBeVisible()
  await expect(
    page.getByRole('dialog', { name: 'Group Details' })
  ).not.toBeVisible()
})
