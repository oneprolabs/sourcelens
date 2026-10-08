<template>
  <section class="trajectory-timeline" aria-label="Trajectory timeline">
    <div class="plot" :style="timelineStyle">
      <div class="labels" aria-hidden="true">
        <div
          v-for="group in laneGroups"
          :key="group.key"
          class="lane-group-label"
        >
          <strong :title="group.label">{{ group.label }}</strong>
          <span v-for="lane in group.lanes" :key="lane.key">
            {{ lane.label }}
          </span>
        </div>
      </div>
      <div
        ref="trackEl"
        class="track"
        tabindex="0"
        :aria-label="t('lensRuns.trajectoryTimelineHint')"
        @pointerdown="onPointerDown"
        @pointermove="onPointerMove"
        @pointerup="onPointerEnd"
        @pointerleave="onPointerLeave"
        @wheel.prevent="onWheel"
        @dblclick.prevent="clearRange"
        @contextmenu.prevent
        @keydown.esc="clearRange"
      >
        <span v-if="empty" class="empty">{{
          t('lensRuns.trajectoryNoTiming')
        }}</span>

        <div v-if="!empty" class="turn-boundaries" aria-hidden="true">
          <span
            v-for="(boundary, index) in visibleBoundaries"
            :key="`b-${index}`"
            class="turn-boundary"
            :style="{ left: boundary.left + '%' }"
          />
        </div>

        <div v-if="!empty" class="lanes" aria-hidden="true">
          <span
            v-for="step in visibleSpans"
            :key="step.event.sequence"
            class="span"
            :class="spanClass(step)"
            :data-span-seq="step.event.sequence"
            :data-error="isErrorEvent(step.event) || undefined"
            :data-range-outside="rangeDimmed(step) || undefined"
            :data-current="
              step.event.sequence === selectedSequence || undefined
            "
            :style="spanStyle(step)"
            @pointerenter="onSpanEnter(step, $event)"
            @pointermove="onSpanMove(step, $event)"
            @pointerleave="onSpanLeave"
          />
        </div>

        <div v-if="!empty" class="lane-group-lines" aria-hidden="true">
          <span
            v-for="line in groupLines"
            :key="line"
            :style="{ top: `${line}px` }"
          />
        </div>

        <div
          v-if="
            hoverFraction !== null && dragging === false && hoveredSpan === null
          "
          class="hover-line"
          aria-hidden="true"
          :style="{ left: `calc(${hoverFraction * 100}% - 1px)` }"
        />

        <template v-if="visibleRange">
          <div
            class="selection"
            :data-dragging="dragging || undefined"
            aria-hidden="true"
            :style="selectionStyle"
          />
          <div
            class="selection-edges"
            :data-dragging="dragging || undefined"
            aria-hidden="true"
            :style="selectionStyle"
          />
        </template>

        <div
          v-if="tooltip"
          class="tooltip"
          role="tooltip"
          :style="{ left: tooltip.x + 'px', top: tooltip.y + 'px' }"
        >
          <strong>{{ tooltip.kind }}</strong>
          <span v-if="tooltip.assistant">{{ tooltip.assistant }}</span>
          <span>{{ tooltip.range }}</span>
          <span v-if="tooltip.duration">{{ tooltip.duration }}</span>
        </div>
      </div>
    </div>
    <div v-if="!empty" class="time-range" aria-hidden="true">
      <span>{{ timeStartText }}</span>
      <span>{{ timeEndText }}</span>
    </div>
  </section>
</template>

<script setup>
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'

const props = defineProps({
  lanes: { type: Array, default: () => [] },
  boundaries: { type: Array, default: () => [] },
  selectedSequence: { type: Number, default: null },
  range: { type: Object, default: null }
})

const emit = defineEmits(['range-change', 'select-event'])

const { t } = useI18n()

const MINIMUM_DRAG_PX = 3
const TOOLTIP_DELAY_MS = 500

const trackEl = ref(null)
const dragging = ref(false)
const draft = ref(null)
const hoverFraction = ref(null)
const hoveredSpan = ref(null)
const tooltip = ref(null)
const viewport = ref(null)
let dragState = null
let tooltipTimer = null

