import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import vm from 'node:vm'
import * as vue from 'vue'

async function component(name, overrides = {}, props = {}) {
  const source = await readFile(
    new URL(
      `../src/admin/pages/${name.includes('/') ? name : `Management/${name}`}.vue`,
      import.meta.url
    ),
    'utf8'
  )
  const script = source
    .split('<script setup>')[1]
    .split('</script>')[0]
    .replace(/^import[^]*? from ['"][^'"]+['"]\n/gm, '')
  const events = []
  const successes = []
  const context = vm.createContext({
    ...vue,
    onMounted() {},
    onBeforeUnmount() {},
    defineProps: () => props,
    defineEmits:
      () =>
      (...args) =>
        events.push(args),
    useI18n: () => ({ t: (key) => key }),
    useToast: () => ({
      showSuccess: (...args) => successes.push(args),
      showError() {}
    }),
    window: { confirm: () => true },
    clearTimeout,
    setTimeout,
    Ban: {},
    Eye: {},
    FileText: {},
    ThumbsDown: {},
    ThumbsUp: {},
    Download: {},
    RefreshCw: {},
    Settings2: {},
    Trash2: {},
    getTenantApiKeys: async () => ({ results: [] }),
    listAssistants: async () => [],
    ...overrides
  })
  vm.runInContext(script, context)
  return { events, successes, run: (code) => vm.runInContext(code, context) }
}

test('bulk dialog freezes reviewed key versions and never offers batch rotation', async () => {
  const { run } = await component('TenantApiKeys')
  run(
    "keys.value = [{uuid: 'one', name: 'First', updated_at: 'v1'}]; selectedKeys.value = ['one']; openBulk('extend')"
  )
  run("keys.value[0].updated_at = 'v2'")
  assert.equal(run('bulkSelection.value[0].updated_at'), 'v1')
  assert.equal(run('bulkAction.value'), 'extend')
  run('closeBulk()')
  run("openBulk('rotate')")
  assert.equal(run('bulkAction.value'), '')
})

test('extension sends the reviewed snapshot once and only closes after the result', async () => {
  let release
  const payloads = []
  const { run, events } = await component(
    'ApiKeyBulkModal',
    {
      bulkUpdateTenantApiKeys: (payload) => {
        payloads.push(JSON.parse(JSON.stringify(payload)))
        return new Promise((resolve) => {
          release = resolve
        })
      }
    },
    {
      action: 'extend',
      keys: [{ uuid: 'one', name: 'First', updated_at: 'v1' }]
    }
  )
  const pending = run('save()')
  await run('save()')
  run('close()')
  assert.equal(payloads.length, 1)
  assert.deepEqual(payloads[0], {
    action: 'extend',
    days: 90,
    keys: [{ uuid: 'one', updated_at: 'v1' }]
  })
  assert.equal(events.length, 0)
  release({
    changed: 0,
    skipped: 1,
    skipped_items: [{ uuid: 'one', reason: 'never_expires' }]
  })
  await pending
  assert.equal(events[0][0], 'updated')
  assert.equal(run('result.value.skipped_items[0].reason'), 'never_expires')
  await run('save()')
  assert.equal(payloads.length, 1)
  run('close()')
  assert.equal(events[1][0], 'close')
})

test('a stale selection preserves the dialog and requires selecting keys again', async () => {
  let requests = 0
  const { run, events } = await component(
    'ApiKeyBulkModal',
    {
      bulkUpdateTenantApiKeys: async () => {
        requests++
        throw { response: { status: 409 } }
      }
    },
    { action: 'revoke', keys: [{ uuid: 'one', updated_at: 'v1' }] }
  )
  await run('save()')
  assert.equal(run('stale.value'), true)
  assert.equal(run('result.value'), null)
  assert.equal(events.length, 0)
  await run('save()')
  assert.equal(requests, 1)
})

test('list requests ignore late responses and clear selection on a fresh search', async () => {
  const releases = []
  const { run } = await component('TenantApiKeys', {
    getTenantApiKeys: () => new Promise((resolve) => releases.push(resolve))
  })
  run("selectedKeys.value = ['old']")
  const first = run('fetchKeys()')
  const second = run('fetchKeys()')
  releases[1]({ count: 1, results: [{ uuid: 'new' }] })
  await second
  releases[0]({ count: 1, results: [{ uuid: 'old' }] })
  await first
  assert.equal(run('keys.value[0].uuid'), 'new')
  assert.equal(run('selectedKeys.value.length'), 0)
})

