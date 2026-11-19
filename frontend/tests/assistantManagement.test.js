import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import vm from 'node:vm'
import * as vue from 'vue'
import { compileScript, parse } from '@vue/compiler-sfc'
import { renderToString } from '@vue/server-renderer'
import { normalizeList } from '../src/pages/lens/adminHelpers.js'
import { createLatestRequestRunner } from '../src/pages/lens/assistantAccessSelectors.js'
import { buildAssistantDetail } from '../src/pages/lens/assistantDetails.js'

async function setup(file, overrides = {}) {
  const source = await readFile(
    new URL(`../src/pages/lens/${file}`, import.meta.url),
    'utf8'
  )
  const events = []
  const context = vm.createContext({
    ...vue,
    ...Object.fromEntries(
      ['Bot', 'BookOpen', 'Code2', 'MessageCircle', 'UsersRound'].map(
        (name) => [name, { render: () => vue.h('span') }]
      )
    ),
    onMounted() {},
    onBeforeUnmount() {},
    defineProps: () => ({ assistants: [{ uuid: 'a', updated_at: 'version' }] }),
    defineEmits:
      () =>
      (...args) =>
        events.push(args),
    useI18n: () => ({ t: (key) => key, locale: vue.ref('en') }),
    useToast: () => ({ showSuccess() {}, showError() {} }),
    useUserStore: () => ({ delegation: null }),
    createLatestRequestRunner,
    normalizeList,
    setTimeout,
    clearTimeout,
    extractErrorMessage: (error) => error.message,
    ...overrides
  })
  const script = source
    .match(/<script setup>([^]*?)<\/script>/)[1]
    .replace(/^import [^]*? from ['"][^'"]+['"]\n/gm, '')
  vm.runInContext(script, context)
  const run = (code) => vm.runInContext(code, context)
  const render = async () => {
    const { descriptor } = parse(source)
    const { bindings } = compileScript(descriptor, { id: 'management' })
    const names = Object.keys(bindings)
    const state = run(
      `({${names.map((name) => `${name}: typeof ${name} === 'undefined' ? undefined : ${name}`).join(',')}})`
    )
    const slot = {
      render() {
        return vue.h('div', [this.$slots.default?.(), this.$slots.footer?.()])
      }
    }
    for (const name of names.filter((name) => /^[A-Z]/.test(name)))
      state[name] ||= slot
    const app = vue.createSSRApp({
      setup: () => ({
        ...run('props'),
        ...Object.fromEntries(
          Object.entries(state).filter(([, value]) => value !== undefined)
        )
      }),
      template: descriptor.template.content
    })
    for (const name of names.filter((name) => /^[A-Z]/.test(name)))
      app.component(name, state[name])
    return renderToString(app)
  }
  return { run, events, render }
}

test('management tabs count all rows and model/node filters compose with attention', async () => {
  const { run } = await setup('Assistants.vue')
  run(`assistants.value = [
    {uuid:'a', status:'active', lensnode:'node', agent_model_ref:'m', management_checks:{issues:[{code:'model_unavailable', field:'agent_model_ref'}]}},
    {uuid:'b', status:'active', lensnode:'other', agent_model_ref:'m', management_checks:{issues:[]}},
    {uuid:'c', status:'archived', management_checks:{issues:[]}}
  ]`)
  assert.equal(run('activeCount.value'), 2)
  assert.equal(run('archivedCount.value'), 1)
  assert.equal(run('attentionCount.value'), 1)
  run(
    "attentionOnly.value = true; nodeFilter.value = 'node'; modelFilter.value = 'm'"
  )
  assert.equal(run('filteredAssistants.value.length'), 1)
  run("nodeFilter.value = 'other'")
  assert.equal(run('filteredAssistants.value.length'), 0)
})

