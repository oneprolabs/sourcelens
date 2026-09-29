<template>
  <section class="trace-workbench" data-testid="run-trajectory-workbench">
    <BaseLoading v-if="loading && !steps.length" />
    <template v-else>
      <div
        v-if="active && (isActive || pendingNewEventCount)"
        class="trace-stream-bar"
      >
        <span class="trace-live-state" :data-state="streamState" role="status">
          <span class="trace-live-dot" aria-hidden="true" />
          {{ streamLabel }}
        </span>
        <button
          v-if="pendingNewEventCount"
          type="button"
          class="trace-new-events"
          @click="scrollToLatest"
        >
          {{ t('lensRuns.traceNewSteps', { n: pendingNewEventCount }) }}
        </button>
      </div>

      <dl class="trace-summary" data-testid="trajectory-summary">
        <div>
          <dt>{{ t('lensRuns.traceDuration') }}</dt>
          <dd>{{ formatDuration(summary.duration_ms) }}</dd>
        </div>
        <div>
          <dt>{{ t('lensRuns.traceSteps') }}</dt>
          <dd>{{ steps.length }}</dd>
        </div>
        <div>
          <dt>{{ t('lensRuns.traceTokens') }}</dt>
          <dd>{{ formatNumber(summary.total_tokens) }}</dd>
        </div>
        <div>
          <dt>{{ t('lensRuns.traceTools') }}</dt>
          <dd>{{ summary.tool_calls || 0 }}</dd>
        </div>
        <div>
          <dt>{{ t('lensRuns.traceRetrievals') }}</dt>
          <dd>{{ typeCount('retrieval') }}</dd>
        </div>
        <div>
          <dt>{{ t('lensRuns.traceDecisions') }}</dt>
          <dd>{{ typeCount('decision') }}</dd>
        </div>
      </dl>

      <section
        v-if="childProgress.length"
        class="trace-child-progress"
        data-testid="trajectory-assistant-progress"
      >
        <div class="trace-section-heading">
          <strong>{{ t('lensRuns.trajectoryAssistantProgress') }}</strong>
          <span>{{ completedChildren }}/{{ childProgress.length }}</span>
        </div>
        <div class="trace-child-list">
          <article
            v-for="child in childProgress"
            :key="child.run_uuid"
            class="trace-child-row"
          >
            <span
              class="trace-child-dot"
              :data-status="child.status"
              aria-hidden="true"
            />
            <strong>{{
              child.assistant_name || t('lensRuns.traceSubagent')
            }}</strong>
            <span class="trace-child-task">{{ child.task }}</span>
            <span class="trace-child-meta"
              >{{ progressStatus(child.status) }} ·
              {{ formatDuration(child.duration_ms) }}</span
            >
          </article>
        </div>
      </section>

      <div class="trace-toolbar" role="toolbar">
        <div
          class="trace-tabs"
          role="tablist"
          :aria-label="t('lensRuns.traceViews')"
        >
          <button
            type="button"
            role="tab"
            :aria-selected="activeView === 'timeline'"
            :class="{ active: activeView === 'timeline' }"
            @click="activeView = 'timeline'"
          >
            {{ t('lensRuns.traceTimeline') }}
          </button>
          <button
            type="button"
            role="tab"
            :aria-selected="activeView === 'graph'"
            :class="{ active: activeView === 'graph' }"
            @click="activeView = 'graph'"
          >
            {{ t('lensRuns.traceGraph') }}
          </button>
        </div>
        <div class="trace-controls">
          <label class="trace-search">
            <Search :size="14" aria-hidden="true" />
            <input
              v-model="query"
              type="search"
              :placeholder="t('lensRuns.trajectorySearch')"
              data-testid="trajectory-search"
            />
          </label>
          <BaseSelect
            v-model="typeFilter"
            class="trace-filter"
            :full-width="false"
            :aria-label="t('lensRuns.traceFilterType')"
          >
            <option value="all">{{ t('lensRuns.trajectoryAll') }}</option>
            <option v-for="type in stepTypes" :key="type" :value="type">
              {{ stepLabel(type) }}
            </option>
          </BaseSelect>
          <button
            type="button"
            class="trace-follow"
            :class="{ active: following }"
            @click="following = !following"
          >
            <span class="trace-toggle-dot" />{{ t('lensRuns.traceFollow') }}
          </button>
          <button
            type="button"
            class="trace-icon-button"
            :title="t('lensRuns.exportAction')"
            @click="exportTrace"
          >
            <Download :size="15" />
          </button>
        </div>
      </div>

      <div v-if="activeView === 'timeline'" class="trace-layout">
        <section
          ref="timelineRef"
          class="trace-timeline"
          data-testid="trajectory-ledger"
          @scroll="handleScroll"
        >
          <div class="trace-timeline-head">
            <span>{{ t('lensRuns.traceRelativeTime') }}</span
            ><span>{{ t('lensRuns.traceStep') }}</span>
          </div>
          <div v-if="!pagedSteps.length" class="trace-empty">
            {{ t('lensRuns.noTimeline') }}
          </div>
          <article
            v-for="step in pagedSteps"
            :key="step.id"
            class="trace-step-row"
            :data-selected="selectedStep?.id === step.id || undefined"
            @click="selectStep(step)"
          >
            <time class="trace-step-time">{{
              relativeTime(step.start_ms)
            }}</time>
            <span
              class="trace-step-marker"
              :class="`type-${step.type}`"
              aria-hidden="true"
            />
            <div class="trace-step-card">
              <div class="trace-step-topline">
                <div class="trace-step-main">
                  <span class="trace-type-icon" :class="`type-${step.type}`"
                    ><component :is="typeIcon(step.type)" :size="15"
                  /></span>
                  <div class="trace-step-heading">
                    <span class="trace-step-type">{{
                      stepLabel(step.type)
                    }}</span
                    ><strong>{{ step.title }}</strong>
                  </div>
                </div>
                <span class="trace-status" :data-status="step.status">{{
                  statusLabel(step.status)
                }}</span>
              </div>
              <p class="trace-step-summary">{{ step.summary }}</p>
              <div class="trace-step-metrics">
                <span
                  ><Clock :size="12" />{{
                    formatDuration(step.duration_ms)
                  }}</span
                >
                <span v-if="step.tokens?.total"
                  ><Layers :size="12" />{{ formatNumber(step.tokens.total) }}
                  {{ t('lensRuns.traceTokensShort') }}</span
                >
                <span v-if="step.assistant_name">{{
                  step.assistant_name
                }}</span>
                <span>#{{ step.id.replace('step_', '') }}</span>
              </div>
            </div>
          </article>
          <div v-if="hasMoreSteps" class="trace-load-more">
            <BaseButton
              variant="outline"
              size="sm"
              @click.stop="showMoreSteps"
              >{{ t('lensRuns.trajectoryLoadMore') }}</BaseButton
            >
          </div>
        </section>

        <aside
          v-if="selectedStep"
          class="trace-inspector"
          data-testid="trajectory-inspector"
        >
          <div class="trace-inspector-header">
            <div>
              <span class="trace-inspector-kicker"
                >{{ stepLabel(selectedStep.type) }}
                {{ t('lensRuns.traceStep') }}</span
              >
              <h3>{{ selectedStep.title }}</h3>
              <code>{{ selectedStep.id }}</code>
            </div>
            <button
              type="button"
              class="trace-close"
              :aria-label="t('common.close')"
              @click="selectedStepId = null"
            >
              <X :size="16" />
            </button>
          </div>
          <div class="trace-inspector-tabs" role="tablist">
            <button
              v-for="tab in inspectorTabs"
              :key="tab.id"
              type="button"
              :class="{ active: inspectorTab === tab.id }"
              @click="inspectorTab = tab.id"
            >
              {{ tab.label }}
            </button>
          </div>
          <div class="trace-inspector-body">
            <template v-if="inspectorTab === 'summary'">
              <section class="trace-detail-section">
                <h4>{{ t('lensRuns.traceExecutionMetrics') }}</h4>
                <div class="trace-detail-grid">
                  <div>
                    <span>{{ t('lensRuns.traceRelativeStart') }}</span
                    ><strong>{{ relativeTime(selectedStep.start_ms) }}</strong>
                  </div>
                  <div>
                    <span>{{ t('lensRuns.traceDuration') }}</span
                    ><strong>{{
                      formatDuration(selectedStep.duration_ms)
                    }}</strong>
                  </div>
                  <div>
                    <span>{{ t('lensRuns.traceStatus') }}</span
                    ><strong :data-status="selectedStep.status">{{
                      statusLabel(selectedStep.status)
                    }}</strong>
                  </div>
                  <div>
                    <span>{{ t('lensRuns.traceTokens') }}</span
                    ><strong>{{
                      formatNumber(selectedStep.tokens?.total)
                    }}</strong>
                  </div>
                </div>
              </section>
              <section class="trace-detail-section">
                <h4>{{ t('lensRuns.traceWhatHappened') }}</h4>
                <div class="trace-notice">
                  <component
                    :is="typeIcon(selectedStep.type)"
                    :size="15"
                  /><span>{{ selectedStep.summary }}</span>
                </div>
              </section>
              <section
                v-if="
                  selectedStep.details?.score != null ||
                  selectedStep.details?.threshold != null
                "
                class="trace-detail-section"
              >
                <h4>{{ t('lensRuns.traceDecisionMetrics') }}</h4>
                <div class="trace-score">
                  <div>
                    <span>{{ t('lensRuns.traceScore') }}</span
                    ><strong>{{ selectedStep.details.score ?? '—' }}</strong>
                  </div>
                  <div>
                    <span>{{ t('lensRuns.traceThreshold') }}</span
                    ><strong>{{
                      selectedStep.details.threshold ?? '—'
                    }}</strong>
                  </div>
                  <div class="trace-score-bar">
                    <span :style="{ width: `${scorePercent}%` }" />
                  </div>
                </div>
              </section>
              <section
                v-if="
                  selectedStep.details?.sources ||
                  selectedStep.details?.documents
                "
                class="trace-detail-section"
              >
                <h4>{{ t('lensRuns.traceSources') }}</h4>
                <div class="trace-source-list">
                  <div v-for="(source, index) in sourceList" :key="index">
                    <span>{{ String(index + 1).padStart(2, '0') }}</span
                    ><strong>{{ source.title || source.name || source }}</strong
                    ><small v-if="source.confidence">{{
                      source.confidence
                    }}</small>
                  </div>
                </div>
              </section>
              <section class="trace-detail-section">
                <h4>{{ t('lensRuns.traceDetails') }}</h4>
                <dl class="trace-detail-list">
                  <div v-for="entry in detailEntries" :key="entry[0]">
                    <dt>{{ humanize(entry[0]) }}</dt>
                    <dd>{{ valueText(entry[1]) }}</dd>
                  </div>
                </dl>
              </section>
            </template>
            <JsonTree
              v-else-if="inspectorTab === 'payload'"
              :data="selectedStep.details"
              :indent="8"
            />
            <template v-else>
              <BaseLoading v-if="rawLoading" />
              <JsonTree v-else :data="rawEvents" :indent="8" />
            </template>
          </div>
        </aside>
        <div v-else class="trace-inspector trace-inspector-empty">
          {{ t('lensRuns.trajectorySelectEvent') }}
        </div>
      </div>

      <section v-else class="trace-graph" data-testid="trajectory-graph">
        <div class="trace-graph-heading">
          <div>
            <h3>{{ t('lensRuns.traceGraphTitle') }}</h3>
            <p>{{ t('lensRuns.traceGraphDescription') }}</p>
          </div>
          <div class="trace-graph-actions">
            <button
              type="button"
              :class="{ active: graphLayout === 'causal' }"
              @click="graphLayout = 'causal'"
            >
              {{ t('lensRuns.traceCausalFlow') }}</button
            ><button
              type="button"
              :class="{ active: graphLayout === 'calls' }"
              @click="graphLayout = 'calls'"
            >
              {{ t('lensRuns.traceCallHierarchy') }}</button
            ><span class="trace-zoom">{{ graphZoom }}%</span
            ><button
              type="button"
              @click="graphZoom = Math.max(70, graphZoom - 10)"
            >
              −</button
            ><button
              type="button"
              @click="graphZoom = Math.min(130, graphZoom + 10)"
            >
              +
            </button>
          </div>
        </div>
        <div class="trace-graph-viewport">
          <div
            class="trace-graph-canvas"
            :class="`layout-${graphLayout}`"
            :style="{ zoom: `${graphZoom}%` }"
          >
            <div class="trace-graph-nodes">
              <button
                v-for="(step, index) in graphSteps"
                :key="step.id"
                type="button"
                class="trace-graph-node"
                :class="[
                  `type-${step.type}`,
                  {
                    selected: selectedStep?.id === step.id,
                    'is-last': index === graphSteps.length - 1
                  }
                ]"
                @click="selectStep(step)"
              >
                <span
                  ><span class="trace-graph-dot" />{{
                    stepLabel(step.type)
                  }}</span
                ><strong>{{ step.title }}</strong
                ><small
                  >{{ relativeTime(step.start_ms) }} ·
                  {{ formatDuration(step.duration_ms) }}</small
                ><em>{{ statusLabel(step.status) }}</em>
              </button>
            </div>
          </div>
        </div>
        <div v-if="selectedStep" class="trace-graph-selected">
          <div>
            <span>{{ t('lensRuns.traceSelectedNode') }}</span
            ><strong>{{ selectedStep.title }}</strong
            ><small>{{ selectedStep.summary }}</small>
          </div>
          <button type="button" @click="activeView = 'timeline'">
            {{ t('lensRuns.traceOpenTimeline') }}
          </button>
        </div>
      </section>
    </template>
  </section>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import {
  Brain,
  Box,
  Clock,
  Download,
  FileText,
  Layers,
  MessageSquare,
  Search,
  Scale,
  Wrench,
  X
} from '@lucide/vue'
import { useI18n } from 'vue-i18n'
import { getAdminRunTrajectory, streamAdminRunTrajectory } from '@/api/lens'
import { useToast } from '@/composables/useToast'
import { extractErrorMessage } from '@/utils/api'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseLoading from '@/components/ui/BaseLoading.vue'
import JsonTree from '@/components/ui/JsonTree.vue'
import BaseSelect from '@/components/ui/BaseSelect.vue'
import {
  TRACE_STEP_TYPES,
  buildTraceGraphOrder,
  filterTraceSteps,
  formatTraceCount,
  formatTraceDuration,
  formatTraceRelative,
  traceTypeCounts
} from './runTrace'

