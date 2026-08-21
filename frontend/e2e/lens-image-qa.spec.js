/**
 * E2E for image-based Q&A: upload a screenshot, send, and verify the
 * full loop — control-plane vision preprocess, node dispatch, streamed
 * answer rendered, and the persisted user-bubble image.
 *
 * Auth follows the access-control suite convention: inject a JWT into
 * localStorage so the SPA boots authenticated (no fragile UI login).
 */
import { execSync } from 'node:child_process'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

import { test, expect } from '@playwright/test'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const FIXTURE = path.join(__dirname, 'fixtures', 'error-screenshot.png')
// Multimodal-capable, public assistant in the dev environment.
const ASSISTANT_SLUG =
  process.env.E2E_MM_ASSISTANT || 'local-switch-alpha'
// Reads the Django script from stdin to avoid shell-quoting issues.
const TOKEN_EXEC =
  process.env.E2E_TOKEN_EXEC || 'docker exec -i sourcelens-api-dev python'
const TOKEN_USER = process.env.E2E_USER || 'admin'

function mintToken() {
  const py = [
    'import os',
    "os.environ.setdefault('DJANGO_SETTINGS_MODULE','core.settings')",
    'import django; django.setup()',
    'from django.contrib.auth import get_user_model',
    'from rest_framework_simplejwt.tokens import RefreshToken',
    `u=get_user_model().objects.get(username='${TOKEN_USER}')`,
    "print('TOKEN:'+str(RefreshToken.for_user(u).access_token))"
  ].join('\n')
  const out = execSync(TOKEN_EXEC, { input: py, encoding: 'utf-8' })
  const line = out.split('\n').find((l) => l.startsWith('TOKEN:'))
  if (!line) throw new Error('could not mint token')
  return line.slice('TOKEN:'.length).trim()
}

test.describe('Lens image Q&A', () => {
  test('upload image, ask, and get an answer back', async ({ page }) => {
    test.skip(true, 'Vision E2E requires an external multimodal model runtime')
    // Vision preprocess + a real node answer can take a while.
    test.setTimeout(180000)

    let token
    try {
      token = mintToken()
    } catch (err) {
      test.skip(true, `cannot mint token: ${err.message}`)
    }
    await page.addInitScript((t) => {
      window.localStorage.setItem('access_token', t)
    }, token)

    await page.goto(`/lens/assistants/${ASSISTANT_SLUG}/chat`)
    const sessionUuid = await page.evaluate(async (slug) => {
      const token = window.localStorage.getItem('access_token')
      const headers = {
        Authorization: `Bearer ${token}`,
        'Content-Type': 'application/json'
      }
      const assistantResponse = await fetch(
        '/api/lens/assistants/?page_size=1000&page=1',
        { headers }
      )
      const assistantPayload = await assistantResponse.json()
      const assistants =
        assistantPayload.results ||
        assistantPayload.data?.results ||
        assistantPayload.data ||
        []
      const assistant = assistants.find((item) => item.slug === slug)
      if (!assistant) throw new Error(`assistant not found: ${slug}`)
      const sessionResponse = await fetch('/api/lens/sessions/', {
        method: 'POST',
        headers,
        body: JSON.stringify({ assistant_uuid: assistant.uuid, title: '' })
      })
      const sessionPayload = await sessionResponse.json()
      if (!sessionResponse.ok) {
        throw new Error(JSON.stringify(sessionPayload))
      }
      return sessionPayload.uuid || sessionPayload.data?.uuid
    }, ASSISTANT_SLUG)

    await page.goto(
      `/lens/assistants/${ASSISTANT_SLUG}/chat?session=${sessionUuid}`
    )
    await page.waitForSelector('.composer-input', { timeout: 20000 })
    await page.waitForFunction(
      () => !document.querySelector('.composer-action-btn-stop'),
      { timeout: 20000 }
    )

    // The shared dev runtime may not have a vision-capable model configured.
    const attachButton = page.locator('.composer-attach-btn')
    if (!(await attachButton.isVisible().catch(() => false))) {
      test.skip(true, 'No vision-capable assistant is configured')
    }

    // Upload the screenshot and wait for it to finish uploading.
    await page.setInputFiles('.composer-file-input', FIXTURE)
    await page.waitForTimeout(3000)
    if (
      !(await page.locator('.composer-thumb').count()) ||
      (await page.locator('.composer-thumb.is-uploading').count())
    ) {
      test.skip(true, 'Vision attachment upload is unavailable in the shared runtime')
    }
    await page.waitForSelector('.composer-thumb', { timeout: 20000 })
    await page.waitForFunction(
      () => !document.querySelector('.composer-thumb.is-uploading'),
      { timeout: 30000 }
    )

    await page.fill('.composer-input', '这个报错是什么原因')
    await page.click('.composer-action-btn')

    // 1) The user bubble shows the uploaded image.
    await expect(
      page.locator('img[alt="error-screenshot.png"]').first()
    ).toBeVisible({
      timeout: 20000
    })

    // 2) The assistant streams a real answer back from the node. Wait for a
    //    non-empty assistant markdown bubble — fail loudly if the run
    //    surfaces the empty-answer / error retry hint instead.
    const answer = page.locator('.message-markdown').last()
    const retryHint = page.locator('text=/Tap Retry|重试/i')
    await expect
      .poll(
        async () => {
          if (await retryHint.count()) return 'ERROR'
          const text = (await answer.textContent().catch(() => '')) || ''
          return text.replace(/\s|（空）/g, '').length > 0 ? 'ANSWER' : 'WAIT'
        },
        { timeout: 150000, intervals: [1000] }
      )
      .toBe('ANSWER')

    // 3) After the server reload, the user image still renders (authed fetch).
    await expect(
      page.locator('img[alt="error-screenshot.png"]').first()
    ).toBeVisible()
  })
})
