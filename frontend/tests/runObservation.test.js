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
