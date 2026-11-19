import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import vm from 'node:vm'

import { isRunAccessDenied } from '../src/utils/runAccess.js'

const source = await readFile(
  new URL('../src/admin/pages/lens/RunTrajectoryPanel.vue', import.meta.url),
  'utf8'
)
const ref = (value) => ({ value })

function panel(overrides = {}) {
  const context = vm.createContext({
    props: { runUuid: 'run-a', active: true },
    AbortController,
    requestId: 0,
    streamController: null,
    fallbackTimer: null,
    finalSyncTimer: null,
    finalSyncDelay: 1000,
    reconnectDelay: 1000,
    events: ref([{ payload: 'Previously authorized evidence' }]),
    summary: ref({ detail: 'Previously authorized summary' }),
    selectedEvent: ref(null),
    hasMore: ref(true),
    loading: ref(false),
    awaitingStreamDone: ref(false),
    streamState: ref('idle'),
    streamCursor: ref(''),
    streamRevision: ref(''),
    streamSequence: ref(0),
    query: ref(''),
    category: ref('all'),
    nextAfterSequence: ref(0),
    shouldFollowTrajectory: () => true,
    clearTimeout() {},
    setTimeout: () => assert.fail('must not schedule after denial'),
    showError() {},
    isRunAccessDenied,
    ...overrides
  })
  for (const name of [
    'stopTrajectoryStream',
    'clearTrajectoryAccess',
    'cancelTerminalSync',
    'fetchTrajectory',
    'refreshAndFollow',
    'scheduleFallbackRefresh',
    'connectTrajectoryStream',
    'syncTerminalTrajectory'
  ]) {
    const start = source.indexOf(`function ${name}(`)
    assert.notEqual(start, -1)
    const end = source.indexOf('\n}', start) + 2
    vm.runInContext(
      (source.slice(start - 6, start) === 'async ' ? 'async ' : '') +
        source.slice(start, end),
      context
    )
  }
  return context
}

for (const status of [401, 403, 404]) {
  test(`trajectory REST denial ${status} clears evidence and ends both recovery paths`, async () => {
    const context = panel({
      getAdminRunTrajectory: async () => {
        throw { response: { status } }
      }
    })
    await context.refreshAndFollow(true, true)
    await context.syncTerminalTrajectory('run-a')
    assert.equal(context.events.value.length, 0)
    assert.equal(Object.keys(context.summary.value).length, 0)
    assert.equal(context.hasMore.value, false)
    assert.equal(context.loading.value, false)
  })
}

for (const status of [403, 404]) {
  test(`trajectory stream denial ${status} stops reconnection`, async () => {
    const context = panel({
      streamAdminRunTrajectory: async () => {
        throw { response: { status } }
      }
    })
    await context.connectTrajectoryStream()
    assert.equal(context.events.value.length, 0)
    assert.equal(context.streamState.value, 'idle')
    assert.equal(context.streamController, null)
  })
}

test('an expired trajectory token can still reach REST token refresh', async () => {
  let retries = 0
  const context = panel({
    streamAdminRunTrajectory: async () => {
      throw { response: { status: 401 } }
    },
    setTimeout: () => {
      retries += 1
    }
  })
  await context.connectTrajectoryStream()
  assert.equal(retries, 1)
})

test('a stale stream denial does not erase evidence for a newer run', async () => {
  const context = panel({
    streamAdminRunTrajectory: async () => {
      context.props.runUuid = 'run-b'
      context.events.value = [{ payload: 'Current run' }]
      throw { response: { status: 403 } }
    }
  })
  await context.connectTrajectoryStream()
  assert.equal(context.events.value[0].payload, 'Current run')
})
