import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import vm from 'node:vm'
import { computed, nextTick, ref } from 'vue'

async function handler(file, names, state) {
  const source = await readFile(
    new URL(`../src/pages/lens/${file}`, import.meta.url),
    'utf8'
  )
  const context = vm.createContext({ nextTick, ...state })
  for (const name of names) {
    const body = source.match(
      new RegExp(`(?:async )?function ${name}\\([^]*?\\n}`)
    )
    assert.ok(body, `${file} contains ${name}`)
    vm.runInContext(body[0], context)
  }
  return (code) => vm.runInContext(code, context)
}

test('embedded connection validates the created resource and retries without another create', async () => {
  let creates = 0
  let validations = 0
  const events = []
  const state = {
    props: { embedded: true },
    saving: ref(false),
    canSave: ref(true),
    mode: ref('create'),
    form: ref({
      name: 'Draft',
      plugin_key: 'github',
      secret_value: 'input-only',
      status: 'active'
    }),
    formError: ref(''),
    buildConnectionPayload: () => ({
      name: 'Draft',
      secret_value: state.form.value.secret_value
    }),
    createConnection: async () => {
      creates++
      return {
        uuid: 'created',
        plugin_key: 'github',
        name: 'Draft',
        status: 'active',
        has_secret: true
      }
    },
    updateConnection: async (uuid) => {
      assert.equal(uuid, 'created')
      return { uuid, has_secret: true }
    },
    validateConnection: async () => {
      if (++validations === 1) throw new Error('Provider unavailable')
    },
    clearSecretFields: () => {
      state.form.value.secret_value = ''
    },
    closeDrawer: () => {
      state.closed = true
    },
    load: async () => {},
    showSuccess() {},
    showError() {},
    t: (key, args) => (args?.error ? `Saved: ${args.error}` : key),
    extractErrorMessage: (error) => error.message,
    emit: (event, payload) =>
      events.push({ event, payload: JSON.parse(JSON.stringify(payload)) })
  }
  const run = await handler('Connections.vue', ['save'], state)
  await run('save(true)')
  assert.equal(events.length, 0)
  assert.equal(state.closed, undefined)
  assert.equal(state.form.value.uuid, 'created')
  assert.equal(state.form.value.name, 'Draft')
  assert.equal(state.form.value.secret_value, '')
  assert.equal(state.formError.value, 'Saved: Provider unavailable')
  await run('save(true)')
  assert.equal(creates, 1)
  assert.equal(validations, 2)
  assert.equal(state.closed, true)
  assert.deepEqual(events, [
    {
      event: 'saved',
      payload: { uuid: 'created', plugin_key: 'github', advance: true }
    }
  ])
})

test('a failed connection list refresh retains the draft and current selection', async () => {
  const state = {
    connectionEditorOpen: ref(true),
    showDrawer: ref(true),
    saving: ref(false),
    form: ref({
      name: 'Draft',
      plugin_key: 'github',
      connection_uuid: 'previous'
    }),
    connections: ref([{ uuid: 'previous' }]),
    listConnections: async () => {
      throw new Error('Unavailable')
    },
    normalizeList: (rows) => rows,
    showError() {},
    t: (key) => key,
    extractErrorMessage: (error) => error.message,
    emit() {},
    resetDatasourceConnectionResult() {
      throw new Error('Must not reset draft')
    }
  }
  const run = await handler(
    'DataSources.vue',
    ['refreshConnections', 'connectionSaved'],
    state
  )
  await run("connectionSaved({ uuid: 'created', plugin_key: 'github' })")
  assert.equal(state.form.value.connection_uuid, 'previous')
  assert.equal(state.form.value.name, 'Draft')
  assert.equal(state.connections.value[0].uuid, 'previous')
})

test('nested connection selection preserves the knowledge draft and waits for its scoped list', async () => {
  let release
  let tested = 0
  const events = []
  const state = {
    connectionEditorOpen: ref(true),
    showDrawer: ref(true),
    saving: ref(false),
    form: ref({
      name: 'Knowledge draft',
      plugin_key: 'github',
      connection_uuid: '',
      public_endpoint: 'https://github.com'
    }),
    datasourceConfig: ref({ repositories: ['acme/docs'] }),
    connections: ref([]),
    listConnections: () =>
      new Promise((resolve) => {
        release = resolve
      }),
    normalizeList: (rows) => rows,
    showError() {},
    t: (key) => key,
    extractErrorMessage: (error) => error.message,
    resetDatasourceConnectionResult() {},
    testDatasourceConnection: async () => {
      tested++
    },
    emit: (event) => events.push(event)
  }
  const run = await handler(
    'DataSources.vue',
    ['refreshConnections', 'connectionSaved'],
    state
  )
  const pending = run(
    "connectionSaved({ uuid: 'created', plugin_key: 'github' })"
  )
  assert.equal(state.form.value.connection_uuid, '')
  release([{ uuid: 'created', plugin_key: 'github', status: 'active' }])
  await pending
  assert.equal(state.form.value.connection_uuid, 'created')
  assert.equal(state.form.value.name, 'Knowledge draft')
  assert.equal(state.datasourceConfig.value.repositories[0], 'acme/docs')
  assert.equal(state.form.value.public_endpoint, '')
  assert.equal(state.connectionEditorOpen.value, false)
  assert.equal(tested, 1)
  assert.deepEqual(events, ['connection-saved'])
})

