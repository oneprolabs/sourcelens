import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

const source = () =>
  readFile(
    new URL('../src/admin/pages/lens/RunObservation.vue', import.meta.url),
    'utf8'
  )

test('run detail title shows the selected task ID', async () => {
  const contents = await source()
  const titleIndex = contents.indexOf("t('lensRuns.detailTitle')")
  const taskIdIndex = contents.indexOf('data-testid="run-detail-id"')

  assert.ok(titleIndex >= 0)
  assert.ok(taskIdIndex > titleIndex)
  assert.match(contents.slice(taskIdIndex, taskIdIndex + 200), /selectedUuid/)
})

test('run task details omit individual model call rows', async () => {
  const contents = await source()

  assert.doesNotMatch(contents, /detail\.model_calls/)
  assert.doesNotMatch(
    contents,
    /v-for="\(call, index\) in detail\.model_calls"/
  )
})

test('run token summary separates totals, token metrics, and call counts', async () => {
  const contents = await source()

  assert.match(contents, /data-testid="run-token-summary"/)
  assert.match(contents, /lensRuns\.promptTokens/)
  assert.match(contents, /lensRuns\.completionTokens/)
  assert.match(contents, /lensRuns\.cachedTokens/)
  assert.match(contents, /lensRuns\.reasoningTokens/)
  assert.doesNotMatch(contents, /toLocaleString\(\) }}↑/)
})

test('run overview groups related fields and localizes analysis depth', async () => {
  const contents = await source()

  assert.match(contents, /data-testid="run-overview-summary"/)
  assert.match(contents, /data-testid="run-overview-execution"/)
  assert.match(contents, /data-testid="run-overview-timing"/)
  assert.match(contents, /data-testid="run-overview-resources"/)
  assert.match(contents, /data-testid="run-analysis-depth"/)
  assert.match(contents, /lensAdmin\.agentRounds/)
  assert.doesNotMatch(contents, /· \{\{ detail\.agent_rounds \}\}/)
})

test('run overview shows the models actually used by the run', async () => {
  const contents = await source()

  assert.match(contents, /data-testid="run-models-used"/)
  assert.match(contents, /lensRuns\.modelsUsed/)
  assert.match(contents, /detail\.models_used/)
})

test('run question collapses after ten lines and can be expanded', async () => {
  const contents = await source()

  assert.match(contents, /data-testid="run-question-section"/)
  assert.match(contents, /data-testid="run-question-content"/)
  assert.match(contents, /data-testid="run-question-toggle"/)
  assert.match(contents, /questionCanExpand/)
  assert.match(contents, /questionExpanded/)
  assert.match(contents, /-webkit-line-clamp: 10/)
  assert.match(contents, /measureQuestionOverflow/)
  assert.match(contents, /common\.collapse/)
  assert.match(contents, /common\.expand/)
})

test('run overview owns resource consumption and uses a dashboard layout', async () => {
  const contents = await source()
  const overviewIndex = contents.indexOf("activeDetailTab === 'overview'")
  const usageIndex = contents.indexOf('data-testid="run-overview-usage"')
  const executionIndex = contents.indexOf('data-testid="run-execution-content"')

  assert.ok(overviewIndex >= 0)
  assert.ok(usageIndex > overviewIndex)
  assert.ok(executionIndex > overviewIndex)
  assert.match(contents, /class="overview-dashboard"/)
  assert.match(contents, /class="overview-time-row"/)
  assert.doesNotMatch(contents, /lensRuns\.submittedAt/)
  assert.match(contents, /lensRuns\.queueTime/)
  assert.match(contents, /lensRuns\.admissionWait/)
  assert.match(contents, /lensRuns\.execWindow/)
  assert.match(contents, /:run-status="detail\.status"/)
  assert.match(contents, /function scheduleDetailRefresh\(\)/)
  assert.match(contents, /data-testid="run-token-summary"/)
  assert.doesNotMatch(contents, /data-testid="run-execution-usage-view"/)
  assert.doesNotMatch(contents, /activeExecutionView === 'usage'/)
})

test('run resources use one ledger with configured and call counts', async () => {
  const contents = await source()

  assert.match(contents, /data-testid="run-resource-ledger"/)
  assert.match(contents, /resource_usage[\s\S]*resources/)
  assert.match(contents, /resource_usage[\s\S]*total_calls/)
  assert.match(contents, /resource_usage[\s\S]*called_resource_count/)
  assert.match(contents, /resource-ledger-row/)
  assert.doesNotMatch(contents, /class="resource-pill"/)
  assert.doesNotMatch(contents, /data-testid="run-configured-resources"/)
  assert.doesNotMatch(contents, /data-testid="run-resource-calls"/)
})