const flatSteps = computed(() => {
  const steps = []
  for (const [laneIndex, lane] of props.lanes.entries()) {
    for (const step of lane.steps) {
      steps.push({ ...step, lane: laneIndex })
    }
  }
  return steps.sort((a, b) => a.startMs - b.startMs)
})

const laneGroups = computed(() => {
  const groups = []
  for (const lane of props.lanes) {
    let group = groups.at(-1)
    if (!group || group.key !== lane.groupKey) {
      group = {
        key: lane.groupKey || 'run',
        label: lane.groupLabel || 'Run',
        lanes: []
      }
      groups.push(group)
    }
    group.lanes.push(lane)
  }
  return groups
})

const timelineHeight = computed(() =>
  Math.max(50, props.lanes.length * 14 + 14)
)

const timelineStyle = computed(() => ({
  '--timeline-height': `${timelineHeight.value}px`
}))

const groupLines = computed(() =>
  laneGroups.value.slice(1).map((_, index) => (index + 1) * 42)
)

const empty = computed(() => flatSteps.value.length === 0)

const fullStart = computed(() => {
  if (empty.value) return 0
  return Math.min(...flatSteps.value.map((step) => step.startMs))
})

const fullEnd = computed(() => {
  if (empty.value) return 0
  return Math.max(
    ...flatSteps.value.map((step) => step.startMs + step.durationMs)
  )
})

const fullDuration = computed(() =>
  Math.max(1, fullEnd.value - fullStart.value)
)

const domainStart = computed(() => viewport.value?.start ?? fullStart.value)

const domainEnd = computed(() => viewport.value?.end ?? fullEnd.value)

const domainDuration = computed(() =>
  Math.max(1, domainEnd.value - domainStart.value)
)

const visibleSpans = computed(() => {
  if (empty.value) return []
  return flatSteps.value.filter(
    (step) =>
      step.event.sequence === props.selectedSequence ||
      (step.startMs <= domainEnd.value &&
        step.startMs + step.durationMs >= domainStart.value)
  )
})

const visibleBoundaries = computed(() => {
  return props.boundaries
    .map((boundary) => ({
      left: ((boundary.time - domainStart.value) / domainDuration.value) * 100
    }))
    .filter((boundary) => boundary.left >= 0 && boundary.left <= 100)
})

function spanStyle(step) {
  const left = ((step.startMs - domainStart.value) / domainDuration.value) * 100
  const width = (step.durationMs / domainDuration.value) * 100
  return {
    top: `${step.lane * 14}px`,
    left: `${left}%`,
    width: `${Math.max(0, width)}%`
  }
}

const visibleRange = computed(() => draft.value || props.range)

const timeStartText = computed(() =>
  empty.value ? '' : clockText(new Date(domainStart.value))
)

const timeEndText = computed(() =>
  empty.value ? '' : clockText(new Date(domainEnd.value))
)

const selectionStyle = computed(() => {
  if (!visibleRange.value) return null
  const left =
    ((visibleRange.value.start - domainStart.value) / domainDuration.value) *
    100
  const width =
    ((visibleRange.value.end - visibleRange.value.start) /
      domainDuration.value) *
    100
  const clampedLeft = Math.min(100, Math.max(0, left))
  const clampedWidth = Math.min(100 - clampedLeft, Math.max(0, width))
  return { left: `${clampedLeft}%`, width: `${clampedWidth}%` }
})

function fractionToTime(fraction) {
  return domainStart.value + fraction * domainDuration.value
}

function timeAtEvent(event) {
  const rect = event.currentTarget.getBoundingClientRect()
  const fraction = clampFraction(
    (event.clientX - rect.left) / Math.max(1, rect.width)
  )
  return { fraction, time: fractionToTime(fraction), rect }
}

function clampFraction(value) {
  return Math.min(1, Math.max(0, value))
}

function spanSeqAt(event) {
  const target = event.target
  if (!(target instanceof HTMLElement)) return null
  const span = target.closest('[data-span-seq]')
  if (!span) return null
  const value = span.dataset.spanSeq
  return value === undefined ? null : Number(value)
}