test('a delayed connection save cannot replace a manually changed selection', async () => {
  let release
  const state = {
    connectionEditorOpen: ref(true),
    showDrawer: ref(true),
    saving: ref(false),
    form: ref({ plugin_key: 'github', connection_uuid: '' }),
    connections: ref([]),
    listConnections: () =>
      new Promise((resolve) => {
        release = resolve
      }),
    normalizeList: (rows) => rows,
    showError() {},
    t: (key) => key,
    extractErrorMessage: (error) => error.message,
    emit() {},
    resetDatasourceConnectionResult() {
      throw new Error('Must not reset draft')
    }
  }
  const run = await handler(
    'DataSources.vue',
    ['refreshConnections', 'connectionSaved'],
    state
  )
  const pending = run(
    "connectionSaved({ uuid: 'created', plugin_key: 'github' })"
  )
  state.form.value.connection_uuid = 'manual-choice'
  release([{ uuid: 'created', plugin_key: 'github', status: 'active' }])
  await pending
  assert.equal(state.form.value.connection_uuid, 'manual-choice')
})

test('setup directs anonymous all-repository access back to knowledge without creating a hidden connection', async () => {
  const source = await readFile(
    new URL('../src/pages/lens/Connections.vue', import.meta.url),
    'utf8'
  )
  const form = ref({
    name: 'Public',
    plugin_key: 'github',
    secret_value: '',
    repositories: ['*']
  })
  const context = vm.createContext({
    computed,
    form,
    props: { embedded: true },
    hasStoredSecret: ref(false),
    connectionResourceField: ref(['repositories', {}]),
    manifest: ref({ connection_schema: { required: ['repositories'] } }),
    mode: ref('create'),
    hasFieldValue: (value) =>
      Array.isArray(value)
        ? value.length > 0
        : Boolean(String(value || '').trim())
  })
  for (const name of ['anonymousAllScope', 'canSave']) {
    vm.runInContext(
      source.match(
        new RegExp(`const ${name} = computed\\(\\(\\) => \\{[^]*?\\n}\\)`)
      )[0],
      context
    )
  }
  assert.equal(vm.runInContext('canSave.value', context), false)
  form.value.secret_value = 'input-only'
  assert.equal(vm.runInContext('canSave.value', context), true)
  form.value.secret_value = ''
  form.value.repositories = ['acme/docs']
  assert.equal(vm.runInContext('canSave.value', context), true)
})

test('late connection refresh cannot attach a connection to a replacement knowledge draft', async () => {
  let release
  const state = {
    connectionEditorOpen: ref(true),
    showDrawer: ref(true),
    saving: ref(false),
    form: ref({ plugin_key: 'github', connection_uuid: '' }),
    connections: ref([]),
    listConnections: () =>
      new Promise((resolve) => {
        release = resolve
      }),
    normalizeList: (rows) => rows,
    showError() {},
    t: (key) => key,
    emit() {},
    resetDatasourceConnectionResult() {
      throw new Error('Must not reset draft')
    }
  }
  const run = await handler(
    'DataSources.vue',
    ['refreshConnections', 'connectionSaved'],
    state
  )
  const pending = run(
    "connectionSaved({ uuid: 'created', plugin_key: 'github' })"
  )
  state.form.value = { plugin_key: 'github', connection_uuid: '' }
  release([{ uuid: 'created', plugin_key: 'github', status: 'active' }])
  await pending
  assert.equal(state.form.value.connection_uuid, '')
})

test('knowledge source entry resets the draft and initializes the selected plugin schema', async () => {
  let resets = 0
  let typeChanges = 0
  const state = {
    loading: ref(false),
    saving: ref(false),
    form: ref({ source_type: 'plugin:github', connection_uuid: 'old' }),
    startCreate() {
      resets++
      state.form.value = { source_type: 'plugin:github', connection_uuid: '' }
    },
    handleDatasourceTypeChange() {
      typeChanges++
      assert.equal(state.form.value.source_type, 'plugin:feishu')
      state.form.value.plugin_key = 'feishu'
    }
  }
  const run = await handler('DataSources.vue', ['startSource'], state)
  state.loading.value = true
  run('startSource({key:"feishu"})')
  assert.equal(resets, 0)
  state.loading.value = false
  state.saving.value = true
  run('startSource({key:"feishu"})')
  assert.equal(resets, 0)
  state.saving.value = false
  run('startSource({key:"feishu"})')
  assert.equal(resets, 1)
  assert.equal(typeChanges, 1)
  assert.equal(state.form.value.plugin_key, 'feishu')
  assert.equal(state.form.value.connection_uuid, '')
})
