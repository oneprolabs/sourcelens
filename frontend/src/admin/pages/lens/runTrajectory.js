export function eventCategory(event) {
  return String(event?.event_type || '').split('.', 1)[0] || 'other'
}

export const ACTIVE_TRAJECTORY_RUN_STATUSES = new Set([
  'queued',
  'running',
  'streaming'
])

export function shouldKeepTrajectoryStream({
  active,
  runUuid,
  runStatus,
  awaitingDone = false
}) {
  return Boolean(
    active &&
      runUuid &&
      (awaitingDone || ACTIVE_TRAJECTORY_RUN_STATUSES.has(runStatus))
  )
}

function dashedUuid(value) {
  const hex = String(value || '')
    .replace(/-/g, '')
    .toLowerCase()
  if (!/^[0-9a-f]{32}$/.test(hex)) return ''
  return [
    hex.slice(0, 8),
    hex.slice(8, 12),
    hex.slice(12, 16),
    hex.slice(16, 20),
    hex.slice(20)
  ].join('-')
}

export function datasourceIdFromPath(path) {
  if (typeof path !== 'string') return ''
  const compact = path.match(/\/sources\/ds_([0-9a-f]{32})(?:\/|$)/i)
  if (compact) return dashedUuid(compact[1])
  const dashed = path.match(
    /\/datasources\/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})(?:\/|$)/i
  )
  return dashed ? dashed[1].toLowerCase() : ''
}

export function workspaceRelativePath(value, options = {}) {
  if (typeof value !== 'string' || !value) return value
  const names = options.datasourceNames || {}
  const lookup = (uuid) =>
    (typeof names.get === 'function' ? names.get(uuid) : names[uuid]) || ''
  const normalized = value.replace(/\\/g, '/')

  const compact = normalized.match(
    /^(?:.*?)\/sources\/ds_([0-9a-f]{32})\/(.+)$/i
  )
  if (compact) {
    const uuid = dashedUuid(compact[1])
    const label = lookup(uuid) || `ds_${compact[1].slice(0, 8)}`
    return `${label}/${compact[2]}`
  }
  const dashed = normalized.match(
    /^(?:.*?)\/datasources\/([0-9a-f-]{36})\/(.+)$/i
  )
  if (dashed) {
    const uuid = dashed[1].toLowerCase()
    const label = lookup(uuid) || `datasource_${uuid.slice(0, 8)}`
    return `${label}/${dashed[2]}`
  }
  const workspace = String(options.workspaceRoot || '').replace(/\/+$/, '')
  if (workspace && normalized.startsWith(`${workspace}/`)) {
    return normalized.slice(workspace.length + 1)
  }
  const session = normalized.match(/^(?:.*?)\/sessions\/[0-9a-f-]{36}\/(.+)$/i)
  if (session) return session[1]
  return value
}

export function trajectoryEventKey(event) {
  const runUuid = String(event?.trace_run_uuid || '')
  const eventId = String(event?.event_id || event?.uuid || '')
  return `${runUuid}:${eventId}`
}

export function mergeTrajectoryEvents(current, incoming) {
  const merged = new Map()
  for (const event of current || []) {
    merged.set(trajectoryEventKey(event), event)
  }
  for (const event of incoming || []) {
    merged.set(trajectoryEventKey(event), event)
  }
  return sortTrajectoryEvents([...merged.values()])
}

export function applyTrajectoryStreamUpdate(current, message) {
  const previousRevision = String(message?.previous_revision || '')
  if (
    message?.type !== 'sync' &&
    previousRevision &&
    current.revision &&
    previousRevision !== current.revision
  ) {
    return { ...current, requiresResync: true }
  }

  return {
    ...current,
    events: mergeTrajectoryEvents(current.events, message?.events),
    summary: message?.summary || current.summary,
    revision: message?.revision || current.revision,
    cursor: message?.cursor || current.cursor,
    sequence: message?.sequence ?? current.sequence,
    run: message?.run || current.run,
    requiresResync: false
  }
}

const INSPECTOR_MIN_WIDTH = 320
const LEDGER_MIN_WIDTH = 420

