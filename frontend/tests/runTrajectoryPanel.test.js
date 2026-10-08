import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

const source = () =>
  readFile(
    new URL('../src/admin/pages/lens/RunTrajectoryPanel.vue', import.meta.url),
    'utf8'
  )

const jsonTreeSource = () =>
  readFile(
    new URL('../src/components/ui/JsonTree.vue', import.meta.url),
    'utf8'
  )

const jsonTreeNodeSource = () =>
  readFile(
    new URL('../src/components/ui/JsonTreeNode.vue', import.meta.url),
    'utf8'
  )

test('switching trajectory steps preserves the active inspector tab', async () => {
  const contents = await source()
  const selectEvent = contents.match(
    /function selectEvent\(event\) \{(?<body>[\s\S]*?)\n\}/
  )
  const inspectorTabs = contents.match(
    /const inspectorTabs = computed\(\(\) => (?<body>\[[\s\S]*?\])\)/
  )

  assert.ok(selectEvent?.groups?.body)
  assert.match(selectEvent.groups.body, /selectedEvent\.value = event/)
  assert.doesNotMatch(selectEvent.groups.body, /inspectorTab\.value/)
  assert.ok(inspectorTabs?.groups?.body)
  assert.match(inspectorTabs.groups.body, /id: 'summary'/)
  assert.match(inspectorTabs.groups.body, /id: 'data'/)
  assert.match(inspectorTabs.groups.body, /id: 'timing'/)
  assert.match(inspectorTabs.groups.body, /trajectoryInspectorData/)
  assert.doesNotMatch(selectEvent.groups.body, /inspectorTab\.value/)
})

test('trajectory timing details live in a tab after raw data', async () => {
  const contents = await source()

  assert.match(contents, /inspectorTab === 'data'/)
  assert.match(contents, /inspectorTab === 'timing'/)
  assert.match(contents, /lensRuns\.trajectoryInspectorTiming/)
  assert.match(contents, /lensRuns\.cachedTokens/)
  assert.doesNotMatch(contents, /inspectorTab === 'raw'/)
})

test('trajectory inspector exposes an accessible drag separator', async () => {
  const contents = await source()

  assert.match(contents, /class="trajectory-resize-handle"/)
  assert.match(contents, /role="separator"/)
  assert.match(contents, /aria-orientation="vertical"/)
  assert.match(contents, /@pointerdown="startInspectorResize"/)
  assert.match(contents, /@pointermove="resizeInspector"/)
  assert.match(contents, /@keydown="resizeInspectorWithKeyboard"/)
  assert.match(contents, /--trajectory-inspector-width/)
})