test('out-of-order list responses cannot overwrite the newest rows or loading state', async () => {
  const pending = []
  const { run } = await setup('Assistants.vue', {
    listAssistants: () =>
      new Promise((resolve, reject) => pending.push({ resolve, reject }))
  })
  const first = run('load()')
  const second = run('load()')
  pending[1].resolve([{ uuid: 'new', status: 'active' }])
  await second
  pending[0].resolve([{ uuid: 'old', status: 'active' }])
  await first
  assert.equal(run('assistants.value[0].uuid'), 'new')
  assert.equal(run('loading.value'), false)
  assert.equal(run('collaborationAssistants.value[0].uuid'), 'new')
})

test('visibility defaults to tenant and blocks empty restricted grants', async () => {
  let calls = 0
  const { run } = await setup('AssistantVisibilityModal.vue', {
    bulkUpdateAssistantVisibility: async () => {
      calls++
      return { updated: 1, skipped: 0 }
    }
  })
  assert.equal(run('visibility.value'), 'tenant')
  run("visibility.value = 'private'")
  await run('save()')
  assert.equal(calls, 0)
})

test('visibility submission pins versions, preserves draft on conflict, and gates closing/repeats', async () => {
  const calls = []
  let reject
  const { run, events } = await setup('AssistantVisibilityModal.vue', {
    bulkUpdateAssistantVisibility: (payload) => {
      calls.push(payload)
      return new Promise((resolve, fail) => {
        reject = fail
      })
    }
  })
  const save = run('save()')
  await run('save()')
  run('close()')
  assert.equal(calls.length, 1)
  assert.equal(calls[0].assistants[0].updated_at, 'version')
  assert.equal(calls[0].visibility, 'tenant')
  assert.equal(events.length, 0)
  reject(new Error('Conflict'))
  await save
  assert.equal(run('error.value'), 'Conflict')
  assert.equal(run('visibility.value'), 'tenant')
  assert.equal(events.length, 0)
})

test('readonly delegation cannot submit a visibility change', async () => {
  let calls = 0
  const { run } = await setup('AssistantVisibilityModal.vue', {
    useUserStore: () => ({ delegation: { mode: 'readonly' } }),
    bulkUpdateAssistantVisibility: async () => {
      calls++
    }
  })
  await run('save()')
  assert.equal(calls, 0)
})

test('delegated visibility changes preserve assignments without loading subjects', async () => {
  const calls = []
  let subjectsRequested = 0
  const { run, render } = await setup('AssistantVisibilityModal.vue', {
    useUserStore: () => ({ delegation: { mode: 'operational' } }),
    listAssistantAccessSubjects: async () => {
      subjectsRequested++
      return { users: [], groups: [] }
    },
    bulkUpdateAssistantVisibility: async (payload) => {
      calls.push(payload)
      return { updated: 1, skipped: 0 }
    }
  })
  run("visibility.value = 'private'")
  await vue.nextTick()
  await run('loadSubjects()')
  assert.equal(subjectsRequested, 0)
  const html = await render()
  assert.doesNotMatch(
    html,
    /lensAdmin.bulkVisibility.searchSubjects|lensAdmin.bulkVisibility.replaceGrants/
  )
  assert.match(html, /lensAdmin.access.delegatedHint/)
  await run('save()')
  assert.equal(calls.length, 1)
  assert.equal(calls[0].visibility, 'private')
  assert.equal(Object.hasOwn(calls[0], 'access_grants'), false)
})

test('delegated assistant detail never renders cached user and group identities', async () => {
  const { run, render } = await setup('AssistantDetailView.vue', {
    defineProps: () => ({
      assistant: {
        name: 'Assistant',
        slug: 'assistant',
        status: 'active',
        visibility: 'private',
        access_grants: [
          {
            type: 'user',
            id: 7,
            username: 'Hidden member',
            email: 'hidden@example.invalid'
          },
          { type: 'group', id: 3, name: 'Hidden group' }
        ]
      }
    }),
    useUserStore: () => ({ delegation: { mode: 'operational' } }),
    useShortDateTime: () => () => '',
    buildAssistantDetail,
    EMPTY_VALUE: '—'
  })
  run("activeTab.value = 'access'")
  const html = await render()
  assert.doesNotMatch(
    html,
    /Hidden member|hidden@example.invalid|Hidden group|noAuthorizedUsers|noAuthorizedGroups/
  )
})