export function clampInspectorWidth(splitWidth, desiredWidth) {
  const maxWidth = Math.max(0, splitWidth - LEDGER_MIN_WIDTH)
  const minWidth = Math.min(INSPECTOR_MIN_WIDTH, maxWidth)
  return Math.min(Math.max(desiredWidth, minWidth), maxWidth)
}

export function timelineLane(category) {
  if (category === 'model') return 'model'
  if (category === 'tool' || category === 'subtool') return 'tools'
  return 'input'
}

export function isSubagentEvent(category, event) {
  const payload = event?.payload || {}
  if (category === 'model') return payload.is_subagent === true
  return String(payload.name || '') === 'task'
}

function eventTime(event) {
  if (event._ms !== undefined && Number.isFinite(event._ms)) return event._ms
  return new Date(event.timestamp).getTime()
}

export function buildTimelineLanes(events, summary) {
  const first = new Date(summary?.first_timestamp).getTime()
  const last = new Date(summary?.last_timestamp).getTime()
  const total = last - first
  if (!Number.isFinite(total) || total <= 0) {
    return [
      { key: 'input', steps: [] },
      { key: 'model', steps: [] },
      { key: 'tools', steps: [] }
    ]
  }

  const byCall = new Map()
  for (const event of events) {
    if (event.call_id) {
      const list = byCall.get(event.call_id) || []
      list.push(event)
      byCall.set(event.call_id, list)
    }
  }

  const stepsByLane = { input: [], model: [], tools: [] }

  function pushStep(category, event, startMs, endMs, subagent, seqs = []) {
    const lane = timelineLane(category)
    const left = Math.max(0, Math.min(99.5, ((startMs - first) / total) * 100))
    const rawWidth = ((endMs - startMs) / total) * 100
    const width = Math.max(rawWidth, 0.6)
    stepsByLane[lane].push({
      event,
      left,
      width: Math.min(width, 100 - left),
      subagent,
      assistantName: event.assistant_name || null,
      startMs,
      durationMs: endMs - startMs,
      seqs: seqs.length > 0 ? seqs : [event.sequence]
    })
  }

  for (const group of byCall.values()) {
    const category = eventCategory(group[0])
    if (!['model', 'tool', 'subtool'].includes(category)) continue
    const times = group.map(eventTime).filter(Number.isFinite)
    if (!times.length) continue
    const subagent = group.some(
      (event) =>
        event.trace_run_role === 'child' || isSubagentEvent(category, event)
    )
    pushStep(
      category,
      group[0],
      Math.min(...times),
      Math.max(...times),
      subagent,
      group.map((event) => event.sequence)
    )
  }

  for (const event of events) {
    const category = eventCategory(event)
    const inCallGroup =
      Boolean(event.call_id) && ['model', 'tool', 'subtool'].includes(category)
    if (inCallGroup) continue
    const startMs = eventTime(event)
    if (!Number.isFinite(startMs)) continue
    pushStep(category, event, startMs, startMs, false)
  }

  return [
    { key: 'input', steps: stepsByLane.input },
    { key: 'model', steps: stepsByLane.model },
    { key: 'tools', steps: stepsByLane.tools }
  ]
}

export function buildTimelineGroups(
  events,
  summary,
  parentLabel = 'Parent Run',
  laneLabels = { input: 'Input', model: 'Model', tools: 'Tools' }
) {
  const groups = new Map()
  for (const event of events) {
    const isChild = event.trace_run_role === 'child'
    const label = isChild ? event.assistant_name || 'Subagent' : parentLabel
    const key = isChild ? `child:${label}` : 'parent'
    const group = groups.get(key) || { key, label, events: [] }
    group.events.push(event)
    groups.set(key, group)
  }

  return [...groups.values()].flatMap((group) =>
    buildTimelineLanes(group.events, summary).map((lane) => ({
      ...lane,
      key: `${group.key}:${lane.key}`,
      groupKey: group.key,
      groupLabel: group.label,
      label: laneLabels[lane.key]
    }))
  )
}