test('creation submits once and keeps its drawer open while saving', async () => {
  let release
  let requests = 0
  const { run } = await component('TenantApiKeys', {
    createTenantApiKey: () => {
      requests++
      return new Promise((resolve) => {
        release = resolve
      })
    }
  })
  run("showCreate.value = true; createForm.name = 'CI'")
  const pending = run('createKey()')
  await run('createKey()')
  run('closeCreate()')
  assert.equal(requests, 1)
  assert.equal(run('showCreate.value'), true)
  release({ key: 'slk_ephemeral_test_value' })
  await pending
  assert.equal(run('createdSecret.value'), 'slk_ephemeral_test_value')
  run('closeSecret()')
  assert.equal(run('createdSecret.value'), '')
})

test('expired and revoked rows cannot invoke misleading enable or rotate actions', async () => {
  const { run } = await component('TenantApiKeys')
  assert.equal(
    run(
      "rowActions({ status: 'expired', raw_status: 'disabled' }).find(a => a.key === 'toggle').disabled"
    ),
    true
  )
  assert.equal(
    run(
      "rowActions({ status: 'revoked' }).filter(a => a.key !== 'detail').every(a => a.disabled)"
    ),
    true
  )
})

test('pagination uses the applied search and returns to a valid page after changes', async () => {
  const calls = []
  const { run } = await component('TenantApiKeys', {
    getTenantApiKeys: async (params) => {
      calls.push({ ...params })
      return { count: 5, results: params.page === 1 ? [{ uuid: 'first' }] : [] }
    }
  })
  run(
    "search.value = 'applied'; searchInput.value = 'draft'; currentPage.value = 2"
  )
  await run('fetchKeys()')
  assert.deepEqual(
    calls.map((params) => params.page),
    [2, 1]
  )
  assert.deepEqual(
    calls.map((params) => params.search),
    ['applied', 'applied']
  )
  assert.equal(run('currentPage.value'), 1)
  assert.equal(run('keys.value[0].uuid'), 'first')
})

test('expired rotation explicitly renews for the confirmed 90 days', async () => {
  const calls = []
  const { run } = await component('TenantApiKeys', {
    rotateTenantApiKey: async (...args) => {
      calls.push(args)
      return { key: 'replacement' }
    }
  })
  await run(
    "rotateKey({uuid: 'expired-key', name: 'Expired', status: 'expired'})"
  )
  assert.equal(calls[0][0], 'expired-key')
  assert.equal(calls[0][1].expires_in_days, 90)
  assert.equal(run('createdSecret.value'), 'replacement')
})

test('platform bulk modal uses only platform actions and the platform endpoint', async () => {
  const calls = []
  const { run } = await component(
    'ApiKeyBulkModal',
    {
      platformBulkApiKeyAction: async (payload) => {
        calls.push(payload)
        return { changed: 1, skipped: 0, notices: [] }
      },
      bulkUpdateTenantApiKeys: () =>
        assert.fail('Platform must not call tenant endpoint')
    },
    {
      platform: true,
      action: 'revoke',
      keys: [{ uuid: 'one', updated_at: 'v1' }]
    }
  )
  await run('save()')
  assert.equal(calls.length, 1)
  assert.equal(calls[0].action, 'revoke')
  assert.equal(Object.hasOwn(calls[0], 'days'), false)
  const invalid = await component(
    'ApiKeyBulkModal',
    {
      platformBulkApiKeyAction: () =>
        assert.fail('Platform must not enable keys')
    },
    {
      platform: true,
      action: 'enable',
      keys: [{ uuid: 'one', updated_at: 'v1' }]
    }
  )
  await invalid.run('save()')
})

