import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import vm from 'node:vm'
import { compileScript, parse } from '@vue/compiler-sfc'
import { renderToString } from '@vue/server-renderer'
import * as vue from 'vue'

import * as access from '../src/pages/lens/assistantAccessSelectors.js'
import * as environment from '../src/pages/lens/assistantEnvironment.js'
import * as skills from '../src/pages/lens/assistantSkills.js'
import { toggleBinding } from '../src/pages/lens/assistantDatasources.js'
import { formatLLMConfigLabel } from '../src/pages/lens/adminHelpers.js'
import { splitList } from '../src/pages/lens/adminHelpers.js'
import { buildWorkspaceGuidePayload } from '../src/pages/lens/assistantWorkspaceGuide.js'

async function drawer(
  overrides = {},
  {
    delegation = null,
    loadAccess = false,
    listAssistantAccessSubjects = async () => ({})
  } = {}
) {
  const source = await readFile(
    new URL(
      '../src/pages/lens/AssistantFormDrawerDirectEnvironment.vue',
      import.meta.url
    ),
    'utf8'
  )
  const script = source
    .match(/<script setup>([^]*?)<\/script>/)[1]
    .replace(/^import [^]*? from ['"][^'"]+['"]\n/gm, '')
  const props = vue.reactive({
    show: false,
    mode: 'create',
    saving: false,
    formResourcesLoading: false,
    continueLabel: '',
    formError: '',
    form: {
      name: '',
      slug: '',
      capability: '',
      mode: 'direct',
      agent_model_ref: '',
      datasource_bindings: [],
      skill_uuids: [],
      mcp_uuids: [],
      plugin_bindings: [],
      collaboration_member_uuids: [],
      decision_gates: {},
      decision_analyses: {},
      skill_environment_drafts: {},
      skill_environment_set_uuids: {},
      mcp_environment_drafts: {},
      mcp_environment_set_uuids: {}
    },
    assistants: [{ uuid: 'member', status: 'active', mode: 'direct' }],
    skills: [{ uuid: 'skill', enabled: true, definition: {} }],
    environmentVariableSets: [],
    mcps: [],
    pluginConnections: [],
    pluginManifests: {},
    llmConfigOptions: [{ uuid: 'model', is_active: true, model_type: 'llm' }],
    datasourceOptions: [{ uuid: 'source', status: 'active' }],
    decisionCatalog: { decisions: [] },
    ...overrides
  })
  const events = []
  const userStore = vue.reactive({ delegation, userHasFeature: () => true })
  const pageSource = await readFile(
    new URL('../src/pages/lens/Assistants.vue', import.meta.url),
    'utf8'
  )
  const payloadContext = vm.createContext({
    form: { value: props.form },
    mode: { value: props.mode },
    skills: { value: props.skills },
    mcps: { value: props.mcps },
    splitList,
    buildWorkspaceGuidePayload,
    userStore,
    ...environment
  })
  for (const name of [
    'buildPayload',
    'buildAccessGrants',
    'buildAssistantSettings',
    'executionBudgetValue'
  ]) {
    vm.runInContext(
      pageSource.match(new RegExp(`function ${name}\\([^]*?\\n}`))[0],
      payloadContext
    )
  }
  const context = vm.createContext({
    ...vue,
    Database: { render: () => vue.h('svg') },
    Cpu: { render: () => vue.h('svg') },
    MessageSquare: { render: () => vue.h('svg') },
    UsersIcon: { render: () => vue.h('svg') },
    ...access,
    ...environment,
    ...skills,
    toggleBinding,
    formatLLMConfigLabel,
    onBeforeUnmount() {},
    defineProps: () => props,
    defineEmits:
      () =>
      (...args) =>
        events.push(args),
    useI18n: () => ({ t: (key) => key, te: () => true }),
    useUserStore: () => userStore,
    EMPTY_VALUE: '',
    document: { addEventListener() {}, removeEventListener() {} },
    generateUUID: () => 'generated-id',
    listAssistantAccessSubjects,
    setTimeout,
    clearTimeout,
    AbortController
  })
  vm.runInContext(script, context)
  const run = (code) => vm.runInContext(code, context)
  // Keep this test independent of access-selector HTTP requests.
  if (!loadAccess) run('initializeAccessSelectors = () => {}')
  props.show = true
  await vue.nextTick()
  const render = async () => {
    const { descriptor } = parse(source)
    const { bindings } = compileScript(descriptor, { id: 'assistant' })
    const names = Object.keys(bindings)
    const state = run(
      `({${names.map((name) => `${name}: typeof ${name} === 'undefined' ? undefined : ${name}`).join(',')}})`
    )
    for (const name of names.filter(
      (name) => /^[A-Z]/.test(name) && !state[name]
    )) {
      state[name] = { render: () => vue.h('span') }
    }
    const app = vue.createSSRApp({
      setup: () => ({
        ...props,
        ...Object.fromEntries(
          Object.entries(state).filter(([, value]) => value !== undefined)
        )
      }),
      template: descriptor.template.content
    })
    const slotComponent = (tag) => ({
      render() {
        return vue.h(tag, [this.$slots.default?.(), this.$slots.footer?.()])
      }
    })
    app.component('BaseDrawer', slotComponent('main'))
    app.component('BaseButton', slotComponent('button'))
    app.component('BaseSelect', slotComponent('select'))
    app.component('RouterLink', slotComponent('a'))
    for (const name of names.filter((name) => /^[A-Z]/.test(name))) {
      if (!['BaseDrawer', 'BaseButton', 'BaseSelect'].includes(name)) {
        app.component(name, state[name] || slotComponent('span'))
      }
    }
    return renderToString(app)
  }
  const payload = () =>
    JSON.parse(
      JSON.stringify(vm.runInContext('buildPayload()', payloadContext))
    )
  return { props, events, run, render, payload, userStore }
}