export function childRunProgress(summary) {
  if (!Array.isArray(summary?.run_progress)) return []
  return summary.run_progress.filter((run) => run?.role === 'child')
}

export function childRunAttempts(run) {
  if (Array.isArray(run?.attempts) && run.attempts.length) {
    return run.attempts
  }
  if (!run?.run_uuid) return []
  return [{ ...run, attempt: 1, retry_of_run_uuid: null }]
}

function eventDepth(event, parentByCall) {
  let parent = event.parent_call_id
  let depth = 0
  const seen = new Set()
  while (parent && !seen.has(parent) && depth < 8) {
    seen.add(parent)
    depth += 1
    parent = parentByCall.get(parent) || ''
  }
  return depth
}

function sequenceValue(event) {
  const value = Number(event?.event_sequence ?? event?.sequence)
  return Number.isFinite(value) ? value : null
}

const TERMINAL_TRAJECTORY_EVENTS = new Set([
  'request.completed',
  'request.failed',
  'run.completed',
  'run.failed',
  'run.cancelled'
])

export function trajectoryStepNumber(event) {
  const suffix =
    eventCategory(event) === 'step'
      ? splitStepName(event).suffix
      : eventSuffix(event)
  if (
    TERMINAL_TRAJECTORY_EVENTS.has(String(event?.event_type || '')) ||
    ['done', 'completed', 'failed', 'cancelled', 'interrupted'].includes(suffix)
  ) {
    return null
  }
  const sequence = sequenceValue(event)
  return Number.isInteger(sequence) && sequence > 0 ? sequence : null
}

export function sortTrajectoryEvents(events) {
  // Sequences belong to one run. Cross-run transport cursors are separate.
  const runOrder = new Map()
  for (const event of events) {
    const key = String(event.trace_run_uuid || '')
    const time = eventTime(event)
    runOrder.set(
      key,
      Math.min(
        runOrder.get(key) ?? Infinity,
        Number.isFinite(time) ? time : Infinity
      )
    )
  }
  const runIndex = new Map(
    [...runOrder.keys()].map((key, index) => [key, index])
  )
  return events
    .map((event, index) => ({ event, index, sequence: sequenceValue(event) }))
    .sort((left, right) => {
      const leftRun = String(left.event.trace_run_uuid || '')
      const rightRun = String(right.event.trace_run_uuid || '')
      if (leftRun !== rightRun) {
        return (
          runOrder.get(leftRun) - runOrder.get(rightRun) ||
          runIndex.get(leftRun) - runIndex.get(rightRun)
        )
      }
      return (
        (left.sequence ?? Infinity) - (right.sequence ?? Infinity) ||
        left.index - right.index
      )
    })
    .map(({ event }) => event)
}

function eventSuffix(event) {
  return (
    String(event?.event_type || '')
      .split('.')
      .pop() || ''
  )
}

const FAILED_STATUSES = new Set([
  'failed',
  'stopped',
  'denied',
  'cancelled',
  'canceled',
  'interrupted'
])

function clamp(value, minimum, maximum) {
  return Math.min(Math.max(value, minimum), maximum)
}

function eventTimeMs(event) {
  if (event?._ms !== undefined && Number.isFinite(event._ms)) return event._ms
  const value = new Date(event?.timestamp).getTime()
  return Number.isFinite(value) ? value : null
}

function optionalNumber(value) {
  if (value === undefined || value === null || value === '') return null
  const number = Number(value)
  return Number.isFinite(number) ? number : null
}

function firstPresent(...values) {
  for (const value of values) {
    if (value !== undefined && value !== null) return value
  }
  return undefined
}

function usageMetric(usage, keys) {
  for (const key of keys) {
    const value = usage?.[key]
    if (value !== undefined && value !== null) return optionalNumber(value)
  }
  return null
}

