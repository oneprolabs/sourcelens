import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

const source = (path) =>
  readFile(new URL(`../src/${path}`, import.meta.url), 'utf8')

test('datasource page reprocesses managed and upload sources with the stored policy', async () => {
  const [page, api] = await Promise.all([
    source('pages/lens/DataSources.vue'),
    source('api/lens.js')
  ])

  assert.match(api, /export async function convertDataSource/)
  assert.match(api, /datasources\/\$\{uuid\}\/convert\//)
  assert.match(page, /convertDataSource/)
  assert.match(page, /isSyncableSourceType\(row\.source_type\)/)
  assert.match(page, /lensAdmin\.actions\.reprocess/)
  assert.match(page, /lensAdmin\.messages\.reprocessConfirm/)
})
