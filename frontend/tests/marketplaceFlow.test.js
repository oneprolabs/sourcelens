import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import vm from 'node:vm'
import { setImmediate } from 'node:timers/promises'
import { compileScript, parse } from '@vue/compiler-sfc'
import { renderToString } from '@vue/server-renderer'
import * as vue from 'vue'

const sourceFor = (file) =>
  readFile(new URL(`../src/${file}`, import.meta.url), 'utf8')
const deferred = () => {
  let resolve, reject
  const promise = new Promise((yes, no) => {
    resolve = yes
    reject = no
  })
  return { promise, resolve, reject }
}
async function marketplace(overrides = {}) {
  const script = (await sourceFor('pages/lens/Marketplace.vue'))
    .split('<script setup>')[1]
    .split('</script>')[0]
    .replace(/^import[^]*? from ['"][^'"]+['"]\n/gm, '')
  const route = vue.reactive({ meta: {} })
  const user = vue.reactive({ delegation: null })
  const errors = []
  const context = vm.createContext({
    ...vue,
    onBeforeUnmount() {},
    useRoute: () => route,
    useUserStore: () => user,
    useI18n: () => ({
      t: (key) => key,
      te: () => false,
      locale: vue.ref('zh-CN')
    }),
    useToast: () => ({ showError: (error) => errors.push(error) }),
    extractErrorMessage: (error) => error.message,
    localizePluginManifest: (row) => row,
    listSkills: async () => [],
    listMcpServers: async () => [],
    listPlugins: async () => [],
    listConnections: async () => [],
    ...overrides
  })
  const scope = vue.effectScope()
  scope.run(() => vm.runInContext(script, context))
  await setImmediate()
  return {
    run: (code) => vm.runInContext(code, context),
    route,
    user,
    errors,
    stop: () => scope.stop()
  }
}
const skill = (uuid, is_official, tenant_enabled = true) => ({
  uuid,
  name: uuid,
  is_official,
  tenant_enabled,
  definition: { description: uuid }
})

test('defaults to discovery when empty and combines enabled official and tenant capabilities', async () => {
  const empty = await marketplace()
  assert.equal(empty.run('view.value'), 'discover')
  empty.stop()
  const state = await marketplace({
    listSkills: async ({ catalog }) =>
      catalog === 'official'
        ? [skill('official-on', true), skill('official-off', true, false)]
        : [skill('private-on', false), skill('private-off', false, false)],
    listPlugins: async () => [
      { key: 'github', display_name: 'GitHub' },
      { key: 'file_upload' }
    ],
    listConnections: async () => [{ uuid: 'instance' }]
  })
  assert.equal(state.run('view.value'), 'enabled')
  assert.deepEqual(
    Array.from(state.run('visibleRows.value'), (row) => row.uuid),
    ['official-on', 'private-on', 'github']
  )
  assert.equal(state.run('connectionCount.value'), 1)
  state.run("view.value = 'discover'")
  assert.deepEqual(
    Array.from(state.run('visibleRows.value'), (row) => row.uuid),
    ['official-on', 'official-off', 'github']
  )
  await state.run('load()')
  assert.equal(state.run('view.value'), 'discover')
  state.stop()
})

test('all sections show six cards with full counts and a type tab shows the complete list', async () => {
  const state = await marketplace({
    listSkills: async ({ catalog }) =>
      catalog === 'official'
        ? Array.from({ length: 8 }, (_, i) => skill(`s${i}`, true))
        : []
  })
  assert.equal(state.run('sections.value[0].rows.length'), 6)
  assert.equal(state.run('sections.value[0].count'), 8)
  assert.equal(state.run('sections.value[0].hasMore'), true)
  assert.equal(state.run('tabs.value[0].count'), 8)
  state.run("kind.value = 'skill'")
  assert.equal(state.run('sections.value[0].rows.length'), 8)
  state.stop()
})

test('partial failures preserve previous cards, mark unknown counts and recover on refresh', async () => {
  let failing = false
  const state = await marketplace({
    listSkills: async ({ catalog }) => {
      if (catalog !== 'official') return []
      if (failing) throw new Error('offline')
      return [skill('kept', true)]
    },
    listConnections: async () => {
      if (failing) throw new Error('offline')
      return [{ uuid: 'connection' }]
    }
  })
  failing = true
  await state.run('load()')
  assert.equal(state.run('rows.value[0].uuid'), 'kept')
  assert.equal(state.run('enabledCount("skill")'), '—')
  assert.equal(state.run('tabs.value[0].count'), '—')
  assert.equal(state.run('connectionCount.value'), '—')
  assert.equal(state.run('error.value'), 'catalog.partialLoadFailed')
  assert.equal(state.run('loading.value'), false)
  failing = false
  await state.run('load()')
  assert.equal(state.run('enabledCount("skill")'), 1)
  assert.equal(state.run('connectionCount.value'), 1)
  assert.equal(state.run('error.value'), '')
  state.stop()
})