test('platform page freezes bulk snapshots and ignores outdated list responses', async () => {
  const releases = []
  const { run } = await component('Platform/PlatformApiKeys', {
    getPlatformApiKeys: () => new Promise((resolve) => releases.push(resolve))
  })
  run(
    "rows.value = [{uuid: 'one', updated_at: 'v1'}]; selectedKeys.value = ['one']; openBulk('disable')"
  )
  run("rows.value[0].updated_at = 'v2'")
  assert.equal(run('bulkSelection.value[0].updated_at'), 'v1')
  const first = run('fetchKeys()')
  const second = run('fetchKeys()')
  releases[1]({ count: 1, results: [{ uuid: 'new' }] })
  await second
  releases[0]({ count: 1, results: [{ uuid: 'old' }] })
  await first
  assert.equal(run('rows.value[0].uuid'), 'new')
  assert.equal(run('selectedKeys.value.length'), 0)
})

test('single platform actions prevent duplicate requests and retain notice status', async () => {
  let release
  let requests = 0
  const { run } = await component('Platform/PlatformApiKeys', {
    platformApiKeyAction: () => {
      requests++
      return new Promise((resolve) => {
        release = resolve
      })
    },
    getPlatformApiKeys: async () => ({ count: 0, results: [] })
  })
  const pending = run("forceAction({uuid: 'one', name: 'Key'}, 'disable')")
  await run("forceAction({uuid: 'one', name: 'Key'}, 'revoke')")
  assert.equal(requests, 1)
  release({ notification: { uuid: 'notice', status: 'pending' } })
  await pending
  assert.equal(run('notices.value[0].status'), 'pending')
  await run("forceAction({uuid: 'one'}, 'enable')")
  assert.equal(requests, 1)
})

test('notice retries show pending delivery without repeating credential control', async () => {
  let release
  let retries = 0
  const { run, events } = await component(
    'Platform/ApiKeyNotices',
    {
      retryPlatformApiKeyNotice: () => {
        retries++
        return new Promise((resolve) => {
          release = resolve
        })
      }
    },
    { notices: [{ uuid: 'notice', status: 'failed' }] }
  )
  const pending = run("retry({ uuid: 'notice', status: 'failed' })")
  await run("retry({ uuid: 'notice', status: 'failed' })")
  assert.equal(retries, 1)
  release({ uuid: 'notice', status: 'pending' })
  await pending
  assert.equal(events[0][0], 'updated')
  assert.equal(events[0][1][0].status, 'pending')
})

test('notice pagination can retrieve older failed notices and ignores a stale refresh', async () => {
  const props = vue.reactive({ notices: [] })
  const releases = []
  const calls = []
  const { run, events } = await component(
    'Platform/ApiKeyNotices',
    {
      getPlatformApiKeyNotices: (params) => {
        calls.push({ ...params })
        return new Promise((resolve) => releases.push(resolve))
      }
    },
    props
  )
  const pageRequest = run('totalCount.value = 40; changePage(1)')
  releases[0]({ count: 40, results: [{ uuid: 'older', status: 'failed' }] })
  await pageRequest
  assert.equal(calls[0].page, 2)
  assert.equal(events[0][1][0].uuid, 'older')
  const pending = run('refresh()')
  props.notices = [{ uuid: 'new', status: 'pending' }]
  await vue.nextTick()
  releases[1]({ count: 40, results: [{ uuid: 'stale', status: 'failed' }] })
  await pending
  assert.equal(events.length, 1)
})

test('revoked keys retain a read-only details entry', async () => {
  const { run } = await component('TenantApiKeys')
  run(
    "handleRowAction('detail', { uuid: 'revoked', name: 'Old', status: 'revoked' })"
  )
  assert.equal(run('detailKey.value.uuid'), 'revoked')
  assert.equal(
    run(
      "rowActions(detailKey.value).find(item => item.key === 'detail').disabled"
    ),
    undefined
  )
})

test('risk summaries are replaced only by the latest list response', async () => {
  const releases = []
  const { run } = await component('TenantApiKeys', {
    getTenantApiKeys: () => new Promise((resolve) => releases.push(resolve))
  })
  const first = run('fetchKeys()')
  const second = run('fetchKeys()')
  releases[1]({
    results: [],
    count: 0,
    summary: { expiring_soon: 3, unused: 7 }
  })
  await second
  releases[0]({
    results: [],
    count: 0,
    summary: { expiring_soon: 0, unused: 0 }
  })
  await first
  assert.equal(run('summary.value.expiring_soon'), 3)
  assert.equal(run('summary.value.unused'), 7)
})

