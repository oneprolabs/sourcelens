import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

const source = (path) =>
  readFile(new URL(`../src/${path}`, import.meta.url), 'utf8')

test('datasource detail drawer reprocesses any source with the stored policy', async () => {
  const [page, drawer, api] = await Promise.all([
    source('pages/lens/DataSources.vue'),
    source('pages/lens/DataSourceDetailDrawer.vue'),
    source('api/lens.js')
  ])

  assert.match(api, /export async function convertDataSource/)
  assert.match(api, /datasources\/\$\{uuid\}\/convert\//)
  assert.match(drawer, /lensAdmin\.actions\.reprocess/)
  assert.match(drawer, /'reprocess'/)
  assert.doesNotMatch(
    drawer,
    /v-if="isSyncableDatasource"[\s\S]{0,400}reprocess/
  )
  assert.match(page, /@reprocess="reprocess"/)
  assert.match(page, /convertDataSource/)
  assert.match(page, /lensAdmin\.messages\.reprocessConfirm/)
})

test('sync records separate sync, upload, and processing tasks', async () => {
  const [drawer, view, api] = await Promise.all([
    source('pages/lens/DataSourceDetailDrawer.vue'),
    source('../backend/lens/views/datasources.py').catch(() => ''),
    source('api/lens.js')
  ])

  assert.match(drawer, /task_type: taskType\.value/)
  assert.match(drawer, /lens_datasource_conversion/)
  assert.match(drawer, /lensAdmin\.datasourceDetail\.details\.taskTypeProcessing/)
  assert.match(drawer, /lensAdmin\.datasourceDetail\.details\.colTaskType/)
  assert.match(drawer, /function taskTypeLabel/)
  assert.match(api, /task_type/)
})
