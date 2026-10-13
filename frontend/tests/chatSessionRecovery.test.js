import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import vm from 'node:vm'

import {
  applyRuntimeEvent,
  createRuntimeState,
  runtimeStateFor,
  terminalSyncEvent
} from '../src/pages/lens/runtimeEvents.js'
import { resolveRunStatus } from '../src/pages/lens/chatRetryHint.js'

const source = await readFile(
  new URL('../src/pages/lens/Chat.vue', import.meta.url),
  'utf8'
)
const ref = (value) => ({ value })
const deferred = () => {
  let resolve
  const promise = new Promise((done) => {
    resolve = done
  })
  return { promise, resolve }
}

function chatContext(overrides = {}) {
  const context = vm.createContext({
    sessionLoadGeneration: 0,
    RUN_POLL_INTERVAL_MS: 3000,
    selectedSessionUuid: ref('a'),
    currentRun: ref({ uuid: 'run-a', status: 'streaming' }),
    messages: ref([]),
    streamController: ref(new AbortController()),
    runStatusResolvingSessionUuid: ref(''),
    messageLoading: ref(false),
    messageError: ref(false),
    booted: ref(true),
    mySharesOpen: ref(false),
    question: ref(''),
    mentionedAssistantUuids: ref([]),
    retryDraft: ref(null),
    composerRef: ref(null),
    failedRunError: ref(null),
    runtimeState: ref(createRuntimeState()),
    partialAnswer: ref(''),
    streamError: ref(''),
    queuePosition: ref(null),
    seenStepEventCounts: new Map(),
    streamTextBuffer: { flush() {}, clear() {} },
    route: { path: '/chat' },
    router: { replace() {} },
    window: { localStorage: {} },
    listMessages: async () => [],
    getRun: async () => ({ uuid: 'run-a', status: 'done' }),
    resetStreamState() {
      context.streamController.value?.abort()
      context.streamController.value = null
      context.runtimeState.value = createRuntimeState()
      context.partialAnswer.value = ''
    },
    clearAttachments() {},
    rememberChat() {},
    scrollToBottom() {},
    clearUnreadSession() {},
    refreshUnreadSessions() {},
    showError() {},
    nextTick: async (callback) => callback?.(),
    startCompletionTracking() {},
    readSse: async () => {},
    sleep: async () => {},
    answerAutoScroller: { request() {} },
    appendAnswerDelta() {},
    mapRunError: (code) => code,
    t: (key) => key,
    resolveRunStatus,
    runtimeStateFor,
    applyRuntimeEvent,
    createRuntimeState,
    terminalSyncEvent,
    ...overrides
  })
  const names = [
    'selectSession',
    'maybeResumeActiveRun',
    'finishSubmittedRun',
    'handleEvent',
    'handleStepEvent',
    'isTerminalRunStatus'
  ]
  for (const name of names) {
    const start = source.indexOf(`function ${name}(`)
    assert.notEqual(start, -1)
    const end = source.indexOf('\n}', start) + 2
    const body = source.slice(start, end)
    vm.runInContext(
      source.slice(start - 6, start) === 'async ' ? `async ${body}` : body,
      context
    )
  }
  context.pushAgentActivity = (event) => {
    context.runtimeState.value = applyRuntimeEvent(
      context.runtimeState.value,
      event
    )
  }
  return context
}

test('switching sessions immediately detaches the old run while history is loading', async () => {
  const history = deferred()
  const context = chatContext({ listMessages: () => history.promise })
  const oldController = context.streamController.value
  const loading = context.selectSession({ uuid: 'b' })
  assert.equal(context.selectedSessionUuid.value, 'b')
  assert.equal(context.currentRun.value, null)
  assert.equal(oldController.signal.aborted, true)
  history.resolve([])
  await loading
})