test('delegated editing omits member assignments from the saved configuration', async () => {
  const { props, run, render, payload } = await drawer(
    { mode: 'edit' },
    { delegation: { mode: 'operational' } }
  )
  Object.assign(props.form, {
    capability: 'general_chat',
    visibility: 'private',
    access_user_ids: [7],
    access_group_ids: [3],
    access_grant_options: [
      {
        type: 'user',
        id: 7,
        username: 'Hidden member',
        email: 'hidden@example.invalid'
      }
    ]
  })
  run('wizardStep.value = wizardStepCount.value')
  const html = await render()
  assert.doesNotMatch(
    html,
    /authorized-groups-selector|authorized-users-selector|hidden@example.invalid/
  )
  assert.match(html, /lensAdmin.access.delegatedHint/)
  assert.equal(payload().visibility, 'private')
  assert.equal(Object.hasOwn(payload(), 'access_grants'), false)
})

test('delegated drawer does not request member or group selectors', async () => {
  const calls = []
  const { run } = await drawer(
    {},
    {
      delegation: { mode: 'operational' },
      loadAccess: true,
      listAssistantAccessSubjects: async () => {
        calls.push('subjects')
        return {}
      }
    }
  )
  await run('loadGroups(1)')
  await run('loadUsers(1)')
  assert.deepEqual(calls, [])
})

test('entering delegation discards pending member and group search responses', async () => {
  const pending = []
  const { run, userStore } = await drawer(
    {},
    {
      loadAccess: true,
      listAssistantAccessSubjects: () =>
        new Promise((resolve) => pending.push(resolve))
    }
  )
  assert.equal(pending.length, 2)
  userStore.delegation = { mode: 'operational' }
  await vue.nextTick()
  for (const resolve of pending)
    resolve({
      users: { results: [{ id: 7, name: 'Hidden stale identity' }] },
      groups: { results: [{ id: 8, name: 'Hidden stale group' }] }
    })
  await vue.nextTick()
  await vue.nextTick()
  assert.equal(run('groupResults.value.length'), 0)
  assert.equal(run('userResults.value.length'), 0)
  assert.equal(run('groupOptions.value.size + userOptions.value.size'), 0)
})

