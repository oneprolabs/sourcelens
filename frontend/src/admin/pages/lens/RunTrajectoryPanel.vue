<template>
  <section data-testid="run-trajectory-workbench" class="run-trajectory">
    <BaseLoading v-if="loading && events.length === 0" />

    <template v-else>
      <dl class="trajectory-stats" data-testid="trajectory-summary">
        <div class="stat">
          <dt>{{ t('lensRuns.trajectoryEvents') }}</dt>
          <dd>{{ summary.event_count || 0 }}</dd>
        </div>
        <div class="stat">
          <dt>{{ t('lensRuns.trajectoryDuration') }}</dt>
          <dd>{{ durationText(summary.duration_ms) }}</dd>
        </div>
        <div class="stat">
          <dt>{{ t('lensRuns.trajectoryModels') }}</dt>
          <dd>{{ summary.model_calls || 0 }}</dd>
        </div>
        <div class="stat">
          <dt>{{ t('lensRuns.trajectoryTools') }}</dt>
          <dd>{{ summary.tool_calls || 0 }}</dd>
        </div>
        <div class="stat">
          <dt>{{ t('lensRuns.totalTokens') }}</dt>
          <dd>{{ (summary.total_tokens || 0).toLocaleString() }}</dd>
        </div>
        <div class="stat stat-error">
          <dt>{{ t('lensRuns.trajectoryErrors') }}</dt>
          <dd>{{ summary.error_count || 0 }}</dd>
        </div>
      </dl>

      <div class="trajectory-toolbar" role="toolbar">
        <div class="toolbar-actions">
          <button
            v-for="item in categoryOptions"
            :key="item.value"
            type="button"
            class="toolbar-toggle"
            :aria-pressed="category === item.value"
            @click="setCategory(item.value)"
          >
            {{ item.label }}
            <span class="toolbar-count">{{ item.count }}</span>
          </button>
          <span class="toolbar-separator" aria-hidden="true" />
          <button
            type="button"
            class="toolbar-toggle"
            :disabled="collapsibleCallIds.length === 0"
            :aria-pressed="allCallsCollapsed"
            :aria-label="
              collapsibleCallIds.length === 0
                ? t('lensRuns.trajectoryNoCollapsibleCalls')
                : allCallsCollapsed
                  ? t('lensRuns.trajectoryExpandAllCalls')
                  : t('lensRuns.trajectoryCollapseAllCalls')
            "
            :title="
              collapsibleCallIds.length === 0
                ? t('lensRuns.trajectoryNoCollapsibleCalls')
                : allCallsCollapsed
                  ? t('lensRuns.trajectoryExpandAllCalls')
                  : t('lensRuns.trajectoryCollapseAllCalls')
            "
            @click="toggleAllCalls"
          >
            <span class="toolbar-glyph" aria-hidden="true">
              {{ allCallsCollapsed ? '⊞' : '⊟' }}
            </span>
            {{ t('lensRuns.trajectoryCalls') }}
          </button>
          <button
            type="button"
            class="toolbar-toggle"
            :aria-pressed="showSnapshots"
            :aria-label="
              showSnapshots
                ? t('lensRuns.trajectoryHideSnapshots')
                : t('lensRuns.trajectoryShowSnapshots')
            "
            :title="
              showSnapshots
                ? t('lensRuns.trajectoryHideSnapshots')
                : t('lensRuns.trajectoryShowSnapshots')
            "
            @click="showSnapshots = !showSnapshots"
          >
            <span class="toolbar-glyph" aria-hidden="true">
              {{ showSnapshots ? '⊞' : '⊟' }}
            </span>
            {{ t('lensRuns.trajectorySnapshots') }}
          </button>
        </div>
        <div class="toolbar-search">
          <Search :size="11" class="search-icon" aria-hidden="true" />
          <input
            v-model="query"
            data-testid="trajectory-search"
            class="search-input"
            type="search"
            :placeholder="t('lensRuns.trajectorySearch')"
          />
        </div>
      </div>

      <TrajectoryTimeline
        v-if="events.length"
        :lanes="timelineLanes"
        :boundaries="groupBoundaries"
        :selected-sequence="selectedEvent ? selectedEvent.sequence : null"
        :range="timelineRange"
        data-testid="trajectory-time-overview"
        @range-change="timelineRange = $event"
        @select-event="onTimelineSelect"
      />

      <div
        v-if="rows.length"
        data-testid="trajectory-ledger"
        class="trajectory-split"
        :class="{ 'trajectory-split-resizing': resizeActive }"
        ref="splitRef"
      >
        <div ref="tablePaneRef" class="table-pane sl-scrollbar">
          <table class="trajectory-table">
            <thead>
              <tr>
                <th class="event-header">Event</th>
                <th>Content</th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="(row, index) in rows"
                :key="row.event.event_id"
                class="ledger-row"
                :class="{ 'turn-start-row': index === 0 }"
                :data-turn-start="index === 0 || undefined"
                :data-turn-end="index === rows.length - 1 || undefined"
                :data-selected="isSelected(row.event) || undefined"
                :data-error="isErrorEvent(row.event) || undefined"
                :style="rowIndentStyle(row)"
                @click="selectEvent(row.event)"
                @keydown.enter="selectEvent(row.event)"
              >
                <td class="event-cell">
                  <span class="turn-rail" aria-hidden="true" />
                  <span
                    v-if="index === 0"
                    class="request-dot"
                    aria-hidden="true"
                  />
                  <span class="seq">#{{ row.event.sequence }}</span>
                  <span class="kind-tag" :class="tagClass(row.event)">
                    <span class="kind-tag-label">{{
                      kindLabel(row.event)
                    }}</span>
                  </span>
                </td>
                <td class="content-cell">
                  <button
                    v-if="row.hasChildren"
                    type="button"
                    class="row-expand"
                    :aria-label="t('lensRuns.trajectoryToggle')"
                    @click.stop="toggleCall(row.event.call_id)"
                  >
                    <ChevronRight
                      v-if="collapsed.has(row.event.call_id)"
                      :size="14"
                    />
                    <ChevronDown v-else :size="14" />
                  </button>
                  <span class="content-text">
                    <span
                      v-if="toolCallText(row.event)"
                      class="content-title content-title-code"
                    >
                      {{ toolCallText(row.event).name }}
                    </span>
                    <span
                      v-else
                      class="content-title"
                      :class="{
                        'content-title-error': isErrorEvent(row.event)
                      }"
                    >
                      {{ eventTitle(row.event) }}
                    </span>
                    <span
                      v-if="toolCallText(row.event)?.args"
                      class="content-args"
                    >
                      {{ toolCallText(row.event).args }}
                    </span>
                  </span>
                  <span class="content-trailing">
                    <span
                      class="content-metrics"
                      :class="{
                        'content-metrics-error': isErrorEvent(row.event)
                      }"
                    >
                      {{ eventMetric(row.event) }}
                    </span>
                    <time class="content-time">
                      {{ timeText(row.event.timestamp) }}
                    </time>
                  </span>
                </td>
              </tr>
            </tbody>
          </table>
        </div>

        <div
          v-if="selectedEvent"
          class="trajectory-resize-handle"
          role="separator"
          aria-label="Resize inspector"
          aria-orientation="vertical"
          tabindex="0"
          @pointerdown="startInspectorResize"
          @pointermove="resizeInspector"
          @lostpointercapture="finishInspectorResize"
          @keydown="resizeInspectorWithKeyboard"
        />

        <aside
          v-if="selectedEvent"
          ref="inspectorRef"
          class="trajectory-inspector"
          data-testid="trajectory-inspector"
          :style="inspectorStyle"
        >
          <div class="inspector-header">
            <div class="inspector-title">
              <span class="inspector-dot" aria-hidden="true" />
              <span class="inspector-name">{{
                eventTitle(selectedEvent)
              }}</span>
              <span class="inspector-location">{{
                selectedEvent.event_type
              }}</span>
            </div>
            <div class="inspector-header-meta">
              <span class="inspector-sequence"
                >#{{ selectedEvent.sequence }}</span
              >
              <span class="kind-tag" :class="tagClass(selectedEvent)">
                {{ kindLabel(selectedEvent) }}
              </span>
            </div>
            <button
              type="button"
              class="inspector-close"
              aria-label="Close"
              @click="selectedEvent = null"
            >
              ×
            </button>
          </div>
          <div class="inspector-tabs" role="tablist">
            <button
              v-for="tab in inspectorTabs"
              :key="tab.id"
              type="button"
              role="tab"
              class="inspector-tab"
              :class="{ 'inspector-tab-active': inspectorTab === tab.id }"
              @click="inspectorTab = tab.id"
            >
              {{ tab.label }}
            </button>
          </div>
          <div class="inspector-body">
            <template v-if="inspectorTab === 'summary'">
              <div class="inspector-event-card">
                <div class="inspector-event-card-title">
                  {{ eventTitle(selectedEvent) }}
                </div>
                <div class="inspector-event-card-type">
                  {{ selectedEvent.event_type }}
                </div>
                <div class="inspector-event-chips">
                  <span :class="statusClass(selectedEvent)">{{
                    statusLabel(selectedEvent)
                  }}</span>
                  <span class="inspector-chip"
                    >Sequence {{ selectedEvent.sequence }}</span
                  >
                  <span
                    v-if="selectedEvent.attempt != null"
                    class="inspector-chip"
                  >
                    Attempt {{ selectedEvent.attempt }}
                  </span>
                </div>
              </div>
              <dl class="overview">
                <div>
                  <dt>Hierarchy</dt>
                  <dd class="mono hierarchy-value">
                    <span v-if="selectedEvent.call_id"
                      >Call {{ selectedEvent.call_id }}</span
                    >
                    <span v-if="selectedEvent.parent_call_id"
                      >Parent {{ selectedEvent.parent_call_id }}</span
                    >
                    <span
                      v-if="
                        !selectedEvent.call_id && !selectedEvent.parent_call_id
                      "
                      >-</span
                    >
                  </dd>
                </div>
                <div>
                  <dt>Timestamp</dt>
                  <dd class="mono wrap-value">{{ selectedEvent.timestamp }}</dd>
                </div>
              </dl>
              <section
                v-if="
                  inspectorInput(selectedEvent) !== undefined ||
                  inspectorOutput(selectedEvent) !== undefined
                "
                class="overview-section inspector-io-section"
              >
                <h4 class="overview-heading">输入 / 输出</h4>
                <div
                  v-if="inspectorInput(selectedEvent) !== undefined"
                  class="inspector-data-block"
                >
                  <span class="inspector-data-label">Input</span>
                  <pre>{{ inspectorValue(inspectorInput(selectedEvent)) }}</pre>
                </div>
                <div
                  v-if="inspectorOutput(selectedEvent) !== undefined"
                  class="inspector-data-block"
                >
                  <span class="inspector-data-label">Output</span>
                  <pre>{{
                    inspectorValue(inspectorOutput(selectedEvent))
                  }}</pre>
                </div>
              </section>
              <section class="overview-section">
                <h4 class="overview-heading">
                  {{ t('lensRuns.trajectoryTiming') }}
                </h4>
                <dl class="overview">
                  <div>
                    <dt>Duration</dt>
                    <dd>
                      {{ durationText(selectedEvent.payload?.duration_ms) }}
                    </dd>
                  </div>
                  <div v-if="selectedEvent.payload?.ttft_ms != null">
                    <dt>TTFT</dt>
                    <dd>{{ durationText(selectedEvent.payload.ttft_ms) }}</dd>
                  </div>
                  <div
                    v-if="selectedEvent.payload?.usage?.total_tokens != null"
                  >
                    <dt>Tokens</dt>
                    <dd>{{ selectedEvent.payload.usage.total_tokens }}</dd>
                  </div>
                  <div
                    v-if="selectedEvent.payload?.usage?.input_tokens != null"
                  >
                    <dt class="indent">Input</dt>
                    <dd>{{ selectedEvent.payload.usage.input_tokens }}</dd>
                  </div>
                  <div
                    v-if="selectedEvent.payload?.usage?.output_tokens != null"
                  >
                    <dt class="indent">Output</dt>
                    <dd>{{ selectedEvent.payload.usage.output_tokens }}</dd>
                  </div>
                  <div
                    v-if="
                      selectedEvent.payload?.usage?.reasoning_tokens != null
                    "
                  >
                    <dt class="indent">Reasoning</dt>
                    <dd>
                      {{ selectedEvent.payload.usage.reasoning_tokens }}
                    </dd>
                  </div>
                </dl>
              </section>
            </template>
            <JsonTree
              v-else-if="inspectorTab === 'payload'"
              :data="selectedEvent.payload"
              :indent="8"
            />
            <JsonTree v-else :data="selectedEvent" :indent="8" />
          </div>
        </aside>
      </div>

      <p v-else class="trajectory-empty" data-testid="trajectory-empty">
        {{ t('lensRuns.noTimeline') }}
      </p>

      <div v-if="hasMore" class="trajectory-load-more">
        <BaseButton
          variant="outline"
          size="sm"
          :loading="loading"
          @click="loadMore"
        >
          {{ t('lensRuns.trajectoryLoadMore') }}
        </BaseButton>
      </div>
    </template>
  </section>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { ChevronDown, ChevronRight, Search } from '@lucide/vue'