function usageMetrics(usage) {
  const inputTokens = usageMetric(usage, ['input_tokens', 'prompt_tokens'])
  const outputTokens = usageMetric(usage, [
    'output_tokens',
    'completion_tokens'
  ])
  const promptDetails = usage?.prompt_tokens_details || {}
  const inputDetails = usage?.input_token_details || {}
  const cachedTokens =
    usageMetric(usage, [
      'cached_tokens',
      'cache_read_input_tokens',
      'cache_read_tokens'
    ]) ??
    usageMetric(promptDetails, ['cached_tokens', 'cache_read_tokens']) ??
    usageMetric(inputDetails, ['cache_read'])
  const cacheCreationTokens =
    usageMetric(usage, [
      'cache_creation_input_tokens',
      'cache_write_input_tokens',
      'cache_creation_tokens'
    ]) ?? usageMetric(inputDetails, ['cache_creation', 'cache_write'])
  const reasoningTokens =
    usageMetric(usage, ['reasoning_tokens']) ??
    usageMetric(usage?.completion_tokens_details, ['reasoning_tokens']) ??
    usageMetric(usage?.output_token_details, ['reasoning'])
  const totalTokens = usageMetric(usage, ['total_tokens'])
  return {
    inputTokens,
    outputTokens,
    cachedTokens,
    cacheCreationTokens,
    reasoningTokens,
    totalTokens:
      totalTokens ??
      (inputTokens !== null && outputTokens !== null
        ? inputTokens + outputTokens
        : null)
  }
}

function splitStepName(event) {
  const raw = String(event?.payload?.name || '')
  const match = /^(.*)\.([a-z]+)$/.exec(raw)
  if (!match) return { base: raw, suffix: '' }
  return { base: match[1], suffix: match[2] }
}

function spanStatus(ordered) {
  const suffixes = ordered.map((event) =>
    eventCategory(event) === 'step'
      ? splitStepName(event).suffix
      : eventSuffix(event)
  )
  if (suffixes.includes('cancelled')) return 'cancelled'
  if (suffixes.includes('interrupted')) return 'interrupted'
  if (suffixes.some((suffix) => FAILED_STATUSES.has(suffix))) return 'failed'
  const last = ordered[ordered.length - 1]?.payload || {}
  if (['partial', 'blocked', 'awaiting_user_input'].includes(last.outcome))
    return last.outcome
  if (last.fallback_reason === 'timeout') return 'timeout'
  if (last.fallback_reason || last.verdict === 'fallback') return 'fallback'
  if (last.ok === false || last.health === 'failed') return 'failed'
  if (last.health === 'degraded' || last.truncated) return 'degraded'
  if (suffixes.some((suffix) => ['done', 'completed'].includes(suffix)))
    return 'completed'
  if (
    suffixes.some((suffix) => ['start', 'started'].includes(suffix)) ||
    ordered.some((event) => event.payload?.name === 'deepagents.agent.invoke')
  )
    return 'running'
  return 'point'
}