test('details discard a previous key response after selecting another key', async () => {
  const props = { selected: { uuid: 'one' }, revision: 0 }
  const releases = []
  const { run } = await component(
    'ApiKeyDetailDrawer',
    {
      getTenantApiKey: () => new Promise((resolve) => releases.push(resolve))
    },
    props
  )
  props.selected = { uuid: 'two' }
  const latest = run('load()')
  releases[1]({ uuid: 'two', name: 'Current', recent_calls: [] })
  await latest
  releases[0]({ uuid: 'one', name: 'Outdated', recent_calls: [] })
  await vue.nextTick()
  assert.equal(run('detail.value.uuid'), 'two')
  assert.equal(run('loading.value'), false)
})

test('details can retry a failed fetch and never call an accepted request completed', async () => {
  let fail = true
  const { run } = await component(
    'ApiKeyDetailDrawer',
    {
      getTenantApiKey: async () => {
        if (fail) throw Error('Private server error')
        return { uuid: 'one', name: 'Recovered', recent_calls: [] }
      }
    },
    { selected: { uuid: 'one' } }
  )
  await run('load()')
  assert.equal(run('error.value'), 'management.apiKeyLoadFailed')
  assert.equal(run('detail.value'), null)
  fail = false
  await run('load()')
  assert.equal(run('detail.value.name'), 'Recovered')
  assert.equal(run('error.value'), '')
  assert.equal(
    run('callStatus({status_code: 201, run_status: "queued"})'),
    'management.apiKeyInsights.run_queued'
  )
  assert.equal(
    run('callStatus({status_code: 201, run_status: "failed"})'),
    'management.apiKeyInsights.run_failed'
  )
  assert.equal(
    run('callStatus({status_code: 403})'),
    'management.apiKeyInsights.rejected'
  )
  assert.equal(
    run('callStatus({status_code: 201})'),
    'management.apiKeyInsights.accepted'
  )
})

test('full history uses only the selected key UUID', async () => {
  const props = vue.reactive({
    selected: { uuid: 'one', name: 'Sensitive name' }
  })
  const { run } = await component(
    'ApiKeyDetailDrawer',
    { getTenantApiKey: async () => ({}) },
    props
  )
  assert.deepEqual(JSON.parse(run('JSON.stringify(historyRoute.value)')), {
    name: 'LensRunObservation',
    query: { api_key: 'one' }
  })
  props.selected = { uuid: 'two' }
  assert.equal(run('historyRoute.value.query.api_key'), 'two')
})

test('history sends the key source with other filters and clears it through the route', async () => {
  const params = []
  const replacements = []
  const { run } = await component('lens/RunObservation', {
    useRoute: () => ({
      path: '/management/lens/runs',
      query: { api_key: 'one', assistant: 'chat' }
    }),
    useRouter: () => ({ replace: (value) => replacements.push(value) }),
    useUserStore: () => ({}),
    useDebounceFn: (fn) => fn,
    getAdminRuns: async (value) => {
      params.push(value)
      return { results: [], total: 0 }
    },
    resolveRunSummary: () => ({ total: 0 })
  })
  run(
    "filters.value.api_key = 'one'; filters.value.assistant = 'chat'; filters.value.status = 'failed'"
  )
  await run('fetchRuns()')
  assert.equal(params[0].api_key, 'one')
  assert.equal(params[0].assistant, 'chat')
  assert.equal(params[0].status, 'failed')
  run('clearApiKeyFilter()')
  assert.deepEqual(JSON.parse(JSON.stringify(replacements[0].query)), {
    assistant: 'chat'
  })
  run('resetFilters()')
  assert.equal(run('filters.value.api_key'), '')
  assert.equal(params[1].api_key, undefined)
})