import { useI18n } from 'vue-i18n'
import { getAdminRunTrajectory } from '@/api/lens'
import { useToast } from '@/composables/useToast'
import { extractErrorMessage } from '@/utils/api'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseLoading from '@/components/ui/BaseLoading.vue'
import JsonTree from '@/components/ui/JsonTree.vue'
import TrajectoryTimeline from './TrajectoryTimeline.vue'
import {
  buildTimelineLanes,
  buildTrajectoryRows,
  clampInspectorWidth,
  eventCategory,
  groupTrajectoryRows
} from './runTrajectory'

const props = defineProps({
  runUuid: { type: String, default: '' },
  active: { type: Boolean, default: false }
})

const { t } = useI18n()
const { showError } = useToast()

const events = ref([])
const summary = ref({})
const loading = ref(false)
const hasMore = ref(false)
const query = ref('')
const category = ref('all')
const selectedEvent = ref(null)
const collapsed = ref(new Set())
const timelineRange = ref(null)
const inspectorTab = ref('summary')
const tablePaneRef = ref(null)
const splitRef = ref(null)
const inspectorRef = ref(null)
const inspectorWidth = ref(null)
const resizeActive = ref(false)
const showSnapshots = ref(false)

let filterTimer = null
let requestId = 0
let resizeOriginX = 0
let resizeOriginWidth = 0

