import assert from 'node:assert/strict'
import test from 'node:test'

import {
  buildTimelineLanes,
  buildTimelineGroups,
  buildTrajectoryRows,
  buildTrajectorySpans,
  childRunAttempts,
  childRunProgress,
  clampInspectorWidth,
  datasourceIdFromPath,
  eventCategory,
  groupTrajectoryRows,
  isSubagentEvent,
  mergeTrajectoryEvents,
  shouldKeepTrajectoryStream,
  sortTrajectoryEvents,
  timelineLane,
  trajectoryEventKey,
  workspaceRelativePath,
  applyTrajectoryStreamUpdate
} from '../src/admin/pages/lens/runTrajectory.js'

test('trajectory stream merges immutable events by run and event identity', () => {
  const existing = [
    {
      trace_run_uuid: 'parent',
      event_id: 'same-id',
      sequence: 1,
      payload: { state: 'old' }
    }
  ]
  const incoming = [
    {
      trace_run_uuid: 'parent',
      event_id: 'same-id',
      sequence: 1,
      payload: { state: 'new' }
    },
    {
      trace_run_uuid: 'child',
      event_id: 'same-id',
      sequence: 2
    }
  ]

  assert.equal(trajectoryEventKey(existing[0]), 'parent:same-id')
  assert.deepEqual(mergeTrajectoryEvents(existing, incoming), incoming)
})

test('trajectory stream rejects a revision gap and requests resync', () => {
  const current = {
    events: [{ trace_run_uuid: 'parent', event_id: 'one', sequence: 1 }],
    summary: { event_count: 1 },
    revision: 'revision-1',
    cursor: 'cursor-1'
  }

  const result = applyTrajectoryStreamUpdate(current, {
    type: 'append',
    previous_revision: 'missing-revision',
    revision: 'revision-3',
    cursor: 'cursor-3',
    events: [{ trace_run_uuid: 'parent', event_id: 'three', sequence: 3 }]
  })

  assert.equal(result.requiresResync, true)
  assert.deepEqual(result.events, current.events)
  assert.equal(result.revision, current.revision)
})

test('trajectory stream stays connected until terminal completion is confirmed', () => {
  assert.equal(
    shouldKeepTrajectoryStream({
      active: true,
      runUuid: 'run-1',
      runStatus: 'done',
      awaitingDone: true
    }),
    true
  )
  assert.equal(
    shouldKeepTrajectoryStream({
      active: true,
      runUuid: 'run-1',
      runStatus: 'done',
      awaitingDone: false
    }),
    false
  )
})

test('child Run progress excludes the coordinator and keeps task context', () => {
  const progress = childRunProgress({
    run_progress: [
      {
        run_uuid: 'parent',
        role: 'parent',
        assistant_name: 'Smart Collaboration',
        status: 'done'
      },
      {
        run_uuid: 'office',
        role: 'child',
        assistant_name: 'Office',
        status: 'running',
        task: 'Prepare the report',
        duration_ms: 1200,
        event_count: 8
      }
    ]
  })

  assert.deepEqual(progress, [
    {
      run_uuid: 'office',
      role: 'child',
      assistant_name: 'Office',
      status: 'running',
      task: 'Prepare the report',
      duration_ms: 1200,
      event_count: 8
    }
  ])
  assert.deepEqual(childRunProgress({}), [])
})

test('child Run attempts preserve retry order and support legacy rows', () => {
  const attempts = [
    { run_uuid: 'attempt-1', attempt: 1, status: 'failed' },
    { run_uuid: 'attempt-2', attempt: 2, status: 'done' }
  ]

  assert.deepEqual(childRunAttempts({ attempts }), attempts)
  assert.deepEqual(
    childRunAttempts({ run_uuid: 'legacy', status: 'running' }),
    [
      {
        run_uuid: 'legacy',
        status: 'running',
        attempt: 1,
        retry_of_run_uuid: null
      }
    ]
  )
})

test('inspector resizing preserves the minimum ledger width', () => {
  assert.equal(clampInspectorWidth(1200, 700), 700)
  assert.equal(clampInspectorWidth(1200, 900), 780)
  assert.equal(clampInspectorWidth(1200, 100), 320)
})

const events = [
  {
    event_id: 'model-start',
    sequence: 1,
    event_type: 'model.started',
    call_id: 'model-1',
    payload: { name: 'agent', messages: [{ content: 'needle' }] }
  },
  {
    event_id: 'tool-start',
    sequence: 2,
    event_type: 'tool.started',
    call_id: 'tool-1',
    parent_call_id: 'model-1',
    payload: { name: 'search', arguments: { query: 'needle' } }
  },
  {
    event_id: 'tool-end',
    sequence: 3,
    event_type: 'tool.completed',
    call_id: 'tool-1',
    parent_call_id: 'model-1',
    payload: { result: 'found' }
  }
]

test('trajectory rows preserve chronological parent depth', () => {
  const rows = buildTrajectoryRows(events, new Set())

  assert.deepEqual(
    rows.map((row) => [row.event.sequence, row.depth]),
    [
      [1, 0],
      [2, 1],
      [3, 1]
    ]
  )
  assert.equal(rows[0].hasChildren, true)
  assert.equal(rows[1].hasChildren, false)
  assert.equal(rows[2].hasChildren, false)
})