test('entering delegation invalidates pending subject searches and cached choices', async () => {
  const userStore = vue.reactive({ delegation: null })
  let resolve
  const { run } = await setup('AssistantVisibilityModal.vue', {
    useUserStore: () => userStore,
    listAssistantAccessSubjects: () =>
      new Promise((done) => {
        resolve = done
      })
  })
  const request = run('loadSubjects()')
  run(
    "subjectCache.value = {'user:7': {id: 7, name: 'Old identity'}}; selectedKeys.value = ['user:7']"
  )
  userStore.delegation = { mode: 'operational' }
  await vue.nextTick()
  resolve({ users: [{ id: 8, name: 'Stale identity' }], groups: [] })
  await request
  assert.equal(run('subjects.value.length'), 0)
  assert.equal(run('selectedKeys.value.length'), 0)
  assert.equal(run('Object.keys(subjectCache.value).length'), 0)
})

test('subject searches ignore stale responses and preserve selected members across pages', async () => {
  const pending = []
  const { run } = await setup('AssistantVisibilityModal.vue', {
    listAssistantAccessSubjects: () =>
      new Promise((resolve, reject) => pending.push({ resolve, reject }))
  })
  const first = run('loadSubjects(1)')
  const second = run('loadSubjects(1)')
  pending[1].resolve({
    users: { count: 31, results: [{ id: 2, name: 'Current' }] },
    groups: { count: 0, results: [] }
  })
  await second
  run("selectedKeys.value = ['user:2']")
  pending[0].resolve({
    users: { count: 1, results: [{ id: 1, name: 'Stale' }] },
    groups: { count: 0, results: [] }
  })
  await first
  assert.equal(run('subjects.value[0].id'), 2)
  assert.equal(run('hasMoreSubjects.value'), true)
  const third = run('loadSubjects(1)')
  pending[2].resolve({
    users: { count: 1, results: [{ id: 3, name: 'New search' }] },
    groups: { count: 0, results: [] }
  })
  await third
  assert.equal(run('subjectOptions.value.length'), 2)
  assert.equal(run('selectedSubjects.value[0].id'), 2)
})

test('restricted submission sends only selected identity fields and emits the real server result', async () => {
  const calls = []
  const { run, events } = await setup('AssistantVisibilityModal.vue', {
    listAssistantAccessSubjects: async () => ({
      users: { count: 1, results: [{ id: 2, name: 'Member' }] },
      groups: { count: 0, results: [] }
    }),
    bulkUpdateAssistantVisibility: async (payload) => {
      calls.push(payload)
      return { updated: 1, skipped: 0 }
    }
  })
  await run('loadSubjects()')
  run("selectedKeys.value = ['user:2']; visibility.value = 'private'")
  await run('save()')
  assert.deepEqual(JSON.parse(JSON.stringify(calls[0].access_grants)), [
    { type: 'user', id: 2 }
  ])
  assert.equal(events[0][0], 'updated')
  assert.equal(events[0][1].updated, 1)
})

test('failed lists retain known rows and expose a retryable error', async () => {
  const { run } = await setup('Assistants.vue', {
    listAssistants: async () => {
      throw new Error('Offline')
    }
  })
  run("assistants.value = [{uuid:'known', status:'active'}]")
  await run('load()')
  assert.equal(run('assistants.value[0].uuid'), 'known')
  assert.equal(run('listError.value'), 'Offline')
  assert.equal(run('loading.value'), false)
})

test('visibility modal renders the public warning, restricted identities, and busy input gate', async () => {
  const { run, render } = await setup('AssistantVisibilityModal.vue', {
    listAssistantAccessSubjects: async () => ({
      users: { count: 1, results: [{ id: 2, name: '<Member>' }] },
      groups: { count: 0, results: [] }
    })
  })
  run("visibility.value = 'public'")
  assert.match(await render(), /lensAdmin.visibility.publicWarning/)
  run("visibility.value = 'private'")
  await run('loadSubjects()')
  const html = await render()
  assert.match(html, /&lt;Member&gt;/)
  assert.match(html, /lensAdmin.bulkVisibility.replaceGrants/)
  run('busy.value = true')
  assert.match(await render(), /<fieldset[^>]*disabled/)
})

