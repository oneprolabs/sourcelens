import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import vm from 'node:vm'
import { computed, ref } from 'vue'
import { format } from 'date-fns'

async function setup(file, extras = {}) {
  const source = await readFile(
    new URL(`../src/admin/pages/${file}`, import.meta.url),
    'utf8'
  )
  const script = source
    .split('<script setup>')[1]
    .split('</script>')[0]
    .replace(/^import[\s\S]*?from ['"][^'"]+['"]\n/gm, '')
  const context = vm.createContext({
    computed,
    ref,
    format,
    Intl,
    Date,
    onMounted() {},
    watch() {},
    useI18n: () => ({ t: (key) => key, locale: ref('en') }),
    ...extras
  })
  vm.runInContext(script, context)
  return (code) => vm.runInContext(code, context)
}

test('dashboard keeps currency totals separate and omits an undefined week-on-week change', async () => {
  const run = await setup('Platform/PlatformOverview.vue')
  run(
    'overview.value = { costs_7d: { USD: 1, CNY: 2 }, run_change_percent: null }'
  )
  const cost = run("cards.value.find(row => row.key === 'costs')")
  assert.match(cost.value, /1/)
  assert.match(cost.value, /2/)
  assert.match(cost.value, / · /)
  assert.equal(run("cards.value.find(row => row.key === 'runs').change"), null)
})

test('failed dashboard refresh retains last known data and displays a retryable error', async () => {
  const run = await setup('Platform/PlatformOverview.vue', {
    getPlatformOverview: async () => {
      throw { response: { data: { detail: 'Unavailable' } } }
    }
  })
  run('overview.value = { run_count_7d: 42 }')
  await run('fetchOverview()')
  assert.equal(run('overview.value.run_count_7d'), 42)
  assert.equal(run('error.value'), 'Unavailable')
  assert.equal(run('loading.value'), false)
})

test('dashboard refresh consumes the latest server generation time and clears the error', async () => {
  const run = await setup('Platform/PlatformOverview.vue', {
    getPlatformOverview: async () => ({
      generated_at: '2026-11-14T01:00:00Z',
      run_count_7d: 3
    })
  })
  run("error.value = 'Previous error'")
  await run('fetchOverview()')
  assert.equal(run('error.value'), '')
  assert.equal(run('overview.value.generated_at'), '2026-11-14T01:00:00Z')
  assert.equal(run("cards.value.find(row => row.key === 'runs').value"), '3')
})

test('attention links preserve the exact server counting window and suppress zero-count items', async () => {
  const run = await setup('Platform/PlatformOverviewActivity.vue', {
    defineProps: () => ({
      overview: {
        attention: {
          offline_node_count: 0,
          notification_failure_count_7d: 2,
          stale_pending_tenant_count: 1,
          notification_start_at: '2026-11-07T01:30:00Z',
          notification_end_at: '2026-11-14T01:30:00Z'
        }
      }
    }),
    document: { documentElement: {} },
    getComputedStyle: () => ({ getPropertyValue: () => 'blue' }),
    ChartJS: { register() {} },
    ArcElement: {},
    BarElement: {},
    CategoryScale: {},
    LinearScale: {},
    Tooltip: {},
    WifiOff: {},
    Send: {},
    Clock: {}
  })
  assert.equal(run('attention.value.length'), 2)
  assert.equal(
    run(
      "attention.value.find(item => item.key === 'notifications').to.query.status"
    ),
    'failed'
  )
  assert.equal(
    run(
      "attention.value.find(item => item.key === 'notifications').to.query.start_date"
    ),
    '2026-11-07T01:30:00Z'
  )
  assert.match(
    run("attention.value.find(item => item.key === 'tenants').to"),
    /status=pending/
  )
})

test('notification drilldown uses the exact dashboard window until the user edits dates', async () => {
  const requests = []
  const run = await setup('Notifications/Records.vue', {
    useRoute: () => ({
      query: {
        status: 'failed',
        start_date: '2026-11-07T01:30:00Z',
        end_date: '2026-11-14T01:30:00Z'
      }
    }),
    useToast: () => ({ showError() {} }),
    useDebounceFn: (fn) => fn,
    notificationsAdminApi: {
      getRecords: async (params) => {
        requests.push(params)
        return { results: [], total: 0 }
      }
    }
  })
  await run('fetchRecords()')
  assert.equal(requests[0].status, 'failed')
  assert.equal(requests[0].start_date, '2026-11-07T01:30:00Z')
  run("filters.value.start_date = '2026-11-10'; onDateFilterChanged()")
  await run('fetchRecords()')
  assert.equal(requests.at(-1).start_date, '2026-11-10')
})
