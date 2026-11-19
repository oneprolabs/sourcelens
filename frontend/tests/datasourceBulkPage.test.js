import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import vm from 'node:vm'
import * as vue from 'vue'
import {
  executeDataSourceBatch,
  freezeDataSourceTargets
} from '../src/pages/lens/datasourceBulk.js'

async function setup(overrides = {}) {
  const source = await readFile(
    new URL('../src/pages/lens/DataSources.vue', import.meta.url),
    'utf8'
  )
  const store = vue.reactive({ userInfo: { id: 1 }, delegation: null })
  const state = { loads: 0, calls: [], store }
  const context = vm.createContext({
    ...vue,
    executeDataSourceBatch,
    freezeDataSourceTargets,
    userStore: store,
    dataSources: vue.ref([
      { uuid: 'one', name: 'One', status: 'active', source_type: 'git' },
      { uuid: 'two', name: 'Two', status: 'active', source_type: 'git' }
    ]),
    readonly: vue.computed(() => store.delegation?.mode === 'readonly'),
    selectedSourceIds: vue.ref(['one']),
    loading: vue.ref(false),
    bulkAction: vue.ref(''),
    bulkTargets: vue.ref([]),
    bulkResults: vue.ref([]),
    bulkStarted: vue.ref(false),
    bulkBusy: vue.ref(false),
    bulkActions: [{ key: 'policy' }, { key: 'pause' }, { key: 'delete' }],
    knowledgeView: vue.ref('mine'),
    knowledgeLayout: vue.ref('table'),
    pageActive: true,
    selectedDataSource: vue.ref(null),
    currentPage: vue.ref(1),
    setDataSourceSyncPolicy: async (uuid, policy) =>
      state.calls.push([uuid, { ...policy }]),
    setDataSourceEnabled: async (uuid, enabled) =>
      state.calls.push([uuid, enabled]),
    getDataSource: async () => ({}),
    syncDataSource: async () => ({}),
    deleteDataSource: async () => {},
    load: async () => {
      state.loads++
    },
    closeDataSourceDetail() {},
    ...overrides
  })
  vm.runInContext(
    source.slice(
      source.indexOf('function selectSourcePage('),
      source.indexOf('</script>')
    ),
    context
  )
  return { state, run: (code) => vm.runInContext(code, context) }
}

test('confirmation freezes visible selected ids and prevents duplicate submission', async () => {
  const { run, state } = await setup()
  run(
    "openBulk('policy'); selectedSourceIds.value = ['two']; dataSources.value[0].name = 'Renamed'"
  )
  assert.equal(run('bulkTargets.value[0].name'), 'One')
  await Promise.all([
    run("runBulk({mode:'manual'})"),
    run("runBulk({mode:'interval', interval_seconds:600})")
  ])
  assert.deepEqual(state.calls, [['one', { mode: 'manual' }]])
  assert.equal(run('bulkResults.value[0].status'), 'success')
  assert.equal(state.loads, 1)
})

test('layout, view, refresh and permission boundaries prevent stale target selection', async () => {
  const { run, state } = await setup()
  run("knowledgeLayout.value = 'cards'")
  await vue.nextTick()
  assert.equal(run('selectedSourceIds.value.length'), 0)
  run("selectSourcePage(true); knowledgeView.value = 'catalog'")
  await vue.nextTick()
  assert.equal(run('selectedSourceIds.value.length'), 0)
  run("selectedSourceIds.value = ['missing']; openBulk('policy')")
  assert.equal(run('bulkAction.value'), '')
  state.store.delegation = { mode: 'readonly' }
  await vue.nextTick()
  run(
    "selectSourcePage(true); selectedSourceIds.value = ['one']; openBulk('pause')"
  )
  assert.equal(run('bulkAction.value'), '')
  assert.equal(state.calls.length, 0)
})

test('changing accounts stops later writes and ignores a late successful response', async () => {
  let release
  const blocked = new Promise((resolve) => {
    release = resolve
  })
  const calls = []
  const { run, state } = await setup({
    setDataSourceEnabled: async (uuid) => {
      calls.push(uuid)
      await blocked
    }
  })
  run("selectSourcePage(true); openBulk('pause')")
  assert.equal(run('bulkBusy.value'), true)
  run('closeBulk()')
  assert.equal(run('bulkAction.value'), 'pause')
  state.store.userInfo = { id: 2 }
  await vue.nextTick()
  release()
  await new Promise((resolve) => setImmediate(resolve))
  assert.deepEqual(calls, ['one'])
  assert.equal(run('bulkAction.value'), '')
  assert.equal(run('bulkResults.value.length'), 0)
  assert.equal(state.loads, 0)
})