test('run resources label plugin tools with their owning plugin', async () => {
  const [contents, english, chinese] = await Promise.all([
    source(),
    readFile(
      new URL('../src/admin/locales/en.json', import.meta.url),
      'utf8'
    ).then(JSON.parse),
    readFile(
      new URL('../src/admin/locales/zh-CN.json', import.meta.url),
      'utf8'
    ).then(JSON.parse)
  ])

  assert.match(contents, /function resourceTypeLabel\(resource\)/)
  assert.match(contents, /resource\.resource_type === 'plugin'/)
  assert.match(contents, /resource\.plugin_name/)
  assert.match(contents, /lensRuns\.pluginResource/)
  assert.equal(english.lensRuns.pluginResource, '{name} Plugin')
  assert.equal(chinese.lensRuns.pluginResource, '{name} 插件')
})

test('run overview separates executor status from business outcome', async () => {
  const contents = await source()

  assert.match(contents, /detail\.executor_status/)
  assert.match(contents, /detail\.outcome/)
  assert.match(contents, /detail\.termination_detail/)
  assert.match(contents, /detail\.failure_summary/)
  assert.match(contents, /lensRuns\.executorStatus/)
  assert.match(contents, /lensRuns\.businessOutcome/)
})

test('run detail exposes three task-oriented tabs in order', async () => {
  const contents = await source()
  const overviewIndex = contents.indexOf("activeDetailTab = 'overview'")
  const executionIndex = contents.indexOf("activeDetailTab = 'execution'")
  const resultsIndex = contents.indexOf("activeDetailTab = 'results'")

  assert.match(contents, /data-testid="close-run-detail"/)
  assert.match(contents, /data-testid="run-execution-tab"/)
  assert.match(contents, /data-testid="run-results-tab"/)
  assert.ok(overviewIndex >= 0)
  assert.ok(executionIndex > overviewIndex)
  assert.ok(resultsIndex > executionIndex)
  assert.doesNotMatch(contents, /activeDetailTab = 'progress'/)
  assert.doesNotMatch(contents, /activeDetailTab = 'evidence'/)
  assert.doesNotMatch(contents, /activeDetailTab = 'files'/)
  assert.doesNotMatch(contents, /activeDetailTab = 'diagnosis'/)
  assert.doesNotMatch(contents, /activeDetailTab = 'trace'/)
  assert.doesNotMatch(contents, /activeDetailTab = 'usage'/)
  assert.match(contents, /RunDiagnosisPanel/)
})

test('run detail tabs stay single-line and scroll on narrow screens', async () => {
  const contents = await source()

  assert.match(
    contents,
    /\.detail-tab\s*\{[^}]*shrink-0[^}]*whitespace-nowrap[^}]*\}/s
  )
})

test('run detail header keeps actions reachable on narrow screens', async () => {
  const contents = await source()

  assert.match(
    contents,
    /flex flex-wrap items-center gap-3 border-b border-gray-200/
  )
  assert.match(
    contents,
    /flex w-full shrink-0 items-center justify-end gap-2 md:w-auto/
  )
})

test('execution analysis groups diagnosis and trajectory', async () => {
  const contents = await source()

  assert.match(contents, /data-testid="run-execution-content"/)
  assert.match(contents, /data-testid="run-execution-trace-view"/)
  assert.match(contents, /data-testid="run-execution-diagnosis-view"/)
  assert.match(contents, /data-testid="run-execution-diagnosis"/)
  assert.match(contents, /activeExecutionView === 'trace'/)
  assert.match(contents, /activeExecutionView === 'diagnosis'/)
  assert.match(contents, /v-if="canDiagnoseRun"/)
  assert.match(contents, /:can-generate="canGenerateDiagnosis"/)
  assert.match(contents, /@navigate="navigateFromEvidence"/)
})

test('low-frequency detail actions stay in the more menu', async () => {
  const contents = await source()

  assert.match(contents, /data-testid="run-detail-more-actions"/)
  assert.match(contents, /detailMoreActions/)
  assert.match(contents, /handleDetailMoreAction/)
  assert.doesNotMatch(contents, /data-testid="generate-run-diagnosis"/)
  assert.doesNotMatch(
    contents,
    /<BaseButton size="sm" variant="outline" @click="exportRun">/
  )
})

