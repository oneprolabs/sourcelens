import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

const source = (path) =>
  readFile(new URL(`../src/${path}`, import.meta.url), 'utf8')

test('reports native sharing support only when the Web Share API exists', async () => {
  const { supportsNativeShare } = await import('../src/utils/nativeShare.js')

  assert.equal(supportsNativeShare({ share() {} }), true)
  assert.equal(supportsNativeShare({}), false)
  assert.equal(supportsNativeShare(null), false)
})

test('distinguishes completed, cancelled, unsupported, and failed shares', async () => {
  const { shareWithNative } = await import('../src/utils/nativeShare.js')
  const payload = {
    title: 'Quarterly review',
    text: 'View this answer',
    url: 'https://example.com/lens/qa/token'
  }

  assert.deepEqual(
    await shareWithNative(payload, { share: async () => undefined }),
    { status: 'shared' }
  )

  const cancelled = new Error('Share cancelled')
  cancelled.name = 'AbortError'
  assert.deepEqual(
    await shareWithNative(payload, {
      share: async () => {
        throw cancelled
      }
    }),
    { status: 'cancelled' }
  )

  assert.deepEqual(await shareWithNative(payload, {}), {
    status: 'unsupported'
  })

  const failure = new Error('Share failed')
  assert.deepEqual(
    await shareWithNative(payload, {
      share: async () => {
        throw failure
      }
    }),
    { error: failure, status: 'failed' }
  )
})

test('uses native sharing for an existing mobile Q&A link', async () => {
  const chat = await source('pages/lens/Chat.vue')

  assert.match(
    chat,
    /isMobile\.value\s*&&\s*shareExisting\.value\s*&&\s*supportsNativeShare\(\)/
  )
  assert.match(chat, /await shareWithNative\(/)
  assert.match(chat, /if \(result\.status === 'cancelled'\) return/)
  assert.match(chat, /shareOpen\.value = true/)
})

test('copies the Q&A link instead of opening the system share sheet', async () => {
  const modal = await source('components/lens/QaShareModal.vue')

  assert.match(modal, /v-if="share"/)
  assert.match(modal, /@click="copy"/)
  assert.match(modal, /t\('lens\.share\.copyLink'\)/)
  assert.doesNotMatch(modal, /shareWithNative/)
  assert.doesNotMatch(modal, /nativeShareAvailable/)
  assert.doesNotMatch(modal, /copyInvitation/)
})
