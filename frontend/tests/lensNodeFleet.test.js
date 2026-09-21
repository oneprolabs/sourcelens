import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

const source = () =>
  readFile(new URL('../src/pages/lens/LensNodes.vue', import.meta.url), 'utf8')

test('LensNode page exposes fleet health and workload', async () => {
  const contents = await source()

  assert.match(contents, /data-testid="lensnode-fleet-summary"/)
  assert.match(contents, /active_run_count/)
  assert.match(contents, /queued_run_count/)
  assert.match(contents, /awaiting_resume_count/)
  assert.match(contents, /last_heartbeat_at/)
})

test('LensNode workload links to filtered run operations', async () => {
  const contents = await source()

  assert.match(contents, /name: 'LensRunObservation'/)
  assert.match(contents, /lensnode: row\.uuid/)
})

test('LensNode list exposes readable versions and drawer capabilities', async () => {
  const [contents, drawer] = await Promise.all([
    source(),
    readFile(
      new URL('../src/pages/lens/LensNodeDetailDrawer.vue', import.meta.url),
      'utf8'
    )
  ])

  assert.match(contents, /whitespace-nowrap.*text-ink-600/)
  assert.match(contents, /runtimeVersion/)
  assert.match(contents, /protocolVersion/)
  assert.match(drawer, /data-testid="lensnode-capabilities"/)
  assert.match(drawer, /supportedTasks/)
  assert.match(drawer, /taskLabel\(/)
})