const inspectorStyle = computed(() =>
  inspectorWidth.value === null
    ? undefined
    : {
        '--trajectory-inspector-width': `${inspectorWidth.value}px`
      }
)

const KIND_BY_CATEGORY = {
  model: 'model',
  tool: 'tool',
  subtool: 'subtool',
  user: 'user',
  context: 'context',
  request: 'context',
  compaction: 'compacted',
  compacted: 'compacted',
  retry: 'retry',
  checkpoint: 'checkpoint',
  cancelled: 'cancelled',
  system: 'system',
  run: 'system',
  step: 'step'
}

const KIND_LABEL = {
  system: 'SYSTEM',
  user: 'USER',
  context: 'CONTEXT',
  compacted: 'COMPACTED',
  model: 'ASSISTANT',
  tool: 'TOOL',
  subtool: 'SUBTOOL',
  retry: 'RETRY',
  checkpoint: 'CHECKPOINT',
  cancelled: 'CANCELLED',
  step: 'STEP'
}

const categoryOptions = computed(() => {
  const counts = summary.value.categories || {}
  const hiddenCategories = new Set(['checkpoint', 'system', 'user', 'run'])
  return [
    {
      value: 'all',
      label: t('lensRuns.trajectoryAll'),
      count: summary.value.event_count || 0
    },
    ...Object.entries(counts)
      .filter(([value]) => !hiddenCategories.has(value))
      .map(([value, count]) => ({ value, label: value, count }))
  ]
})

const SNAPSHOT_EVENT_TYPES = new Set(['system.snapshot', 'tools.snapshot'])

const baseEvents = computed(() => {
  if (showSnapshots.value) return events.value
  return events.value.filter(
    (event) => !SNAPSHOT_EVENT_TYPES.has(event.event_type)
  )
})

const filteredEvents = computed(() => {
  if (!timelineRange.value) return baseEvents.value
  return baseEvents.value.filter((event) => !isOutsideTimelineRange(event))
})

const rows = computed(() =>
  buildTrajectoryRows(filteredEvents.value, collapsed.value)
)

const groupedRows = computed(() => groupTrajectoryRows(rows.value))

const timelineLanes = computed(() =>
  buildTimelineLanes(baseEvents.value, summary.value)
)

const timelineDomain = computed(() => {
  const times = []
  for (const lane of timelineLanes.value) {
    for (const step of lane.steps) {
      times.push(step.startMs, step.startMs + step.durationMs)
    }
  }
  const start = Math.min(...times)
  const end = Math.max(...times)
  return { start, end, duration: Math.max(1, end - start) }
})

const groupBoundaries = computed(() => {
  const domain = timelineDomain.value
  if (!Number.isFinite(domain.duration)) return []
  return groupedRows.value
    .map((group) => {
      const time = eventTime(group.rows[0]?.event)
      if (!Number.isFinite(time)) return null
      return { time }
    })
    .filter((boundary) => boundary !== null)
})

const collapsibleCallIds = computed(() =>
  rows.value.filter((row) => row.hasChildren).map((row) => row.event.call_id)
)

const allCallsCollapsed = computed(
  () =>
    collapsibleCallIds.value.length > 0 &&
    collapsibleCallIds.value.every((id) => collapsed.value.has(id))
)

const inspectorTabs = computed(() => [
  { id: 'summary', label: t('lensRuns.trajectoryInspectorSummary') },
  { id: 'payload', label: t('lensRuns.trajectoryInspectorPayload') },
  { id: 'raw', label: t('lensRuns.trajectoryInspectorRaw') }
])

function kindOf(event) {
  const categoryValue = eventCategory(event)
  return KIND_BY_CATEGORY[categoryValue] || 'system'
}

function kindLabel(event) {
  return KIND_LABEL[kindOf(event)] || 'EVENT'
}