function buildSpan({
  id,
  name,
  category,
  events,
  parentCallId = null,
  runKey = '',
  recordType = 'event'
}) {
  const ordered = sortTrajectoryEvents(events)
  const startEvent = ordered[0] || null
  const endEvent = ordered[ordered.length - 1] || null
  const times = ordered.map(eventTimeMs).filter((value) => value !== null)
  const startMs = times.length ? Math.min(...times) : null
  const endMs = times.length ? Math.max(...times) : null
  const explicitDuration = optionalNumber(
    firstPresent(
      startEvent?.payload?.duration_ms,
      endEvent?.payload?.duration_ms
    )
  )
  const durationMs =
    startMs !== null && endMs !== null && endMs >= startMs
      ? endMs - startMs
      : explicitDuration
  const usage = [...ordered].reverse().find((event) => event.payload?.usage)
    ?.payload.usage
  const tokenUsage = usageMetrics(usage)
  const ttftEvent = ordered.find(
    (event) => optionalNumber(event?.payload?.ttft_ms) !== null
  )
  const first = (key) =>
    ordered.map((event) => event?.payload?.[key]).find((value) => value != null)
  const baseName = name.split(' · ')[0]
  const boundaryEvents =
    recordType === 'call'
      ? ordered.filter(
          (event) =>
            !event.payload?.diagnostics?.includes('late_event') &&
            (category === 'step'
              ? String(event.payload?.name || '').replace(
                  STEP_STATUS_SUFFIX,
                  ''
                ) === baseName
              : eventCategory(event) === category)
        )
      : []
  const boundarySuffix = (event) =>
    category === 'step' ? splitStepName(event).suffix : eventSuffix(event)
  let statusEvents = boundaryEvents.length ? boundaryEvents : ordered
  if (category === 'run') {
    const latestStart = statusEvents.findLastIndex(
      (event) => event.event_type === 'run.started'
    )
    if (latestStart >= 0) statusEvents = statusEvents.slice(latestStart)
  }
  const started = boundaryEvents.find(
    (event) =>
      ['start', 'started'].includes(boundarySuffix(event)) ||
      ['deepagents.agent.create', 'deepagents.agent.invoke'].includes(
        event.payload?.name
      )
  )
  const finished = (recordType === 'call' ? [...statusEvents] : [])
    .reverse()
    .find(
      (event) =>
        ['done', 'completed'].includes(boundarySuffix(event)) ||
        FAILED_STATUSES.has(boundarySuffix(event))
    )
  const diagnostics = [
    ...new Set(ordered.flatMap((event) => event.payload?.diagnostics || []))
  ]
  if (
    recordType === 'event' &&
    STEP_STATUS_SUFFIX.test(
      String(startEvent?.payload?.name || startEvent?.event_type)
    )
  ) {
    diagnostics.push('unbound_lifecycle')
  }
  const status = spanStatus(statusEvents)
  return {
    id,
    name,
    category,
    recordType,
    diagnostics,
    lifecycle:
      finished?.payload?.lifecycle ||
      (finished
        ? FAILED_STATUSES.has(status)
          ? status
          : 'completed'
        : started
          ? 'running'
          : null),
    outcome: finished?.payload?.outcome || null,
    health: finished?.payload?.health || null,
    usageSource:
      first('usage_source') || (category === 'model' ? 'agent_model' : null),
    startedAt: started?.timestamp || null,
    finishedAt: finished?.timestamp || null,
    runKey,
    stage: startEvent?.payload?.stage || null,
    gate: startEvent?.payload?.gate || null,
    plugin: firstPresent(
      startEvent?.payload?.plugin,
      endEvent?.payload?.plugin,
      ...ordered.map((event) => event?.payload?.plugin)
    ),
    skill: firstPresent(
      startEvent?.payload?.skill,
      startEvent?.payload?.arguments?.skill,
      startEvent?.payload?.skill_name,
      endEvent?.payload?.skill,
      ...ordered.map(
        (event) => event?.payload?.skill ?? event?.payload?.arguments?.skill
      )
    ),
    callId: startEvent?.call_id || null,
    parentCallId: parentCallId || startEvent?.parent_call_id || null,
    startEvent,
    endEvent,
    start: startEvent,
    end: endEvent,
    events: ordered,
    startMs: started ? eventTimeMs(started) : startMs,
    endMs: finished ? eventTimeMs(finished) : endMs,
    durationMs:
      started && finished
        ? Math.max(0, eventTimeMs(finished) - eventTimeMs(started))
        : started
          ? null
          : durationMs,
    status,
    ttftMs: optionalNumber(ttftEvent?.payload?.ttft_ms),
    ...tokenUsage,
    toolCount: optionalNumber(startEvent?.payload?.tool_count),
    skillCount: optionalNumber(startEvent?.payload?.skill_count),
    pluginToolCount: optionalNumber(startEvent?.payload?.plugin_tool_count),
    input: firstPresent(
      first('arguments'),
      first('input'),
      first('params'),
      first('request')
    ),
    output: firstPresent(first('result'), first('output'), first('response')),
    isSubagent:
      ordered.some((event) => event?.trace_run_role === 'child') ||
      ordered.some((event) => event?.payload?.is_subagent === true),
    assistantName: first('assistant_name') || null,
    attempt: optionalNumber(first('attempt')),
    seq: startEvent?.sequence ?? null,
    depth: 0,
    hasChildren: false,
    parentId: null
  }
}

