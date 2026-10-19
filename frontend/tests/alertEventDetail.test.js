import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import vm from 'node:vm'

const ref = (value) => ({ value })
const deferred = () => {
  let resolve, reject
  const promise = new Promise((yes, no) => {
    resolve = yes
    reject = no
  })
  return { promise, resolve, reject }
}

async function contextFor(name, api) {
  const source = await readFile(
    new URL('../src/admin/pages/Alerts/Events.vue', import.meta.url),
    'utf8'
  )
  const start = source.indexOf(`async function ${name}(`)
  assert.ok(start >= 0, `${name} exists`)
  const end = source.indexOf('\n}', start) + 2
  const context = vm.createContext({
    selectedUuid: ref('first'),
    detail: ref(null),
    detailLoading: ref(false),
    detailError: ref(''),
    detailRequestId: 0,
    loadRequestId: 0,
    events: ref([]),
    total: ref(0),
    page: ref(1),
    pageSize: ref(20),
    subject: ref(''),
    eventType: ref(''),
    loading: ref(false),
    error: ref(''),
    route: { query: {} },
    alertsAdminApi: api,
    alertApiError: (err) => err.message
  })
  vm.runInContext(source.slice(start, end), context)
  return context
}

test('switching notification details ignores older responses and errors', async () => {
  for (const fail of [false, true]) {
    const old = deferred(),
      latest = deferred()
    const context = await contextFor('fetchDetail', {
      getEvent: (id) => (id === 'first' ? old.promise : latest.promise)
    })
    const first = vm.runInContext('fetchDetail()', context)
    context.selectedUuid.value = 'second'
    const second = vm.runInContext('fetchDetail()', context)
    latest.resolve({ uuid: 'second', status: 'sent' })
    await second
    if (fail) old.reject(new Error('Stale failure'))
    else old.resolve({ uuid: 'first', status: 'failed' })
    await first
    assert.equal(context.detail.value.uuid, 'second')
    assert.equal(context.detailError.value, '')
    assert.equal(context.detailLoading.value, false)
  }
})

test('closing notification details prevents a late failure from reopening content', async () => {
  const request = deferred()
  const context = await contextFor('fetchDetail', {
    getEvent: () => request.promise
  })
  const pending = vm.runInContext('fetchDetail()', context)
  context.selectedUuid.value = ''
  context.detail.value = null
  request.reject(new Error('Closed drawer failure'))
  await pending
  assert.equal(context.detail.value, null)
  assert.equal(context.detailError.value, '')
})

test('notification filters keep the newest list response', async () => {
  const old = deferred(),
    latest = deferred()
  const context = await contextFor('load', {
    getEvents: ({ page }) => (page === 1 ? old.promise : latest.promise)
  })
  const first = vm.runInContext('load(1)', context)
  const second = vm.runInContext('load(2)', context)
  latest.resolve({ results: [{ uuid: 'new' }], count: 1 })
  await second
  old.resolve({ results: [{ uuid: 'old' }], count: 99 })
  await first
  assert.equal(context.events.value[0].uuid, 'new')
  assert.equal(context.total.value, 1)
})
