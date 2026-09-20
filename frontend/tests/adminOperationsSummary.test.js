import assert from 'node:assert/strict'
import test from 'node:test'

import {
  formatOperationMetric,
  resolveFleetSummary,
  resolveRunSummary
} from '../src/admin/utils/operationsSummary.js'

test('run summary keeps the total while old APIs leave status counts unknown', () => {
  const summary = resolveRunSummary({
    total: 144,
    results: Array.from({ length: 20 }, () => ({ status: 'done' }))
  })

  assert.deepEqual(summary, {
    total: 144,
    running: null,
    streaming: null,
    queued: null,
    failed: null,
    done: null
  })
})

test('run summary uses server-provided fleet-wide status counts', () => {
  const summary = resolveRunSummary({
    total: 20,
    summary: {
      total: 144,
      running: 2,
      streaming: 3,
      queued: 4,
      failed: 24,
      done: 110
    },
    results: []
  })

  assert.equal(summary.total, 144)
  assert.equal(summary.running, 2)
  assert.equal(summary.streaming, 3)
  assert.equal(summary.failed, 24)
})

test('fleet summary derives health but not missing workload from a complete page', () => {
  const summary = resolveFleetSummary({
    count: 1,
    results: [{ status: 'online' }]
  })

  assert.deepEqual(summary, {
    online: 1,
    unresponsive: 0,
    offline: 0,
    draining: 0,
    active_runs: null,
    queued_runs: null,
    awaiting_resume: null
  })
})

test('fleet summary preserves server-provided workload totals', () => {
  const summary = resolveFleetSummary({
    count: 1,
    fleet_summary: {
      online: 1,
      unresponsive: 0,
      offline: 0,
      draining: 0,
      active_runs: 2,
      queued_runs: 3,
      awaiting_resume: 1
    },
    results: []
  })

  assert.equal(summary.active_runs, 2)
  assert.equal(summary.queued_runs, 3)
  assert.equal(summary.awaiting_resume, 1)
})

test('unknown operation metrics render as unavailable instead of zero', () => {
  assert.equal(formatOperationMetric(undefined), '—')
  assert.equal(formatOperationMetric(null), '—')
  assert.equal(formatOperationMetric(0), '0')
  assert.equal(formatOperationMetric(12), '12')
})