function onPointerDown(event) {
  if (event.button === 2) {
    dragState = {
      pointerId: event.pointerId,
      rightClick: true,
      click: true,
      panAnchorClientX: event.clientX,
      panAnchorStart: domainStart.value,
      panMoved: false
    }
    if (typeof event.currentTarget.setPointerCapture === 'function') {
      event.currentTarget.setPointerCapture(event.pointerId)
    }
    return
  }
  if (event.button !== 0) return
  const { time } = timeAtEvent(event)
  const seq = spanSeqAt(event)
  dragState = {
    pointerId: event.pointerId,
    anchorClientX: event.clientX,
    anchorTime: time,
    seq,
    click: true
  }
  dragging.value = true
  draft.value = { start: time, end: time }
  if (typeof event.currentTarget.setPointerCapture === 'function') {
    event.currentTarget.setPointerCapture(event.pointerId)
  }
}

function onPointerMove(event) {
  const { fraction, time } = timeAtEvent(event)
  hoverFraction.value = fraction
  if (dragState === null || dragState.pointerId !== event.pointerId) return
  if (dragState.rightClick) {
    const delta = event.clientX - dragState.panAnchorClientX
    if (Math.abs(delta) >= MINIMUM_DRAG_PX) dragState.panMoved = true
    if (viewport.value !== null && Math.abs(delta) >= 1) {
      const rect = event.currentTarget.getBoundingClientRect()
      const shift = (delta / Math.max(1, rect.width)) * domainDuration.value
      const nextStart = clamp(
        dragState.panAnchorStart - shift,
        fullStart.value,
        fullEnd.value - domainDuration.value
      )
      viewport.value = {
        start: nextStart,
        end: nextStart + domainDuration.value
      }
    }
    return
  }
  if (Math.abs(event.clientX - dragState.anchorClientX) >= MINIMUM_DRAG_PX) {
    dragState.click = false
  }
  draft.value = orderedRange(dragState.anchorTime, time)
}

function onPointerEnd(event) {
  if (dragState === null || dragState.pointerId !== event.pointerId) return
  if (dragState.rightClick) {
    const moved = dragState.panMoved
    dragState = null
    if (!moved) clearRange()
    return
  }
  const { time } = timeAtEvent(event)
  const selected = orderedRange(dragState.anchorTime, time)
  const click = dragState.click
  const clickedSeq = dragState.seq
  dragState = null
  dragging.value = false
  draft.value = null
  if (click && clickedSeq !== null) {
    emit('range-change', null)
    emit('select-event', clickedSeq)
    return
  }
  const rangeWidth = selected.end - selected.start
  const minimumSelection =
    domainDuration.value / Math.max(1, flatSteps.value.length)
  if (click || rangeWidth < minimumSelection) {
    const center = click ? selected.start : (selected.start + selected.end) / 2
    const half = minimumSelection / 2
    emit('range-change', {
      start: Math.max(domainStart.value, center - half),
      end: Math.min(domainEnd.value, center + half)
    })
    emit('select-event', nearestSequence(center))
    return
  }
  emit('range-change', selected)
  emit('select-event', nearestSequence(selected.start))
}

function onPointerLeave() {
  if (dragging.value) return
  hoverFraction.value = null
  clearTooltip()
}

function nearestSequence(time) {
  let best = null
  let bestDistance = Infinity
  for (const step of flatSteps.value) {
    const stepEnd = step.startMs + step.durationMs
    const distance =
      time < step.startMs
        ? step.startMs - time
        : time > stepEnd
          ? time - stepEnd
          : 0
    if (distance < bestDistance) {
      bestDistance = distance
      best = step.event.sequence
    }
  }
  return best
}

function clamp(value, minimum, maximum) {
  return Math.min(maximum, Math.max(minimum, value))
}

const MINIMUM_ZOOM_SPANS = 4

function onWheel(event) {
  event.preventDefault()
  const rect = trackEl.value?.getBoundingClientRect()
  if (!rect) return
  const fraction = clampFraction(
    (event.clientX - rect.left) / Math.max(1, rect.width)
  )
  const anchorTime = domainStart.value + fraction * domainDuration.value
  const minimumDuration = Math.min(
    fullDuration.value,
    fullDuration.value /
      Math.min(20, Math.max(MINIMUM_ZOOM_SPANS, flatSteps.value.length))
  )
  const nextDuration = Math.min(
    fullDuration.value,
    Math.max(
      minimumDuration,
      domainDuration.value * Math.exp(event.deltaY * 0.0015)
    )
  )
  if (nextDuration >= fullDuration.value * 0.999) {
    viewport.value = null
    return
  }
  const nextStart = clamp(
    anchorTime - fraction * nextDuration,
    fullStart.value,
    fullEnd.value - nextDuration
  )
  viewport.value = { start: nextStart, end: nextStart + nextDuration }
}

