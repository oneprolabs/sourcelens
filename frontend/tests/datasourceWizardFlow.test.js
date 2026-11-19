import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import vm from 'node:vm'
import * as vue from 'vue'

async function wizard(overrides = {}) {
  const source = await readFile(
    new URL('../src/pages/lens/DataSourceFormDrawer.vue', import.meta.url),
    'utf8'
  )
  const script = source
    .match(/<script setup>([^]*?)<\/script>/)[1]
    .replace(/^import [^]*? from ['"][^'"]+['"]\n/gm, '')
  const props = vue.reactive({
    show: false,
    mode: 'create',
    saving: false,
    form: {
      source_type: 'plugin:github',
      plugin_key: 'github',
      name: '',
      connection_uuid: ''
    },
    config: {},
    plugins: [],
    pluginManifest: { datasource_schema: { required: ['repositories'] } },
    connections: [],
    credentials: [],
    lensnodes: [],
    connectionResult: null,
    ...overrides
  })
  const events = []
  const context = vm.createContext({
    ...vue,
    defineProps: () => props,
    defineEmits:
      () =>
      (...args) =>
        events.push(args),
    useI18n: () => ({ t: (key) => key, te: () => false }),
    localizePluginManifest: (value) => value || {}
  })
  vm.runInContext(script, context)
  const run = (code) => vm.runInContext(code, context)
  return { props, run, events }
}

test('source and connection steps cannot bypass validated content', async () => {
  const { props, run } = await wizard()
  assert.deepEqual(Array.from(run('wizardStepsMeta.value.map(s => s.key)')), [
    'basic',
    'connection',
    'content',
    'sync'
  ])
  run('nextWizardStep()')
  assert.equal(run('activeStepKey.value'), 'connection')
  assert.equal(run('canProceedWizard.value'), true)
  run('nextWizardStep()')
  assert.equal(run('activeStepKey.value'), 'content')
  props.form.name = 'Docs'
  props.config.repositories = ['acme/docs']
  run('nextWizardStep()')
  assert.equal(run('activeStepKey.value'), 'content')
  props.connectionResult = { status: 'failed' }
  assert.equal(run('canProceedWizard.value'), false)
  props.connectionResult = { status: 'success' }
  assert.equal(run('canProceedWizard.value'), true)
  props.config.repositories = []
  assert.equal(run('canProceedWizard.value'), false)
  props.config.repositories = ['acme/docs']
  run('nextWizardStep()')
  assert.equal(run('activeStepKey.value'), 'sync')
})

test('cloud source requires connection selection before choosing content', async () => {
  const { props, run } = await wizard({
    form: {
      source_type: 'plugin:feishu',
      plugin_key: 'feishu',
      name: 'Docs',
      connection_uuid: ''
    }
  })
  run('nextWizardStep(); nextWizardStep()')
  assert.equal(run('activeStepKey.value'), 'connection')
  props.form.connection_uuid = 'selected'
  run('nextWizardStep()')
  assert.equal(run('activeStepKey.value'), 'content')
  assert.equal(run('canProceedWizard.value'), false)
})

test('upload skips connection in both directions and requires an online approved node', async () => {
  const { props, run } = await wizard({
    form: {
      source_type: 'plugin:file_upload',
      plugin_key: 'file_upload',
      name: 'Uploads',
      lensnode_uuid: 'node'
    },
    pluginManifest: { datasource_source_type: 'managed_workspace' },
    lensnodes: [
      { uuid: 'node', status: 'offline', enrollment_status: 'approved' }
    ]
  })
  run('nextWizardStep()')
  assert.equal(run('activeStepKey.value'), 'content')
  assert.equal(run('canProceedWizard.value'), false)
  props.lensnodes[0].status = 'online'
  assert.equal(run('canProceedWizard.value'), true)
  run('prevWizardStep()')
  assert.equal(run('activeStepKey.value'), 'basic')
  run('nextWizardStep(); nextWizardStep()')
  assert.equal(run('activeStepKey.value'), 'sync')
  run('prevWizardStep()')
  assert.equal(run('activeStepKey.value'), 'content')
})

test('saving freezes source selection, step navigation and forward actions', async () => {
  const { props, run, events } = await wizard()
  props.saving = true
  run(
    "selectSourceType('plugin:feishu'); nextWizardStep(); goToWizardStep(2); prevWizardStep()"
  )
  assert.equal(props.form.source_type, 'plugin:github')
  assert.equal(run('wizardStep.value'), 1)
  assert.equal(events.length, 0)
  props.saving = false
  run("selectSourceType('plugin:feishu')")
  assert.equal(props.form.source_type, 'plugin:feishu')
  assert.deepEqual(events, [['type-change']])
})

test('selecting the current source advances without resetting existing content', async () => {
  const { props, run, events } = await wizard()
  props.show = true
  props.config.repositories = ['acme/docs']
  await vue.nextTick()
  events.length = 0
  await run("selectSourceType('plugin:github')")
  assert.equal(run('activeStepKey.value'), 'connection')
  assert.deepEqual(Array.from(props.config.repositories), ['acme/docs'])
  assert.equal(events.filter(([event]) => event === 'type-change').length, 0)
})

test('source selection does not advance a drawer closed during the next tick', async () => {
  const { props, run } = await wizard()
  props.show = true
  const selecting = run("selectSourceType('plugin:feishu')")
  props.show = false
  await selecting
  assert.equal(run('activeStepKey.value'), 'basic')
})