test('risk replacement selects only assistants affected in the chosen model slot', async () => {
  const { run } = await setup('Assistants.vue')
  run(`assistants.value = [
    {uuid:'main', status:'active', management_checks:{issues:[{code:'model_unavailable', field:'agent_model_ref'}]}},
    {uuid:'vision', status:'active', management_checks:{issues:[{code:'model_unavailable', field:'multimodal_model_ref'}]}},
    {uuid:'node', status:'active', management_checks:{issues:[{code:'node_unavailable', field:'lensnode'}]}}
  ]`)
  run("replaceAffectedModels('multimodal_model_ref')")
  assert.deepEqual(Array.from(run('selectedAssistantUuids.value')), ['vision'])
  assert.equal(run('replacementInitialField.value'), 'multimodal_model_ref')
})

test('assistant list renders live reasons and keeps failed loading distinct from an empty list', async () => {
  const { run, render } = await setup('Assistants.vue', {
    formatAssistantType: (value) => value
  })
  run(
    `assistants.value = [{uuid:'a', name:'Needs repair', slug:'repair', capability:'general_chat', status:'active', visibility:'tenant', management_checks:{issues:[{code:'node_unavailable',field:'lensnode'}]}}]`
  )
  run("listView.value = 'table'")
  const html = await render()
  assert.match(html, /Needs repair/)
  assert.match(html, /lensAdmin.managementChecks.node_unavailable/)
  run("assistants.value = []; listError.value = 'Offline'")
  const failed = await render()
  assert.match(failed, /Offline/)
  assert.doesNotMatch(
    failed,
    /lensAdmin.pages.assistants.emptyActive|lensAdmin.pages.assistants.noFilterResults/
  )
})

test('assistant cards are the default, status layers stay distinct and table mode retains filtering and selection', async () => {
  const { run } = await setup('Assistants.vue')
  run(`assistants.value = [
    {uuid:'a', name:'A', status:'active', is_enabled:true, management_checks:{issues:[],models:{m:'Readable model'},questions_30d:7},agent_model_ref:'m'},
    {uuid:'b', name:'B', status:'active', is_enabled:false, management_checks:{issues:[{code:'model_unavailable'}]}},
    {uuid:'c', name:'C', status:'active', management_checks:{issues:[{code:'model_unavailable'}]}},
    {uuid:'d', name:'D', status:'active', management_checks:{issues:[{code:'model_missing'}]}}
  ]; searchQuery.value = 'A'`)
  assert.equal(run('listView.value'), 'cards')
  assert.equal(run('assistantState(assistants.value[0]).tone'), 'success')
  assert.equal(run('assistantState(assistants.value[1]).tone'), 'muted')
  assert.equal(run('assistantState(assistants.value[2]).tone'), 'danger')
  assert.equal(run('assistantState(assistants.value[3]).tone'), 'warning')
  assert.equal(run('assistantModelName(assistants.value[0])'), 'Readable model')
  run('showBatchTable(); togglePageSelection(true)')
  assert.equal(run('listView.value'), 'table')
  assert.equal(run('searchQuery.value'), 'A')
  assert.deepEqual(Array.from(run('selectedAssistantUuids.value')), ['a'])
  run("handleAssistantAction('delete', assistants.value[0])")
  assert.equal(run('deletionBatch.value[0].uuid'), 'a')
})