test('tenant access selectors preserve paged searches and separate user and group results', async () => {
  const calls = []
  const { run, props, render } = await drawer(
    { mode: 'edit' },
    {
      loadAccess: true,
      listAssistantAccessSubjects: async (params, config) => {
        calls.push({ params, signal: config.signal })
        return {
          users: {
            count: 21,
            results: [{ id: params.page, name: `Member ${params.page}` }]
          },
          groups: {
            count: 21,
            results: [{ id: 100 + params.page, name: `Group ${params.page}` }]
          }
        }
      }
    }
  )
  await vue.nextTick()
  await vue.nextTick()
  assert.equal(run('userResults.value[0].name'), 'Member 1')
  assert.equal(run('groupResults.value[0].name'), 'Group 1')
  await run('loadGroups(2)')
  await run('loadUsers(2)')
  assert.deepEqual(
    Array.from(run('groupResults.value.map(row => row.id)')),
    [101, 102]
  )
  assert.deepEqual(
    Array.from(run('userResults.value.map(row => row.id)')),
    [1, 2]
  )
  assert.equal(run('userTotal.value'), 21)
  assert.equal(run('groupTotal.value'), 21)
  assert.equal(calls[2].params.page, 2)
  assert.equal(calls[2].params.page_size, 20)
  assert.equal(calls[2].signal instanceof AbortSignal, true)
  assert.equal(Object.hasOwn(calls[2].params, 'compact'), false)
  run("groupSearch.value = ' team '")
  await vue.nextTick()
  await run('loadGroups(1)')
  assert.equal(calls.at(-1).params.search, 'team')
  assert.deepEqual(Array.from(run('groupResults.value.map(row => row.id)')), [
    101
  ])
  props.form.capability = 'general_chat'
  props.form.visibility = 'private'
  props.form.access_user_ids = []
  props.form.access_group_ids = []
  await vue.nextTick()
  run('wizardStep.value = wizardStepCount.value')
  assert.match(await render(), /Member 1/)
  run('resetPendingAccessRequests()')
})

test('new assistants start with a type picker and use only the steps required by that type', async () => {
  const { run } = await drawer()
  assert.equal(run('typePickerOpen.value'), true)
  for (const [type, expected] of [
    ['knowledge_qa', ['basic', 'execution', 'tools', 'access']],
    ['code_analysis', ['basic', 'execution', 'tools', 'access']],
    ['general_chat', ['basic', 'tools', 'access']],
    ['smart', ['basic', 'execution', 'access']]
  ]) {
    run(`selectAssistantType('${type}')`)
    await vue.nextTick()
    assert.deepEqual(
      Array.from(run('wizardStepsMeta.value.map(step => step.key)')),
      expected
    )
    assert.equal(run('typePickerOpen.value'), false)
  }
})

test('quick creation waits for usable resources and emits through the existing save event', async () => {
  const { props, events, run } = await drawer({ llmConfigOptions: [] })
  run("selectAssistantType('general_chat', true)")
  props.form.name = 'Quick draft'
  props.form.skill_uuids = ['skill']
  assert.equal(run('canSaveAssistant.value'), false)
  props.llmConfigOptions = [
    { uuid: 'disabled', is_active: false },
    { uuid: 'decision', model_type: 'decision', is_active: true },
    { uuid: 'model', model_type: 'llm', is_active: true }
  ]
  await vue.nextTick()
  assert.equal(props.form.agent_model_ref, 'model')
  assert.equal(props.form.slug, 'assistant-generated-id')
  assert.equal(props.form.visibility, 'private')
  assert.equal(run('canSaveAssistant.value'), true)
  run('submitAssistant(true)')
  assert.deepEqual(events, [['save', true]])
  props.saving = true
  run('submitAssistant(true); nextWizardStep(); selectAssistantType("smart")')
  assert.equal(events.length, 1)
  assert.equal(props.form.mode, 'direct')
})