function clearRange() {
  emit('range-change', null)
}

function orderedRange(left, right) {
  return left <= right
    ? { start: left, end: right }
    : { start: right, end: left }
}

function spanClass(step) {
  return `span-${spanKind(step.event)}`
}

function spanKind(event) {
  const category = String(event.event_type || '').split('.', 1)[0]
  if (category === 'model') return 'model'
  if (category === 'tool') return 'tool'
  if (category === 'subtool') return 'subtool'
  if (category === 'user') return 'user'
  if (category === 'context' || category === 'request') return 'context'
  return 'system'
}

function rangeDimmed(step) {
  if (!props.range) return false
  const stepEnd = step.startMs + step.durationMs
  return !(step.startMs <= props.range.end && stepEnd >= props.range.start)
}

function isErrorEvent(event) {
  const status = String(event.event_type || '')
    .split('.')
    .pop()
  return ['failed', 'cancelled', 'interrupted'].includes(status)
}

function spanKindLabel(kind) {
  return (
    {
      system: 'SYSTEM',
      user: 'USER',
      context: 'CONTEXT',
      model: 'ASSISTANT',
      tool: 'TOOL',
      subtool: 'SUBTOOL'
    }[kind] || 'EVENT'
  )
}

function onSpanEnter(step, event) {
  hoveredSpan.value = step
  showTooltip(step, event)
}

function onSpanMove(step, event) {
  hoveredSpan.value = step
  showTooltip(step, event)
}

function onSpanLeave() {
  hoveredSpan.value = null
  clearTooltip()
}

function showTooltip(step, event) {
  clearTimeout(tooltipTimer)
  const kind = spanKindLabel(spanKind(step.event))
  const start = new Date(step.startMs)
  const end = new Date(step.startMs + step.durationMs)
  const range = `${clockText(start)} → ${clockText(end)}`
  const duration = `${t('lensRuns.trajectoryTooltipTotal')} ${durationText(step.durationMs)}`
  tooltipTimer = setTimeout(() => {
    const rect = event.currentTarget.getBoundingClientRect()
    const trackRect = trackEl.value
      ? trackEl.value.getBoundingClientRect()
      : rect
    const x = event.clientX - trackRect.left
    const y = Math.max(
      4,
      Math.min(trackRect.height - 4, rect.top - trackRect.top)
    )
    tooltip.value = {
      kind,
      assistant: step.assistantName,
      range,
      duration,
      x,
      y
    }
  }, TOOLTIP_DELAY_MS)
}

function clearTooltip() {
  clearTimeout(tooltipTimer)
  tooltip.value = null
}

function clockText(date) {
  const two = (value) => String(value).padStart(2, '0')
  return `${two(date.getHours())}:${two(date.getMinutes())}:${two(date.getSeconds())}.${String(date.getMilliseconds()).padStart(3, '0')}`
}

function durationText(value) {
  if (value == null || !Number.isFinite(value)) return '—'
  if (value < 1000) return `${Math.round(value)}ms`
  return `${(value / 1000).toFixed(2)}s`
}
</script>

<style scoped>
.trajectory-timeline {
  position: relative;
  z-index: 1;
  isolation: isolate;
  flex: none;
  border-bottom: 1px solid var(--t-border-l2);
  user-select: none;
}

.plot {
  display: grid;
  grid-template-columns: 188px minmax(0, 1fr);
  height: var(--timeline-height, 50px);
  overflow: hidden;
  background: var(--t-bg-2);
}

.labels {
  box-sizing: border-box;
  overflow: hidden;
  padding-top: 7px;
  border-right: 1px solid var(--t-border-l1);
  color: var(--t-text-3);
  font-size: 10px;
  line-height: 1;
}

.lane-group-label {
  display: grid;
  grid-template-columns: minmax(0, 132px) 44px;
  grid-template-rows: repeat(3, 14px);
  height: 42px;
  padding: 0 4px;
  border-top: 1px solid transparent;
}

.lane-group-label + .lane-group-label {
  border-top-color: var(--t-border-l2);
}