test('trajectory rows sort events by sequence while preserving duplicate order', () => {
  const unordered = [
    { ...events[2] },
    { ...events[0] },
    { ...events[1] },
    { event_id: 'same-sequence', sequence: 2, event_type: 'tool.note' }
  ]

  assert.deepEqual(
    buildTrajectoryRows(unordered, new Set()).map((row) => row.event.event_id),
    ['model-start', 'tool-start', 'same-sequence', 'tool-end']
  )
  assert.deepEqual(
    sortTrajectoryEvents([
      { event_id: 'missing' },
      { event_id: 'three', sequence: 3 },
      { event_id: 'one', sequence: '1' }
    ]).map((event) => event.event_id),
    ['one', 'three', 'missing']
  )
})

test('collapsing a call hides every descendant event', () => {
  const rows = buildTrajectoryRows(events, new Set(['model-1']))

  assert.deepEqual(
    rows.map((row) => row.event.sequence),
    [1]
  )
})

test('trajectory rows group by category and step base name', () => {
  const grouped = groupTrajectoryRows(
    buildTrajectoryRows(
      [
        {
          event_id: 'stage-start',
          sequence: 1,
          event_type: 'step.event',
          payload: { name: 'deepagents.runtime.stage.start' }
        },
        {
          event_id: 'stage-done',
          sequence: 2,
          event_type: 'step.event',
          payload: { name: 'deepagents.runtime.stage.done' }
        },
        {
          event_id: 'model-start',
          sequence: 3,
          event_type: 'model.started',
          call_id: 'model-1'
        },
        {
          event_id: 'resource',
          sequence: 4,
          event_type: 'step.event',
          payload: { name: 'resources.materialized' }
        }
      ],
      new Set()
    )
  )

  assert.deepEqual(
    grouped.map((group) => group.label),
    ['deepagents.runtime.stage', 'model', 'resources.materialized']
  )
  assert.equal(grouped[0].rows.length, 2)
  assert.equal(grouped[1].category, 'model')
  assert.equal(grouped[2].rows.length, 1)
})

test('trajectory events expose their filter category', () => {
  assert.equal(eventCategory(events[1]), 'tool')
})

test('timeline lane mapping groups categories into three lanes', () => {
  assert.equal(timelineLane('model'), 'model')
  assert.equal(timelineLane('tool'), 'tools')
  assert.equal(timelineLane('subtool'), 'tools')
  assert.equal(timelineLane('request'), 'input')
  assert.equal(timelineLane('user'), 'input')
})

test('subagent detection reads model is_subagent and task tool name', () => {
  assert.equal(
    isSubagentEvent('model', {
      payload: { is_subagent: true }
    }),
    true
  )
  assert.equal(isSubagentEvent('tool', { payload: { name: 'task' } }), true)
  assert.equal(isSubagentEvent('tool', { payload: { name: 'search' } }), false)
})

test('timeline lanes fill per-call duration and keep events on lanes', () => {
  const span = 10 * 1000
  const summary = {
    first_timestamp: new Date(0).toISOString(),
    last_timestamp: new Date(span).toISOString()
  }
  const timelineEvents = [
    {
      event_id: 'request',
      sequence: 1,
      event_type: 'request.started',
      timestamp: new Date(0).toISOString()
    },
    {
      event_id: 'model-start',
      sequence: 2,
      event_type: 'model.started',
      call_id: 'model-1',
      timestamp: new Date(1000).toISOString(),
      payload: { name: 'agent' }
    },
    {
      event_id: 'model-end',
      sequence: 3,
      event_type: 'model.completed',
      call_id: 'model-1',
      timestamp: new Date(4000).toISOString(),
      payload: { name: 'agent', duration_ms: 3000 }
    },
    {
      event_id: 'tool-start',
      sequence: 4,
      event_type: 'tool.started',
      call_id: 'tool-1',
      parent_call_id: 'model-1',
      timestamp: new Date(5000).toISOString(),
      payload: { name: 'task', arguments: {} }
    },
    {
      event_id: 'tool-end',
      sequence: 5,
      event_type: 'tool.completed',
      call_id: 'tool-1',
      parent_call_id: 'model-1',
      timestamp: new Date(8000).toISOString(),
      payload: { name: 'task', result: 'ok' }
    }
  ]
  const lanes = buildTimelineLanes(timelineEvents, summary)

  assert.deepEqual(
    lanes.map((lane) => lane.key),
    ['input', 'model', 'tools']
  )
  assert.equal(lanes[0].steps.length, 1)
  assert.equal(lanes[0].steps[0].event.event_type, 'request.started')

  const modelStep = lanes[1].steps[0]
  assert.equal(modelStep.event.event_id, 'model-start')
  assert.equal(modelStep.left, 10)
  assert.equal(modelStep.width, 30)
  assert.equal(modelStep.subagent, false)
  assert.equal(modelStep.startMs, 1000)
  assert.equal(modelStep.durationMs, 3000)

  const toolStep = lanes[2].steps[0]
  assert.equal(toolStep.event.event_id, 'tool-start')
  assert.equal(toolStep.left, 50)
  assert.equal(toolStep.width, 30)
  assert.equal(toolStep.subagent, true)
  assert.equal(toolStep.startMs, 5000)
  assert.equal(toolStep.durationMs, 3000)
})

