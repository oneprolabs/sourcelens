import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import vm from 'node:vm'
import { ref } from 'vue'

async function handler(file, name, extras = {}) {
  const source = await readFile(
    new URL(`../src/${file}`, import.meta.url),
    'utf8'
  )
  const body = source.match(new RegExp(`async function ${name}\\([^]*?\\n}`))[0]
  const events = []
  const context = vm.createContext({
    ref,
    props: { embedded: true },
    emit: (event, payload) =>
      events.push({
        event,
        payload: JSON.parse(JSON.stringify(payload ?? null))
      }),
    t: (key) => key,
    showSuccess() {},
    showInfo() {},
    showError() {},
    extractErrorMessage: (error) => error.message,
    ...extras
  })
  vm.runInContext(body, context)
  return { run: (code) => vm.runInContext(code, context), events }
}

test('model save emits continuation only after the server accepts a valid tested form', async () => {
  let release
  const state = {
    form: { provider: 'openai', model_type: 'llm', is_active: true },
    formSaving: ref(false),
    formMessage: ref(''),
    formMessageSuccess: ref(false),
    configFormElement: ref({ reportValidity: () => true }),
    submitDisabled: ref(false),
    editingId: ref(''),
    buildFormConfigPayload: () => ({ model: 'test-model' }),
    llmAdminApi: {
      postLLMConfig: () =>
        new Promise((resolve) => {
          release = resolve
        })
    },
    closeConfigModal() {},
    loadAll() {}
  }
  const { run, events } = await handler(
    'admin/pages/LLM/Config.vue',
    'submitConfigForm',
    state
  )
  const saving = run('submitConfigForm(true)')
  assert.equal(events.length, 0)
  assert.equal(state.formSaving.value, true)
  release({ uuid: 'created-model' })
  await saving
  assert.deepEqual(events, [{ event: 'saved', payload: { advance: true } }])
  assert.equal(state.formSaving.value, false)
  state.submitDisabled.value = true
  await run('submitConfigForm(true)')
  assert.equal(events.length, 1)
  state.submitDisabled.value = false
  state.configFormElement.value.reportValidity = () => false
  await run('submitConfigForm(true)')
  assert.equal(events.length, 1)
})

test('assistant save failure retains inputs and does not signal continuation', async () => {
  let closed = false
  const state = {
    saving: ref(false),
    formError: ref(''),
    form: ref({ name: 'Kept draft' }),
    selectedVisionModelIsEligible: () => true,
    buildPayload: () => ({ name: 'Kept draft' }),
    createAssistant() {},
    updateAssistant() {},
    saveByMode: async () => {
      throw new Error('Invalid skill binding')
    },
    closeDrawer: () => {
      closed = true
    },
    load() {}
  }
  const { run, events } = await handler(
    'pages/lens/Assistants.vue',
    'save',
    state
  )
  await run('save(true)')
  assert.equal(closed, false)
  assert.equal(state.form.value.name, 'Kept draft')
  assert.equal(state.formError.value, 'Invalid skill binding')
  assert.equal(events.length, 0)
})

test('file upload failure keeps the created source and draft for retry before continuing', async () => {
  let createCount = 0
  let uploadCount = 0
  let closed = false
  const state = {
    saving: ref(false),
    formError: ref(''),
    mode: ref('create'),
    form: ref({
      name: 'Draft upload',
      plugin_key: 'file_upload',
      source_type: 'upload'
    }),
    pendingUploadFiles: ref([{ name: 'guide.md', size: 3 }]),
    uploadBaselineNames: ref([]),
    dataSources: ref([]),
    canSaveDatasource: () => true,
    buildCreatePayloads: async () => [{ name: 'Draft upload' }],
    buildPayload: () => ({ name: 'Draft upload' }),
    getDataSourceUploadLimits: async () => ({ max_bytes: 100 }),
    isSupportedUploadFile: () => true,
    createDataSource: async () => {
      createCount++
      return { uuid: 'created-upload' }
    },
    updateDataSource: async (uuid) => {
      assert.equal(uuid, 'created-upload')
    },
    uploadDataSourceFile: async (uuid) => {
      assert.equal(uuid, 'created-upload')
      uploadCount++
      if (uploadCount === 1) throw new Error('Upload interrupted')
    },
    deleteDataSourceUpload() {},
    closeDrawer: () => {
      closed = true
    },
    load() {}
  }
  const { run, events } = await handler(
    'pages/lens/DataSources.vue',
    'save',
    state
  )
  await run('save(true)')
  assert.equal(closed, false)
  assert.equal(state.form.value.name, 'Draft upload')
  assert.equal(state.form.value.uuid, 'created-upload')
  assert.equal(state.pendingUploadFiles.value.length, 1)
  assert.equal(events.length, 0)
  assert.equal(state.formError.value, 'Upload interrupted')
  await run('save(true)')
  assert.equal(createCount, 1)
  assert.equal(uploadCount, 2)
  assert.equal(closed, true)
  assert.deepEqual(events, [{ event: 'saved', payload: { advance: true } }])
})
