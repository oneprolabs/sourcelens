import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import vm from 'node:vm'

const source = await readFile(
  new URL('../src/admin/pages/Notifications/Stats.vue', import.meta.url),
  'utf8'
)

function statisticsContext(getStats) {
  const ref = (value) => ({ value })
  const context = vm.createContext({
    statsRequestId: 0,
    statsData: ref(null),
    statsError: ref(false),
    loading: ref(false),
    granularity: ref('day'),
    startDate: ref('2026-10-19'),
    endDate: ref('2026-10-19'),
    userScope: ref(''),
    notificationsAdminApi: { getStats }
  })
  const start = source.indexOf('async function fetchStats()')
  const end = source.indexOf('function goToRecords()', start)
  vm.runInContext(source.slice(start, end), context)
  return context
}

test('failed statistics remain distinguishable from an empty period and can recover', async () => {
  let fail = true
  const payload = { summary: { total: 3 } }
  const state = statisticsContext(async () => {
    if (fail) throw new Error('Network Error')
    return payload
  })

  await state.fetchStats()
  assert.equal(state.statsError.value, true)
  assert.equal(state.statsData.value, null)
  assert.equal(state.loading.value, false)

  fail = false
  await state.fetchStats()
  assert.equal(state.statsError.value, false)
  assert.equal(state.statsData.value, payload)
  assert.equal(state.loading.value, false)
})

test('an older filter response cannot replace the latest statistics', async () => {
  const requests = []
  const state = statisticsContext(
    () => new Promise((resolve) => requests.push(resolve))
  )
  const oldRequest = state.fetchStats()
  state.granularity.value = 'month'
  const latestRequest = state.fetchStats()
  const latest = { summary: { total: 9 } }
  requests[1](latest)
  await latestRequest
  requests[0]({ summary: { total: 1 } })
  await oldRequest
  assert.equal(state.statsData.value, latest)
})

test('a stale failure cannot clear the latest data or its loading state', async () => {
  const requests = []
  const state = statisticsContext(
    () => new Promise((resolve, reject) => requests.push({ resolve, reject }))
  )
  const oldRequest = state.fetchStats()
  const latestRequest = state.fetchStats()
  requests[0].reject(new Error('Old request failed'))
  await oldRequest
  assert.equal(state.loading.value, true)
  assert.equal(state.statsError.value, false)
  const latest = { summary: { total: 0 } }
  requests[1].resolve(latest)
  await latestRequest
  assert.equal(state.statsData.value, latest)
  assert.equal(state.statsError.value, false)
})

test('channel statistics merge legacy WeCom rows and retain webhook and custom providers', async () => {
  const { notificationChannelStats } = await import(
    '../src/admin/utils/notificationTargets.js'
  )
  const rows = notificationChannelStats([
    { provider_type: 'wechat', count: 2 },
    { provider_type: 'wecom', count: '3' },
    { provider_type: 'webhook', count: 1 },
    { provider_type: 'custom', count: 4, provider_display_name: 'Custom' }
  ])
  assert.equal(rows.filter((row) => row.provider_type === 'wecom').length, 1)
  assert.equal(
    rows.some((row) => row.provider_type === 'wechat'),
    false
  )
  assert.equal(rows.find((row) => row.provider_type === 'wecom').count, 5)
  assert.equal(rows.find((row) => row.provider_type === 'wecom').percent, 50)
  assert.equal(rows.find((row) => row.provider_type === 'webhook').count, 1)
  assert.equal(rows.find((row) => row.provider_type === 'custom').count, 4)
  assert.equal(
    rows.reduce((sum, row) => sum + row.count, 0),
    10
  )
})

test('statistics failure and retry labels exist in every supported language', async () => {
  for (const language of ['en', 'zh-CN', 'es']) {
    const messages = JSON.parse(
      await readFile(
        new URL(`../src/admin/locales/${language}.json`, import.meta.url),
        'utf8'
      )
    )
    const stats = messages.notificationManagement.stats
    for (const key of ['loadErrorTitle', 'loadErrorDescription', 'retry']) {
      assert.equal(typeof stats[key], 'string', `${language}: ${key}`)
      assert.ok(stats[key].length > 0)
    }
  }
})