test('assistant summary card renders escaped live data, a visible issue and a real question count', async () => {
  const { render } = await setup('AssistantSummaryCard.vue', {
    defineProps: () => ({
      assistant: {
        uuid: 'a',
        name: '<Internal assistant>',
        slug: 'internal',
        visibility: 'tenant',
        datasource_bindings: [{ uuid: 'source' }],
        skill_summary: { enabled: 2 },
        mcp_summary: { enabled: 3 },
        management_checks: {
          issues: [{ code: 'node_unavailable' }],
          questions_30d: 17
        }
      },
      state: { label: 'Needs attention', tone: 'warning' },
      modelName: 'Model',
      nodeName: 'Node',
      issueLabel: 'Node unavailable'
    })
  })
  const html = await render()
  assert.match(html, /&lt;Internal assistant&gt;/)
  assert.match(html, /Node unavailable/)
  assert.match(html, /<b>17<\/b>/)
  assert.match(html, /has-warning/)
  assert.match(html, /<button[^>]*type="button"/)
})

test('enablement views distinguish paused assistants from archived rows and keep repairs available', async () => {
  const { run } = await setup('Assistants.vue')
  run(`assistants.value = [
    {uuid:'on',status:'active',is_enabled:true},
    {uuid:'off',status:'active',is_enabled:false,management_checks:{issues:[{field:'agent_model_ref'}]}},
    {uuid:'old',status:'archived',is_enabled:false}
  ]`)
  assert.equal(run('enabledCount.value'), 1)
  assert.equal(run('disabledCount.value'), 1)
  run("showEnablementView('disabled')")
  assert.deepEqual(Array.from(run('viewRows.value.map(row => row.uuid)')), [
    'off'
  ])
  run('showAttention()')
  assert.equal(run('viewRows.value[0].uuid'), 'off')
  run("selectedAssistantUuids.value = ['off']; openEnablement(true)")
  run("assistants.value[1].updated_at = 'new-version'")
  assert.equal(run('enablementBatch.value[0].updated_at'), undefined)
  assert.equal(run('enablementTarget.value'), true)
})

test('enablement pins versions, prevents repeats and closing, and retains a conflict for retry', async () => {
  const calls = []
  let reject
  const { run, events } = await setup('AssistantEnablementModal.vue', {
    defineProps: () => ({
      assistants: [{ uuid: 'a', updated_at: 'version' }],
      enabled: false
    }),
    bulkUpdateAssistantEnablement: (payload) => {
      calls.push(payload)
      return new Promise((resolve, fail) => {
        reject = fail
      })
    }
  })
  const saving = run('save()')
  await run('save()')
  run('close()')
  assert.equal(calls.length, 1)
  assert.equal(calls[0].enabled, false)
  assert.equal(calls[0].assistants[0].updated_at, 'version')
  assert.equal(events.length, 0)
  reject(new Error('Conflict'))
  await saving
  assert.equal(run('error.value'), 'Conflict')
  assert.equal(run('result.value'), null)
})

test('enablement displays skipped identities and reasons and applies once', async () => {
  let calls = 0
  const { run, render, events } = await setup('AssistantEnablementModal.vue', {
    defineProps: () => ({
      assistants: [{ uuid: 'a', name: '<Repair>', updated_at: 'version' }],
      enabled: true
    }),
    bulkUpdateAssistantEnablement: async () => {
      calls++
      return {
        updated: 0,
        skipped: 1,
        skipped_items: [{ uuid: 'a', reason: 'configuration' }]
      }
    }
  })
  await run('save()')
  await run('save()')
  assert.equal(calls, 1)
  assert.equal(events[0][0], 'updated')
  const html = await render()
  assert.match(html, /&lt;Repair&gt;/)
  assert.match(html, /lensAdmin.enablement.configuration/)
})

test('readonly delegation cannot submit enablement', async () => {
  let calls = 0
  const { run } = await setup('AssistantEnablementModal.vue', {
    useUserStore: () => ({ delegation: { mode: 'readonly' } }),
    bulkUpdateAssistantEnablement: async () => {
      calls++
    }
  })
  await run('save()')
  assert.equal(calls, 0)
})

