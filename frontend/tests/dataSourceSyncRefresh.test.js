import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

import {
  collectCompletedTaskResults,
  mergeDataSourceSyncStatuses,
  nextSyncStatusRefreshDelay
} from '../src/pages/lens/dataSourceSyncRefresh.js'

const source = (path) =>
  readFile(new URL(`../src/${path}`, import.meta.url), 'utf8')

test('lightweight sync statuses update rows without replacing static fields', () => {
  const rows = [
    {
      uuid: 'datasource-1',
      name: 'Repository',
      current_sync: { task_id: 'old-task', progress_percent: 10 },
      sync_state: { last_status: 'running' }
    }
  ]
  const statuses = [
    {
      uuid: 'datasource-1',
      current_sync: { task_id: 'old-task', progress_percent: 42 },
      last_task: { task_id: 'old-task', status: 'SUCCESS' },
      sync_state: { last_status: 'running' },
      last_synced_at: null,
      last_error: ''
    }
  ]

  assert.deepEqual(mergeDataSourceSyncStatuses(rows, statuses), [
    {
      uuid: 'datasource-1',
      name: 'Repository',
      current_sync: { task_id: 'old-task', progress_percent: 42 },
      last_task: { task_id: 'old-task', status: 'SUCCESS' },
      sync_state: { last_status: 'running' },
      last_synced_at: null,
      last_error: ''
    }
  ])
})

test('sync status refresh backs off while unchanged and resets on progress', () => {
  assert.equal(nextSyncStatusRefreshDelay(5000, false), 10000)
  assert.equal(nextSyncStatusRefreshDelay(20000, false), 30000)
  assert.equal(nextSyncStatusRefreshDelay(30000, false), 30000)
  assert.equal(nextSyncStatusRefreshDelay(30000, true), 5000)
})

test('a watched task keeps its terminal result after it stops running', () => {
  const watched = new Set()
  const running = [
    {
      uuid: 'datasource-1',
      current_sync: { task_id: 'task-1', status: 'STARTED' },
      last_task: { task_id: 'task-1', status: 'STARTED' }
    }
  ]
  let completed = collectCompletedTaskResults(running, watched, {})
  assert.deepEqual(completed, {})
  assert.ok(watched.has('task-1'))

  const finished = [
    {
      uuid: 'datasource-1',
      current_sync: null,
      last_task: {
        task_id: 'task-1',
        status: 'SUCCESS',
        progress_message: 'Upload complete'
      }
    }
  ]
  completed = collectCompletedTaskResults(finished, watched, completed)
  assert.deepEqual(completed, {
    'datasource-1': {
      task_id: 'task-1',
      status: 'SUCCESS',
      progress_message: 'Upload complete'
    }
  })
})

test('a terminal task never watched this session is not shown', () => {
  const completed = collectCompletedTaskResults(
    [
      {
        uuid: 'datasource-1',
        current_sync: null,
        last_task: { task_id: 'old-task', status: 'SUCCESS' }
      }
    ],
    new Set(),
    {}
  )
  assert.deepEqual(completed, {})
})

test('a fast task created after page load is shown without being polled', () => {
  const completed = collectCompletedTaskResults(
    [
      {
        uuid: 'datasource-1',
        current_sync: null,
        last_task: {
          task_id: 'fast-task',
          status: 'SUCCESS',
          created_at: '2026-09-20T07:23:00Z'
        }
      }
    ],
    new Set(),
    {},
    new Date('2026-09-20T07:22:00Z').getTime()
  )
  assert.deepEqual(completed, {
    'datasource-1': {
      task_id: 'fast-task',
      status: 'SUCCESS',
      created_at: '2026-09-20T07:23:00Z'
    }
  })
})

test('starting a new task clears the previous terminal result', () => {
  const watched = new Set(['task-1'])
  const completed = collectCompletedTaskResults(
    [
      {
        uuid: 'datasource-1',
        current_sync: { task_id: 'task-2', status: 'PENDING' },
        last_task: { task_id: 'task-2', status: 'PENDING' }
      }
    ],
    watched,
    {
      'datasource-1': { task_id: 'task-1', status: 'SUCCESS' }
    }
  )
  assert.deepEqual(completed, {})
  assert.ok(watched.has('task-2'))
})

test('datasource page polls the lightweight endpoint instead of the full list', async () => {
  const [page, api] = await Promise.all([
    source('pages/lens/DataSources.vue'),
    source('api/lens.js')
  ])

  assert.match(api, /listDataSourceSyncStatuses/)
  assert.match(api, /datasources\/sync-statuses\//)
  assert.match(page, /listDataSourceSyncStatuses/)
  assert.match(page, /SYNC_STATUS_REFRESH_MAX_DURATION_MS/)
  assert.match(page, /document\.visibilityState/)
  assert.doesNotMatch(
    page,
    /async function refreshDataSourceRows[\s\S]*listDataSources/
  )
})