test('timeline groups keep input, model and tools together per assistant', () => {
  const summary = {
    first_timestamp: new Date(0).toISOString(),
    last_timestamp: new Date(1000).toISOString()
  }
  const events = ['parent', 'Office', 'ttt'].flatMap((assistant, index) => [
    {
      event_id: `${assistant}-input`,
      event_type: 'request.started',
      timestamp: new Date(index * 100).toISOString(),
      ...(assistant === 'parent'
        ? {}
        : { trace_run_role: 'child', assistant_name: assistant })
    },
    {
      event_id: `${assistant}-model`,
      event_type: 'model.completed',
      call_id: `${assistant}-model-call`,
      timestamp: new Date(index * 100 + 10).toISOString(),
      ...(assistant === 'parent'
        ? {}
        : { trace_run_role: 'child', assistant_name: assistant })
    },
    {
      event_id: `${assistant}-tool`,
      event_type: 'tool.completed',
      call_id: `${assistant}-tool-call`,
      timestamp: new Date(index * 100 + 20).toISOString(),
      ...(assistant === 'parent'
        ? {}
        : { trace_run_role: 'child', assistant_name: assistant })
    }
  ])

  const groups = buildTimelineGroups(events, summary, 'Smart Collaboration')

  assert.deepEqual(
    groups.map((lane) => [lane.groupLabel, lane.label]),
    [
      ['Smart Collaboration', 'Input'],
      ['Smart Collaboration', 'Model'],
      ['Smart Collaboration', 'Tools'],
      ['Office', 'Input'],
      ['Office', 'Model'],
      ['Office', 'Tools'],
      ['ttt', 'Input'],
      ['ttt', 'Model'],
      ['ttt', 'Tools']
    ]
  )
})

test('timeline groups keep a direct assistant run as one three-lane group', () => {
  const summary = {
    first_timestamp: new Date(0).toISOString(),
    last_timestamp: new Date(1000).toISOString()
  }
  const groups = buildTimelineGroups(
    [
      {
        event_id: 'direct-model',
        event_type: 'model.completed',
        timestamp: new Date(500).toISOString()
      }
    ],
    summary,
    'AGIOne-AI-Assistant'
  )

  assert.deepEqual(
    groups.map((lane) => [lane.groupLabel, lane.label]),
    [
      ['AGIOne-AI-Assistant', 'Input'],
      ['AGIOne-AI-Assistant', 'Model'],
      ['AGIOne-AI-Assistant', 'Tools']
    ]
  )
})

test('timeline lanes treat single events as minimal markers', () => {
  const summary = {
    first_timestamp: new Date(0).toISOString(),
    last_timestamp: new Date(1000).toISOString()
  }
  const lanes = buildTimelineLanes(
    [
      {
        event_id: 'user',
        sequence: 1,
        event_type: 'user.message',
        timestamp: new Date(500).toISOString()
      }
    ],
    summary
  )
  assert.equal(lanes[0].steps.length, 1)
  assert.equal(lanes[0].steps[0].width, 0.6)
})

test('timeline lanes return empty steps without a valid window', () => {
  const lanes = buildTimelineLanes(
    [{ event_id: 'x', event_type: 'model.started', timestamp: null }],
    { first_timestamp: null, last_timestamp: null }
  )
  lanes.forEach((lane) => assert.equal(lane.steps.length, 0))
})

const at = (ms) => new Date(ms).toISOString()

