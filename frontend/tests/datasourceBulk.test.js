import assert from 'node:assert/strict'
import test from 'node:test'

import {
  executeDataSourceBatch,
  dataSourceBulkSkipReason
} from '../src/pages/lens/datasourceBulk.js'

const source = (uuid, extra = {}) => ({
  uuid,
  name: uuid,
  source_type: 'git',
  status: 'active',
  ...extra
})

test('retry submits only failed active sources and reports skips individually', async () => {
  const calls = []
  const results = await executeDataSourceBatch({
    targets: [
      source('failed', { sync_state: { last_status: 'failed' } }),
      source('ok'),
      source('paused', {
        status: 'disabled',
        sync_state: { last_status: 'failed' }
      }),
      source('upload', { source_type: 'upload' }),
      source('running', { current_sync: { task_id: 'task' } })
    ],
    action: 'retry',
    api: {
      get: async (uuid) =>
        source(uuid, { sync_state: { last_status: 'failed' } }),
      sync: async (uuid) => {
        calls.push(uuid)
        return { task_id: 'new-task' }
      }
    }
  })
  assert.deepEqual(calls, ['failed'])
  assert.deepEqual(
    results.map((r) => r.status),
    ['success', 'skipped', 'skipped', 'skipped', 'skipped']
  )
  assert.equal(results[0].taskId, 'new-task')
})

test('retry rechecks the failed state before submitting and never retries recovered items', async () => {
  const results = await executeDataSourceBatch({
    targets: [source('recovered', { sync_state: { last_status: 'failed' } })],
    action: 'retry',
    api: {
      get: async (uuid) =>
        source(uuid, { sync_state: { last_status: 'success' } }),
      sync: async () => assert.fail('Recovered items cannot be retried')
    }
  })
  assert.equal(results[0].status, 'skipped')
  assert.equal(results[0].reason, 'notFailed')
})

test('one rejected request does not hide failures or stop later targets', async () => {
  const results = await executeDataSourceBatch({
    targets: [source('first'), source('bad'), source('last')],
    action: 'pause',
    api: {
      setEnabled: async (uuid, enabled) => {
        assert.equal(enabled, false)
        if (uuid === 'bad') throw new Error('Denied')
      }
    }
  })
  assert.deepEqual(
    results.map((r) => r.status),
    ['success', 'failed', 'success']
  )
  assert.equal(results[1].error.message, 'Denied')
})

test('execution freezes names, ids, policy and status and deduplicates targets', async () => {
  let release
  const blocked = new Promise((resolve) => {
    release = resolve
  })
  const targets = [source('one'), source('two'), source('two')]
  const policy = { mode: 'interval', interval_seconds: 3600 }
  const calls = []
  const promise = executeDataSourceBatch({
    targets,
    action: 'policy',
    policy,
    api: {
      setPolicy: async (uuid, payload) => {
        calls.push([uuid, { ...payload }])
        if (uuid === 'one') await blocked
      }
    }
  })
  targets[1].uuid = 'foreign'
  targets[1].name = 'Changed'
  targets.push(source('new'))
  policy.interval_seconds = 21600
  release()
  const results = await promise
  assert.deepEqual(calls, [
    ['one', { mode: 'interval', interval_seconds: 3600 }],
    ['two', { mode: 'interval', interval_seconds: 3600 }]
  ])
  assert.equal(results[1].name, 'two')
})

test('leaving or losing write permission stops further requests and late UI updates', async () => {
  let active = true
  const updates = []
  const calls = []
  const results = await executeDataSourceBatch({
    targets: [source('one'), source('two')],
    action: 'delete',
    canContinue: () => active,
    onResult: (r) => updates.push(r),
    api: {
      remove: async (uuid) => {
        calls.push(uuid)
        active = false
      }
    }
  })
  assert.deepEqual(calls, ['one'])
  assert.equal(results.length, 1)
  assert.equal(updates.length, 0)
})

test('upload policies and already matching pause/resume states are skipped', () => {
  assert.equal(
    dataSourceBulkSkipReason(
      source('up', { plugin_key: 'file_upload' }),
      'policy'
    ),
    'manualSource'
  )
  assert.equal(
    dataSourceBulkSkipReason(
      source('ws', { source_type: 'managed_workspace' }),
      'sync'
    ),
    'manualSource'
  )
  assert.equal(
    dataSourceBulkSkipReason(source('on'), 'resume'),
    'alreadyEnabled'
  )
  assert.equal(
    dataSourceBulkSkipReason(source('off', { status: 'disabled' }), 'pause'),
    'alreadyDisabled'
  )
  assert.equal(
    dataSourceBulkSkipReason(source('off', { status: 'disabled' }), 'delete'),
    ''
  )
})