.lane-group-label strong {
  grid-row: 1 / 4;
  align-self: center;
  overflow: hidden;
  color: var(--t-text-2);
  font-weight: 600;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.lane-group-label span {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  color: var(--t-text-4);
}

.track {
  position: relative;
  overflow: hidden;
  cursor: crosshair;
  touch-action: none;
  outline: none;
}

.track:focus-visible {
  outline: 1px solid var(--t-accent);
  outline-offset: -1px;
}

.empty {
  position: absolute;
  top: 50%;
  left: 50%;
  transform: translate(-50%, -50%);
  color: var(--t-text-4);
  font-size: 13px;
}

.time-range {
  display: flex;
  align-items: center;
  justify-content: space-between;
  box-sizing: border-box;
  width: 100%;
  height: 18px;
  padding: 0 4px 0 192px;
  border-bottom: 1px solid var(--t-border-l2);
  color: var(--t-text-4);
  font:
    10px/18px ui-monospace,
    SFMono-Regular,
    Menlo,
    monospace;
  font-variant-numeric: tabular-nums;
  user-select: none;
  white-space: nowrap;
}

.turn-boundaries {
  position: absolute;
  z-index: 3;
  top: 0;
  bottom: 0;
  left: 0;
  right: 0;
  pointer-events: none;
}

.turn-boundary {
  position: absolute;
  top: 0;
  bottom: 0;
  width: 1px;
  background: var(--t-border-l2);
}

.lanes {
  position: absolute;
  z-index: 2;
  top: 7px;
  bottom: 7px;
  left: 0;
  right: 0;
}

.lane-group-lines {
  position: absolute;
  z-index: 1;
  top: 7px;
  right: 0;
  bottom: 7px;
  left: 0;
  pointer-events: none;
}

.lane-group-lines span {
  position: absolute;
  right: 0;
  left: 0;
  height: 1px;
  background: var(--t-border-l2);
}

.span {
  position: absolute;
  height: 8px;
  min-width: 2px;
  border-radius: 1px;
  background: var(--t-text-3);
  opacity: 0.78;
}

.span-user {
  background: var(--t-user);
}
.span-context {
  background: var(--t-context);
}
.span-model {
  background: var(--t-model);
}
.span-tool,
.span-subtool {
  background: var(--t-tool);
}
.span-system {
  background: var(--t-text-3);
  opacity: 0.55;
}

.span[data-error='true'] {
  background: var(--t-error);
}

.span[data-current='true'] {
  z-index: 1;
  opacity: 1;
  box-shadow:
    0 0 0 1px var(--t-bg-1),
    0 0 0 2px var(--t-accent);
}

.span[data-range-outside='true'] {
  opacity: 0.2;
}

.hover-line {
  position: absolute;
  z-index: 4;
  top: 0;
  bottom: 0;
  width: 2px;
  background: var(--t-accent);
  pointer-events: none;
}

.selection {
  position: absolute;
  z-index: 1;
  top: 0;
  bottom: 0;
  min-width: 1px;
  background: color-mix(in srgb, var(--t-accent) 12%, transparent);
  box-shadow:
    -100vw 0 0 100vw color-mix(in srgb, var(--t-bg-1) 58%, transparent),
    100vw 0 0 100vw color-mix(in srgb, var(--t-bg-1) 58%, transparent);
  pointer-events: none;
}

.selection-edges {
  position: absolute;
  z-index: 4;
  top: 0;
  bottom: 0;
  min-width: 1px;
  pointer-events: none;
}

.selection-edges::before,
.selection-edges::after {
  position: absolute;
  top: 0;
  bottom: 0;
  width: 3px;
  background: var(--t-accent);
  content: '';
}

.selection-edges::before {
  left: 0;
}
.selection-edges::after {
  right: 0;
}

.tooltip {
  position: absolute;
  z-index: 6;
  display: flex;
  flex-direction: column;
  min-width: max-content;
  padding: 5px 8px;
  border: 1px solid var(--t-border-l2);
  border-radius: 4px;
  background: var(--t-bg-2);
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.16);
  color: var(--t-text-1);
  font-size: 11px;
  line-height: 16px;
  pointer-events: none;
  transform: translate(-50%, -100%);
}

.tooltip strong {
  color: var(--t-text-2);
  font-weight: 600;
}

.tooltip span {
  color: var(--t-text-3);
}
</style>
