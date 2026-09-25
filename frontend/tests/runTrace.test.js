import assert from 'node:assert/strict'
import test from 'node:test'

import {
  buildTraceGraphOrder,
  filterTraceSteps,
  formatTraceDuration,
  formatTraceRelative,
  mergeTraceEvents,
  sortTraceEvents,
  stepRawEvents,
  traceEventKey,
  traceTypeCounts
} from '../src/admin/pages/lens/runTrace.js'

test('trace events merge by run and event identity', () => {
  const existing = [
    {
      trace_run_uuid: 'parent',
      event_id: 'same',
      sequence: 1,
      payload: { v: 1 }
    }
  ]
  const incoming = [
    {
      trace_run_uuid: 'parent',
      event_id: 'same',
      sequence: 1,
      payload: { v: 2 }
    },
    { trace_run_uuid: 'child', event_id: 'same', sequence: 2 }
  ]

  assert.equal(traceEventKey(existing[0]), 'parent:same')
  assert.deepEqual(mergeTraceEvents(existing, incoming), [
    {
      trace_run_uuid: 'parent',
      event_id: 'same',
      sequence: 1,
      payload: { v: 2 }
    },
    { trace_run_uuid: 'child', event_id: 'same', sequence: 2 }
  ])
})

test('trace events sort by sequence with missing sequences last', () => {
  assert.deepEqual(
    sortTraceEvents([
      { event_id: 'missing' },
      { event_id: 'three', sequence: 3 },
      { event_id: 'one', sequence: '1' }
    ]).map((event) => event.event_id),
    ['one', 'three', 'missing']
  )
})

const steps = [
  {
    id: 'step_1',
    type: 'model',
    title: 'Understand request',
    summary: 'Extract intent',
    details: { model_ref: 'gpt-test' }
  },
  {
    id: 'step_2',
    type: 'retrieval',
    title: 'Search docs',
    summary: '12 results',
    details: { query: 'blue green' }
  },
  {
    id: 'step_3',
    type: 'tool',
    title: 'GitHub Search',
    summary: '23 results',
    details: {}
  }
]

test('trace steps filter by type and search across details', () => {
  assert.deepEqual(
    filterTraceSteps(steps, { type: 'retrieval' }).map((step) => step.id),
    ['step_2']
  )
  assert.deepEqual(
    filterTraceSteps(steps, { query: 'github' }).map((step) => step.id),
    ['step_3']
  )
  assert.deepEqual(
    filterTraceSteps(steps, { type: 'tool', query: 'blue green' }).map(
      (step) => step.id
    ),
    []
  )
  assert.equal(filterTraceSteps(steps).length, 3)
})

test('trace type counts aggregate per semantic type', () => {
  assert.deepEqual(traceTypeCounts(steps), { model: 1, retrieval: 1, tool: 1 })
  assert.deepEqual(traceTypeCounts([]), {})
})

test('causal graph order follows start time and call layout follows depth', () => {
  const graphSteps = [
    { id: 'step_2', start_ms: 200 },
    { id: 'step_1', start_ms: 100 },
    { id: 'step_3', start_ms: 300 }
  ]
  const edges = [
    { from: 'step_1', to: 'step_2', kind: 'child' },
    { from: 'step_2', to: 'step_3', kind: 'child' }
  ]

  assert.deepEqual(
    buildTraceGraphOrder(graphSteps, edges, 'causal').map((step) => step.id),
    ['step_1', 'step_2', 'step_3']
  )
  assert.deepEqual(
    buildTraceGraphOrder(graphSteps, edges, 'calls').map((step) => step.id),
    ['step_1', 'step_2', 'step_3']
  )
  assert.deepEqual(
    buildTraceGraphOrder(
      [
        { id: 'child', start_ms: 10 },
        { id: 'parent', start_ms: 20 }
      ],
      [{ from: 'parent', to: 'child', kind: 'child' }],
      'calls'
    ).map((step) => step.id),
    ['parent', 'child']
  )
})

test('trace formatting covers sub-second and second durations', () => {
  assert.equal(formatTraceDuration(183), '183ms')
  assert.equal(formatTraceDuration(2800), '2.8s')
  assert.equal(formatTraceDuration(null), '—')
  assert.equal(formatTraceRelative(0), '0.0s')
  assert.equal(formatTraceRelative(1200), '1.2s')
  assert.equal(formatTraceRelative(undefined), '—')
})

test('step raw events resolve by event ids', () => {
  const events = [
    { event_id: 'a', event_type: 'model.started' },
    { event_id: 'b', event_type: 'model.completed' },
    { event_id: 'c', event_type: 'tool.started' }
  ]

  assert.deepEqual(
    stepRawEvents({ event_ids: ['a', 'b'] }, events).map(
      (event) => event.event_id
    ),
    ['a', 'b']
  )
  assert.deepEqual(stepRawEvents(null, events), [])
})