function tagClass(event) {
  return `tag-${kindOf(event)}`
}

function isErrorEvent(event) {
  const status = String(event.event_type || '')
    .split('.')
    .pop()
  return ['failed', 'cancelled', 'interrupted'].includes(status)
}

function isSelected(event) {
  return selectedEvent.value?.sequence === event.sequence
}

const eventTimeRange = computed(() => {
  const map = new Map()
  for (const lane of timelineLanes.value) {
    for (const step of lane.steps) {
      const start = step.startMs
      const end = start + step.durationMs
      for (const seq of step.seqs || [step.event.sequence]) {
        const previous = map.get(seq)
        if (previous) {
          previous.start = Math.min(previous.start, start)
          previous.end = Math.max(previous.end, end)
        } else {
          map.set(seq, { start, end })
        }
      }
    }
  }
  return map
})

function eventTime(event) {
  if (event._ms !== undefined && Number.isFinite(event._ms)) return event._ms
  return new Date(event.timestamp).getTime()
}

function isOutsideTimelineRange(event) {
  if (!timelineRange.value) return false
  const span = eventTimeRange.value.get(event.sequence)
  if (!span) {
    const time = eventTime(event)
    if (!Number.isFinite(time)) return false
    return time < timelineRange.value.start || time > timelineRange.value.end
  }
  return (
    span.end < timelineRange.value.start || span.start > timelineRange.value.end
  )
}

function latestSequence(items) {
  return items.reduce((latest, event) => {
    const sequence = Number(event?.sequence)
    return Number.isFinite(sequence) ? Math.max(latest, sequence) : latest
  }, 0)
}

function toolCallText(event) {
  const kind = kindOf(event)
  if (kind !== 'tool' && kind !== 'subtool') return null
  const text = eventTitle(event)
  const separator = text.indexOf(' · ')
  if (separator === -1) return { name: text, args: '' }
  return { name: text.slice(0, separator), args: text.slice(separator + 3) }
}

function eventTitle(event) {
  return event.payload?.name || event.payload?.model_ref || event.event_type
}

function eventMetric(event) {
  const payload = event.payload || {}
  const parts = []
  if (payload.duration_ms != null) parts.push(durationText(payload.duration_ms))
  if (payload.ttft_ms != null)
    parts.push(`TTFT ${durationText(payload.ttft_ms)}`)
  if (payload.usage?.total_tokens != null) {
    parts.push(`${payload.usage.total_tokens} tokens`)
  }
  return parts.join(' · ')
}

function inspectorInput(event) {
  const payload = event?.payload || {}
  return payload.arguments ?? payload.input ?? payload.params ?? payload.request
}

function inspectorOutput(event) {
  const payload = event?.payload || {}
  return payload.result ?? payload.output ?? payload.response
}

function inspectorValue(value) {
  if (typeof value === 'string') return value.slice(0, 2400)
  try {
    return JSON.stringify(value, null, 2).slice(0, 2400)
  } catch {
    return String(value)
  }
}

function statusClass(event) {
  const base = 'status-badge'
  const status = String(event.event_type || '')
    .split('.')
    .pop()
  if (['failed', 'cancelled', 'interrupted'].includes(status)) {
    return `${base} status-badge-error`
  }
  if (['completed', 'done'].includes(status)) {
    return `${base} status-badge-success`
  }
  return `${base} status-badge-neutral`
}

function statusLabel(event) {
  const status = String(event.event_type || '')
    .split('.')
    .pop()
  if (['failed', 'cancelled', 'interrupted'].includes(status)) return 'Failed'
  if (['completed', 'done'].includes(status)) return 'Completed'
  return status || 'Pending'
}

function durationText(value) {
  if (value === null || value === undefined) return '-'
  if (value < 1000) return `${Math.round(value)}ms`
  return `${(value / 1000).toFixed(1)}s`
}

function timeText(value) {
  if (!value) return '-'
  const date = new Date(value)
  return Number.isNaN(date.getTime())
    ? String(value)
    : date.toLocaleTimeString([], { hour12: false })
}

function selectEvent(event) {
  selectedEvent.value = event
}

function rowIndentStyle(row) {
  const depth = Math.min(Math.max(Number(row.depth) || 0, 0), 8)
  return { '--trajectory-indent': `${depth * 18}px` }
}

function setInspectorWidth(width) {
  const splitWidth = splitRef.value?.clientWidth
  if (!splitWidth) return
  inspectorWidth.value = clampInspectorWidth(splitWidth, width)
}

function startInspectorResize(event) {
  if (event.button !== 0) return
  event.preventDefault()
  resizeOriginX = event.clientX
  resizeOriginWidth = inspectorRef.value?.getBoundingClientRect().width || 0
  resizeActive.value = true
  event.currentTarget.setPointerCapture(event.pointerId)
}

function resizeInspector(event) {
  if (!resizeActive.value) return
  setInspectorWidth(resizeOriginWidth - (event.clientX - resizeOriginX))
}

function finishInspectorResize() {
  resizeActive.value = false
}

function resizeInspectorWithKeyboard(event) {
  if (!['ArrowLeft', 'ArrowRight'].includes(event.key)) return
  event.preventDefault()
  const currentWidth =
    inspectorWidth.value ||
    inspectorRef.value?.getBoundingClientRect().width ||
    0
  const delta = event.key === 'ArrowLeft' ? 32 : -32
  setInspectorWidth(currentWidth + delta)
}

async function scrollSelectedIntoView() {
  await nextTick()
  const row = tablePaneRef.value?.querySelector('tr[data-selected="true"]')
  row?.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
}

function onTimelineSelect(sequence) {
  const event = events.value.find(
    (candidate) => candidate.sequence === sequence
  )
  if (event) {
    selectEvent(event)
    scrollSelectedIntoView()
  }
}

function toggleCall(callId) {
  const next = new Set(collapsed.value)
  if (next.has(callId)) next.delete(callId)
  else next.add(callId)
  collapsed.value = next
}

function toggleAllCalls() {
  if (collapsibleCallIds.value.length === 0) return
  const next = new Set(collapsed.value)
  if (allCallsCollapsed.value) {
    for (const id of collapsibleCallIds.value) next.delete(id)
  } else {
    for (const id of collapsibleCallIds.value) next.add(id)
  }
  collapsed.value = next
}