function spanId(kind, runKey, key) {
  return runKey ? `${kind}:${runKey}:${key}` : `${kind}:${key}`
}

function stepSpanName(base, event) {
  const stage = event?.payload?.stage
  if (stage && base === 'deepagents.runtime.stage') {
    return `${base} · ${stage}`
  }
  return base
}

function buildCallSpan(runKey, callId, group) {
  const ordered = sortTrajectoryEvents(group)
  const primary =
    ordered.find((event) => eventCategory(event) !== 'step') || ordered[0] || {}
  const category = eventCategory(primary)
  const name =
    category === 'step'
      ? stepSpanName(
          String(primary?.payload?.name || '').replace(STEP_STATUS_SUFFIX, ''),
          primary
        )
      : String(
          firstPresent(
            primary?.payload?.name,
            primary?.payload?.model_ref,
            callId
          )
        )
  return buildSpan({
    id: spanId('call', runKey, callId),
    recordType: 'call',
    name,
    category,
    events: ordered,
    parentCallId: primary?.parent_call_id || null,
    runKey
  })
}

function makePointSpan(runKey, event) {
  return buildSpan({
    id: spanId('event', runKey, event?.event_id || event?.sequence),
    name: String(
      firstPresent(event?.payload?.name, event?.event_type, 'event')
    ),
    category: eventCategory(event),
    events: [event],
    parentCallId: event?.parent_call_id || null,
    runKey
  })
}

function spanOrder(left, right) {
  if (left.runKey === right.runKey && left.seq != null && right.seq != null) {
    return left.seq - right.seq
  }
  const leftStart = left.startMs === null ? Infinity : left.startMs
  const rightStart = right.startMs === null ? Infinity : right.startMs
  if (leftStart !== rightStart) return leftStart - rightStart
  return (left.seq ?? 0) - (right.seq ?? 0)
}

export function buildTrajectorySpans(
  events,
  { complete = true, runStatuses = {} } = {}
) {
  const ordered = sortTrajectoryEvents(events).map((event) => {
    const payload = event?.payload || {}
    return {
      ...event,
      call_id:
        event?.call_id || payload.call_id || payload.invocation_id || null,
      parent_call_id: event?.parent_call_id || payload.parent_call_id || null
    }
  })
  const spans = []
  const callGroups = new Map()
  const runKeyOf = (event) => String(event?.trace_run_uuid || '')

  for (const event of ordered) {
    const runKey = runKeyOf(event)
    if (event.call_id) {
      const groupKey = `${runKey}|${event.call_id}`
      const group = callGroups.get(groupKey) || {
        runKey,
        callId: event.call_id,
        events: []
      }
      group.events.push(event)
      callGroups.set(groupKey, group)
      continue
    }
    spans.push(makePointSpan(runKey, event))
  }

  for (const group of callGroups.values()) {
    spans.push(buildCallSpan(group.runKey, group.callId, group.events))
  }
  const endedRuns = new Set(
    Object.entries(runStatuses)
      .filter(
        ([, status]) => status && !ACTIVE_TRAJECTORY_RUN_STATUSES.has(status)
      )
      .map(([key]) => key)
  )
  for (const event of ordered) {
    if (
      ['run.completed', 'run.failed', 'run.cancelled'].includes(
        event.event_type
      )
    )
      endedRuns.add(runKeyOf(event))
  }
  for (const span of spans) {
    if (
      complete &&
      span.finishedAt &&
      !span.startedAt &&
      !span.diagnostics.includes('missing_start')
    ) {
      span.diagnostics.push('missing_start')
    }
    if (
      complete &&
      span.startedAt &&
      !span.finishedAt &&
      endedRuns.has(span.runKey)
    ) {
      span.diagnostics.push('missing_end')
      span.status = 'incomplete'
    }
    if (complete && span.category !== 'run' && !span.parentCallId)
      span.diagnostics.push('orphan_event')
  }
  return attachSpanHierarchy(spans, complete)
}

