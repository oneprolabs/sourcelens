import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import vm from 'node:vm'
import * as vue from 'vue'

async function setup(file, overrides = {}) {
  const source = await readFile(
    new URL(`../src/${file}`, import.meta.url),
    'utf8'
  )
  const props = vue.reactive({
    open: true,
    runUuid: 'run',
    question: 'Question'
  })
  const events = []
  const context = vm.createContext({
    ...vue,
    onMounted() {},
    defineProps: () => props,
    defineEmits:
      () =>
      (...args) =>
        events.push(args),
    useI18n: () => ({ t: (key) => key }),
    useUserStore: () => ({ delegation: null }),
    useToast: () => ({ showSuccess() {}, showError() {} }),
    getSharePolicy: async () => ({
      policy: 'inherit',
      effective_policy: 'login',
      requires_choice: true
    }),
    ...overrides
  })
  const script = source
    .match(/<script setup>([^]*?)<\/script>/)[1]
    .replace(/^import [^]*? from ['"][^'"]+['"]\n/gm, '')
  vm.runInContext(script, context)
  return { run: (code) => vm.runInContext(code, context), props, events }
}

const modal = 'components/lens/QaShareModal.vue'
const review = 'admin/pages/lens/ShareReview.vue'

test('new shares default to the server effective policy without an explicit public override', async () => {
  const payloads = []
  const { run } = await setup(modal, {
    shareRun: async (uuid, payload) => {
      payloads.push(payload)
      return { uuid: 'share' }
    }
  })
  await run('loadAccessPolicy()')
  assert.equal(run('accessChoice.value'), 'login')
  assert.equal(run('showsAccessChoice.value'), true)
  await run('create()')
  assert.equal(Object.hasOwn(payloads[0], 'require_login'), false)
  run("onAccessChoiceChange('public')")
  await run('create()')
  assert.equal(payloads[1].require_login, false)
})

test('tenant and selected-member shares follow visibility and offer no public override', async () => {
  const { run } = await setup(modal, {
    getSharePolicy: async () => ({
      policy: 'login',
      effective_policy: 'login',
      source: 'visibility',
      requires_choice: false
    })
  })
  await run('loadAccessPolicy()')
  assert.equal(run('showsAccessChoice.value'), false)
  assert.equal(
    run('accessPolicyHint.value'),
    'lens.qa.shareAccessFollowsAssistant'
  )
})

test('policy load failure and out-of-order responses preserve the restrictive choice', async () => {
  const pending = []
  const { run } = await setup(modal, {
    getSharePolicy: () =>
      new Promise((resolve, reject) => pending.push({ resolve, reject }))
  })
  const first = run('loadAccessPolicy()')
  const second = run('loadAccessPolicy()')
  pending[1].reject(new Error('Unavailable'))
  await second
  pending[0].resolve({
    policy: 'public',
    effective_policy: 'public',
    requires_choice: true
  })
  await first
  assert.equal(run('accessChoice.value'), 'login')
  assert.equal(run('showsAccessChoice.value'), false)
})

test('share creation blocks repeated submissions and closing until the result arrives', async () => {
  let finish
  let calls = 0
  const { run, events } = await setup(modal, {
    shareRun: () => {
      calls++
      return new Promise((resolve) => {
        finish = resolve
      })
    }
  })
  const saving = run('create()')
  await run('create()')
  run('emitClose()')
  assert.equal(calls, 1)
  assert.equal(events.length, 0)
  finish({ uuid: 'share' })
  await saving
  assert.equal(events[0][0], 'shared')
})

test('readonly delegation blocks moderation before issuing a write', async () => {
  let calls = 0
  const { run } = await setup(review, {
    useUserStore: () => ({ delegation: { mode: 'readonly' } }),
    updateAdminShare: async () => {
      calls++
    }
  })
  await run("apply({ uuid: 'share' }, { is_listed: true })")
  assert.equal(calls, 0)
})

test('review refresh failure keeps rows and exposes a retryable error', async () => {
  const { run } = await setup(review, {
    listAdminShares: async () => {
      throw new Error('Unavailable')
    }
  })
  run("rows.value = [{ uuid: 'kept' }]; totalCount.value = 1")
  await run('load()')
  assert.equal(run('loadError.value'), true)
  assert.equal(run('rows.value[0].uuid'), 'kept')
  assert.equal(run('totalCount.value'), 1)
})

test('review ignores list and detail responses superseded by a different tab or selection', async () => {
  const lists = []
  const details = []
  const { run } = await setup(review, {
    listAdminShares: () => new Promise((resolve) => lists.push(resolve)),
    getAdminShare: () => new Promise((resolve) => details.push(resolve))
  })
  const firstList = run('load()')
  const secondList = run("activeTab.value = 'hidden'; load()")
  lists[1]({ results: [{ uuid: 'hidden' }], count: 1 })
  await secondList
  lists[0]({ results: [{ uuid: 'pending' }], count: 1 })
  await firstList
  assert.equal(run('rows.value[0].uuid'), 'hidden')
  const firstDetail = run("openDetail({ uuid: 'old' })")
  const secondDetail = run("openDetail({ uuid: 'new' })")
  details[1]({ uuid: 'new' })
  await secondDetail
  details[0]({ uuid: 'old' })
  await firstDetail
  assert.equal(run('detail.value.uuid'), 'new')
})

test('assistant detail labels tenant visibility without presenting it as public', async () => {
  const source = await readFile(
    new URL('../src/pages/lens/AssistantDetailView.vue', import.meta.url),
    'utf8'
  )
  const context = vm.createContext({
    computed: vue.computed,
    props: { assistant: { visibility: 'tenant' } },
    t: (key) => key
  })
  const expression = source.match(
    /const visibilityLabel = computed\([^]*?\n\)/
  )[0]
  vm.runInContext(expression, context)
  assert.equal(
    vm.runInContext('visibilityLabel.value', context),
    'lensAdmin.visibility.tenant'
  )
})

test('failed moderation detail can be retried without losing the selected share', async () => {
  let calls = 0
  const { run } = await setup(review, {
    getAdminShare: async () => {
      if (++calls === 1) throw new Error('Unavailable')
      return { uuid: 'share', answer: 'Complete answer' }
    }
  })
  await run("openDetail({ uuid: 'share' })")
  assert.equal(run('detailError.value'), true)
  assert.equal(run('selectedUuid.value'), 'share')
  await run('loadDetail()')
  assert.equal(run('detailError.value'), false)
  assert.equal(run('detail.value.answer'), 'Complete answer')
})