test('enablement waits for acceptance, rejects duplicates and preserves state after failure', async () => {
  const pending = deferred()
  let calls = 0
  const state = await marketplace({
    listSkills: async ({ catalog }) =>
      catalog === 'official' ? [skill('s', true, false)] : [],
    setCatalogEnablement: () => {
      calls++
      return pending.promise
    }
  })
  const saving = state.run('toggle(rows.value[0])')
  await state.run('toggle(rows.value[0])')
  assert.equal(calls, 1)
  assert.equal(state.run('rows.value[0].tenant_enabled'), false)
  pending.resolve({ tenant_enabled: true })
  await saving
  assert.equal(state.run('rows.value[0].tenant_enabled'), true)
  state.run(
    'setCatalogEnablement = async () => { throw new Error("rejected") }'
  )
  await state.run('toggle(rows.value[0])')
  assert.equal(state.run('rows.value[0].tenant_enabled'), true)
  assert.deepEqual(state.errors, ['rejected'])
  assert.equal(state.run('saving.value'), '')
  state.stop()
})

test('readonly, private resources and platform connector mode cannot write enablement or open editors', async () => {
  const state = await marketplace({
    setCatalogEnablement: () => assert.fail('Forbidden write')
  })
  state.user.delegation = { mode: 'readonly' }
  await state.run('toggle({ kind: "skill", uuid: "s", is_official: true })')
  state.run('openEditor("skill")')
  assert.equal(state.run('editor.value'), '')
  state.user.delegation = null
  await state.run('toggle({ kind: "skill", uuid: "s", is_official: false })')
  state.route.meta.connectorOnly = true
  await vue.nextTick()
  await vue.nextTick()
  await state.run('toggle({ kind: "skill", uuid: "s", is_official: true })')
  state.run('openEditor("skill")')
  assert.equal(state.run('editor.value'), '')
  state.stop()
})

test('closing and switching details invalidate old requests; failed details can retry', async () => {
  const requests = []
  const state = await marketplace({
    getSkill: () => {
      const request = deferred()
      requests.push(request)
      return request.promise
    }
  })
  const first = state.run('openDetail({kind:"skill", uuid:"first"})')
  const second = state.run('openDetail({kind:"skill", uuid:"second"})')
  requests[1].resolve({ name: 'new' })
  await second
  requests[0].resolve({ name: 'old' })
  await first
  assert.equal(state.run('detail.value.name'), 'new')
  const closing = state.run('openDetail({kind:"skill", uuid:"closing"})')
  state.run('closeDetail()')
  requests[2].resolve({ name: 'late' })
  await closing
  assert.equal(state.run('detail.value'), null)
  const failing = state.run('openDetail({kind:"skill", uuid:"retry"})')
  requests[3].reject(new Error('unavailable'))
  await failing
  assert.equal(state.run('detailError.value'), 'unavailable')
  const retry = state.run('openDetail(detail.value)')
  requests[4].resolve({ name: 'recovered' })
  await retry
  assert.equal(state.run('detailError.value'), '')
  assert.equal(state.run('detail.value.name'), 'recovered')
  state.stop()
})

test('same-page editors receive identities and refresh the actual catalog after saving', async () => {
  let reads = 0
  const state = await marketplace({
    listSkills: async () => {
      reads++
      return []
    }
  })
  state.run('openEditor("skill")')
  assert.equal(state.run('editorProps.value.initialCreateMethod'), 'upload')
  state.run('openEditor("skill", {uuid:"private-skill"})')
  assert.equal(state.run('editorProps.value.initialSkillUuid'), 'private-skill')
  state.run('openEditor("mcp", {uuid:"private-mcp"})')
  assert.equal(state.run('editorProps.value.initialMcpUuid'), 'private-mcp')
  state.run('openEditor("connector", {uuid:"github"})')
  assert.equal(state.run('editorProps.value.initialPluginKey'), 'github')
  const before = reads
  await state.run('editorSaved()')
  assert.equal(state.run('editor.value'), '')
  assert.equal(reads, before + 2)
  state.stop()
})

async function renderCard(row, disabled = false) {
  const { descriptor } = parse(
    await sourceFor('pages/lens/components/CatalogCard.vue')
  )
  const script = compileScript(descriptor, {
    id: 'catalog-card',
    inlineTemplate: true
  })
    .content.replace(
      /^import[^]*? from ['"]([^'"]+)['"];?$/gm,
      (statement, module) => {
        if (module === 'vue') return statement
        if (module === 'vue-i18n')
          return 'const useI18n = () => ({t: (key) => key})'
        if (module.includes('CatalogTile')) return 'const CatalogTile = "span"'
        return 'const Check = "i", LoaderCircle = "i", Pencil = "i", Plus = "i"'
      }
    )
    .replace(
      /import \{([^}]+)\} from ['"]vue['"]/g,
      (_, names) =>
        'const {' + names.replace(/\s+as\s+/g, ':') + '} = globalThis.vue'
    )
    .replace('export default', 'globalThis.component =')
  const context = vm.createContext({ vue })
  vm.runInContext(script, context)
  return renderToString(vue.createSSRApp(context.component, { row, disabled }))
}