test('a delayed final history response cannot overwrite a newer visit to the same session', async () => {
  const history = deferred()
  const context = chatContext({ listMessages: () => history.promise })
  const generation = context.sessionLoadGeneration
  const finishing = context.finishSubmittedRun(
    'run-a',
    'a',
    () => context.sessionLoadGeneration === generation
  )
  await new Promise((resolve) => setImmediate(resolve))
  context.sessionLoadGeneration += 2
  context.messages.value = [{ content: 'Current answer' }]
  context.currentRun.value = { uuid: 'run-new', status: 'streaming' }
  history.resolve([{ content: 'Old answer' }])
  await finishing
  assert.equal(context.messages.value[0].content, 'Current answer')
  assert.equal(context.currentRun.value.uuid, 'run-new')
})

test('an active history placeholder remains visible as live progress before SSE sync', async () => {
  const streaming = deferred()
  const thinking = {
    status: 'streaming',
    steps: [
      {
        visibility: 'user',
        event_type: 'plan.updated',
        payload: {
          revision: 1,
          steps: [
            { id: 'query', title: 'Query invoices', status: 'in_progress' }
          ]
        }
      }
    ]
  }
  const context = chatContext({
    messages: ref([
      { role: 'user', run: 'run-a', content: 'Invoice question' },
      { role: 'assistant', uuid: 'answer-a', content: 'Working', thinking }
    ]),
    getRun: async () => ({ uuid: 'run-a', status: 'streaming' }),
    readSse: () => streaming.promise
  })
  let current = true
  const restoring = context.maybeResumeActiveRun('a', () => current)
  await new Promise((resolve) => setImmediate(resolve))
  assert.equal(context.partialAnswer.value, 'Working')
  assert.equal(context.runtimeState.value.plan[0].status, 'in_progress')
  assert.equal(context.messages.value.length, 1)
  current = false
  streaming.resolve()
  await restoring
})

test('reconnecting to the same snapshot replaces activity instead of duplicating it', () => {
  const context = chatContext()
  const sync = {
    type: 'sync',
    status: 'streaming',
    content: '',
    steps: [
      {
        sequence: 1,
        detail: {
          events: [
            {
              agent_event: 'tool.read_file.invoke',
              activity: 'reading_context'
            }
          ]
        }
      }
    ]
  }
  context.handleEvent(sync)
  context.partialAnswer.value = 'Answer discarded by token_reset'
  context.pushAgentActivity({
    agent_event: 'tool.search_workspace.invoke',
    activity: 'searching_sources'
  })
  context.handleEvent(sync)
  assert.equal(context.runtimeState.value.activities.length, 1)
  assert.equal(context.runtimeState.value.activities[0].count, 1)
  assert.equal(context.partialAnswer.value, '')
})

test('status resolution retries while keeping history visible and unresolved', async () => {
  const wait = deferred()
  let polls = 0
  const context = chatContext({
    messages: ref([
      { role: 'user', run: 'run-a', content: 'Invoice question' }
    ]),
    runStatusResolvingSessionUuid: ref('a'),
    getRun: async () => {
      if (++polls === 1) throw new Error('network unavailable')
      return { uuid: 'run-a', status: 'failed', error: 'EXECUTION_FAILED' }
    },
    sleep: () => wait.promise,
    listMessages: async () => [{ role: 'assistant', run: 'run-a', content: '' }]
  })
  const restoring = context.maybeResumeActiveRun('a', () => true)
  await new Promise((resolve) => setImmediate(resolve))
  assert.equal(context.messages.value[0].content, 'Invoice question')
  assert.equal(context.runStatusResolvingSessionUuid.value, 'a')
  wait.resolve()
  await restoring
  assert.equal(context.runStatusResolvingSessionUuid.value, '')
  assert.equal(context.failedRunError.value, 'EXECUTION_FAILED')
  assert.equal(context.messages.value[0].role, 'assistant')
})

test('final history reconciliation retries a transient failure', async () => {
  let loads = 0
  const context = chatContext({
    listMessages: async () => {
      if (++loads === 1) throw new Error('temporary request failure')
      return [{ content: 'Final partial answer' }]
    },
    getRun: async () => ({ uuid: 'run-a', status: 'done', outcome: 'partial' })
  })
  await context.finishSubmittedRun('run-a', 'a', () => true)
  assert.equal(loads, 2)
  assert.equal(context.messages.value[0].content, 'Final partial answer')
  assert.equal(context.currentRun.value.outcome, 'partial')
})