test('deletion preview pins the selected versions and requires each exact slug', async () => {
  const calls = []
  const { run } = await setup('AssistantDeletionModal.vue', {
    bulkDeleteAssistants: async (payload) => {
      calls.push(payload)
      return {
        assistants: [
          {
            uuid: 'a',
            updated_at: 'version',
            slug: 'exact-a',
            impact: {},
            skip_reason: null
          },
          {
            uuid: 'b',
            updated_at: 'other-version',
            slug: 'exact-b',
            impact: {},
            skip_reason: null
          }
        ],
        confirmation_token: 'review'
      }
    }
  })
  await run('loadPreview()')
  assert.equal(calls[0].preview, true)
  assert.equal(calls[0].assistants[0].updated_at, 'version')
  assert.equal(run('canDelete.value'), false)
  run("confirmations.value.a = 'exact-a'; confirmations.value.b = 'exact-b '")
  assert.equal(run('canDelete.value'), false)
  run("confirmations.value.b = 'exact-b'")
  assert.equal(run('canDelete.value'), true)
})

test('deletion sends the reviewed signature, guards repeated actions and shows skipped identities', async () => {
  const calls = []
  let finish
  const { run, events, render } = await setup('AssistantDeletionModal.vue', {
    defineProps: () => ({
      assistants: [
        { uuid: 'a', name: 'Delete A', updated_at: 'v' },
        { uuid: 'b', name: 'Busy B', updated_at: 'v2' }
      ]
    }),
    bulkDeleteAssistants: (payload) => {
      calls.push(payload)
      return new Promise((resolve) => {
        finish = resolve
      })
    }
  })
  run(`review.value = {assistants: [
    {uuid:'a', name:'Delete A', slug:'exact-a', updated_at:'v', impact:{}, skip_reason:null},
    {uuid:'b', name:'Busy B', slug:'busy-b', updated_at:'v2', impact:{}, skip_reason:'active_work'}
  ], confirmation_token:'signed'}; confirmations.value.a = 'exact-a'`)
  const saving = run('save()')
  await run('save()')
  run('close()')
  assert.equal(calls.length, 1)
  assert.equal(calls[0].confirmation_token, 'signed')
  assert.equal(calls[0].preview, false)
  assert.equal(calls[0].assistants[0].confirmation_slug, 'exact-a')
  assert.equal(calls[0].assistants[1].confirmation_slug, 'busy-b')
  assert.equal(events.length, 0)
  finish({
    deleted: 1,
    skipped: 1,
    skipped_items: [{ uuid: 'b', reason: 'active_work' }]
  })
  await saving
  assert.equal(events[0][0], 'updated')
  const html = await render()
  assert.match(html, /Busy B/)
  assert.match(html, /lensAdmin.deletion.active_work/)
  await run('save()')
  assert.equal(calls.length, 1)
})

test('deletion conflict requires a fresh explicit review and new slug input', async () => {
  const calls = []
  const { run } = await setup('AssistantDeletionModal.vue', {
    listAssistants: async () => [{ uuid: 'a', updated_at: 'new-version' }],
    bulkDeleteAssistants: async (payload) => {
      calls.push(payload)
      if (!payload.preview)
        throw Object.assign(new Error('Changed'), { response: { status: 409 } })
      return {
        assistants: [
          {
            uuid: 'a',
            slug: 'new-slug',
            updated_at: 'new-version',
            skip_reason: null
          }
        ],
        confirmation_token: 'new-signature'
      }
    }
  })
  run(
    "review.value = {assistants:[{uuid:'a',slug:'old',updated_at:'version'}], confirmation_token:'old-signature'}; confirmations.value.a = 'old'"
  )
  await run('save()')
  assert.equal(run('review.value'), null)
  assert.equal(run('error.value'), 'Changed')
  await run('save()')
  assert.equal(calls.length, 1)
  await run('loadPreview(true)')
  assert.equal(calls[1].assistants[0].updated_at, 'new-version')
  assert.equal(run('review.value.confirmation_token'), 'new-signature')
  assert.equal(run('canDelete.value'), false)
})

