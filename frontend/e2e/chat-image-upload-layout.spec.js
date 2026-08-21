import { expect, test } from '@playwright/test'

const viewports = [
  { name: 'desktop', width: 1280, height: 800 },
  { name: 'mobile', width: 390, height: 844 }
]

async function mockChat(page) {
  await page.addInitScript(() => {
    localStorage.setItem('access_token', 'test-token')
  })

  await page.route('**/api/**', async (route) => {
    const request = route.request()
    const path = new URL(request.url()).pathname
    if (!path.startsWith('/api/')) {
      await route.continue()
      return
    }

    if (
      path === '/api/lens/sessions/session-1/attachments/' &&
      request.method() === 'POST'
    ) {
      await new Promise((resolve) => setTimeout(resolve, 200))
      return
    }

    const payloads = {
      '/api/v1/auth/user': {
        username: 'tester',
        features: ['workspace'],
        permissions: []
      },
      '/api/lens/assistants/': [
        {
          uuid: 'assistant-1',
          slug: 'image-layout-test',
          name: 'Image Layout Test',
          status: 'active',
          multimodal_model_ref: 'model-1',
          can_process_images: true
        }
      ],
      '/api/lens/shares/': [],
      '/api/lens/sessions/': [
        {
          uuid: 'session-1',
          title: 'Image layout test',
          status: 'active',
          assistant: 'assistant-1'
        }
      ],
      '/api/lens/sessions/session-1/messages/': []
    }

    await route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify({ data: payloads[path] ?? [] })
    })
  })
}

async function pasteImage(page) {
  await page.locator('.composer-input').evaluate((element) => {
    const encoded =
      'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk' +
      '+A8AAQUBAScY42YAAAAASUVORK5CYII='
    const data = Uint8Array.from(atob(encoded), (character) =>
      character.charCodeAt(0)
    )
    const transfer = new DataTransfer()
    transfer.items.add(new File([data], 'pasted.png', { type: 'image/png' }))
    element.dispatchEvent(
      new ClipboardEvent('paste', {
        bubbles: true,
        cancelable: true,
        clipboardData: transfer
      })
    )
  })
}

for (const viewport of viewports) {
  test(`pasted image upload spinner is centered on ${viewport.name}`, async ({
    page
  }) => {
    await page.setViewportSize(viewport)
    await mockChat(page)
    await page.goto('/lens/assistants/image-layout-test/chat')

    await expect(page.locator('.composer-input')).toBeVisible()
    await pasteImage(page)

    const thumb = page.locator('.composer-thumb.is-uploading')
    const spinner = thumb.locator('.composer-thumb-spinner')
    await expect(spinner).toBeVisible()
    await page.addStyleTag({
      content: `
        .composer-thumb-spinner {
          animation-delay: -0.35s !important;
          animation-play-state: paused !important;
        }
      `
    })

    const centers = await thumb.evaluate((element) => {
      const thumbBox = element.getBoundingClientRect()
      const spinnerBox = element
        .querySelector('.composer-thumb-spinner')
        .getBoundingClientRect()
      return {
        thumbX: thumbBox.left + thumbBox.width / 2,
        thumbY: thumbBox.top + thumbBox.height / 2,
        spinnerX: spinnerBox.left + spinnerBox.width / 2,
        spinnerY: spinnerBox.top + spinnerBox.height / 2
      }
    })

    expect(Math.abs(centers.spinnerX - centers.thumbX)).toBeLessThanOrEqual(1)
    expect(Math.abs(centers.spinnerY - centers.thumbY)).toBeLessThanOrEqual(1)
  })
}