function attachSpanHierarchy(spans, complete) {
  const byId = new Map(spans.map((span) => [span.id, span]))
  for (const span of spans) {
    if (!span.parentCallId) continue
    const parentId = spanId('call', span.runKey, span.parentCallId)
    if (byId.has(parentId)) {
      span.parentId = parentId
      if (span.parentId === span.id) {
        span.diagnostics.push('parent_cycle')
        span.parentId = null
      }
    } else if (complete) {
      span.diagnostics.push('missing_parent')
    }
  }

  // Break malformed parent cycles before building the tree.
  for (const span of spans) {
    const seen = new Set([span.id])
    let parent = span.parentId
    while (parent && byId.has(parent)) {
      if (seen.has(parent)) {
        span.diagnostics.push('parent_cycle')
        span.parentId = null
        break
      }
      seen.add(parent)
      parent = byId.get(parent).parentId
    }
  }

  const children = new Map()
  for (const span of spans) {
    if (span.parentId && byId.has(span.parentId)) {
      const list = children.get(span.parentId) || []
      list.push(span)
      children.set(span.parentId, list)
    } else {
      span.parentId = null
    }
  }

  const ordered = []
  const visited = new Set()
  const visit = (span, depth) => {
    if (visited.has(span.id)) return
    visited.add(span.id)
    span.depth = depth
    const kids = (children.get(span.id) || []).sort(spanOrder)
    span.hasChildren = kids.length > 0
    span.seq = span.startEvent?.sequence ?? null
    ordered.push(span)
    for (const kid of kids) visit(kid, depth + 1)
    span.issueCount =
      span.diagnostics.length +
      kids.reduce((sum, kid) => sum + kid.issueCount, 0)
    span.hasWarnings =
      span.issueCount > 0 ||
      [
        'failed',
        'cancelled',
        'interrupted',
        'incomplete',
        'degraded',
        'fallback',
        'timeout',
        'partial',
        'blocked'
      ].includes(span.status) ||
      kids.some((kid) => kid.hasWarnings)
    // Keep the step's own timing separate from the complete subtree window.
    const starts = [
      span.startMs,
      ...kids.map((kid) => kid.hierarchyStartMs)
    ].filter((value) => Number.isFinite(value))
    const ends = [span.endMs, ...kids.map((kid) => kid.hierarchyEndMs)].filter(
      (value) => Number.isFinite(value)
    )
    span.hierarchyStartMs = starts.length ? Math.min(...starts) : null
    span.hierarchyEndMs = ends.length ? Math.max(...ends) : null
    span.hierarchyDurationMs =
      span.category === 'run' && span.startedAt && span.finishedAt
        ? span.durationMs
        : starts.length && ends.length
          ? Math.max(0, span.hierarchyEndMs - span.hierarchyStartMs)
          : null
    // Sum each call once; cache and reasoning counts are separate breakdowns.
    span.hierarchyTokens = {}
    for (const key of [
      'totalTokens',
      'inputTokens',
      'outputTokens',
      'cachedTokens',
      'cacheCreationTokens',
      'reasoningTokens'
    ]) {
      const values = [
        span[key],
        ...kids.map((kid) => kid.hierarchyTokens[key])
      ].filter((value) => Number.isFinite(value))
      span.hierarchyTokens[key] = values.length
        ? values.reduce((sum, value) => sum + value, 0)
        : null
    }
  }
  for (const root of spans.filter((span) => !span.parentId).sort(spanOrder)) {
    visit(root, 0)
  }
  for (const span of spans) {
    if (!visited.has(span.id)) {
      span.seq = span.startEvent?.sequence ?? null
      ordered.push(span)
    }
  }

  for (const span of ordered) {
    const ancestors = []
    let parent = span.parentId ? byId.get(span.parentId) : null
    const seen = new Set()
    while (parent && !seen.has(parent.id)) {
      seen.add(parent.id)
      ancestors.unshift({
        id: parent.id,
        name: parent.name,
        category: parent.category,
        stage: parent.stage || null,
        gate: parent.gate || null,
        callId: parent.callId || null
      })
      parent = parent.parentId ? byId.get(parent.parentId) : null
    }
    span.ancestors = ancestors
  }
  return ordered
}

