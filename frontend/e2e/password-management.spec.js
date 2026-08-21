import { expect, test } from '@playwright/test'

test('requests password setup or reset without exposing eligibility', async ({
  page
}) => {
  let resetRequests = 0
  let requestPayload = null
  await page.addInitScript(() => {
    localStorage.setItem('userLanguage', 'en')
  })
  await page.route('**/api/v1/auth/password/reset', async (route) => {
    resetRequests += 1
    requestPayload = route.request().postDataJSON()
    await route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify({
        success: true,
        message: 'Password reset email sent successfully'
      })
    })
  })

  await page.goto('/login')
  const passwordMode = page.getByRole('button', {
    name: /Use (account password|email and password)/
  })
  await passwordMode.click()
  await page.getByRole('button', { name: 'Forgot password?' }).click()

  const email = page.getByLabel('Email address')
  await expect(email).toHaveAttribute('autocomplete', 'email')
  await email.fill('invalid-email')
  await page.getByRole('button', { name: 'Send setup or reset link' }).click()
  await expect(page.getByText('Enter a valid email address')).toBeVisible()
  expect(resetRequests).toBe(0)

  await email.fill('person@example.com')
  await email.press('Enter')
  await expect(
    page.getByText(
      'If an eligible account exists for this email, a setup or reset link has been sent.'
    )
  ).toBeVisible()
  expect(resetRequests).toBe(1)
  expect(requestPayload).toEqual({ email: 'person@example.com' })
})

test('changes an authenticated local password from Security settings', async ({
  page
}) => {
  const changePayloads = []
  await page.addInitScript(() => {
    localStorage.setItem('access_token', 'test-access-token')
    localStorage.setItem('userLanguage', 'en')
  })
  await page.route('**/*', async (route) => {
    const url = new URL(route.request().url())
    if (!url.pathname.startsWith('/api/')) {
      await route.continue()
      return
    }
    if (url.pathname.includes('/v1/auth/user')) {
      await route.fulfill({
        contentType: 'application/json',
        body: JSON.stringify({
          data: {
            id: 7,
            username: 'person',
            email: 'person@example.com',
            auth_info: { can_change_password: true },
            access_profile: {
              visible_features: ['workspace'],
              landing_path: '/lens/assistants/demo/chat'
            },
            roles: [],
            permissions: []
          }
        })
      })
      return
    }
    if (url.pathname.includes('/v1/auth/password/change')) {
      const payload = route.request().postDataJSON()
      changePayloads.push(payload)
      if (changePayloads.length === 1) {
        await route.fulfill({
          status: 400,
          contentType: 'application/json',
          body: JSON.stringify({
            data: { oldPassword: ['Current password is incorrect'] }
          })
        })
        return
      }
      if (changePayloads.length === 2) {
        await route.fulfill({
          status: 400,
          contentType: 'application/json',
          body: JSON.stringify({
            data: {
              non_field_errors: ['Password is too similar to the username']
            }
          })
        })
        return
      }
      await route.fulfill({
        contentType: 'application/json',
        body: JSON.stringify({ data: { detail: 'New password saved' } })
      })
      return
    }
    await route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify({ data: [] })
    })
  })

  const profileLoaded = page.waitForResponse((response) =>
    response.url().includes('/api/v1/auth/user')
  )
  await page.goto('/404')
  await profileLoaded
  await page.evaluate(() => {
    const app = document.querySelector('#app').__vue_app__
    const pinia = Reflect.ownKeys(app._context.provides)
      .map((key) => app._context.provides[key])
      .find((value) => value?._s instanceof Map)
    pinia._s.get('ui').openSettings()
  })
  await page.getByRole('button', { name: 'Security' }).click()

  const currentPassword = page.getByLabel('Current password')
  const newPassword = page.getByLabel(/^New password/)
  const confirmPassword = page.getByLabel('Confirm new password')
  await expect(currentPassword).toHaveAttribute(
    'autocomplete',
    'current-password'
  )
  await expect(newPassword).toHaveAttribute('autocomplete', 'new-password')

  await currentPassword.fill('Original7Qx9')
  await newPassword.fill('onlyletters')
  await confirmPassword.fill('onlyletters')
  await page.getByRole('button', { name: 'Change password' }).click()
  await expect(
    page.getByText('Password must contain both letters and numbers')
  ).toBeVisible()
  expect(changePayloads).toHaveLength(0)

  await newPassword.fill('R7vM2Qp9Lx4')
  await confirmPassword.fill('R7vM2Qp9Lx4')
  await confirmPassword.press('Enter')
  await expect(page.getByText('Current password is incorrect')).toBeVisible()

  await currentPassword.fill('Original7Qx9')
  await page.getByRole('button', { name: 'Change password' }).click()
  await expect(
    page.getByText('Password is too similar to the username')
  ).toBeVisible()

  await newPassword.fill('N4kW8mZ2qP7')
  await confirmPassword.fill('N4kW8mZ2qP7')
  await page.getByRole('button', { name: 'Change password' }).click()
  await expect(page.getByText('Password changed successfully')).toBeVisible()
  expect(changePayloads[2]).toEqual({
    oldPassword: 'Original7Qx9',
    newPassword1: 'N4kW8mZ2qP7',
    newPassword2: 'N4kW8mZ2qP7'
  })

  await expect(
    page.getByRole('button', { name: 'Forgot your current password?' })
  ).toHaveCount(0)
})