const spanEvents = [
  {
    event_id: 'request',
    sequence: 1,
    event_type: 'request.started',
    timestamp: at(0)
  },
  {
    event_id: 'user',
    sequence: 2,
    event_type: 'user.message',
    timestamp: at(0)
  },
  {
    event_id: 'runtime',
    call_id: 'runtime',
    sequence: 3,
    event_type: 'step.event',
    timestamp: at(0),
    payload: { name: 'deepagents.runtime.start' }
  },
  {
    event_id: 'model-start',
    sequence: 4,
    event_type: 'model.started',
    call_id: 'model-1',
    timestamp: at(1000),
    payload: { name: 'model.agent' }
  },
  {
    event_id: 'model-first',
    sequence: 5,
    event_type: 'model.first_token',
    call_id: 'model-1',
    timestamp: at(1100),
    payload: { ttft_ms: 100 }
  },
  {
    event_id: 'model-message',
    sequence: 6,
    event_type: 'assistant.message',
    call_id: 'model-1',
    timestamp: at(1500),
    payload: { content: 'thinking' }
  },
  {
    event_id: 'tool-start',
    sequence: 7,
    event_type: 'tool.started',
    call_id: 'tool-1',
    parent_call_id: 'model-1',
    timestamp: at(1600),
    payload: { name: 'search_workspace', arguments: { query: 'needle' } }
  },
  {
    event_id: 'tool-step-start',
    call_id: 'tool-1',
    parent_call_id: 'model-1',
    sequence: 8,
    event_type: 'step.event',
    timestamp: at(1600),
    payload: { name: 'tool.search_workspace.start' }
  },
  {
    event_id: 'tool-end',
    sequence: 9,
    event_type: 'tool.completed',
    call_id: 'tool-1',
    parent_call_id: 'model-1',
    timestamp: at(3000),
    payload: { name: 'search_workspace', result: { count: 3 } }
  },
  {
    event_id: 'tool-step-done',
    call_id: 'tool-1',
    parent_call_id: 'model-1',
    sequence: 10,
    event_type: 'step.event',
    timestamp: at(3000),
    payload: { name: 'tool.search_workspace.done' }
  },
  {
    event_id: 'gate-start',
    sequence: 11,
    event_type: 'step.event',
    timestamp: at(2000),
    payload: {
      name: 'deepagents.decision.gate.start',
      call_id: 'gate:search_needed',
      parent_call_id: 'model-1'
    }
  },
  {
    event_id: 'gate-done',
    sequence: 12,
    event_type: 'step.event',
    timestamp: at(2200),
    payload: {
      name: 'deepagents.decision.gate.done',
      call_id: 'gate:search_needed',
      parent_call_id: 'model-1'
    }
  },
  {
    event_id: 'model-end',
    sequence: 13,
    event_type: 'model.completed',
    call_id: 'model-1',
    timestamp: at(4000),
    payload: { usage: { total_tokens: 123 } }
  }
]

test('trajectory spans group explicit lifecycle and internal progress IDs', () => {
  const spans = buildTrajectorySpans(spanEvents)
  const byId = Object.fromEntries(spans.map((span) => [span.id, span]))

  assert.equal(byId['call:model-1'].category, 'model')
  assert.equal(byId['call:model-1'].depth, 0)
  assert.equal(byId['call:model-1'].hasChildren, true)
  assert.equal(byId['call:model-1'].status, 'completed')
  assert.equal(byId['call:model-1'].durationMs, 3000)
  assert.equal(byId['call:model-1'].ttftMs, 100)
  assert.equal(byId['call:model-1'].totalTokens, 123)
  assert.equal(byId['call:model-1'].events.length, 4)

  const tool = byId['call:tool-1']
  assert.equal(tool.parentId, 'call:model-1')
  assert.equal(tool.depth, 1)
  assert.deepEqual(tool.input, { query: 'needle' })
  assert.deepEqual(tool.output, { count: 3 })
  assert.equal(tool.durationMs, 1400)

  const gate = byId['call:gate:search_needed']
  assert.equal(gate.name, 'deepagents.decision.gate')
  assert.equal(gate.status, 'completed')
  assert.equal(gate.depth, 1)

  assert.equal(
    spans.some((span) => span.name.startsWith('tool.search_workspace')),
    false
  )
})

test('open call spans are marked as running', () => {
  const rows = buildTrajectoryRows(
    [
      {
        event_id: 'model-started',
        sequence: 1,
        event_type: 'model.started',
        call_id: 'model-1',
        timestamp: at(0),
        payload: { name: 'model.agent' }
      }
    ],
    new Set(),
    { aggregateCalls: true }
  )

  assert.equal(rows[0].span.status, 'running')
})

test('model spans normalize token usage and cache metrics', () => {
  const rows = buildTrajectoryRows(
    [
      {
        event_id: 'model-started',
        sequence: 1,
        event_type: 'model.started',
        call_id: 'model-usage',
        timestamp: at(0),
        payload: { name: 'model.agent' }
      },
      {
        event_id: 'model-completed',
        sequence: 2,
        event_type: 'model.completed',
        call_id: 'model-usage',
        timestamp: at(1200),
        payload: {
          usage: {
            prompt_tokens: 1000,
            completion_tokens: 240,
            total_tokens: 1240,
            prompt_tokens_details: { cached_tokens: 600 }
          },
          ttft_ms: 180
        }
      }
    ],
    new Set(),
    { aggregateCalls: true }
  )

  assert.deepEqual(
    [
      rows[0].span.inputTokens,
      rows[0].span.outputTokens,
      rows[0].span.cachedTokens,
      rows[0].span.totalTokens,
      rows[0].span.ttftMs
    ],
    [1000, 240, 600, 1240, 180]
  )
})

test('aggregated trajectory rows expose sorted nesting and waterfall geometry', () => {
  const rows = buildTrajectoryRows(spanEvents, new Set(), {
    aggregateCalls: true
  })

  assert.deepEqual(
    rows.map((row) => [row.span.id, row.depth]),
    [
      ['event:request', 0],
      ['event:user', 0],
      ['call:runtime', 0],
      ['call:model-1', 0],
      ['call:tool-1', 1],
      ['call:gate:search_needed', 1]
    ]
  )

  const model = rows.find((row) => row.span.id === 'call:model-1')
  assert.equal(model.waterfall.left, 25)
  assert.equal(model.waterfall.width, 75)

  const tool = rows.find((row) => row.span.id === 'call:tool-1')
  assert.equal(tool.waterfall.left, 40)
  assert.equal(tool.waterfall.width, 35)
  assert.equal(tool.event.event_id, 'tool-start')
  assert.equal(tool.event.span, tool.span)
})