const STEP_PAGE_SIZE = 500
const MAX_RAW_EVENT_IDS = 100

const props = defineProps({
  runUuid: { type: String, default: '' },
  assistantName: { type: String, default: '' },
  active: { type: Boolean, default: false },
  runStatus: { type: String, default: '' }
})
const emit = defineEmits(['run-update'])
const { t } = useI18n()
const { showError, showSuccess } = useToast()

const steps = ref([])
const edges = ref([])
const summary = ref({})
const loading = ref(false)
const query = ref('')
const typeFilter = ref('all')
const selectedStepId = ref(null)
const activeView = ref('timeline')
const inspectorTab = ref('summary')
const following = ref(true)
const graphLayout = ref('causal')
const graphZoom = ref(100)
const streamState = ref('idle')
const pendingNewEventCount = ref(0)
const visibleLimit = ref(STEP_PAGE_SIZE)
const rawEvents = ref([])
const rawLoading = ref(false)
const timelineRef = ref(null)
let streamController = null
let fallbackTimer = null
let requestId = 0
let rawRequestId = 0

const stepTypes = TRACE_STEP_TYPES
const inspectorTabs = computed(() => [
  { id: 'summary', label: t('lensRuns.trajectoryInspectorSummary') },
  { id: 'payload', label: t('lensRuns.trajectoryInspectorPayload') },
  { id: 'raw', label: t('lensRuns.trajectoryInspectorRaw') }
])
const isActive = computed(() =>
  ['queued', 'running', 'streaming'].includes(props.runStatus)
)
const streamLabel = computed(() =>
  streamState.value === 'live'
    ? t('lensRuns.trajectoryStreamLive')
    : streamState.value === 'reconnecting'
      ? t('lensRuns.trajectoryStreamReconnecting')
      : t('lensRuns.trajectoryStreamConnecting')
)
const childProgress = computed(() =>
  (summary.value.run_progress || []).filter((item) => item.role === 'child')
)
const completedChildren = computed(
  () =>
    childProgress.value.filter((item) =>
      ['done', 'failed', 'cancelled'].includes(item.status)
    ).length
)
const selectedStep = computed(
  () => steps.value.find((step) => step.id === selectedStepId.value) || null
)
const filteredSteps = computed(() =>
  filterTraceSteps(steps.value, { type: typeFilter.value, query: query.value })
)
const pagedSteps = computed(() =>
  filteredSteps.value.slice(0, visibleLimit.value)
)
const hasMoreSteps = computed(
  () => filteredSteps.value.length > visibleLimit.value
)
const graphSteps = computed(() =>
  buildTraceGraphOrder(filteredSteps.value, edges.value, graphLayout.value)
)
const sourceList = computed(() => {
  const details = selectedStep.value?.details || {}
  return Array.isArray(details.documents)
    ? details.documents
    : Array.isArray(details.sources)
      ? details.sources
      : []
})
const detailEntries = computed(() =>
  Object.entries(selectedStep.value?.details || {})
    .filter(([key]) => !['documents', 'sources', 'preview'].includes(key))
    .slice(0, 24)
)
const scorePercent = computed(() =>
  Math.max(
    0,
    Math.min(
      100,
      Number(
        selectedStep.value?.details?.score ??
          selectedStep.value?.details?.value ??
          0
      ) * 100
    )
  )
)