function setCategory(value) {
  category.value = value
}

async function fetchTrajectory(append = false) {
  if (!props.runUuid) return
  const currentRequestId = ++requestId
  loading.value = true
  try {
    const afterSequence = append ? latestSequence(events.value) : 0
    const data = await getAdminRunTrajectory(props.runUuid, {
      page_size: 500,
      after_sequence: afterSequence,
      q: query.value.trim() || undefined,
      category: category.value === 'all' ? undefined : category.value
    })
    if (currentRequestId !== requestId) return
    const normalize = (items) =>
      (items || []).map((event) => ({
        ...event,
        _ms: new Date(event.timestamp).getTime()
      }))
    const nextEvents = normalize(data.results)
    events.value = append ? [...events.value, ...nextEvents] : nextEvents
    summary.value = data.summary || {}
    hasMore.value = Boolean(data.has_more)
    if (!append) {
      selectedEvent.value = null
      timelineRange.value = null
      inspectorTab.value = 'summary'
    }
  } catch (error) {
    if (currentRequestId !== requestId) return
    showError(extractErrorMessage(error, t('common.error')))
    hasMore.value = false
  } finally {
    if (currentRequestId === requestId) loading.value = false
  }
}

function reset() {
  requestId += 1
  events.value = []
  summary.value = {}
  selectedEvent.value = null
  collapsed.value = new Set()
  query.value = ''
  category.value = 'all'
  hasMore.value = false
  timelineRange.value = null
}

function loadMore() {
  fetchTrajectory(true)
}

watch(
  () => props.runUuid,
  () => {
    reset()
    if (props.active) fetchTrajectory()
  }
)

watch([query, category], () => {
  if (!props.active) return
  clearTimeout(filterTimer)
  filterTimer = setTimeout(() => {
    fetchTrajectory()
  }, 250)
})

watch(filteredEvents, (filtered) => {
  if (
    selectedEvent.value &&
    !filtered.some((event) => event.sequence === selectedEvent.value.sequence)
  ) {
    selectedEvent.value = null
  }
})

watch(
  () => props.active,
  (active) => {
    if (active && events.value.length === 0) fetchTrajectory()
  },
  { immediate: true }
)

onBeforeUnmount(() => clearTimeout(filterTimer))
</script>

<style scoped>
.run-trajectory {
  --t-accent: #4176e6;
  --t-bg-1: #ffffff;
  --t-bg-2: #ffffff;
  --t-bg-hover: rgba(0, 0, 0, 0.045);
  --t-bg-active: rgba(0, 0, 0, 0.07);
  --t-border-l1: rgba(0, 0, 0, 0.05);
  --t-border-l2: rgba(0, 0, 0, 0.11);
  --t-text-1: #0f1115;
  --t-text-2: #61666b;
  --t-text-3: #81858c;
  --t-text-4: #adb2b8;
  --t-user: #4176e6;
  --t-user-bg: #e4edfd;
  --t-context: #22c55e;
  --t-context-bg: #e6faed;
  --t-model: #886bae;
  --t-model-bg: #efeff6;
  --t-tool: #dd8629;
  --t-tool-bg: #fef5e7;
  --t-subtool: #ba864f;
  --t-subtool-bg: #fef9f1;
  --t-system: #61666b;
  --t-system-bg: #f3f4f6;
  --t-retry: #dd8629;
  --t-retry-bg: #fef5e7;
  --t-checkpoint: #61666b;
  --t-checkpoint-bg: #f3f4f6;
  --t-cancelled: #ec1313;
  --t-cancelled-bg: #fef0f0;
  --t-error: #ec1313;

  display: flex;
  flex-direction: column;
  min-width: 0;
  color: var(--t-text-1);
  background: var(--t-bg-1);
}

:root[data-theme='dark'] .run-trajectory {
  --t-accent: #679efe;
  --t-bg-1: #232324;
  --t-bg-2: #2c2c2e;
  --t-bg-hover: rgba(255, 255, 255, 0.06);
  --t-bg-active: rgba(255, 255, 255, 0.1);
  --t-border-l1: rgba(255, 255, 255, 0.07);
  --t-border-l2: rgba(255, 255, 255, 0.13);
  --t-text-1: #f9fafb;
  --t-text-2: #cfd3d6;
  --t-text-3: #adb2b8;
  --t-text-4: #85878b;
  --t-user: #679efe;
  --t-user-bg: #34415b;
  --t-context: #22c55e;
  --t-context-bg: #233c2c;
  --t-model: #9474bc;
  --t-model-bg: #2e2837;
  --t-tool: #dd8629;
  --t-tool-bg: #27241f;
  --t-subtool: #cf9a56;
  --t-subtool-bg: #2a2825;
  --t-system: #cfd3d6;
  --t-system-bg: #3a3a3c;
  --t-retry: #dd8629;
  --t-retry-bg: #27241f;
  --t-checkpoint: #cfd3d6;
  --t-checkpoint-bg: #3a3a3c;
  --t-cancelled: #f25a5a;
  --t-cancelled-bg: #3b2626;
  --t-error: #f25a5a;
}

/* Stats bar */
.trajectory-stats {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px;
  margin: 0 0 12px;
}

.trajectory-stats .stat {
  min-width: 0;
  padding: 8px 12px;
  border: 1px solid var(--t-border-l2);
  border-radius: 8px;
  background: var(--t-bg-2);
}