test('quick skills exclude disabled resources, credentials, workspace guides and plugin dependencies', async () => {
  const { props, run } = await drawer({
    skills: [
      { uuid: 'ok', enabled: true, definition: {} },
      { uuid: 'disabled', enabled: false, definition: {} },
      { uuid: 'guide', kind: 'workspace_guide', definition: {} },
      {
        uuid: 'env',
        definition: { environment: [{ name: 'TOKEN', required: true }] }
      },
      {
        uuid: 'plugin',
        definition: { required_plugins: [{ plugin: 'github' }] }
      }
    ]
  })
  run("selectAssistantType('general_chat', true)")
  props.form.name = 'Draft'
  assert.deepEqual(
    Array.from(run('quickSkillOptions.value.map(skill => skill.uuid)')),
    ['ok']
  )
  props.form.skill_uuids = ['env']
  assert.equal(run('canSaveAssistant.value'), false)
  props.form.skill_uuids = ['ok']
  assert.equal(run('canSaveAssistant.value'), true)
})

test('final save validates skipped earlier steps and rejects stale knowledge or member selections', async () => {
  const { props, events, run } = await drawer()
  run("selectAssistantType('knowledge_qa')")
  run('wizardStep.value = wizardStepsMeta.value.length; submitAssistant()')
  assert.equal(events.length, 0)
  props.form.name = 'Knowledge'
  props.form.slug = 'knowledge'
  props.form.agent_model_ref = 'model'
  run('quickCreate.value = true')
  props.form.datasource_bindings = [{ datasource_uuid: 'missing' }]
  assert.equal(run('canSaveAssistant.value'), false)
  props.form.datasource_bindings = [{ datasource_uuid: 'source' }]
  assert.equal(run('canSaveAssistant.value'), true)
  run("selectAssistantType('smart', true)")
  await vue.nextTick()
  assert.equal(props.form.datasource_bindings.length, 0)
  props.form.collaboration_member_uuids = ['missing']
  assert.equal(run('canSaveAssistant.value'), false)
  props.form.collaboration_member_uuids = ['member']
  assert.equal(run('canSaveAssistant.value'), true)
})

test('switching quick creation to steps retains the draft and edit opens the stored type directly', async () => {
  const { props, run } = await drawer()
  run("selectAssistantType('code_analysis', true)")
  props.form.name = 'Keep me'
  props.form.datasource_bindings = [{ datasource_uuid: 'source' }]
  run('useStepByStep()')
  assert.equal(run('quickCreate.value'), false)
  assert.equal(props.form.name, 'Keep me')
  assert.equal(props.form.datasource_bindings.length, 1)
  props.show = false
  await vue.nextTick()
  props.mode = 'edit'
  props.form.mode = 'smart'
  props.form.capability = 'general_chat'
  props.show = true
  await vue.nextTick()
  assert.equal(run('typePickerOpen.value'), false)
  assert.equal(run('wizardStepsMeta.value.length'), 3)
})

test('the full drawer renders the picker, each type-specific step and quick required resources', async () => {
  const { props, run, render } = await drawer()
  const picker = await render()
  assert.equal((picker.match(/lensAdmin.creation.stepByStep/g) || []).length, 4)
  assert.equal(
    (picker.match(/lensAdmin.creation.quickCreate/g) || []).length,
    4
  )
  assert.doesNotMatch(picker, /lensAdmin.wizard.finish/)
  for (const type of [
    'knowledge_qa',
    'code_analysis',
    'general_chat',
    'smart'
  ]) {
    run(`selectAssistantType('${type}')`)
    await vue.nextTick()
    const count = run('wizardStepCount.value')
    for (let step = 1; step <= count; step++) {
      run(`wizardStep.value = ${step}`)
      const html = await render()
      assert.doesNotMatch(html, /assistant-type-picker|assistant-quick-create/)
      if (type === 'smart')
        assert.doesNotMatch(html, /lensAdmin.wizard.skillsSection/)
      if (step === count) assert.match(html, /lensAdmin.wizard.finish/)
    }
    run(`selectAssistantType('${type}', true)`)
    await vue.nextTick()
    const quick = await render()
    assert.match(quick, /data-testid="assistant-quick-create"/)
    assert.doesNotMatch(quick, /assistant-wizard-sidebar/)
    if (type === 'general_chat')
      assert.doesNotMatch(quick, /lensAdmin.datasourceSelection.title/)
  }
  props.mode = 'edit'
  run('typePickerOpen.value = true')
  assert.doesNotMatch(await render(), /lensAdmin.creation.quickCreate/)
})

