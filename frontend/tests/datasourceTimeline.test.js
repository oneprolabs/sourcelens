import assert from 'node:assert/strict'
import test from 'node:test'
import {
  dataSourceTaskOutcome,
  dataSourceTaskSummary
} from '../src/pages/lens/datasourceTimeline.js'
const t = (key, args) => `${key.split('.').at(-1)}:${args.count}`

test('task outcomes distinguish partial conversion, retry and cancellation from success', () => {
  for (const [status, expected] of Object.entries({
    SUCCESS: 'completed',
    FAILURE: 'failed',
    STARTED: 'running',
    PENDING: 'pending',
    RETRY: 'retrying',
    REVOKED: 'cancelled',
    CANCELLING: 'cancelling'
  })) {
    assert.equal(dataSourceTaskOutcome({ status }), expected)
  }
  assert.equal(
    dataSourceTaskOutcome({
      status: 'SUCCESS',
      metadata: { sync_summary: { partial_success: true } }
    }),
    'partial'
  )
  assert.equal(
    dataSourceTaskOutcome({
      status: 'SUCCESS',
      metadata: { conversion_summary: { failed: 1 } }
    }),
    'partial'
  )
})

test('timeline shows supplied metrics including zero and never fabricates missing counters', () => {
  assert.equal(
    dataSourceTaskSummary(
      { metadata: { sync_summary: { changed: 12, deleted: 0, failed: 2 } } },
      t
    ),
    'changed:12 · deleted:0 · failedCount:2'
  )
  assert.equal(dataSourceTaskSummary({}, t), '')
  assert.equal(
    dataSourceTaskSummary(
      {
        metadata: {
          sync_summary: { changed: 'bad', skipped: -1 },
          progress_message: 'Waiting'
        }
      },
      t
    ),
    'Waiting'
  )
  assert.equal(
    dataSourceTaskSummary(
      { error: 'SOURCE_ACCESS_DENIED', metadata: { progress_message: 'Done' } },
      t
    ),
    'SOURCE_ACCESS_DENIED'
  )
  assert.equal(
    dataSourceTaskSummary(
      {
        module: 'lens_datasource_conversion',
        metadata: { conversion_summary: { skipped: 3, failed: 0 } }
      },
      t
    ),
    'skipped:3 · failedCount:0'
  )
})