test('operations center shows summary, node and model filters, and metrics', async () => {
  const contents = await source()

  assert.match(contents, /data-testid="run-status-summary"/)
  assert.match(contents, /filters\.lensnode/)
  assert.match(contents, /filters\.model/)
  assert.match(contents, /r\.tool_call_count/)
  assert.match(contents, /r\.retry_count/)
  assert.match(contents, /r\.budget_consumption/)
})

test('operations center distinguishes loading, empty, and error states', async () => {
  const contents = await source()

  assert.match(contents, /data-testid="run-loading-state"/)
  assert.match(contents, /data-testid="run-empty-state"/)
  assert.match(contents, /data-testid="run-error-state"/)
  assert.match(contents, /data-testid="run-refresh-error"/)
  assert.match(contents, /hasLoaded/)
  assert.match(contents, /loadError/)
  assert.match(contents, /@click="fetchRuns"/)
  assert.match(contents, /lensRuns\.retry/)
  assert.match(contents, /hasLoaded\.value = true/)
  assert.match(
    contents,
    /v-else-if="hasLoaded && !loadError && runs\.length === 0"/
  )
  assert.doesNotMatch(contents, /runs\.value = \[\]/)
})

test('summary cards do not turn unknown metrics into zero while loading', async () => {
  const contents = await source()

  assert.match(contents, /loading && !hasLoaded/)
  assert.match(contents, /const loading = ref\(true\)/)
  assert.match(contents, /statusSummary\.value\?\.total/)
  assert.doesNotMatch(contents, /statusSummary\.value\.total \|\| 0/)
})

test('run actions use server-provided availability and confirmations', async () => {
  const contents = await source()

  assert.match(contents, /r\.available_actions\?\.cancel/)
  assert.match(contents, /r\.available_actions\?\.retry/)
  assert.match(contents, /detail\.available_actions\?\.resume/)
  assert.match(contents, /data-testid="run-action-confirm"/)
  assert.match(contents, /cancelAdminRun/)
  assert.match(contents, /retryAdminRun/)
  assert.match(contents, /resumeAdminRun/)
})

test('detail groups progress, evidence, artifacts, and diagnostics by task', async () => {
  const contents = await source()

  assert.match(contents, /data-testid="run-results-tab"/)
  assert.match(contents, /data-testid="run-results-content"/)
  assert.match(contents, /data-testid="run-execution-tab"/)
  assert.match(contents, /data-testid="run-overview-usage"/)
  assert.match(contents, /data-testid="run-token-summary"/)
  assert.doesNotMatch(contents, /data-testid="run-execution-usage-view"/)
  assert.match(contents, /data-testid="run-live-progress"/)
  assert.match(contents, /data-testid="run-execution-diagnosis"/)
  assert.match(contents, /data-testid="run-resource-ledger"/)
  assert.doesNotMatch(contents, /data-testid="run-evidence-tab"/)
  assert.doesNotMatch(contents, /data-testid="run-files-tab"/)
  assert.match(contents, /detail\.citations/)
  assert.match(contents, /detail\.output_files/)
  assert.match(
    contents,
    /\(detail\.citations \|\| \[\]\)\.length\s*\+\s*\(detail\.output_files \|\| \[\]\)\.length/
  )
  assert.match(contents, /citation\.supports/)
})

test('secondary run filters are disclosed on demand', async () => {
  const contents = await source()

  assert.match(contents, /data-testid="toggle-run-advanced-filters"/)
  assert.match(contents, /data-testid="run-advanced-filters"/)
  assert.match(contents, /advancedFiltersOpen/)
  assert.match(contents, /advancedFilterCount/)
  assert.match(contents, /filters\.lensnode/)
  assert.match(contents, /filters\.model/)
  assert.match(contents, /filters\.start_date/)
  assert.match(contents, /filters\.end_date/)
})

test('run statuses are localized consistently across list and detail', async () => {
  const contents = await source()

  assert.match(contents, /statusText\(r\.status\)/)
  assert.match(
    contents,
    /statusText\(\s*detail\.executor_status\s*\|\|\s*detail\.status\s*\)/s
  )
})

