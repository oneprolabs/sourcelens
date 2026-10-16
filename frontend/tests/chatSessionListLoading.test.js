import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import vm from 'node:vm'

const source = await readFile(
  new URL('../src/pages/lens/Chat.vue', import.meta.url),
  'utf8'
)
const ref = (value) => ({ value })

function deferred() {
  let resolve
  let reject
  const promise = new Promise((done, fail) => {
    resolve = done
    reject = fail
  })
  return { promise, resolve, reject }
}

function sessionListContext({ archived, mutation, listing }) {
  const session = {
    uuid: 'session-a',
    status: archived ? 'archived' : 'active'
  }
  const context = vm.createContext({
    sessionLoadGeneration: 0,
    sessionListGeneration: 0,
    selectedAssistant: ref({ slug: 'assistant-a' }),
    isSmartCollaborationConversation: ref(false),
    isSmartCollaborationRoute: ref(false),
    showArchivedSessions: ref(archived),
    sessionSearchOpen: ref(false),
    sessionSearchResults: ref([]),
    sessionsLoading: ref(false),
    sessionsError: ref(false),
    sessions: ref([session]),
    selectedSessionUuid: ref(session.uuid),
    messages: ref([]),
    currentRun: ref(null),
    question: ref(''),
    mentionedAssistantUuids: ref([]),
    retryDraft: ref(null),
    clarificationAnswers: ref({}),
    clarificationErrors: ref({}),
    composerRef: ref(null),
    route: { path: '/a/assistant-a', query: {} },
    router: { replace() {} },
    listSessions: () => listing.promise,
    archiveSession: () => mutation.promise,
    restoreSession: () => mutation.promise,
    clearAttachments() {},
    rememberChat() {},
    resetStreamState() {},
    cancelRename() {},
    closeDeleteSession() {},
    showSuccess() {},
    showError(message) {
      assert.fail(`Unexpected session action error: ${message}`)
    },
    t: (key) => key,
    selectSession: async ({ uuid }) => {
      context.sessionLoadGeneration += 1
      context.selectedSessionUuid.value = uuid
    },
    nextTick: async (callback) => callback?.()
  })

  for (const name of [
    'loadSessions',
    'switchSessionView',
    'selectSearchedSession',
    'clearSessionSelection',
    'archiveManagedSession',
    'restoreManagedSession'
  ]) {
    const start = source.indexOf(`function ${name}(`)
    assert.notEqual(start, -1)
    const end = source.indexOf('\n}', start) + 2
    const body = source.slice(start, end)
    vm.runInContext(
      source.slice(start - 6, start) === 'async ' ? `async ${body}` : body,
      context
    )
  }
  return { context, session }
}

for (const fails of [false, true]) {
  test(`a stale archived list ${fails ? 'failure' : 'response'} preserves the view selected through search`, async () => {
    const listing = deferred()
    const { context } = sessionListContext({
      archived: false,
      mutation: deferred(),
      listing
    })
    const switchingView = context.switchSessionView(true)
    context.sessionSearchResults.value = [
      { uuid: 'searched-session', archived: false }
    ]
    await context.selectSearchedSession(context.sessionSearchResults.value[0])

    if (fails) listing.reject(new Error('List request failed'))
    else listing.resolve([{ uuid: 'archived-session', archived: true }])
    await switchingView

    assert.equal(context.sessionsLoading.value, false)
    assert.equal(context.sessionsError.value, false)
    assert.equal(context.showArchivedSessions.value, false)
    assert.equal(context.sessions.value[0].uuid, 'searched-session')
    assert.equal(context.selectedSessionUuid.value, 'searched-session')
  })
}

test('a pending list completes without overriding an explicit session selection', async () => {
  const listing = deferred()
  const { context } = sessionListContext({
    archived: false,
    mutation: deferred(),
    listing
  })
  const switchingView = context.switchSessionView(true)
  await context.selectSession({ uuid: 'searched-session' })

  listing.resolve([{ uuid: 'first-session' }, { uuid: 'searched-session' }])
  await switchingView

  assert.equal(context.sessionsLoading.value, false)
  assert.equal(context.sessions.value.length, 2)
  assert.equal(context.selectedSessionUuid.value, 'searched-session')
})

for (const { archived, action, destination } of [
  { archived: false, action: 'archiveManagedSession', destination: 'archived' },
  { archived: true, action: 'restoreManagedSession', destination: 'recent' }
]) {
  test(`${action} finishing during a switch to ${destination} does not strand the session list loader`, async () => {
    const mutation = deferred()
    const listing = deferred()
    const { context, session } = sessionListContext({
      archived,
      mutation,
      listing
    })
    const changingStatus = context[action](session)
    const switchingView = context.switchSessionView(!archived)
    assert.equal(context.sessionsLoading.value, true)

    mutation.resolve()
    await changingStatus
    listing.resolve([{ uuid: 'session-b' }])
    await switchingView

    assert.equal(context.sessionsLoading.value, false)
    assert.equal(context.sessionsError.value, false)
    assert.equal(context.sessions.value[0]?.uuid, 'session-b')
    assert.equal(context.selectedSessionUuid.value, 'session-b')
  })

  test(`${action} preserves the moved session when its destination view has already loaded`, async () => {
    const mutation = deferred()
    const listing = deferred()
    const { context, session } = sessionListContext({
      archived,
      mutation,
      listing
    })
    const changingStatus = context[action](session)
    const switchingView = context.switchSessionView(!archived)
    listing.resolve([
      { uuid: 'session-a', status: archived ? 'active' : 'archived' },
      { uuid: 'session-b' }
    ])
    await switchingView

    mutation.resolve()
    await changingStatus

    assert.equal(context.sessionsLoading.value, false)
    assert.equal(context.showArchivedSessions.value, !archived)
    assert.deepEqual(
      Array.from(context.sessions.value, (item) => item.uuid),
      ['session-a', 'session-b']
    )
    assert.equal(context.selectedSessionUuid.value, 'session-a')
  })
}