function typeIcon(type) {
  return (
    {
      model: Brain,
      retrieval: Search,
      decision: Scale,
      tool: Wrench,
      artifact: FileText,
      message: MessageSquare,
      reasoning: Brain,
      system: Box
    }[type] || Box
  )
}
function stepLabel(type) {
  return t(`lensRuns.traceType.${type}`, type)
}
function statusLabel(status) {
  return t(`lensRuns.traceStatus.${status}`, status)
}
function progressStatus(status) {
  return t(`lensRuns.traceStatus.${status}`, status)
}
function typeCount(type) {
  return traceTypeCounts(steps.value)[type] || 0
}
function formatNumber(value) {
  return formatTraceCount(value)
}
function formatDuration(value) {
  return formatTraceDuration(value)
}
function relativeTime(value) {
  return formatTraceRelative(value)
}
function humanize(value) {
  return value
    .replace(/_/g, ' ')
    .replace(/\b\w/g, (letter) => letter.toUpperCase())
}
function valueText(value) {
  if (typeof value === 'string') return value.slice(0, 480)
  try {
    return JSON.stringify(value)
  } catch {
    return String(value)
  }
}
function selectStep(step) {
  selectedStepId.value = step.id
  inspectorTab.value = 'summary'
  rawEvents.value = []
}
function showMoreSteps() {
  visibleLimit.value += STEP_PAGE_SIZE
}
function isFollowingTail() {
  const pane = timelineRef.value
  return !pane || pane.scrollHeight - pane.scrollTop - pane.clientHeight < 56
}
function handleScroll() {
  if (isFollowingTail()) pendingNewEventCount.value = 0
}
async function scrollToLatest() {
  await nextTick()
  if (timelineRef.value)
    timelineRef.value.scrollTop = timelineRef.value.scrollHeight
  pendingNewEventCount.value = 0
}
function reset() {
  stopStream()
  steps.value = []
  edges.value = []
  summary.value = {}
  selectedStepId.value = null
  rawEvents.value = []
  visibleLimit.value = STEP_PAGE_SIZE
  pendingNewEventCount.value = 0
  streamState.value = 'idle'
}
async function fetchTrace(silent = false) {
  if (!props.runUuid) return false
  const id = ++requestId
  loading.value = true
  try {
    const data = await getAdminRunTrajectory(props.runUuid, { page_size: 1 })
    if (id !== requestId) return false
    steps.value = data.steps || []
    edges.value = data.edges || []
    summary.value = data.summary || {}
    return true
  } catch (error) {
    if (!silent) showError(extractErrorMessage(error, t('common.error')))
    return false
  } finally {
    if (id === requestId) loading.value = false
  }
}
async function fetchRawEvents(step) {
  if (!props.runUuid || !step) return
  const ids = (step.event_ids || []).slice(0, MAX_RAW_EVENT_IDS)
  if (!ids.length) {
    rawEvents.value = []
    return
  }
  const id = ++rawRequestId
  rawLoading.value = true
  try {
    const data = await getAdminRunTrajectory(props.runUuid, {
      event_ids: ids.join(','),
      page_size: 500
    })
    if (id !== rawRequestId) return
    rawEvents.value = data.results || []
  } catch (error) {
    if (id === rawRequestId)
      showError(extractErrorMessage(error, t('common.error')))
  } finally {
    if (id === rawRequestId) rawLoading.value = false
  }
}
async function fetchAllEvents() {
  const collected = []
  let afterSequence = 0
  for (;;) {
    const data = await getAdminRunTrajectory(props.runUuid, {
      after_sequence: afterSequence,
      page_size: 500
    })
    const rows = data.results || []
    collected.push(...rows)
    if (!data.has_more || rows.length === 0) break
    afterSequence = Number(data.next_after_sequence) || 0
  }
  return collected
}
function stopStream() {
  if (streamController) streamController.abort()
  streamController = null
  clearTimeout(fallbackTimer)
  fallbackTimer = null
  streamState.value = 'idle'
}
function scheduleReconnect() {
  clearTimeout(fallbackTimer)
  if (!props.active || !isActive.value) return
  streamState.value = 'reconnecting'
  fallbackTimer = setTimeout(() => {
    fallbackTimer = null
    void startStream()
  }, 1200)
}
async function startStream() {
  if (!props.active || !props.runUuid || (!isActive.value && !streamController))
    return
  if (streamController) return
  const controller = new AbortController()
  streamController = controller
  streamState.value = 'connecting'
  let completed = false
  try {
    await streamAdminRunTrajectory(props.runUuid, {
      mode: 'steps',
      signal: controller.signal,
      onEvent(message) {
        if (controller !== streamController) return
        if (message.type === 'ping') return
        const wasAtTail = isFollowingTail()
        const before = steps.value.length
        if (message.steps) steps.value = message.steps
        if (message.edges) edges.value = message.edges
        if (message.summary) summary.value = message.summary
        if (message.run) emit('run-update', message.run)
        streamState.value = message.type === 'done' ? 'idle' : 'live'
        completed = message.type === 'done'
        const delta = Math.max(steps.value.length - before, 0)
        if (delta > 0) {
          if (following.value && wasAtTail) void scrollToLatest()
          else pendingNewEventCount.value += delta
        }
      }
    })
  } catch (error) {
    if (error?.name !== 'AbortError') scheduleReconnect()
  } finally {
    if (streamController === controller) streamController = null
  }
  if (!completed) scheduleReconnect()
}
async function exportTrace() {
  try {
    const events = await fetchAllEvents()
    const payload = {
      run: {
        id: props.runUuid,
        status: props.runStatus,
        duration_ms: summary.value.duration_ms,
        tokens: summary.value.total_tokens
      },
      summary: summary.value,
      steps: steps.value,
      edges: edges.value,
      events
    }
    const blob = new Blob([JSON.stringify(payload, null, 2)], {
      type: 'application/json'
    })
    const link = document.createElement('a')
    link.href = URL.createObjectURL(blob)
    link.download = `${props.runUuid}-trace.json`
    link.click()
    URL.revokeObjectURL(link.href)
    showSuccess(t('lensRuns.exportSuccess'))
  } catch (error) {
    showError(extractErrorMessage(error, t('common.error')))
  }
}
function traceShouldRun() {
  return (
    props.active &&
    props.runUuid &&
    (isActive.value || steps.value.length === 0)
  )
}
watch(
  () => props.runUuid,
  () => {
    reset()
    if (props.active) void fetchTrace().then(() => startStream())
  }
)
watch(
  () => props.active,
  (active) => {
    if (active) {
      if (!steps.value.length) void fetchTrace().then(() => startStream())
      else void startStream()
    } else stopStream()
  },
  { immediate: true }
)
watch(
  () => props.runStatus,
  () => {
    if (traceShouldRun()) void startStream()
    else if (!isActive.value) stopStream()
  }
)
watch(inspectorTab, (tab) => {
  if (tab === 'raw') void fetchRawEvents(selectedStep.value)
})
watch(selectedStepId, () => {
  if (inspectorTab.value === 'raw') void fetchRawEvents(selectedStep.value)
})
watch([query, typeFilter], () => {
  visibleLimit.value = STEP_PAGE_SIZE
})
onBeforeUnmount(stopStream)
</script>

