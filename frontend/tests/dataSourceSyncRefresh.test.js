import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

import {
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