test('collapsing an aggregated model span hides its nested calls', () => {
  const rows = buildTrajectoryRows(spanEvents, new Set(['call:model-1']), {
    aggregateCalls: true
  })

  assert.equal(
    rows.some((row) => row.span.id === 'call:tool-1'),
    false
  )
  assert.equal(
    rows.some((row) => row.span.id.startsWith('call:gate')),
    false
  )
  assert.equal(
    rows.some((row) => row.span.id === 'call:model-1'),
    true
  )
})

test('phases and the agent loop attach only to explicit parents', () => {
  const event = (sequence, call_id, parent_call_id, name, time) => ({
    event_id: String(sequence),
    sequence,
    call_id,
    parent_call_id,
    event_type: 'step.event',
    timestamp: at(time),
    payload: { name }
  })
  const events = [
    event(1, 'root', null, 'deepagents.runtime.start', 0),
    event(2, 'stage', 'root', 'deepagents.runtime.stage.start', 10),
    {
      ...event(3, 'control', 'stage', 'model.control', 20),
      event_type: 'model.started'
    },
    {
      ...event(4, 'control', 'stage', 'model.control', 80),
      event_type: 'model.completed'
    },
    event(5, 'stage', 'root', 'deepagents.runtime.stage.done', 90),
    event(6, 'loop', 'root', 'deepagents.agent.invoke', 100),
    {
      ...event(7, 'model', 'loop', 'model.agent', 110),
      event_type: 'model.started'
    },
    {
      ...event(8, 'tool', 'model', 'search_workspace', 120),
      event_type: 'tool.started'
    },
    {
      ...event(9, 'tool', 'model', 'search_workspace', 180),
      event_type: 'tool.completed'
    },
    {
      ...event(10, 'model', 'loop', 'model.agent', 200),
      event_type: 'model.completed'
    },
    event(11, 'loop', 'root', 'deepagents.agent.invoke.done', 290),
    event(12, 'root', null, 'deepagents.runtime.done', 300)
  ]
  const spans = buildTrajectorySpans(events)
  assert.deepEqual(
    spans.map(({ callId, depth }) => [callId, depth]),
    [
      ['root', 0],
      ['stage', 1],
      ['control', 2],
      ['loop', 1],
      ['model', 2],
      ['tool', 3]
    ]
  )
  assert.equal(spans.find((s) => s.callId === 'loop').durationMs, 190)
})

test('agent creation is a separate step from the execution loop', () => {
  const events = [
    { event_type: 'run.started', call_id: 'root', payload: { name: 'run' } },
    {
      event_type: 'step.event',
      call_id: 'create',
      parent_call_id: 'root',
      payload: {
        name: 'deepagents.agent.create',
        tool_count: 24,
        skill_count: 2
      }
    },
    {
      event_type: 'step.event',
      call_id: 'create',
      parent_call_id: 'root',
      payload: { name: 'deepagents.agent.create.done' }
    },
    {
      event_type: 'step.event',
      call_id: 'loop',
      parent_call_id: 'root',
      payload: { name: 'deepagents.agent.invoke' }
    },
    {
      event_type: 'model.started',
      call_id: 'model',
      parent_call_id: 'loop',
      payload: { name: 'model.agent' }
    },
    {
      event_type: 'model.completed',
      call_id: 'model',
      parent_call_id: 'loop',
      payload: { usage: { total_tokens: 1 } }
    },
    {
      event_type: 'step.event',
      call_id: 'loop',
      parent_call_id: 'root',
      payload: { name: 'deepagents.agent.invoke.done' }
    },
    {
      event_type: 'run.completed',
      call_id: 'root',
      payload: { outcome: 'completed' }
    }
  ].map((event, index) => ({
    ...event,
    event_id: String(index),
    sequence: index + 1,
    timestamp: at(index * 10)
  }))
  const spans = buildTrajectorySpans(events)
  assert.deepEqual(
    spans.map(({ callId, depth }) => [callId, depth]),
    [
      ['root', 0],
      ['create', 1],
      ['loop', 1],
      ['model', 2]
    ]
  )
  const create = spans.find((s) => s.callId === 'create')
  assert.equal(create.hasChildren, false)
  assert.equal(create.toolCount, 24)
  assert.equal(create.skillCount, 2)
  assert.equal(create.durationMs, 10)
})

