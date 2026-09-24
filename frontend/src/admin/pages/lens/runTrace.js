export const TRACE_STEP_TYPES = [
  'model',
  'retrieval',
  'decision',
  'tool',
  'artifact',
  'message',
  'reasoning'
]

export function traceEventKey(event) {
  const runUuid = String(event?.trace_run_uuid || '')
  const eventId = String(event?.event_id || event?.uuid || '')
  return `${runUuid}:${eventId}`
}

function sequenceValue(event) {
  const value = Number(event?.sequence)
  return Number.isFinite(value) ? value : null
}

export function sortTraceEvents(events) {
  return (events || [])
    .map((event, index) => ({ event, index, sequence: sequenceValue(event) }))
    .sort((left, right) => {
      if (left.sequence === null && right.sequence === null) {
        return left.index - right.index
      }
      if (left.sequence === null) return 1
      if (right.sequence === null) return -1
      return left.sequence - right.sequence || left.index - right.index
    })
    .map(({ event }) => event)
}

export function mergeTraceEvents(current, incoming) {
  const merged = new Map()
  for (const event of current || []) {
    merged.set(traceEventKey(event), event)
  }
  for (const event of incoming || []) {
    merged.set(traceEventKey(event), event)
  }
  return sortTraceEvents([...merged.values()])
}

export function filterTraceSteps(steps, { type = 'all', query = '' } = {}) {
  const needle = String(query || '')
    .trim()
    .toLowerCase()
  return (steps || []).filter((step) => {
    if (type !== 'all' && step.type !== type) return false
    if (!needle) return true
    const haystack = [
      step.title,
      step.summary,
      step.label,
      step.type,
      JSON.stringify(step.details || {})
    ]
      .join(' ')
      .toLowerCase()
    return haystack.includes(needle)
  })
}

export function traceTypeCounts(steps) {
  const counts = {}
  for (const step of steps || []) {
    counts[step.type] = (counts[step.type] || 0) + 1
  }
  return counts
}

export function buildTraceGraphOrder(steps, edges, layout = 'causal') {
  const list = [...(steps || [])]
  const byStart = (left, right) =>
    (left.start_ms || 0) - (right.start_ms || 0) ||
    String(left.id).localeCompare(String(right.id))
  if (layout !== 'calls') return list.sort(byStart)

  const parentByChild = new Map(
    (edges || [])
      .filter((edge) => edge.kind === 'child')
      .map((edge) => [edge.to, edge.from])
  )
  const depthOf = (stepId) => {
    let depth = 0
    let parent = parentByChild.get(stepId)
    const seen = new Set()
    while (parent && !seen.has(parent) && depth < 32) {
      seen.add(parent)
      depth += 1
      parent = parentByChild.get(parent)
    }
    return depth
  }
  return list.sort(
    (left, right) =>
      depthOf(left.id) - depthOf(right.id) || byStart(left, right)
  )
}

export function formatTraceDuration(value) {
  const ms = Number(value)
  if (value == null || !Number.isFinite(ms)) return '—'
  if (ms < 1000) return `${Math.round(ms)}ms`
  return `${(ms / 1000).toFixed(1)}s`
}

export function formatTraceRelative(value) {
  const ms = Number(value)
  if (value == null || !Number.isFinite(ms)) return '—'
  return `${(ms / 1000).toFixed(1)}s`
}

export function formatTraceCount(value) {
  return Number(value || 0).toLocaleString()
}

export function stepRawEvents(step, events) {
  if (!step) return []
  const ids = new Set(step.event_ids || [])
  return (events || []).filter(
    (event) => ids.has(event.event_id) || event.event_id === step.event_id
  )
}