test('a stale history response cannot replace a different key filter result', async () => {
  const releases = []
  const { run } = await component('lens/RunObservation', {
    useRoute: () => ({ query: {} }),
    useRouter: () => ({}),
    useUserStore: () => ({}),
    useDebounceFn: (fn) => fn,
    getAdminRuns: () => new Promise((resolve) => releases.push(resolve)),
    resolveRunSummary: (data) => ({ total: data.total })
  })
  run("filters.value.api_key = 'old'")
  const old = run('fetchRuns()')
  run("filters.value.api_key = 'current'")
  const current = run('fetchRuns()')
  releases[1]({ results: [{ uuid: 'current' }], total: 1 })
  await current
  releases[0]({ results: [{ uuid: 'old' }], total: 9 })
  await old
  assert.equal(run('runs.value[0].uuid'), 'current')
  assert.equal(run('total.value'), 1)
  assert.equal(run('statusSummary.value.total'), 1)
})

test('creation sends the optional purpose and resets it for the next key', async () => {
  const payloads = []
  const { run } = await component('TenantApiKeys', {
    createTenantApiKey: async (data) => {
      payloads.push(data)
      return { key: 'test-only-key' }
    }
  })
  run("createForm.name = 'CI'; createForm.description = '  Summarize builds  '")
  await run('createKey()')
  assert.equal(payloads[0].description, 'Summarize builds')
  run('openCreate()')
  assert.equal(run('createForm.description'), '')
})

test('platform identification prefers the authoritative suffix and supports old responses', async () => {
  const { run } = await component('Platform/PlatformApiKeys')
  assert.equal(
    run(
      "maskedKey({ key_display: 'slk_••••X7Qz', key_prefix: 'slk_oldprefix' })"
    ),
    'slk_••••X7Qz'
  )
  assert.equal(
    run("maskedKey({ key_prefix: 'slk_legacy12' })"),
    'slk_legacy12••••'
  )
})

test('key settings preserve the reviewed version and update scope without rotating or renewing', async () => {
  const payloads = []
  const { run } = await component('TenantApiKeys', {
    updateTenantApiKey: async (uuid, data) => {
      payloads.push({ uuid, ...data })
      return { uuid }
    }
  })
  run(
    "openSettings({ uuid: 'one', name: 'CI', description: 'Purpose', scope: 'selected', assistant_uuids: ['a'], updated_at: 'v1', status: 'disabled' })"
  )
  assert.equal(run('createForm.description'), 'Purpose')
  run("createForm.assistantUuids.push('b'); createForm.name = 'Updated'")
  await run('createKey()')
  assert.equal(payloads[0].updated_at, 'v1')
  assert.equal(payloads[0].scope, 'selected')
  assert.equal(payloads[0].assistant_uuids.join(','), 'a,b')
  assert.equal(payloads[0].expires_in_days, undefined)
  assert.equal(payloads[0].status, undefined)
  assert.equal(run('createdSecret.value'), '')
  assert.equal(run('editingKey.value'), null)
})

test('conflicting settings retain the draft and do not retry or refresh the reviewed version', async () => {
  let requests = 0
  const { run } = await component('TenantApiKeys', {
    updateTenantApiKey: async () => {
      requests++
      throw { response: { status: 409 } }
    }
  })
  run(
    "openSettings({ uuid: 'one', name: 'CI', scope: 'all', updated_at: 'v1', status: 'active' }); createForm.description = 'Draft'"
  )
  await run('createKey()')
  assert.equal(requests, 1)
  assert.equal(run('showCreate.value'), true)
  assert.equal(run('editingKey.value.updated_at'), 'v1')
  assert.equal(run('createForm.description'), 'Draft')
  assert.equal(run('createError.value'), 'management.apiKeySettingsConflict')
})

test('settings never silently drop old selections and allow removing unavailable ones', async () => {
  const { run } = await component('TenantApiKeys', {
    listAssistants: async () => [{ uuid: 'known' }]
  })
  run(
    "openSettings({ uuid: 'key', name: 'CI', scope: 'selected', assistant_uuids: ['known', 'deleted'], updated_at: 'v1', status: 'active' })"
  )
  await run('fetchAssistants()')
  assert.equal(run('createForm.assistantUuids.join()'), 'known,deleted')
  assert.equal(run('unavailableSelections.value.join()'), 'deleted')
  run("removeUnavailableSelection('deleted')")
  assert.equal(run('createForm.assistantUuids.join()'), 'known')
})