test('explicit runtime call ids build the span tree directly', () => {
  const events = [
    {
      event_id: 'r',
      sequence: 1,
      event_type: 'step.event',
      timestamp: at(0),
      call_id: 'run:1',
      payload: { name: 'deepagents.runtime.start' }
    },
    {
      event_id: 's1',
      sequence: 2,
      event_type: 'step.event',
      timestamp: at(1),
      call_id: 'stage:2',
      parent_call_id: 'run:1',
      payload: { name: 'deepagents.runtime.stage.start', stage: 'resources' }
    },
    {
      event_id: 's2',
      sequence: 3,
      event_type: 'step.event',
      timestamp: at(2),
      call_id: 'stage:2',
      parent_call_id: 'run:1',
      payload: { name: 'deepagents.runtime.stage.done', stage: 'resources' }
    },
    {
      event_id: 'c',
      sequence: 4,
      event_type: 'step.event',
      timestamp: at(3),
      call_id: 'agent:3',
      parent_call_id: 'run:1',
      payload: { name: 'deepagents.agent.create', tool_count: 9 }
    },
    {
      event_id: 'i',
      sequence: 5,
      event_type: 'step.event',
      timestamp: at(4),
      call_id: 'agent-loop:4',
      parent_call_id: 'agent:3',
      payload: { name: 'deepagents.agent.invoke', max_agent_turns: 100 }
    },
    {
      event_id: 'm1',
      sequence: 6,
      event_type: 'model.started',
      timestamp: at(5),
      call_id: 'm1',
      parent_call_id: 'agent-loop:4',
      payload: { name: 'model.agent' }
    },
    {
      event_id: 't1',
      sequence: 7,
      event_type: 'tool.started',
      timestamp: at(6),
      call_id: 't1',
      parent_call_id: 'm1',
      payload: { name: 'search_workspace' }
    },
    {
      event_id: 't2',
      sequence: 8,
      event_type: 'tool.completed',
      timestamp: at(7),
      call_id: 't1',
      parent_call_id: 'm1',
      payload: { name: 'search_workspace', result: {} }
    },
    {
      event_id: 'm2',
      sequence: 9,
      event_type: 'model.completed',
      timestamp: at(8),
      call_id: 'm1',
      parent_call_id: 'agent-loop:4',
      payload: { name: 'model.agent', usage: { total_tokens: 1 } }
    }
  ]

  const rows = buildTrajectoryRows(events, new Set(), { aggregateCalls: true })
  const depthByName = Object.fromEntries(
    rows.map((row) => [row.span.name, row.depth])
  )

  assert.equal(depthByName['deepagents.runtime'], 0)
  assert.equal(depthByName['deepagents.runtime.stage · resources'], 1)
  assert.equal(depthByName['deepagents.agent.create'], 1)
  assert.equal(depthByName['deepagents.agent.invoke'], 2)
  assert.equal(depthByName['model.agent'], 3)
  assert.equal(depthByName['search_workspace'], 4)
})

test('tool spans resolve plugin and skill identities', () => {
  const events = [
    {
      event_id: 'p1',
      sequence: 1,
      event_type: 'step.event',
      timestamp: at(0),
      payload: {
        name: 'tool.plugin.start',
        plugin: 'github',
        tool: 'github_pr_get',
        invocation_id: 'call_1'
      }
    },
    {
      event_id: 't1',
      sequence: 2,
      event_type: 'tool.started',
      call_id: 'call_1',
      parent_call_id: 'm1',
      timestamp: at(1),
      payload: { name: 'github_pr_get', arguments: { number: 1 } }
    },
    {
      event_id: 't2',
      sequence: 3,
      event_type: 'tool.completed',
      call_id: 'call_1',
      parent_call_id: 'm1',
      timestamp: at(2),
      payload: { name: 'github_pr_get', result: { ok: true } }
    },
    {
      event_id: 'p2',
      sequence: 4,
      event_type: 'step.event',
      timestamp: at(3),
      payload: {
        name: 'tool.plugin.done',
        plugin: 'github',
        tool: 'github_pr_get',
        invocation_id: 'call_1'
      }
    },
    {
      event_id: 's1',
      sequence: 5,
      event_type: 'tool.started',
      call_id: 'call_2',
      timestamp: at(4),
      payload: {
        name: 'run_skill_script',
        arguments: { skill: 'engineering-report', script: 'build.sh' }
      }
    },
    {
      event_id: 's2',
      sequence: 6,
      event_type: 'tool.completed',
      call_id: 'call_2',
      timestamp: at(5),
      payload: { name: 'run_skill_script', result: { ok: true } }
    }
  ]

  const spans = buildTrajectorySpans(events)
  const byId = Object.fromEntries(spans.map((span) => [span.id, span]))

  assert.equal(byId['call:call_1'].plugin, 'github')
  assert.equal(byId['call:call_2'].skill, 'engineering-report')
  assert.equal(
    spans.some((span) => span.name === 'tool.plugin'),
    false
  )
})

