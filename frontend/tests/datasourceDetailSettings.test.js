import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import vm from 'node:vm'
import * as vue from 'vue'

const source = await readFile(
  new URL('../src/pages/lens/DataSourceDetailSettings.vue', import.meta.url),
  'utf8'
)
function setup(save = async () => ({ uuid: 'one' })) {
  const calls = [],
    events = []
  const props = vue.reactive({
    show: true,
    blocked: false,
    datasource: {
      uuid: 'one',
      source_type: 'git',
      sync_policy: {
        mode: 'crontab',
        cron: '13 7 * * 1',
        timezone: 'Europe/Paris',
        conversion: { max_file_size_mb: 30 }
      }
    }
  })
  const store = vue.reactive({ userInfo: { id: 1 }, delegation: null })
  const context = vm.createContext({
    ...vue,
    onBeforeUnmount: () => {},
    defineExpose: () => {},
    props,
    userStore: store,
    t: (k) => k,
    dataSourcePolicyLabel: () => 'Custom',
    extractErrorMessage: (e) => e.message,
    emit: (...event) => events.push(event),
    saveDataSourceSettings: async (uuid, payload) => {
      calls.push([uuid, JSON.parse(JSON.stringify(payload))])
      return save(uuid, payload)
    }
  })
  vm.runInContext(
    source.slice(source.indexOf('const preset ='), source.indexOf('</script>')),
    context
  )
  return {
    props,
    store,
    calls,
    events,
    run: (code) => vm.runInContext(code, context)
  }
}

test('detail settings preserve a custom schedule, save only changed fields and reset an override', async () => {
  const { run, calls, props } = setup()
  assert.equal(run('preset.value'), 'custom')
  await run('saveSettings()')
  assert.equal(calls.length, 0)
  run('fileLimit.value = 40')
  await run('saveSettings()')
  assert.deepEqual(calls, [['one', { max_file_size_mb: 40 }]])
  props.datasource.sync_policy = {
    mode: 'interval',
    interval_seconds: 7200,
    conversion: { max_file_size_mb: 40 }
  }
  await vue.nextTick()
  run("fileLimit.value = ''")
  await run('saveSettings()')
  assert.deepEqual(calls[1], ['one', { max_file_size_mb: null }])
})

test('saving freezes the request, blocks duplicate writes and retains failed drafts for retry', async () => {
  let release
  const pending = new Promise((resolve) => {
    release = resolve
  })
  let fail = true
  const { run, calls, events } = setup(async () => {
    await pending
    if (fail) throw new Error('Cannot save')
    return { uuid: 'one' }
  })
  run("preset.value = 'manual'; fileLimit.value = 25")
  const first = run('saveSettings()')
  await run('saveSettings()')
  assert.equal(calls.length, 1)
  assert.deepEqual(calls[0], [
    'one',
    { schedule: { mode: 'manual' }, max_file_size_mb: 25 }
  ])
  release()
  await first
  assert.equal(run('error.value'), 'Cannot save')
  assert.equal(run('dirty.value'), true)
  assert.equal(events.filter((e) => e[0] === 'saved').length, 0)
  fail = false
  await run('saveSettings()')
  assert.equal(events.filter((e) => e[0] === 'saved').length, 1)
  assert.equal(run('dirty.value'), false)
})

test('closing, changing targets or identities prevents stale saved events and resets drafts', async () => {
  for (const change of ['close', 'target', 'identity']) {
    let release
    const pending = new Promise((resolve) => {
      release = resolve
    })
    const { run, props, store, events } = setup(() => pending)
    run("preset.value = 'daily'")
    const request = run('saveSettings()')
    if (change === 'close') props.show = false
    else if (change === 'target')
      props.datasource = { uuid: 'two', source_type: 'upload', sync_policy: {} }
    else store.userInfo = { id: 2 }
    release({ uuid: 'one' })
    await request
    assert.equal(events.filter((e) => e[0] === 'saved').length, 0)
    assert.equal(run('saving.value'), false)
    assert.equal(run('dirty.value'), false)
  }
})

test('read-only and invalid values cannot submit, and upload never sends a schedule', async () => {
  const { run, props, store, calls } = setup()
  run('fileLimit.value = 0')
  await run('saveSettings()')
  assert.equal(calls.length, 0)
  assert.ok(run('error.value'))
  run('fileLimit.value = 12.5')
  await run('saveSettings()')
  assert.equal(calls.length, 0)
  run('fileLimit.value = 20')
  props.blocked = true
  await run('saveSettings()')
  props.blocked = false
  store.delegation = { mode: 'readonly' }
  run('fileLimit.value = 20')
  await run('saveSettings()')
  assert.equal(calls.length, 0)
  store.delegation = null
  props.datasource = { uuid: 'two', source_type: 'upload', sync_policy: {} }
  run("fileLimit.value = 25; preset.value = 'daily'")
  await run('saveSettings()')
  assert.deepEqual(calls, [['two', { max_file_size_mb: 25 }]])
})

test('late task details cannot replace a later datasource even when the task id matches', async () => {
  const drawer = await readFile(
    new URL('../src/pages/lens/DataSourceDetailDrawer.vue', import.meta.url),
    'utf8'
  )
  let release
  const pending = new Promise((resolve) => {
    release = resolve
  })
  const context = vm.createContext({
    props: { datasource: { uuid: 'one' } },
    expandedTaskSequence: 0,
    expandedTaskId: vue.ref(7),
    expandedTask: vue.ref(null),
    expandedTaskError: vue.ref(''),
    expandedTaskDetailLoading: vue.ref(false),
    TASK_METADATA_FIELDS: '',
    api: { get: () => pending },
    extractResponseData: (r) => r.data,
    extractErrorMessage: (e) => e.message,
    t: (k) => k
  })
  vm.runInContext(
    drawer.slice(
      drawer.indexOf('async function loadExpandedTask('),
      drawer.indexOf('function taskTypeLabel(')
    ),
    context
  )
  const request = vm.runInContext('loadExpandedTask(7)', context)
  context.props.datasource = { uuid: 'two' }
  release({ data: { id: 7, metadata: { error: 'old data' } } })
  await request
  assert.equal(context.expandedTask.value, null)
})