test('actual cards expose official enablement, tenant editing and connector setup with readonly actions disabled', async () => {
  const official = await renderCard({
    ...skill('official', true),
    kind: 'skill',
    version: 'v2'
  })
  assert.match(official, /catalog.official/)
  assert.match(official, /catalog.disable/)
  assert.match(official, /v2/)
  const privateCard = await renderCard(
    { ...skill('private', false), kind: 'mcp' },
    true
  )
  assert.match(privateCard, /catalog.tenantOwned/)
  assert.match(
    privateCard,
    /<button[^>]*disabled[^>]*aria-label="common.edit: private"/
  )
  assert.doesNotMatch(privateCard, /catalog.disable/)
  const connector = await renderCard(
    { uuid: 'github', name: 'GitHub', kind: 'connector' },
    true
  )
  assert.match(
    connector,
    /<button[^>]*disabled[^>]*aria-label="catalog.configureConnection: GitHub"/
  )
  assert.doesNotMatch(connector, /catalog.boundCount|catalog.disable/)
})

for (const file of ['Skills.vue', 'Mcp.vue']) {
  test(`${file} embedded saves retain failed drafts, guard close and duplicate submits, then signal accepted save`, async () => {
    const source = await sourceFor(`pages/lens/${file}`)
    const body = ['closeModal', 'save']
      .map(
        (name) =>
          source.match(new RegExp(`(?:async )?function ${name}\\([^]*?\\n}`))[0]
      )
      .join('\n')
    const pending = deferred()
    let attempt = 0
    const events = []
    const state = {
      ...vue,
      props: { embedded: true },
      emit: (event) => events.push(event),
      saving: vue.ref(false),
      formError: vue.ref(''),
      showModal: vue.ref(true),
      form: vue.ref({ name: 'kept draft' }),
      mode: vue.ref('create'),
      createMethod: vue.ref('manual'),
      activeMethod: vue.ref('manual'),
      githubUrl: vue.ref(''),
      editSourceType: vue.ref('manual'),
      packageFile: vue.ref(null),
      environmentChanged: vue.ref(false),
      t: (key) => key,
      showSuccess() {},
      showError() {},
      extractErrorMessage: (error) => error.message,
      skillErrorMessage: (error) => error.message,
      buildPayload: () => ({ name: 'kept draft' }),
      saveByMode: async () => {
        if (++attempt === 1) throw new Error('rejected')
        await pending.promise
      },
      load: () => assert.fail('Embedded saves must let the market refresh')
    }
    const context = vm.createContext(state)
    vm.runInContext('let skillRequestId = 0;\n' + body, context)
    const run = (code) => vm.runInContext(code, context)
    await run('save()')
    assert.equal(state.form.value.name, 'kept draft')
    assert.equal(state.showModal.value, true)
    assert.equal(state.formError.value, 'rejected')
    assert.equal(events.length, 0)
    const saving = run('save()')
    run('closeModal()')
    await run('save()')
    assert.equal(attempt, 2)
    assert.equal(state.showModal.value, true)
    assert.equal(events.length, 0)
    pending.resolve()
    await saving
    assert.deepEqual(events, ['saved'])
    assert.equal(state.showModal.value, false)
    assert.equal(state.saving.value, false)
    assert.deepEqual(Object.keys(state.form.value), [])
  })
}

test('a failed first catalog load does not freeze discovery as the default after recovery', async () => {
  let failing = true
  const state = await marketplace({
    listSkills: async ({ catalog }) => {
      if (catalog !== 'official') return []
      if (failing) throw new Error('unavailable')
      return [skill('enabled-on-retry', true)]
    }
  })
  assert.equal(state.run('view.value'), 'discover')
  assert.equal(state.run('tabs.value[1].count'), '—')
  failing = false
  await state.run('load()')
  assert.equal(state.run('view.value'), 'enabled')
  state.stop()
})

test('overlapping catalog refreshes keep the newest accepted response', async () => {
  const requests = []
  const active = await marketplace({
    listSkills: async ({ catalog }) => {
      if (catalog !== 'official') return []
      const request = deferred()
      requests.push(request)
      return request.promise
    }
  })
  const newer = active.run('load()')
  requests[1].resolve([skill('newest', true)])
  await newer
  requests[0].resolve([skill('stale', true)])
  await setImmediate()
  assert.equal(active.run('rows.value[0].uuid'), 'newest')
  assert.equal(active.run('loading.value'), false)
  active.stop()
})