export function buildTrajectoryRows(events, collapsed, options = {}) {
  if (!options.aggregateCalls) {
    const orderedEvents = sortTrajectoryEvents(events)
    const parentByCall = new Map()
    const childParents = new Set()
    const firstEventByCall = new Map()
    for (const event of orderedEvents) {
      if (event.call_id && !firstEventByCall.has(event.call_id)) {
        firstEventByCall.set(event.call_id, event)
      }
      if (event.call_id && event.parent_call_id) {
        parentByCall.set(event.call_id, event.parent_call_id)
        childParents.add(event.parent_call_id)
      }
    }
    return orderedEvents.flatMap((event) => {
      let parent = event.parent_call_id
      const seen = new Set()
      while (parent && !seen.has(parent)) {
        if (collapsed.has(parent)) return []
        seen.add(parent)
        parent = parentByCall.get(parent) || ''
      }
      return [
        {
          event,
          stepNumber: trajectoryStepNumber(event),
          depth: eventDepth(event, parentByCall),
          hasChildren: Boolean(
            event.call_id &&
              childParents.has(event.call_id) &&
              firstEventByCall.get(event.call_id) === event
          )
        }
      ]
    })
  }

  const spans = buildTrajectorySpans(events, options)
  const timed = spans.filter((span) => span.hierarchyStartMs !== null)
  const minStart = timed.length
    ? Math.min(...timed.map((span) => span.hierarchyStartMs))
    : 0
  const maxEnd = timed.length
    ? Math.max(
        ...timed.map((span) => span.hierarchyEndMs ?? span.hierarchyStartMs)
      )
    : 0
  const range = Math.max(1, maxEnd - minStart)
  const parentIds = new Map(spans.map((span) => [span.id, span.parentId]))

  return spans.flatMap((span) => {
    let parent = span.parentId
    const seen = new Set()
    while (parent && !seen.has(parent)) {
      if (collapsed.has(parent)) return []
      seen.add(parent)
      parent = parentIds.get(parent) || null
    }
    const isCollapsed = collapsed.has(span.id)
    const showDuration = !span.hasChildren || isCollapsed
    const displayDurationMs = span.hasChildren
      ? span.hierarchyDurationMs
      : span.durationMs
    const displayStartMs = span.hasChildren
      ? span.hierarchyStartMs
      : span.startMs
    const left =
      displayStartMs === null ? 0 : (displayStartMs - minStart) / range
    const width =
      displayDurationMs === null || displayDurationMs === undefined
        ? 0
        : displayDurationMs / range
    return [
      {
        event: { ...span.startEvent, span },
        span,
        stepNumber: trajectoryStepNumber(span.startEvent),
        depth: span.depth,
        hasChildren: span.hasChildren,
        isCollapsed,
        showDuration,
        displayDurationMs,
        showTokens: showDuration,
        displayTokens: span.hasChildren ? span.hierarchyTokens : span,
        waterfall: {
          left: clamp(left * 100, 0, 100),
          width: clamp(
            Math.max(width * 100, span.startMs === null ? 0 : 0.7),
            0,
            100
          )
        }
      }
    ]
  })
}

const STEP_STATUS_SUFFIX =
  /\.(start|started|done|completed|failed|stopped|denied|cancelled|canceled|interrupted)$/

function stepGroupName(event) {
  const name = String(event?.payload?.name || '')
  if (!name) return 'step'
  return name.replace(STEP_STATUS_SUFFIX, '')
}

export function groupTrajectoryRows(rows) {
  const groups = []
  const index = new Map()
  for (const row of rows) {
    const category = eventCategory(row.event)
    let key = category
    let label = category
    if (category === 'step') {
      key = `step:${stepGroupName(row.event)}`
      label = stepGroupName(row.event)
    }
    if (!index.has(key)) {
      const group = { key, category, label, rows: [] }
      index.set(key, group)
      groups.push(group)
    }
    index.get(key).rows.push(row)
  }
  return groups
}