test('deletion network failure retains review and confirmation inputs', async () => {
  const { run, events } = await setup('AssistantDeletionModal.vue', {
    bulkDeleteAssistants: async () => {
      throw new Error('Offline')
    }
  })
  run(
    "review.value = {assistants:[{uuid:'a',slug:'exact'}],confirmation_token:'signed'}; confirmations.value.a = 'exact'"
  )
  await run('save()')
  assert.equal(run('confirmations.value.a'), 'exact')
  assert.equal(run('review.value.confirmation_token'), 'signed')
  assert.equal(run('error.value'), 'Offline')
  assert.equal(events.length, 0)
})

test('readonly deletion and batches with no eligible assistants cannot submit', async () => {
  let calls = 0
  const { run } = await setup('AssistantDeletionModal.vue', {
    useUserStore: () => ({ delegation: { mode: 'readonly' } }),
    bulkDeleteAssistants: async () => {
      calls++
    }
  })
  run(
    "review.value = {assistants:[{uuid:'a',slug:'exact'}]}; confirmations.value.a = 'exact'"
  )
  await run('save()')
  assert.equal(calls, 0)
  run(
    "userStore.delegation = null; review.value.assistants[0].skip_reason = 'evaluation_data'"
  )
  await run('save()')
  assert.equal(calls, 0)
})

test('deletion selection supports archived rows and takes a stable snapshot', async () => {
  const { run } = await setup('Assistants.vue')
  run(
    "assistants.value = [{uuid:'archived',name:'Old',status:'archived',updated_at:'v'}]; selectedAssistantUuids.value = ['archived']; openDeletion()"
  )
  assert.equal(run('deletionBatch.value[0].uuid'), 'archived')
  run("assistants.value[0].updated_at = 'later'")
  assert.equal(run('deletionBatch.value[0].updated_at'), 'v')
})

test('assistant details ignore superseded, closed and identity-switched requests', async () => {
  const pending = []
  const store = vue.reactive({ userInfo: { id: 1 }, delegation: null })
  const { run } = await setup('Assistants.vue', {
    useUserStore: () => store,
    getAssistant: () => new Promise((resolve) => pending.push(resolve))
  })
  const first = run("openDetails({uuid:'old'})")
  const second = run("openDetails({uuid:'new'})")
  pending[1]({ uuid: 'new' })
  await second
  pending[0]({ uuid: 'old' })
  await first
  assert.equal(run('detailAssistant.value.uuid'), 'new')
  const closed = run("openDetails({uuid:'closed'})")
  run('closeDetails()')
  pending[2]({ uuid: 'closed' })
  await closed
  assert.equal(run('detailAssistant.value'), null)
  const switched = run("openDetails({uuid:'foreign'})")
  store.userInfo = { id: 2 }
  await vue.nextTick()
  pending[3]({ uuid: 'foreign' })
  await switched
  assert.equal(run('detailAssistant.value'), null)
})

test('detail runtime shows real models and budgets separately from the overview and resets for a new target', async () => {
  const props = vue.reactive({
    embedded: true,
    modelNames: { model: 'Real model' },
    assistant: {
      uuid: 'one',
      name: '<script>alert(1)</script>',
      slug: 'safe',
      status: 'active',
      settings: {
        execution_budget: { max_tokens: 123456, max_agent_turns: 0 }
      },
      agent_model_ref: 'model',
      datasource_bindings: []
    }
  })
  const { run, render } = await setup('AssistantDetailView.vue', {
    defineProps: () => props,
    useShortDateTime: () => () => '',
    buildAssistantDetail,
    EMPTY_VALUE: '—'
  })
  let html = await render()
  assert.match(html, /&lt;script&gt;alert\(1\)&lt;\/script&gt;/)
  assert.doesNotMatch(html, /<script>|Real model|123456/)
  run("activeTab.value='runtime'")
  html = await render()
  assert.match(html, /Real model/)
  assert.match(html, /123456/)
  assert.match(html, /<dd[^>]*>0<\/dd>/)
  assert.match(html, /—/)
  props.assistant = { uuid: 'two', name: 'Other', settings: {} }
  await vue.nextTick()
  assert.equal(run('activeTab.value'), 'overview')
  run("activeTab.value='runtime'")
  html = await render()
  assert.doesNotMatch(html, /Real model|123456/)
})