test('merged spans surface plugin identity from any member event', () => {
  const events = [
    {
      event_id: 'g1',
      sequence: 1,
      event_type: 'step.event',
      timestamp: at(0),
      call_id: 'gate:1',
      payload: { name: 'deepagents.decision.gate.start', gate: 'search_needed' }
    },
    {
      event_id: 'p1',
      sequence: 2,
      event_type: 'step.event',
      timestamp: at(1),
      call_id: 'gate:1',
      payload: {
        name: 'tool.plugin.start',
        plugin: 'typesafe',
        tool: 'typesafe_noul'
      }
    },
    {
      event_id: 'p2',
      sequence: 3,
      event_type: 'step.event',
      timestamp: at(2),
      call_id: 'gate:1',
      payload: { name: 'tool.plugin.done', plugin: 'typesafe', ok: true }
    },
    {
      event_id: 'g2',
      sequence: 4,
      event_type: 'step.event',
      timestamp: at(3),
      call_id: 'gate:1',
      payload: {
        name: 'deepagents.decision.gate.done',
        gate: 'search_needed',
        verdict: 'accept',
        value: 0.9,
        threshold: 0.5
      }
    }
  ]

  const spans = buildTrajectorySpans(events)
  const gate = spans.find((span) => span.name === 'deepagents.decision.gate')
  assert.equal(gate.plugin, 'typesafe')
  assert.equal(gate.events.length, 4)
})

test('explicit evidence review spans skip frontend synthesis', () => {
  const events = [
    {
      event_id: 'r1',
      sequence: 1,
      event_type: 'step.event',
      timestamp: at(0),
      call_id: 'evidence:1',
      parent_call_id: 'agent-loop:1',
      payload: { name: 'deepagents.evidence.review.start' }
    },
    {
      event_id: 'g1',
      sequence: 2,
      event_type: 'step.event',
      timestamp: at(1),
      call_id: 'gate:1',
      parent_call_id: 'evidence:1',
      payload: { name: 'deepagents.decision.gate.start', gate: 'search_needed' }
    },
    {
      event_id: 'g2',
      sequence: 3,
      event_type: 'step.event',
      timestamp: at(2),
      call_id: 'gate:1',
      parent_call_id: 'evidence:1',
      payload: {
        name: 'deepagents.decision.gate.done',
        gate: 'search_needed',
        verdict: 'accept'
      }
    },
    {
      event_id: 'v1',
      sequence: 4,
      event_type: 'step.event',
      timestamp: at(3),
      call_id: 'evidence:1',
      parent_call_id: 'agent-loop:1',
      payload: { name: 'deepagents.evidence.verified', verdicts: {} }
    },
    {
      event_id: 'r2',
      sequence: 5,
      event_type: 'step.event',
      timestamp: at(4),
      call_id: 'evidence:1',
      parent_call_id: 'agent-loop:1',
      payload: { name: 'deepagents.evidence.review.done' }
    }
  ]

  const spans = buildTrajectorySpans(events)
  const reviews = spans.filter(
    (span) => span.name === 'deepagents.evidence.review'
  )
  assert.equal(reviews.length, 1)
  assert.equal(reviews[0].events.length, 3)

  const rows = buildTrajectoryRows(events, new Set(), { aggregateCalls: true })
  const depthById = Object.fromEntries(
    rows.map((row) => [row.span.id, row.depth])
  )
  assert.equal(depthById['call:evidence:1'], 0)
  assert.equal(depthById['call:gate:1'], 1)
})

test('workspace-relative paths resolve datasource names', () => {
  const names = new Map([
    ['2af51ebd-e319-4cb1-9a26-9dfaaf88c9b0', 'AGIOneDocs']
  ])
  const sessionPath =
    '/workspace/sessions/196a0425-2f91-42d9-a0d5-b20569296ea5/' +
    'sources/ds_2af51ebde3194cb19a269dfaaf88c9b0/docs/index.md'

  assert.equal(
    datasourceIdFromPath(sessionPath),
    '2af51ebd-e319-4cb1-9a26-9dfaaf88c9b0'
  )
  assert.equal(
    workspaceRelativePath(sessionPath, { datasourceNames: names }),
    'AGIOneDocs/docs/index.md'
  )
  assert.equal(
    workspaceRelativePath(
      '/workspace/datasources/2af51ebd-e319-4cb1-9a26-9dfaaf88c9b0/docs/a.md',
      { datasourceNames: names }
    ),
    'AGIOneDocs/docs/a.md'
  )
  assert.equal(
    workspaceRelativePath(
      '/workspace/sessions/196a0425-2f91-42d9-a0d5-b20569296ea5/' +
        'sources/ds_ffffffffffffffffffffffffffffffff/x.md',
      { datasourceNames: names }
    ),
    'ds_ffffffff/x.md'
  )
  assert.equal(
    workspaceRelativePath('/opt/lensnode/docs/a.md', {
      workspaceRoot: '/opt/lensnode'
    }),
    'docs/a.md'
  )
  assert.equal(
    workspaceRelativePath('docs/a.md', { workspaceRoot: '/opt/lensnode' }),
    'docs/a.md'
  )
  assert.equal(
    workspaceRelativePath(
      '/workspace/sessions/196a0425-2f91-42d9-a0d5-b20569296ea5/tmp/x.txt'
    ),
    'tmp/x.txt'
  )
})

test('instant invocation annotations never imply a running call', () => {
  const spans = buildTrajectorySpans([
    {
      event_id: 'annotation',
      sequence: 1,
      event_type: 'step.event',
      timestamp: at(0),
      payload: { name: 'tool.read_file.invoke' }
    },
    {
      event_id: 'loop',
      sequence: 2,
      event_type: 'step.event',
      call_id: 'loop',
      timestamp: at(0),
      payload: { name: 'deepagents.agent.invoke' }
    }
  ])
  assert.equal(
    spans.find((s) => s.startEvent.event_id === 'annotation').status,
    'point'
  )
  assert.equal(spans.find((s) => s.callId === 'loop').status, 'running')
})