.trajectory-stats dt {
  overflow: hidden;
  color: var(--t-text-3);
  font-size: 11px;
  line-height: 16px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.trajectory-stats dd {
  margin: 1px 0 0;
  color: var(--t-text-1);
  font-size: 17px;
  font-variant-numeric: tabular-nums;
  font-weight: 600;
  line-height: 22px;
}

.trajectory-stats .stat-error dd {
  color: var(--t-error);
}

@media (min-width: 640px) {
  .trajectory-stats {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }
}

@media (min-width: 1024px) {
  .trajectory-stats {
    grid-template-columns: repeat(6, minmax(0, 1fr));
  }
}

/* Toolbar */
.trajectory-toolbar {
  display: flex;
  flex: none;
  align-items: center;
  box-sizing: border-box;
  width: 100%;
  height: 34px;
  padding: 0 6px;
  gap: 8px;
  border: 1px solid var(--t-border-l2);
  border-radius: 8px 8px 0 0;
  background: var(--t-bg-1);
}

.toolbar-actions {
  display: flex;
  flex: none;
  align-items: center;
  min-width: 0;
  gap: 2px;
  overflow-x: auto;
  scrollbar-width: none;
}

.toolbar-actions::-webkit-scrollbar {
  display: none;
}

.toolbar-toggle {
  display: inline-flex;
  flex: none;
  align-items: center;
  height: 20px;
  padding: 0 7px;
  gap: 4px;
  border: 0;
  border-radius: 3px;
  color: var(--t-text-3);
  background: transparent;
  cursor: pointer;
  font-size: 12px;
  line-height: 20px;
}

.toolbar-toggle:hover {
  color: var(--t-text-1);
  background: var(--t-bg-hover);
}

.toolbar-toggle[aria-pressed='true'] {
  color: var(--t-text-1);
  background: var(--t-bg-hover);
}

.toolbar-toggle:disabled {
  color: var(--t-text-4);
  cursor: not-allowed;
  background: transparent;
}

.toolbar-toggle:focus-visible {
  outline: 1px solid var(--t-accent);
  outline-offset: 1px;
}

.toolbar-count {
  color: var(--t-text-4);
  font-size: 10px;
  font-variant-numeric: tabular-nums;
}

.toolbar-separator {
  flex: none;
  width: 1px;
  height: 14px;
  margin: 0 3px;
  background: var(--t-border-l2);
}

.toolbar-glyph {
  color: var(--t-text-3);
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 14px;
  line-height: 14px;
}

.toolbar-search {
  display: flex;
  flex: 0 1 200px;
  align-items: center;
  min-width: 84px;
  height: 22px;
  margin-left: auto;
  padding: 0 6px;
  gap: 4px;
  border: 1px solid var(--t-border-l2);
  border-radius: 4px;
  color: var(--t-text-4);
  background: var(--t-bg-2);
}

.toolbar-search:hover {
  border-color: var(--t-text-4);
}

.toolbar-search:focus-within {
  border-color: var(--t-accent);
  background: var(--t-bg-1);
}

.search-icon {
  flex: none;
}

.search-input {
  min-width: 0;
  width: 100%;
  padding: 0;
  border: 0;
  outline: 0;
  color: var(--t-text-1);
  background: transparent;
  font-size: 12px;
  line-height: 20px;
}

.search-input::placeholder {
  color: var(--t-text-4);
}

.search-input::-webkit-search-cancel-button {
  width: 12px;
  height: 12px;
  cursor: pointer;
}

/* Split layout */
.trajectory-split {
  position: relative;
  display: flex;
  min-height: 28rem;
  max-height: 42rem;
  overflow: hidden;
  border: 1px solid var(--t-border-l2);
  border-top: 0;
  border-radius: 0 0 8px 8px;
  background: var(--t-bg-1);
}

.trajectory-split-resizing {
  cursor: col-resize;
  user-select: none;
}

.table-pane {
  flex: 1;
  min-width: 420px;
  overflow: auto;
}

/* Ledger table */
.trajectory-table {
  width: 100%;
  min-width: 560px;
  border-spacing: 0;
  table-layout: fixed;
  color: var(--t-text-1);
  background: var(--t-bg-1);
  font-size: 12px;
}

.trajectory-table th {
  position: sticky;
  top: 0;
  z-index: 3;
  box-sizing: border-box;
  height: 30px;
  padding: 0 8px;
  overflow: hidden;
  border-bottom: 1px solid var(--t-border-l2);
  color: var(--t-text-3);
  background: var(--t-bg-2);
  font-size: 12px;
  font-weight: 500;
  text-align: left;
  text-overflow: ellipsis;
  user-select: none;
  white-space: nowrap;
}

.event-header {
  width: 122px;
  padding-right: 4px !important;
  text-align: right !important;
}

.trajectory-table td {
  box-sizing: border-box;
  height: 30px;
  padding: 0 8px;
  overflow: hidden;
  border-bottom: 1px solid var(--t-border-l1);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.trajectory-table tbody tr {
  cursor: default;
  outline: none;
  transition:
    background-color 120ms ease-in-out,
    opacity 120ms ease-in-out;
}

.trajectory-table tbody tr:hover {
  background: var(--t-bg-hover);
}

.trajectory-table tbody tr[data-selected='true'] {
  background: var(--t-bg-active);
}

.trajectory-table tbody tr:focus-visible {
  box-shadow: inset 0 0 0 1px var(--t-accent);
}

/* Turn boundary + rail */
.turn-start-row td {
  position: relative;
  overflow: visible;
}

.trajectory-table tbody .turn-start-row:not(:first-child) td::before {
  position: absolute;
  z-index: 1;
  top: 0;
  right: 0;
  left: 0;
  height: 2px;
  background: var(--t-border-l1);
  content: '';
  pointer-events: none;
  transform: translateY(-50%);
}

.event-cell {
  position: relative;
  padding-right: 4px !important;
  padding-left: calc(34px + var(--trajectory-indent, 0px)) !important;
}

.turn-rail {
  position: absolute;
  top: -1px;
  bottom: -1px;
  left: 0;
  width: 2px;
  background: color-mix(in srgb, var(--t-accent) 22%, var(--t-bg-1));
  pointer-events: none;
}

.ledger-row[data-error='true'] .turn-rail {
  background: color-mix(in srgb, var(--t-error) 22%, var(--t-bg-1));
}

.ledger-row[data-selected='true'] .turn-rail {
  top: 0;
  bottom: 0;
  width: 3px;
  background: var(--t-accent);
}

.ledger-row[data-error='true'][data-selected='true'] .turn-rail {
  background: var(--t-error);
}

.request-dot {
  position: absolute;
  z-index: 6;
  top: -8px;
  left: 12px;
  width: 16px;
  height: 16px;
  pointer-events: none;
}

.request-dot::before {
  position: absolute;
  top: 5.5px;
  left: 5.5px;
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: var(--t-text-4);
  box-shadow:
    0 0 0 2px var(--t-bg-1),
    0 0 0 3px transparent;
  content: '';
}

.ledger-row[data-error='true'] .request-dot::before {
  background: var(--t-error);
}

.seq {
  position: absolute;
  top: 0;
  bottom: 0;
  left: 8px;
  display: flex;
  align-items: center;
  color: var(--t-text-3);
  font:
    10px/12px ui-monospace,
    SFMono-Regular,
    Menlo,
    monospace;
  font-variant-numeric: tabular-nums;
}

.kind-tag {
  display: inline-flex;
  flex: none;
  align-items: center;
  box-sizing: border-box;
  height: 19px;
  padding: 0 5px;
  border: 1px solid transparent;
  border-radius: 4px;
  font-size: 10px;
  font-weight: 650;
  letter-spacing: 0.035em;
  line-height: 16px;
  user-select: none;
}

.kind-tag-label {
  display: inline-block;
  max-width: 72px;
  overflow: hidden;
  white-space: nowrap;
}

.tag-user {
  color: var(--t-user);
  background: var(--t-user-bg);
}
.tag-context {
  color: var(--t-context);
  background: var(--t-context-bg);
}
.tag-model {
  color: var(--t-model);
  background: var(--t-model-bg);
}
.tag-tool {
  color: var(--t-tool);
  background: var(--t-tool-bg);
}
.tag-subtool {
  color: var(--t-subtool);
  background: var(--t-subtool-bg);
}
.tag-system,
.tag-compacted,
.tag-checkpoint,
.tag-step {
  color: var(--t-system);
  background: var(--t-system-bg);
}
.tag-retry {
  color: var(--t-retry);
  background: var(--t-retry-bg);
}
.tag-cancelled {
  color: var(--t-cancelled);
  background: var(--t-cancelled-bg);
}

/* Content cell */
.content-cell {
  display: flex;
  align-items: center;
  gap: 12px;
  padding-right: 8px !important;
  padding-left: calc(8px + var(--trajectory-indent, 0px)) !important;
}

.row-expand {
  display: inline-flex;
  flex: none;
  align-items: center;
  justify-content: center;
  width: 20px;
  height: 20px;
  margin-left: -4px;
  padding: 0;
  border: 0;
  border-radius: 3px;
  color: var(--t-text-3);
  background: transparent;
  cursor: pointer;
}

.row-expand:hover {
  color: var(--t-text-1);
  background: var(--t-bg-hover);
}

.row-expand:focus-visible {
  outline: 1px solid var(--t-accent);
}

.content-text {
  display: flex;
  flex: 1 1 auto;
  align-items: center;
  min-width: 0;
  gap: 7px;
}

.content-title {
  min-width: 0;
  overflow: hidden;
  color: var(--t-text-1);
  font-size: 12px;
  line-height: 18px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.content-title-code {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-weight: 400;
}

.content-title-error {
  color: var(--t-error);
}

.content-args {
  min-width: 0;
  overflow: hidden;
  color: var(--t-text-2);
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 12px;
  line-height: 18px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.content-trailing {
  display: flex;
  flex: none;
  align-items: center;
  gap: 12px;
}

.content-metrics {
  color: var(--t-text-3);
  font-size: 12px;
  white-space: nowrap;
}

.content-metrics-error {
  color: var(--t-error);
}

.content-time {
  flex: none;
  color: var(--t-text-4);
  font:
    11px/16px ui-monospace,
    SFMono-Regular,
    Menlo,
    monospace;
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}

/* Inspector drawer */
.trajectory-resize-handle {
  position: relative;
  z-index: 4;
  flex: 0 0 8px;
  width: 8px;
  margin: 0 -4px;
  outline: 0;
  cursor: col-resize;
  touch-action: none;
}

.trajectory-resize-handle::after {
  position: absolute;
  top: 0;
  bottom: 0;
  left: 3px;
  width: 2px;
  background: transparent;
  content: '';
}

.trajectory-resize-handle:hover::after,
.trajectory-resize-handle:focus-visible::after,
.trajectory-split-resizing .trajectory-resize-handle::after {
  background: var(--t-accent);
}

.trajectory-inspector {
  display: flex;
  flex: none;
  flex-direction: column;
  width: var(--trajectory-inspector-width, clamp(320px, 42%, 520px));
  max-width: calc(100% - 420px);
  min-width: 0;
  min-height: 0;
  border-left: 1px solid var(--t-border-l2);
  background: var(--t-bg-1);
  animation: inspector-slide-in 180ms ease-out;
}

@keyframes inspector-slide-in {
  from {
    opacity: 0;
    transform: translateX(12px);
  }
  to {
    opacity: 1;
    transform: translateX(0);
  }
}

.inspector-header {
  display: flex;
  flex: none;
  align-items: center;
  justify-content: space-between;
  box-sizing: border-box;
  height: 42px;
  padding: 0 8px 0 12px;
  border-bottom: 1px solid var(--t-border-l2);
}

.inspector-title {
  display: flex;
  align-items: center;
  min-width: 0;
  gap: 8px;
}

.inspector-header-meta {
  display: inline-flex;
  flex: none;
  align-items: center;
  gap: 5px;
}

.inspector-sequence {
  color: var(--t-text-3);
  font:
    10px/16px ui-monospace,
    SFMono-Regular,
    Menlo,
    monospace;
  font-variant-numeric: tabular-nums;
}

.inspector-header-meta .kind-tag {
  height: 18px;
  padding: 0 5px;
  font-size: 9px;
}

.inspector-dot {
  flex: none;
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: var(--t-text-3);
}

.inspector-name {
  flex: none;
  max-width: 180px;
  overflow: hidden;
  font:
    500 12px/16px ui-monospace,
    SFMono-Regular,
    Menlo,
    monospace;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.inspector-location {
  min-width: 0;
  overflow: hidden;
  color: var(--t-text-3);
  font:
    11px/16px ui-monospace,
    SFMono-Regular,
    Menlo,
    monospace;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.inspector-close {
  display: inline-flex;
  flex: none;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  padding: 0;
  border: 0;
  border-radius: 6px;
  color: var(--t-text-2);
  background: transparent;
  cursor: pointer;
  font-size: 18px;
  line-height: 18px;
}

.inspector-close:hover {
  color: var(--t-text-1);
  background: var(--t-bg-hover);
}

.inspector-close:focus-visible,
.inspector-tab:focus-visible {
  outline: 1px solid var(--t-accent);
  outline-offset: -1px;
}

.inspector-tabs {
  display: flex;
  flex: none;
  box-sizing: border-box;
  width: 100%;
  height: 34px;
  padding: 0 8px;
  gap: 1px;
  overflow-x: auto;
  border-bottom: 1px solid var(--t-border-l2);
  scrollbar-width: none;
  white-space: nowrap;
}

.inspector-tabs::-webkit-scrollbar {
  display: none;
}

.inspector-tab {
  position: relative;
  flex: none;
  padding: 0 9px;
  border: 0;
  color: var(--t-text-3);
  background: transparent;
  cursor: pointer;
  font-size: 13px;
}

.inspector-tab:hover {
  color: var(--t-text-1);
  background: var(--t-bg-hover);
}

.inspector-tab-active {
  color: var(--t-accent);
}

.inspector-tab-active::after {
  position: absolute;
  right: 9px;
  bottom: 0;
  left: 9px;
  height: 2px;
  border-radius: 1px 1px 0 0;
  background: var(--t-accent);
  content: '';
}

.inspector-body {
  display: flex;
  flex: 1;
  min-height: 0;
  flex-direction: column;
  overflow: auto;
  padding: 0 0 12px;
}

.inspector-event-card {
  margin: 10px 12px 2px;
  padding: 10px 11px;
  border: 1px solid var(--t-border-l1);
  border-radius: 7px;
  background: var(--t-bg-2);
}

.inspector-event-card-title {
  overflow: hidden;
  color: var(--t-text-1);
  font-size: 13px;
  font-weight: 600;
  line-height: 18px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.inspector-event-card-type {
  margin-top: 2px;
  overflow-wrap: anywhere;
  color: var(--t-text-3);
  font:
    11px/16px ui-monospace,
    SFMono-Regular,
    Menlo,
    monospace;
}

.inspector-event-chips {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 5px;
  margin-top: 8px;
}

.inspector-event-chips .status-badge {
  margin: 0;
}

.inspector-chip {
  display: inline-flex;
  align-items: center;
  min-height: 18px;
  padding: 0 6px;
  border: 1px solid var(--t-border-l1);
  border-radius: 999px;
  color: var(--t-text-2);
  background: var(--t-bg-1);
  font-size: 10px;
  line-height: 16px;
}

.status-badge {
  display: inline-flex;
  margin: 10px 12px 0;
  padding: 1px 8px;
  border-radius: 999px;
  font-size: 10px;
  font-weight: 600;
  line-height: 16px;
}

.status-badge-error {
  color: var(--t-cancelled);
  background: var(--t-cancelled-bg);
}
.status-badge-success {
  color: var(--t-context);
  background: var(--t-context-bg);
}
.status-badge-neutral {
  color: var(--t-text-3);
  background: var(--t-system-bg);
}

.overview {
  margin: 0;
  padding: 8px 0 4px;
  font-size: 13px;
  line-height: 20px;
}

.overview > div {
  display: grid;
  grid-template-columns: 82px minmax(0, 1fr);
  min-height: 22px;
  padding: 0 14px;
  align-items: start;
}

.overview dt {
  padding-top: 1px;
  color: var(--t-text-3);
}

.overview dt.indent {
  padding-left: 12px;
}

.overview dd {
  min-width: 0;
  margin: 0;
  overflow: hidden;
  color: var(--t-text-1);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.overview dd.mono {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 12px;
}

.overview dd.wrap-value {
  overflow-wrap: anywhere;
  white-space: normal;
}

.hierarchy-value {
  display: flex;
  flex-direction: column;
  gap: 1px;
  line-height: 17px;
}

.hierarchy-value span {
  overflow-wrap: anywhere;
}

.inspector-io-section {
  padding-bottom: 6px;
}

.inspector-data-block {
  margin: 5px 12px 8px;
}

.inspector-data-label {
  display: block;
  margin-bottom: 3px;
  color: var(--t-text-3);
  font-size: 11px;
  font-weight: 600;
  text-transform: uppercase;
}

.inspector-data-block pre {
  max-height: 160px;
  margin: 0;
  padding: 8px 9px;
  overflow: auto;
  border: 1px solid var(--t-border-l1);
  border-radius: 5px;
  color: var(--t-text-1);
  background: var(--t-bg-2);
  font:
    11px/16px ui-monospace,
    SFMono-Regular,
    Menlo,
    monospace;
  overflow-wrap: anywhere;
  white-space: pre-wrap;
}

.overview dd .sub {
  color: var(--t-text-3);
}

.overview-section {
  border-top: 1px solid var(--t-border-l1);
}

.overview-heading {
  margin: 0;
  padding: 6px 14px 2px;
  color: var(--t-text-2);
  font-size: 13px;
  font-weight: 600;
  user-select: none;
}

.trajectory-empty {
  margin: 0;
  padding: 48px 20px;
  border: 1px dashed var(--t-border-l2);
  border-radius: 8px;
  color: var(--t-text-4);
  font-size: 13px;
  text-align: center;
}

.trajectory-load-more {
  display: flex;
  justify-content: center;
  padding-top: 12px;
}

/* Scrollbars: only the ledger pane and JSON code blocks scroll, using the
   app-wide thin scrollbar convention. */
@media (max-width: 900px) {
  .table-pane {
    min-width: 0;
  }

  .trajectory-resize-handle {
    display: none;
  }

  .trajectory-inspector {
    position: absolute;
    z-index: 5;
    top: 0;
    right: 0;
    bottom: 0;
    width: min(92%, 420px);
    max-width: 92%;
    border-left-color: var(--t-border-l2);
    box-shadow: -12px 0 32px rgba(0, 0, 0, 0.14);
  }

  .event-header {
    width: 92px;
  }

  .event-cell {
    padding-left: 30px !important;
  }
}
</style>