<style scoped>
.trace-workbench {
  --trace-canvas: #f5f6f8;
  --trace-surface: #fff;
  --trace-raised: #fbfcfe;
  --trace-border: #dfe3ea;
  --trace-soft: #edf0f4;
  --trace-text: #17191c;
  --trace-secondary: #4d5561;
  --trace-muted: #717987;
  --trace-subtle: #9ba2ae;
  --trace-blue: #375ad9;
  --trace-blue-soft: #e9efff;
  --trace-green: #178a65;
  --trace-green-soft: #e5f7f0;
  --trace-amber: #a86f19;
  --trace-amber-soft: #fff4dd;
  --trace-red: #c54f58;
  --trace-red-soft: #fdecee;
  --trace-purple: #7857d5;
  --trace-purple-soft: #f0ebff;
  --trace-cyan: #207f96;
  --trace-cyan-soft: #e4f7fb;
  min-width: 0;
  color: var(--trace-text);
}
:root[data-theme='dark'] .trace-workbench {
  --trace-canvas: #18191b;
  --trace-surface: #242527;
  --trace-raised: #2a2c2f;
  --trace-border: #3a3d41;
  --trace-soft: #303236;
  --trace-text: #ebedf0;
  --trace-secondary: #b9bdc5;
  --trace-muted: #7d838d;
  --trace-subtle: #5d636c;
  --trace-blue: #6e91ff;
  --trace-blue-soft: #263657;
  --trace-green: #53c99b;
  --trace-green-soft: #173c32;
  --trace-amber: #e7b86b;
  --trace-amber-soft: #46351e;
  --trace-red: #f18181;
  --trace-red-soft: #48282b;
  --trace-purple: #b69cff;
  --trace-purple-soft: #332b53;
  --trace-cyan: #64c6da;
  --trace-cyan-soft: #173a44;
}
.trace-stream-bar,
.trace-toolbar,
.trace-section-heading,
.trace-step-topline,
.trace-graph-heading,
.trace-graph-selected {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}
.trace-stream-bar {
  min-height: 26px;
  margin-bottom: 8px;
  color: var(--trace-muted);
  font-size: 11px;
}
.trace-live-state {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  font-weight: 600;
}
.trace-live-dot,
.trace-toggle-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--trace-green);
}
.trace-live-state[data-state='connecting'] .trace-live-dot,
.trace-live-state[data-state='reconnecting'] .trace-live-dot {
  background: var(--trace-amber);
  animation: trace-pulse 1.2s infinite;
}
@keyframes trace-pulse {
  50% {
    opacity: 0.35;
  }
}
.trace-new-events,
.trace-follow,
.trace-icon-button,
.trace-graph-actions button,
.trace-graph-selected button {
  border: 1px solid var(--trace-border);
  border-radius: 5px;
  color: var(--trace-secondary);
  background: var(--trace-surface);
  cursor: pointer;
}
.trace-new-events,
.trace-follow {
  min-height: 30px;
  padding: 0 10px;
}
.trace-new-events {
  color: var(--trace-blue);
}
.trace-follow {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}
.trace-follow.active {
  color: var(--trace-green);
  background: var(--trace-green-soft);
}
.trace-icon-button {
  display: grid;
  place-items: center;
  width: 30px;
  height: 30px;
}
.trace-summary {
  display: grid;
  grid-template-columns: repeat(6, minmax(0, 1fr));
  gap: 8px;
  margin: 0 0 16px;
}
.trace-summary div {
  min-height: 68px;
  padding: 12px 13px;
  border: 1px solid var(--trace-border);
  border-radius: 7px;
  background: var(--trace-surface);
}
.trace-summary dt {
  color: var(--trace-muted);
  font-size: 11px;
}
.trace-summary dd {
  margin: 5px 0 0;
  font-size: 18px;
  font-weight: 650;
  font-variant-numeric: tabular-nums;
}
.trace-child-progress {
  margin-bottom: 16px;
  padding: 12px 14px;
  border: 1px solid var(--trace-border);
  border-radius: 7px;
  background: var(--trace-surface);
}
.trace-section-heading {
  margin-bottom: 9px;
  font-size: 12px;
}
.trace-section-heading span {
  color: var(--trace-muted);
  font-size: 11px;
}
.trace-child-list {
  display: grid;
  gap: 4px;
}
.trace-child-row {
  display: grid;
  grid-template-columns: 8px minmax(100px, 160px) minmax(0, 1fr) auto;
  align-items: center;
  gap: 9px;
  padding: 7px 0;
  border-top: 1px solid var(--trace-soft);
  font-size: 11px;
}
.trace-child-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--trace-blue);
}
.trace-child-dot[data-status='done'] {
  background: var(--trace-green);
}
.trace-child-dot[data-status='failed'] {
  background: var(--trace-red);
}
.trace-child-task,
.trace-child-meta {
  color: var(--trace-muted);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.trace-toolbar {
  flex-wrap: wrap;
  padding: 11px 13px;
  border: 1px solid var(--trace-border);
  border-radius: 7px 7px 0 0;
  background: var(--trace-surface);
}
.trace-tabs,
.trace-controls,
.trace-graph-actions {
  display: flex;
  align-items: center;
  gap: 6px;
}
.trace-tabs button,
.trace-graph-actions button {
  min-height: 29px;
  padding: 0 10px;
  border: 0;
  border-radius: 4px;
  color: var(--trace-muted);
  background: transparent;
  cursor: pointer;
  font-size: 12px;
}
.trace-tabs button.active,
.trace-tabs button:hover,
.trace-graph-actions button.active {
  color: var(--trace-text);
  background: var(--trace-blue-soft);
  box-shadow: inset 0 -2px 0 var(--trace-blue);
}
.trace-controls {
  flex-wrap: wrap;
}
.trace-search {
  display: flex;
  align-items: center;
  gap: 6px;
  width: 200px;
  height: 30px;
  padding: 0 9px;
  border: 1px solid var(--trace-border);
  border-radius: 5px;
  color: var(--trace-subtle);
  background: var(--trace-canvas);
}
.trace-search input {
  min-width: 0;
  width: 100%;
  border: 0;
  outline: 0;
  background: transparent;
  font-size: 11px;
}
.trace-filter {
  height: 30px;
  padding: 0 24px 0 8px;
  border: 1px solid var(--trace-border);
  border-radius: 5px;
  color: var(--trace-secondary);
  background: var(--trace-canvas);
}
.trace-layout {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 365px;
  min-height: 560px;
  border: 1px solid var(--trace-border);
  border-top: 0;
  background: var(--trace-surface);
}
.trace-timeline {
  min-width: 0;
  max-height: 650px;
  overflow: auto;
}
.trace-timeline-head {
  display: grid;
  grid-template-columns: 76px 1fr;
  gap: 18px;
  min-height: 44px;
  align-items: center;
  padding: 0 18px;
  border-bottom: 1px solid var(--trace-border);
  color: var(--trace-muted);
  font-size: 10px;
  letter-spacing: 0.07em;
  text-transform: uppercase;
}
.trace-step-row {
  position: relative;
  display: grid;
  grid-template-columns: 76px 1fr;
  gap: 18px;
  padding: 10px 18px;
  cursor: pointer;
}
.trace-step-row::before {
  content: '';
  position: absolute;
  top: 0;
  bottom: 0;
  left: 94px;
  width: 1px;
  background: var(--trace-border);
}
.trace-step-time {
  z-index: 1;
  padding-top: 14px;
  color: var(--trace-muted);
  font:
    11px ui-monospace,
    SFMono-Regular,
    Menlo,
    monospace;
  text-align: right;
}
.trace-step-marker {
  position: absolute;
  z-index: 2;
  top: 23px;
  left: 90px;
  width: 9px;
  height: 9px;
  border: 2px solid var(--trace-surface);
  border-radius: 50%;
  background: var(--trace-muted);
  box-shadow: 0 0 0 1px var(--trace-border);
}
.trace-step-marker.type-model {
  background: var(--trace-purple);
}
.trace-step-marker.type-retrieval {
  background: var(--trace-cyan);
}
.trace-step-marker.type-decision {
  background: var(--trace-amber);
}
.trace-step-marker.type-tool {
  background: var(--trace-blue);
}
.trace-step-marker.type-artifact {
  background: var(--trace-green);
}
.trace-step-card {
  min-width: 0;
  padding: 13px 14px 12px 16px;
  border: 1px solid var(--trace-border);
  border-radius: 7px;
  background: var(--trace-raised);
  transition: 0.15s;
}
.trace-step-row:hover .trace-step-card {
  border-color: var(--trace-blue);
}
.trace-step-row[data-selected='true'] .trace-step-card {
  border-color: var(--trace-blue);
  background: var(--trace-blue-soft);
  box-shadow: inset 3px 0 var(--trace-blue);
}
.trace-step-main {
  display: flex;
  min-width: 0;
  align-items: center;
  gap: 10px;
}
.trace-type-icon {
  display: grid;
  place-items: center;
  flex: 0 0 27px;
  width: 27px;
  height: 27px;
  border-radius: 6px;
}
.trace-type-icon.type-model {
  color: var(--trace-purple);
  background: var(--trace-purple-soft);
}
.trace-type-icon.type-retrieval {
  color: var(--trace-cyan);
  background: var(--trace-cyan-soft);
}
.trace-type-icon.type-decision {
  color: var(--trace-amber);
  background: var(--trace-amber-soft);
}
.trace-type-icon.type-tool {
  color: var(--trace-blue);
  background: var(--trace-blue-soft);
}
.trace-type-icon.type-artifact {
  color: var(--trace-green);
  background: var(--trace-green-soft);
}
.trace-step-heading {
  min-width: 0;
}
.trace-step-type,
.trace-inspector-kicker {
  display: block;
  color: var(--trace-muted);
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0.07em;
  text-transform: uppercase;
}
.trace-step-heading strong {
  display: block;
  margin-top: 2px;
  overflow: hidden;
  font-size: 13px;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.trace-status {
  flex: none;
  padding: 3px 7px;
  border-radius: 12px;
  color: var(--trace-green);
  background: var(--trace-green-soft);
  font-size: 10px;
  font-weight: 600;
}
.trace-status[data-status='running'] {
  color: var(--trace-blue);
  background: var(--trace-blue-soft);
}
.trace-status[data-status='failed'],
.trace-status[data-status='cancelled'] {
  color: var(--trace-red);
  background: var(--trace-red-soft);
}
.trace-step-summary {
  margin: 8px 0 0;
  color: var(--trace-secondary);
  font-size: 12px;
}
.trace-step-metrics {
  display: flex;
  flex-wrap: wrap;
  gap: 7px 14px;
  margin-top: 10px;
  color: var(--trace-muted);
  font-size: 11px;
}
.trace-step-metrics span {
  display: inline-flex;
  align-items: center;
  gap: 5px;
}
.trace-load-more,
.trace-empty {
  padding: 24px;
  text-align: center;
}
.trace-empty {
  color: var(--trace-muted);
}
.trace-inspector {
  min-width: 0;
  border-left: 1px solid var(--trace-border);
  background: var(--trace-surface);
}
.trace-inspector-empty {
  display: grid;
  place-items: center;
  padding: 20px;
  color: var(--trace-muted);
  font-size: 12px;
  text-align: center;
}
.trace-inspector-header {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  padding: 19px 19px 15px;
  border-bottom: 1px solid var(--trace-border);
}
.trace-inspector-header h3 {
  margin: 4px 0 3px;
  font-size: 15px;
}
.trace-inspector-header code {
  color: var(--trace-subtle);
  font-size: 10px;
}
.trace-close {
  display: grid;
  place-items: center;
  width: 26px;
  height: 26px;
  border: 0;
  border-radius: 4px;
  color: var(--trace-muted);
  background: transparent;
  cursor: pointer;
}
.trace-inspector-tabs {
  display: flex;
  gap: 3px;
  padding: 8px 12px;
  border-bottom: 1px solid var(--trace-soft);
}
.trace-inspector-tabs button {
  padding: 5px 8px;
  border: 0;
  border-radius: 4px;
  color: var(--trace-muted);
  background: transparent;
  cursor: pointer;
  font-size: 11px;
}
.trace-inspector-tabs button.active {
  color: var(--trace-text);
  background: var(--trace-blue-soft);
}
.trace-inspector-body {
  max-height: 560px;
  overflow: auto;
  padding: 17px 19px 25px;
}
.trace-detail-section + .trace-detail-section {
  margin-top: 21px;
}
.trace-detail-section h4 {
  margin: 0 0 9px;
  color: var(--trace-muted);
  font-size: 10px;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}
.trace-detail-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 7px;
}
.trace-detail-grid div,
.trace-score,
.trace-notice {
  padding: 10px;
  border: 1px solid var(--trace-soft);
  border-radius: 5px;
  background: var(--trace-canvas);
}
.trace-detail-grid span,
.trace-score span {
  display: block;
  color: var(--trace-muted);
  font-size: 10px;
}
.trace-detail-grid strong,
.trace-score strong {
  display: block;
  margin-top: 3px;
  font-size: 13px;
}
.trace-detail-grid strong[data-status='failed'],
.trace-detail-grid strong[data-status='cancelled'] {
  color: var(--trace-red);
}
.trace-notice {
  display: flex;
  gap: 8px;
  color: var(--trace-secondary);
  font-size: 11px;
}
.trace-score {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px;
}
.trace-score-bar {
  grid-column: 1/-1;
  height: 4px;
  overflow: hidden;
  border-radius: 3px;
  background: var(--trace-border);
}
.trace-score-bar span {
  display: block;
  height: 100%;
  background: var(--trace-cyan);
}
.trace-source-list,
.trace-detail-list {
  border-top: 1px solid var(--trace-soft);
}
.trace-source-list div,
.trace-detail-list div {
  display: flex;
  gap: 8px;
  padding: 8px 0;
  border-bottom: 1px solid var(--trace-soft);
  font-size: 11px;
}
.trace-source-list span {
  color: var(--trace-cyan);
  font-weight: 700;
}
.trace-source-list strong {
  flex: 1;
  color: var(--trace-secondary);
}
.trace-source-list small {
  color: var(--trace-muted);
}
.trace-detail-list dt {
  color: var(--trace-muted);
}
.trace-detail-list dd {
  flex: 1;
  margin: 0;
  color: var(--trace-secondary);
  text-align: right;
  overflow-wrap: anywhere;
}
.trace-graph {
  padding: 22px;
  border: 1px solid var(--trace-border);
  border-top: 0;
  background: var(--trace-canvas);
}
.trace-graph-heading {
  align-items: flex-start;
}
.trace-graph-heading h3 {
  margin: 0;
  font-size: 15px;
}
.trace-graph-heading p {
  margin: 4px 0 0;
  color: var(--trace-muted);
  font-size: 11px;
}
.trace-graph-actions {
  flex-wrap: wrap;
}
.trace-graph-actions button {
  min-height: 28px;
  padding: 0 9px;
}
.trace-zoom {
  color: var(--trace-muted);
  font:
    10px ui-monospace,
    SFMono-Regular,
    Menlo,
    monospace;
}
.trace-graph-viewport {
  margin-top: 16px;
  overflow: auto;
  border: 1px solid var(--trace-border);
  border-radius: 7px;
}
.trace-graph-canvas {
  min-width: 860px;
  min-height: 440px;
  padding: 60px 35px 35px;
  background-color: var(--trace-surface);
  background-image: radial-gradient(var(--trace-soft) 0.7px, transparent 0.7px);
  background-size: 18px 18px;
  transform-origin: top left;
}
.trace-graph-nodes {
  display: grid;
  grid-template-columns: repeat(4, minmax(170px, 1fr));
  gap: 16px;
  align-items: start;
}
.trace-graph-node {
  position: relative;
  min-height: 120px;
  padding: 12px;
  border: 1px solid var(--trace-border);
  border-radius: 7px;
  color: var(--trace-text);
  background: var(--trace-raised);
  box-shadow: 0 8px 20px rgb(36 45 67 / 8%);
  cursor: pointer;
  text-align: left;
}
.trace-graph-node:not(.is-last)::after {
  position: absolute;
  top: 50%;
  right: -20px;
  color: var(--trace-subtle);
  content: '→';
  font-size: 16px;
  transform: translateY(-50%);
}
.layout-calls .trace-graph-node:not(.is-last)::after {
  color: var(--trace-amber);
  content: '↳';
}
.trace-graph-node.selected {
  border-color: var(--trace-blue);
  box-shadow: 0 0 0 2px rgb(55 90 217 / 15%);
}
.trace-graph-node > span {
  display: flex;
  align-items: center;
  gap: 6px;
  color: var(--trace-muted);
  font-size: 10px;
  font-weight: 700;
  text-transform: uppercase;
}
.trace-graph-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--trace-blue);
}
.trace-graph-node strong {
  display: block;
  margin-top: 8px;
  font-size: 12px;
}
.trace-graph-node small,
.trace-graph-node em {
  display: block;
  margin-top: 8px;
  color: var(--trace-muted);
  font-size: 10px;
  font-style: normal;
}
.trace-graph-node em {
  color: var(--trace-green);
}
.trace-graph-selected {
  min-height: 62px;
  margin-top: 12px;
  padding: 11px 13px;
  border: 1px solid var(--trace-border);
  border-radius: 6px;
  background: var(--trace-surface);
}
.trace-graph-selected > div {
  min-width: 0;
}
.trace-graph-selected span {
  display: block;
  color: var(--trace-muted);
  font-size: 9px;
  text-transform: uppercase;
}
.trace-graph-selected strong,
.trace-graph-selected small {
  display: block;
  margin-top: 3px;
}
.trace-graph-selected small {
  color: var(--trace-muted);
  font-size: 10px;
}
.trace-graph-selected button {
  flex: none;
  min-height: 28px;
  padding: 0 10px;
}
@media (max-width: 900px) {
  .trace-summary {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }
  .trace-layout {
    display: block;
  }
  .trace-inspector {
    min-height: 420px;
    border-top: 1px solid var(--trace-border);
    border-left: 0;
  }
  .trace-child-row {
    grid-template-columns: 8px minmax(90px, 140px) minmax(0, 1fr);
  }
  .trace-child-meta {
    grid-column: 2/-1;
  }
}
@media (max-width: 560px) {
  .trace-summary {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .trace-toolbar {
    align-items: flex-start;
  }
  .trace-controls {
    width: 100%;
  }
  .trace-search {
    flex: 1;
    width: auto;
  }
  .trace-timeline-head {
    grid-template-columns: 58px 1fr;
    padding-inline: 12px;
  }
  .trace-step-row {
    grid-template-columns: 58px 1fr;
    gap: 12px;
    padding-inline: 12px;
  }
  .trace-step-row::before {
    left: 70px;
  }
  .trace-step-marker {
    left: 66px;
  }
  .trace-step-topline {
    display: block;
  }
  .trace-status {
    display: inline-flex;
    margin-top: 8px;
  }
  .trace-graph {
    padding: 14px;
  }
}
</style>