test('compaction completion snapshots do not invent call lifecycle boundaries', () => {
  const spans = buildTrajectorySpans([
    {
      event_id: 'root',
      sequence: 1,
      event_type: 'run.started',
      call_id: 'root',
      timestamp: at(0)
    },
    {
      event_id: 'compaction',
      sequence: 2,
      event_type: 'compaction.completed',
      parent_call_id: 'root',
      timestamp: at(100),
      payload: {
        name: 'deepagents.summarization.compacted',
        before_tokens: 32193,
        after_tokens: 15589,
        saved_tokens: 16604
      }
    }
  ])
  const snapshot = spans.find(
    (span) => span.startEvent.event_id === 'compaction'
  )
  assert.equal(snapshot.recordType, 'event')
  assert.equal(snapshot.startedAt, null)
  assert.equal(snapshot.finishedAt, null)
  assert.equal(snapshot.lifecycle, null)
  assert.deepEqual(snapshot.diagnostics, [])
  assert.equal(snapshot.parentId, 'call:root')
})

test('business outcomes and gate fallbacks are distinct from successful completion', () => {
  for (const outcome of ['partial', 'blocked', 'awaiting_user_input']) {
    const spans = buildTrajectorySpans([
      {
        event_id: 'start',
        sequence: 1,
        event_type: 'run.started',
        call_id: 'root',
        timestamp: at(0)
      },
      {
        event_id: 'end',
        sequence: 2,
        event_type: 'run.completed',
        call_id: 'root',
        timestamp: at(100),
        payload: { lifecycle: 'completed', outcome, health: 'degraded' }
      }
    ])
    assert.equal(spans[0].status, outcome)
    assert.equal(spans[0].lifecycle, 'completed')
  }
  for (const [reason, status] of [
    ['timeout', 'timeout'],
    ['low_confidence', 'fallback']
  ]) {
    const spans = buildTrajectorySpans([
      {
        event_id: 'start',
        sequence: 1,
        event_type: 'step.event',
        call_id: 'gate',
        timestamp: at(0),
        payload: { name: 'deepagents.decision.gate.start' }
      },
      {
        event_id: 'end',
        sequence: 2,
        event_type: 'step.event',
        call_id: 'gate',
        timestamp: at(100),
        payload: {
          name: 'deepagents.decision.gate.done',
          fallback_reason: reason
        }
      }
    ])
    assert.equal(spans[0].status, status)
  }
})

test('late completion cannot replace interruption or extend the run duration', () => {
  const events = [
    { event_type: 'run.started', call_id: 'root', payload: { name: 'run' } },
    { event_type: 'model.started', call_id: 'model', parent_call_id: 'root' },
    {
      event_type: 'model.interrupted',
      call_id: 'model',
      parent_call_id: 'root',
      payload: { reason: 'cancelled' }
    },
    {
      event_type: 'run.cancelled',
      call_id: 'root',
      payload: { lifecycle: 'cancelled' }
    },
    {
      event_type: 'model.completed',
      call_id: 'model',
      parent_call_id: 'root',
      payload: {
        diagnostics: ['late_event', 'after_run_finished'],
        usage: { total_tokens: 10 }
      }
    }
  ].map((e, i) => ({
    ...e,
    event_id: String(i),
    sequence: i + 1,
    timestamp: at(i * 100)
  }))
  const spans = buildTrajectorySpans(events)
  const model = spans.find((s) => s.callId === 'model')
  assert.equal(model.status, 'interrupted')
  assert.equal(model.durationMs, 100)
  assert.deepEqual(model.diagnostics, ['late_event', 'after_run_finished'])
  const rows = buildTrajectoryRows(events, new Set(['call:root']), {
    aggregateCalls: true
  })
  assert.equal(rows.length, 1)
  assert.equal(rows[0].displayDurationMs, 300)
  assert.equal(rows[0].displayTokens.totalTokens, 10)
})

test('missing lifecycle and parent records are diagnosed only for complete data', () => {
  const events = [
    {
      event_id: 'root',
      sequence: 1,
      event_type: 'run.completed',
      call_id: 'root',
      timestamp: at(100)
    },
    {
      event_id: 'orphan',
      sequence: 2,
      event_type: 'model.started',
      call_id: 'model',
      parent_call_id: 'absent',
      timestamp: at(0)
    }
  ]
  const spans = buildTrajectorySpans(events)
  const model = spans.find((s) => s.callId === 'model')
  assert.equal(model.status, 'incomplete')
  assert.ok(model.diagnostics.includes('missing_end'))
  assert.ok(model.diagnostics.includes('missing_parent'))
  assert.ok(
    spans.find((s) => s.callId === 'root').diagnostics.includes('missing_start')
  )
  assert.deepEqual(
    buildTrajectorySpans(events, { complete: false }).flatMap(
      (s) => s.diagnostics
    ),
    []
  )
})