test('opening a run from a notification loads that run even when the drawer is already open', async () => {
  const { default: vm } = await import('node:vm')
  const contents = await source()
  const start = contents.indexOf('function openDetail(uuid)')
  const end = contents.indexOf('\n}', start) + 2
  const ref = (value) => ({ value })
  const loaded = []
  const context = vm.createContext({
    selectedUuid: ref('previous-run'),
    detailVisible: ref(true),
    detail: ref({ uuid: 'previous-run' }),
    activeDetailTab: ref('execution'),
    activeExecutionView: ref('diagnosis'),
    previewFile: ref({}),
    questionCanExpand: ref(true),
    questionExpanded: ref(true),
    fetchDetail: async () => loaded.push(context.selectedUuid.value)
  })
  vm.runInContext(contents.slice(start, end), context)
  context.openDetail('notification-run')
  assert.equal(context.selectedUuid.value, 'notification-run')
  assert.equal(context.detailVisible.value, true)
  assert.equal(context.detail.value, null)
  assert.equal(context.activeDetailTab.value, 'overview')
  assert.deepEqual(loaded, ['notification-run'])
})

test('a previous run request cannot replace the selected run or end its loading state', async () => {
  const { default: vm } = await import('node:vm')
  const contents = await source()
  const start = contents.indexOf('async function fetchDetail(')
  const end = contents.indexOf('\n}', start) + 2
  for (const failed of [false, true]) {
    let resolveRequest, rejectRequest
    const errors = []
    const context = vm.createContext({
      selectedUuid: { value: 'previous-run' },
      detail: { value: null },
      detailLoading: { value: false },
      questionExpanded: { value: false },
      getAdminRun: () =>
        new Promise((resolve, reject) => {
          resolveRequest = resolve
          rejectRequest = reject
        }),
      showError: (message) => errors.push(message),
      extractErrorMessage: (error) => error.message,
      t: (key) => key,
      measureQuestionOverflow: async () => {}
    })
    vm.runInContext(contents.slice(start, end), context)
    const pending = context.fetchDetail()
    context.selectedUuid.value = 'notification-run'
    const current = { uuid: 'notification-run' }
    context.detail.value = current
    context.detailLoading.value = true
    if (failed) rejectRequest(new Error('Previous request failed'))
    else resolveRequest({ uuid: 'previous-run' })
    await pending
    assert.equal(context.detail.value, current)
    assert.equal(context.detailLoading.value, true)
    assert.deepEqual(errors, [])
  }
})

test('readonly delegation blocks both new and already confirmed run actions', async () => {
  const { default: vm } = await import('node:vm')
  const contents = await source()
  const extract = (name) => {
    const start = contents.indexOf(name)
    return contents.slice(start, contents.indexOf('\n}', start) + 2)
  }
  const context = vm.createContext({
    canOperateRuns: { value: false },
    pendingAction: { value: null },
    actionLoading: { value: false },
    cancelAdminRun: () => assert.fail('readonly cancellation reached the API')
  })
  vm.runInContext(extract('function requestRunAction('), context)
  vm.runInContext(extract('async function confirmRunAction('), context)
  context.requestRunAction('cancel', { uuid: 'running-run' })
  assert.equal(context.pendingAction.value, null)
  context.pendingAction.value = { type: 'cancel', run: { uuid: 'running-run' } }
  await context.confirmRunAction()
  assert.equal(context.actionLoading.value, false)
})

test('consumer export preserves readable content without requesting provider trajectory', async () => {
  const { default: vm } = await import('node:vm')
  const contents = await source()
  const start = contents.indexOf('async function exportRun(')
  const script = contents.slice(start, contents.indexOf('\n}', start) + 2)
  for (const allowed of [false, true]) {
    let trajectoryRequests = 0
    let exported
    const context = vm.createContext({
      selectedUuid: { value: 'consumer-run' },
      detail: { value: { answer: 'Consumer answer' } },
      canInspectConfiguration: { value: allowed },
      getAdminRunTrajectoryExport: async () => {
        trajectoryRequests++
        return { events: ['provider trace'] }
      },
      Blob: class { constructor(parts) { exported = JSON.parse(parts[0]) } },
      URL: { createObjectURL: () => 'blob:export', revokeObjectURL: () => {} },
      document: {
        createElement: () => ({ click() {} }),
        body: { appendChild() {}, removeChild() {} }
      },
      t: (key) => key,
      showSuccess() {},
      showError: (error) => assert.fail(error),
      extractErrorMessage: (error) => error.message
    })
    vm.runInContext(script, context)
    await context.exportRun()
    assert.equal(trajectoryRequests, allowed ? 1 : 0)
    assert.equal(exported.run.answer, 'Consumer answer')
    assert.equal(Boolean(exported.trajectory), allowed)
  }
})