test('quick knowledge selections produce valid root bindings and toggling off removes them', async () => {
  const { props, run } = await drawer()
  run("selectAssistantType('knowledge_qa', true)")
  props.form.name = 'Knowledge'
  run('toggleQuickDatasource(props.datasourceOptions[0], true)')
  assert.equal(run('canSaveAssistant.value'), true)
  assert.deepEqual(JSON.parse(JSON.stringify(props.form.datasource_bindings)), [
    {
      datasource_uuid: 'source',
      item_uuid: null,
      mount_name: 'ds_source',
      required: true
    }
  ])
  run('toggleQuickDatasource(props.datasourceOptions[0], false)')
  assert.equal(run('canSaveAssistant.value'), false)
})

test('quick creation submits the advertised defaults through the real payload builder for all types', async () => {
  for (const type of [
    'knowledge_qa',
    'code_analysis',
    'general_chat',
    'smart'
  ]) {
    const { props, run, payload } = await drawer()
    run(`selectAssistantType('${type}', true)`)
    await vue.nextTick()
    props.form.name = `Quick ${type}`
    if (type === 'general_chat') props.form.skill_uuids = ['skill']
    else if (type === 'smart')
      props.form.collaboration_member_uuids = ['member']
    else run('toggleQuickDatasource(props.datasourceOptions[0], true)')
    assert.equal(run('canSaveAssistant.value'), true)
    const data = payload()
    assert.equal(data.agent_model_ref, 'model')
    assert.equal(data.visibility, 'private')
    assert.deepEqual(data.access_grants, [])
    assert.deepEqual(data.settings.execution_budget, {
      max_tokens: 0,
      max_agent_turns: 0
    })
    assert.deepEqual(data.settings.retrieval_policy, {
      exclude_extensions: [],
      exclude_dirs: []
    })
    assert.deepEqual(data.mcp_bindings, [])
    assert.deepEqual(data.plugin_bindings, [])
    assert.equal(data.mode, type === 'smart' ? 'smart' : 'direct')
    assert.equal(data.skill_bindings.length, type === 'general_chat' ? 1 : 0)
    assert.equal(
      data.datasource_bindings.length,
      ['code_analysis', 'knowledge_qa'].includes(type) ? 1 : 0
    )
    if (type === 'smart')
      assert.deepEqual(data.collaboration_member_uuids, ['member'])
  }
})

test('step-by-step final save retains Skill environment and plugin dependency validation', async () => {
  const { props, run } = await drawer({
    skills: [
      {
        uuid: 'skill',
        definition: { environment: [{ name: 'TOKEN', required: true }] }
      }
    ]
  })
  run("selectAssistantType('general_chat')")
  props.form.name = 'Configured'
  props.form.slug = 'configured'
  props.form.agent_model_ref = 'model'
  props.form.skill_uuids = ['skill']
  run('wizardStep.value = 3')
  assert.equal(run('canSaveAssistant.value'), false)
  props.form.skill_environment_drafts.skill = {
    values: { TOKEN: 'input-only' }
  }
  assert.equal(run('canSaveAssistant.value'), true)
  props.skills[0].definition.required_plugins = [
    { plugin: 'github', capabilities: ['issues.read'] }
  ]
  assert.equal(run('canSaveAssistant.value'), false)
  props.skills[0].definition.required_plugins = []
  assert.equal(run('canSaveAssistant.value'), true)
})

test('authorization offers tenant visibility and preserves it in the saved payload', async () => {
  const { props, run, render, payload } = await drawer()
  run("selectAssistantType('general_chat'); wizardStep.value = 3")
  props.form.visibility = 'tenant'
  props.form.name = 'Tenant helper'
  const html = await render()
  assert.match(html, /value="tenant"/)
  assert.match(html, /lensAdmin.visibility.tenant/)
  assert.equal(payload().visibility, 'tenant')
  assert.deepEqual(payload().access_grants, [])
})