test('trajectory rows expose bounded depth indentation styles', async () => {
  const contents = await source()

  assert.match(contents, /:style="rowIndentStyle\(row\)"/)
  assert.match(contents, /function rowIndentStyle\(row\)/)
  assert.match(contents, /--trajectory-indent/)
  assert.match(contents, /padding-left: calc\(34px \+ var\(--trajectory-indent/)
  assert.match(contents, /padding-left: calc\(30px \+ var\(--trajectory-indent/)
})

test('trajectory ledger renders a span tree with a waterfall column', async () => {
  const contents = await source()

  assert.match(contents, /class="span-header"/)
  assert.match(contents, /class="waterfall-header"/)
  assert.match(contents, /class="waterfall-track"/)
  assert.match(contents, /:style="waterfallStyle\(row\)"/)
  assert.match(contents, /function waterfallStyle\(row\)/)
  assert.match(contents, /@click\.stop="toggleSpan\(row\.span\.id\)"/)
  assert.match(contents, /function toggleSpan\(spanId\)/)
  assert.match(contents, /:class="rowStatusClass\(row\)"/)
})

test('trajectory spans expose human-readable step labels', async () => {
  const contents = await source()

  assert.match(contents, /const SPAN_LABEL_KEYS = \{/)
  assert.match(contents, /const STAGE_LABEL_KEYS = \{/)
  assert.match(contents, /function spanLabel\(span\)/)
  assert.match(
    contents,
    /'deepagents\.agent\.invoke': 'trajectoryStepAgentInvoke'/
  )
  assert.match(contents, /trajectoryStepRuntimeStage/)
  assert.match(contents, /function rowTitle\(row\)/)
  assert.match(contents, /return spanLabel\(row\?\.span\)/)
})

test('trajectory spans expose a workload summary line', async () => {
  const contents = await source()

  assert.match(contents, /function spanSummary\(span, showTiming = true\)/)
  assert.match(contents, /function rowSummary\(row\)/)
  assert.match(contents, /class="content-summary"/)
  assert.match(contents, /class="inspector-event-card-summary"/)
  assert.match(contents, /const GATE_LABEL_KEYS = \{/)
  assert.match(contents, /trajectorySummaryHits/)
})

test('trajectory spans render plugin and skill chips', async () => {
  const contents = await source()

  assert.match(contents, /row\.span\.plugin/)
  assert.match(contents, /row\.span\.skill/)
  assert.match(contents, /class="plugin-chip"/)
  assert.match(contents, /plugin-chip-skill/)
  assert.match(contents, /\.plugin-chip \{/)
})

test('trajectory rows tag plugin, skill and system types', async () => {
  const contents = await source()

  assert.match(contents, /plugin: 'PLUGIN'/)
  assert.match(contents, /skill: 'SKILL'/)
  assert.match(contents, /stage: 'STAGE'/)
  assert.match(contents, /gate: 'GATE'/)
  assert.match(contents, /evidence: 'EVIDENCE'/)
  assert.match(contents, /agent: 'AGENT'/)
  assert.match(contents, /step: 'system'/)
  assert.match(contents, /if \(span\?\.skill\) return 'skill'/)
  assert.match(contents, /if \(span\?\.plugin\) return 'plugin'/)
  assert.match(contents, /const SPAN_KIND_BY_BASE = \{/)
  assert.match(contents, /'deepagents\.agent\.create': 'agent'/)
  assert.match(contents, /\.tag-plugin \{/)
  assert.match(contents, /\.tag-skill \{/)
  assert.match(contents, /\.tag-stage \{/)
  assert.match(contents, /\.tag-gate \{/)
  assert.match(contents, /\.tag-evidence \{/)
  assert.match(contents, /\.tag-agent \{/)
})

test('trajectory data tab exposes tool input and output previews', async () => {
  const contents = await source()

  assert.match(contents, /inspectorTab === 'data'/)
  assert.match(contents, /trajectoryDetailInput/)
  assert.match(contents, /trajectoryDetailOutput/)
  assert.match(contents, /inspectorInput\(selectedEvent\)/)
  assert.match(contents, /inspectorOutput\(selectedEvent\)/)
  assert.match(contents, /function inspectorValue\(value\)/)
})

test('trajectory JSON uses compact recursive indentation', async () => {
  const [panel, tree, node] = await Promise.all([
    source(),
    jsonTreeSource(),
    jsonTreeNodeSource()
  ])

  assert.match(panel, /<JsonTree[\s\S]*?:indent="8"/)
  assert.match(tree, /indent: \{ type: Number, default: 14 \}/)
  assert.match(tree, /:indent="indent"/)
  assert.match(node, /depth \* indent/)
  assert.match(node, /:indent="indent"/)
})

test('active runs follow the trajectory stream with polling fallback', async () => {
  const contents = await source()

  assert.match(contents, /runStatus: \{ type: String, default: '' \}/)
  assert.match(
    contents,
    /const ACTIVE_RUN_STATUSES = ACTIVE_TRAJECTORY_RUN_STATUSES/
  )
  assert.match(contents, /streamAdminRunTrajectory/)
  assert.match(contents, /new AbortController\(\)/)
  assert.match(contents, /const awaitingStreamDone = ref\(false\)/)
  assert.match(contents, /function scheduleFallbackRefresh\(\)/)
  assert.match(contents, /function syncTerminalTrajectory\(runUuid\)/)
  assert.match(contents, /requiresResync/)
  assert.match(contents, /else if \(loaded === false\)/)
  assert.match(contents, /controller !== streamController/)
  assert.match(contents, /controller\?\.abort\(\)/)
  assert.match(
    contents,
    /if \(streamController\) return\s+if \(active && events\.value\.length === 0\)/
  )
})

test('live trajectory follows the tail without interrupting history review', async () => {
  const contents = await source()

  assert.match(contents, /@scroll="handleTrajectoryScroll"/)
  assert.match(contents, /const pendingNewEventCount = ref\(0\)/)
  assert.match(contents, /function isFollowingTrajectoryTail\(\)/)
  assert.match(contents, /function scrollToLatestTrajectory\(\)/)
  assert.match(contents, /trajectory-new-events/)
})

test('live events do not advance the historical pagination cursor', async () => {
  const contents = await source()

  assert.match(contents, /const nextAfterSequence = ref\(0\)/)
  assert.match(
    contents,
    /const afterSequence = append \? nextAfterSequence\.value : 0/
  )
  assert.match(contents, /nextAfterSequence\.value = Number\(/)
  assert.doesNotMatch(contents, /function latestSequence\(/)
})