test('provider execution panels are not mounted without configuration authority', async () => {
  const { parse, compileScript } = await import('@vue/compiler-sfc')
  const { parse: parseTemplate } = await import('@vue/compiler-dom')
  const { renderToString } = await import('@vue/server-renderer')
  const vue = await import('vue')
  const { default: vm } = await import('node:vm')
  const { descriptor } = parse(await source())
  const ast = parseTemplate(descriptor.template.content)
  const ids = new Set(['run-execution-tab', 'run-execution-content', 'run-overview-resources'])
  const snippets = []
  const visit = (node) => {
    if (node.props?.some((prop) => prop.name === 'data-testid' && ids.has(prop.value?.content))) {
      snippets.push(node.loc.source)
      return
    }
    for (const child of node.children || []) visit(child)
  }
  visit(ast)
  assert.equal(snippets.length, ids.size)
  const { descriptor: component } = parse(`<template>${snippets.join('')}</template><script setup>
    defineProps(['canInspectConfiguration', 'detail', 'canDiagnoseRun'])
    const t = (key) => key
    const activeDetailTab = 'execution'
    const activeExecutionView = 'trace'
    const selectedUuid = 'consumer-run'
    const canOperateRuns = false
    const canGenerateDiagnosis = false
    const applyTrajectoryRunUpdate = () => {}
    const navigateFromEvidence = () => {}
  </script>`)
  const helpers = {}
  const script = compileScript(component, { id: 'run-evidence-test', inlineTemplate: true }).content
    .replace(/import \{([^}]+)\} from "vue"/g, (_, names) => {
      for (const item of names.split(',')) {
        const [name, alias] = item.trim().split(/\s+as\s+/)
        helpers[alias || name] = vue[name]
      }
      return ''
    }).replace('export default', 'globalThis.component =')
  const context = vm.createContext(helpers)
  vm.runInContext(script, context)
  for (const allowed of [false, true]) {
    let mounted = 0
    const app = vue.createSSRApp(context.component, {
      canInspectConfiguration: allowed,
      canDiagnoseRun: allowed,
      detail: { execution: { resource_usage: {} } }
    })
    for (const name of ['RunTrajectoryPanel', 'RunDiagnosisPanel']) {
      app.component(name, { setup() { mounted++; return () => vue.h('div', name) } })
    }
    const html = await renderToString(app)
    assert.equal(mounted, allowed ? 2 : 0)
    assert.equal(html.includes('run-execution-tab'), allowed)
    assert.equal(html.includes('run-overview-resources'), allowed)
  }
})

test('delegated run requests exclude directory filters from bookmarked URLs', async () => {
  const { default: vm } = await import('node:vm')
  const contents = await source()
  const start = contents.indexOf('async function fetchRuns(')
  const script = contents.slice(start, contents.indexOf('\n}', start) + 2)
  const requests = []
  const ref = (value) => ({ value })
  const context = vm.createContext({
    listRequestVersion: 0,
    loading: ref(false),
    loadError: ref(''),
    hasLoaded: ref(false),
    page: ref(1),
    pageSize: ref(20),
    runs: ref([]),
    total: ref(0),
    statusSummary: ref(null),
    resultSummary: ref(null),
    isDelegated: ref(true),
    filters: ref({
      username: 'private-member',
      user_id: '17',
      group_id: '21',
      assistant: 'visible'
    }),
    getAdminRuns: async (params) => {
      requests.push(JSON.parse(JSON.stringify(params)))
      return { results: [{ uuid: 'run' }], total: 1 }
    },
    resolveRunSummary: () => ({}),
    showError: (error) => assert.fail(error),
    extractErrorMessage: (error) => error.message,
    t: (key) => key
  })
  vm.runInContext(script, context)
  await context.fetchRuns()
  assert.deepEqual(requests[0], {
    page: 1,
    page_size: 20,
    assistant: 'visible'
  })
  context.isDelegated.value = false
  await context.fetchRuns()
  assert.equal(requests[1].username, 'private-member')
  assert.equal(requests[1].user_id, '17')
  assert.equal(context.runs.value[0].uuid, 'run')
})
