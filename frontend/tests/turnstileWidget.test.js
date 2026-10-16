import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

async function mountWidget(
  fetchResponse,
  config = { enabled: true, siteKey: 'site-key' }
) {
  const source = await readFile(
    new URL('../src/components/TurnstileWidget.vue', import.meta.url),
    'utf8'
  )
  const script = source
    .match(/<script setup>([\s\S]*?)<\/script>/)[1]
    .replace(/^import .*$/gm, '')
    .replace('import.meta.env.VITE_TURNSTILE_SITE_KEY', '"site-key"')
  let mount
  let exposed
  let renders = 0
  const events = []
  const window = {
    turnstile: {
      render: () => {
        renders += 1
        return 1
      },
      reset() {},
      remove() {}
    }
  }
  const run = new Function(
    'onMounted',
    'onBeforeUnmount',
    'ref',
    'getTurnstileConfig',
    'defineEmits',
    'defineExpose',
    'fetch',
    'window',
    script
  )
  run(
    (callback) => {
      mount = callback
    },
    () => {},
    (value) => ({ value }),
    () => config,
    () =>
      (...args) =>
        events.push(args),
    (value) => {
      exposed = value
    },
    fetchResponse,
    window
  )
  await mount()
  return { renders, events, reset: exposed.reset }
}

test('wrapped required=false skips rendering and remains bypassed after reset', async () => {
  const widget = await mountWidget(async () => ({
    ok: true,
    json: async () => ({
      code: 0,
      message: 'success',
      data: { required: false }
    })
  }))
  assert.equal(widget.renders, 0)
  assert.deepEqual(widget.events, [['verified', '']])
  widget.reset()
  assert.deepEqual(widget.events, [
    ['verified', ''],
    ['verified', '']
  ])
})

test('required=true renders the challenge', async () => {
  const widget = await mountWidget(async () => ({
    ok: true,
    json: async () => ({ data: { required: true } })
  }))
  assert.equal(widget.renders, 1)
})

test('backend required policy overrides an outdated frontend disabled switch', async () => {
  const widget = await mountWidget(
    async () => ({
      ok: true,
      json: async () => ({ data: { required: true } })
    }),
    { enabled: false, siteKey: 'site-key' }
  )
  assert.equal(widget.renders, 1)
  assert.deepEqual(widget.events, [])
})

test('missing site key never reports verified when backend requires verification', async () => {
  const widget = await mountWidget(
    async () => ({
      ok: true,
      json: async () => ({ data: { required: true } })
    }),
    { enabled: true, siteKey: '' }
  )
  assert.equal(widget.renders, 0)
  assert.equal(widget.events[0]?.[0], 'error')
})

test('failed or malformed policy responses keep the challenge enabled', async () => {
  for (const fetchResponse of [
    async () => {
      throw new Error('offline')
    },
    async () => ({
      ok: false,
      json: async () => ({ data: { required: false } })
    }),
    async () => ({ ok: true, json: async () => ({ required: false }) })
  ]) {
    assert.equal((await mountWidget(fetchResponse)).renders, 1)
  }
})
