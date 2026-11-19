import assert from 'node:assert/strict'
import test from 'node:test'

import {
  answerSummary,
  answerCompletionTitle,
  clearUnreadSession,
  handleTerminalRun,
  pollRunUntilTerminal,
  readUnreadSessions,
  requestNativeNotificationPermission,
  shouldReviewUnreadSession
} from '../src/utils/answerCompletionNotifications.js'

class MemoryStorage {
  constructor() {
    this.values = new Map()
  }

  getItem(key) {
    return this.values.get(key) ?? null
  }

  setItem(key, value) {
    this.values.set(key, String(value))
  }
}

function completionOptions(overrides = {}) {
  return {
    documentRef: {
      hasFocus: () => true,
      visibilityState: 'visible'
    },
    indicatorEnabled: true,
    run: { status: 'done', uuid: 'run-1' },
    selectedSessionUuid: 'session-b',
    sessionUuid: 'session-a',
    storage: new MemoryStorage(),
    ...overrides
  }
}

function createNotificationApi(permission = 'granted', requested = permission) {
  class NotificationApi {
    static instances = []

    static permission = permission

    static requestPermissionCalls = 0

    static async requestPermission() {
      NotificationApi.requestPermissionCalls += 1
      NotificationApi.permission = requested
      return requested
    }

    constructor(title, options) {
      this.title = title
      this.options = options
      this.closed = false
      NotificationApi.instances.push(this)
    }

    close() {
      this.closed = true
    }
  }

  return NotificationApi
}

test('uses a completion label without an unread count in the browser title', () => {
  assert.equal(
    answerCompletionTitle({
      baseTitle: 'SourceLens',
      completionLabel: 'Answer completed',
      hasUnread: true
    }),
    'Answer completed · SourceLens'
  )
  assert.equal(
    answerCompletionTitle({
      baseTitle: 'SourceLens',
      completionLabel: 'Answer completed',
      hasUnread: false
    }),
    'SourceLens'
  )
})

test('reviews the selected unread conversation once the page is visible', () => {
  const unreadSessions = { 'session-a': 'run-1' }

  assert.equal(
    shouldReviewUnreadSession({
      documentRef: { visibilityState: 'visible' },
      selectedSessionUuid: 'session-a',
      unreadSessions
    }),
    true
  )
  assert.equal(
    shouldReviewUnreadSession({
      documentRef: { visibilityState: 'hidden' },
      selectedSessionUuid: 'session-a',
      unreadSessions
    }),
    false
  )
})

test('marks a completed non-selected conversation unread', () => {
  const options = completionOptions()

  const result = handleTerminalRun(options)

  assert.equal(result.unreadChanged, true)
  assert.equal(result.inAppNotificationRequested, true)
  assert.equal(result.nativeNotificationShown, false)
  assert.deepEqual(readUnreadSessions(options.storage), {
    'session-a': 'run-1'
  })

  clearUnreadSession(options.storage, 'session-a')
  assert.deepEqual(readUnreadSessions(options.storage), {})
})

test('marks the selected conversation unread while its tab is inactive', () => {
  const options = completionOptions({
    documentRef: {
      hasFocus: () => false,
      visibilityState: 'hidden'
    },
    selectedSessionUuid: 'session-a'
  })

  const result = handleTerminalRun(options)

  assert.equal(result.unreadChanged, true)
  assert.equal(result.inAppNotificationRequested, false)
})

test('does not mark the visible selected conversation unread', () => {
  const options = completionOptions({ selectedSessionUuid: 'session-a' })

  const result = handleTerminalRun(options)

  assert.equal(result.unreadChanged, false)
  assert.equal(result.inAppNotificationRequested, false)
  assert.equal(result.nativeNotificationShown, false)
  assert.deepEqual(readUnreadSessions(options.storage), {})
})

test('does not mark a completion unread when reminders are disabled', () => {
  const options = completionOptions({ indicatorEnabled: false })

  const result = handleTerminalRun(options)

  assert.equal(result.unreadChanged, false)
  assert.equal(result.inAppNotificationRequested, false)
  assert.deepEqual(readUnreadSessions(options.storage), {})
})

test('deduplicates replayed completion events by run uuid', () => {
  const options = completionOptions()

  const first = handleTerminalRun(options)
  const replay = handleTerminalRun(options)

  assert.equal(first.unreadChanged, true)
  assert.equal(replay.unreadChanged, false)
  assert.equal(first.inAppNotificationRequested, true)
  assert.equal(replay.inAppNotificationRequested, false)
  assert.deepEqual(readUnreadSessions(options.storage), {
    'session-a': 'run-1'
  })
})

test('deduplicates a stale run replay after a newer completion', () => {
  const storage = new MemoryStorage()
  const first = completionOptions({ storage })
  const newer = completionOptions({
    run: { status: 'done', uuid: 'run-2' },
    storage
  })

  assert.equal(handleTerminalRun(first).unreadChanged, true)
  assert.equal(handleTerminalRun(newer).unreadChanged, true)
  assert.equal(handleTerminalRun(first).unreadChanged, false)
  assert.deepEqual(readUnreadSessions(storage), {
    'session-a': 'run-2'
  })
})

test('builds a concise plain-text answer summary', () => {
  assert.equal(
    answerSummary('## Result\n\nUse **SourceLens** and [read more](/docs).'),
    'Result Use SourceLens and read more.'
  )
  assert.equal(answerSummary('123456789', 5), '12345…')
})

test('does not treat failed or cancelled runs as completed answers', () => {
  for (const status of ['failed', 'cancelled']) {
    const options = completionOptions({
      run: { status, uuid: `run-${status}` }
    })

    const result = handleTerminalRun(options)

    assert.deepEqual(result, {
      inAppNotificationRequested: false,
      nativeNotificationShown: false,
      unreadChanged: false
    })
    assert.deepEqual(readUnreadSessions(options.storage), {})
  }
})

test('requests native notification permission only when it can prompt', async () => {
  const NotificationApi = createNotificationApi('default', 'granted')

  assert.equal(
    await requestNativeNotificationPermission(NotificationApi),
    'granted'
  )
  assert.equal(NotificationApi.requestPermissionCalls, 1)

  NotificationApi.permission = 'denied'
  assert.equal(
    await requestNativeNotificationPermission(NotificationApi),
    'denied'
  )
  assert.equal(NotificationApi.requestPermissionCalls, 1)
  assert.equal(await requestNativeNotificationPermission(undefined), null)
})

test('selects native delivery without an in-app notification when hidden', () => {
  const NotificationApi = createNotificationApi()
  const storage = new MemoryStorage()
  let focused = 0
  let openedSession = ''
  const options = completionOptions({
    documentRef: {
      hasFocus: () => false,
      visibilityState: 'hidden'
    },
    nativeNotification: {
      body: 'Your answer is ready.',
      enabled: true,
      NotificationRef: NotificationApi,
      onOpenConversation: (sessionUuid) => {
        openedSession = sessionUuid
      },
      title: 'Answer completed',
      windowRef: {
        focus: () => {
          focused += 1
        }
      }
    },
    storage
  })

  const result = handleTerminalRun(options)

  assert.deepEqual(result, {
    inAppNotificationRequested: false,
    nativeNotificationShown: true,
    unreadChanged: true
  })
  assert.equal(NotificationApi.instances.length, 1)
  assert.deepEqual(NotificationApi.instances[0].options, {
    body: 'Your answer is ready.',
    renotify: false,
    tag: 'sourcelens-answer-run-1'
  })

  NotificationApi.instances[0].onclick()

  assert.equal(focused, 1)
  assert.equal(openedSession, 'session-a')
  assert.equal(NotificationApi.instances[0].closed, true)
})

test('keeps native delivery independent from in-app reminders', () => {
  const NotificationApi = createNotificationApi()
  const options = completionOptions({
    documentRef: {
      hasFocus: () => false,
      visibilityState: 'hidden'
    },
    indicatorEnabled: false,
    nativeNotification: {
      body: 'Your answer is ready.',
      enabled: true,
      NotificationRef: NotificationApi,
      title: 'Answer completed'
    },
    selectedSessionUuid: 'session-a'
  })

  assert.deepEqual(handleTerminalRun(options), {
    inAppNotificationRequested: false,
    nativeNotificationShown: true,
    unreadChanged: false
  })
  assert.equal(NotificationApi.instances.length, 1)
})

test('does not show a native notification in the active tab', () => {
  const NotificationApi = createNotificationApi()
  const options = completionOptions({
    nativeNotification: {
      body: 'Your answer is ready.',
      enabled: true,
      NotificationRef: NotificationApi,
      title: 'Answer completed'
    }
  })

  assert.equal(handleTerminalRun(options).nativeNotificationShown, false)
  assert.equal(NotificationApi.instances.length, 0)
})

test('deduplicates native notifications for replayed completion events', () => {
  const NotificationApi = createNotificationApi()
  const options = completionOptions({
    documentRef: {
      hasFocus: () => false,
      visibilityState: 'hidden'
    },
    nativeNotification: {
      body: 'Your answer is ready.',
      enabled: true,
      NotificationRef: NotificationApi,
      title: 'Answer completed'
    }
  })

  assert.equal(handleTerminalRun(options).nativeNotificationShown, true)
  assert.equal(handleTerminalRun(options).nativeNotificationShown, false)
  assert.equal(NotificationApi.instances.length, 1)
})

test('keeps only unread state when hidden native delivery is unavailable', () => {
  const storage = new MemoryStorage()
  const options = completionOptions({
    documentRef: {
      hasFocus: () => false,
      visibilityState: 'hidden'
    },
    nativeNotification: {
      enabled: true,
      NotificationRef: createNotificationApi('denied')
    },
    storage
  })

  assert.deepEqual(handleTerminalRun(options), {
    inAppNotificationRequested: false,
    nativeNotificationShown: false,
    unreadChanged: true
  })
  assert.equal(
    handleTerminalRun(completionOptions({ storage }))
      .inAppNotificationRequested,
    false
  )
})

test('does not deliver the same run through both channels', () => {
  const storage = new MemoryStorage()
  const visible = completionOptions({ storage })
  const hidden = completionOptions({
    documentRef: {
      hasFocus: () => false,
      visibilityState: 'hidden'
    },
    nativeNotification: {
      body: 'Generic answer ready text',
      enabled: true,
      NotificationRef: createNotificationApi(),
      title: 'Answer completed'
    },
    storage
  })

  assert.equal(handleTerminalRun(visible).inAppNotificationRequested, true)
  assert.equal(handleTerminalRun(hidden).nativeNotificationShown, false)
})

test('does not notify another tab after the answer was visibly reviewed', () => {
  const storage = new MemoryStorage()
  const visible = completionOptions({
    selectedSessionUuid: 'session-a',
    storage
  })
  const hidden = completionOptions({
    documentRef: {
      hasFocus: () => false,
      visibilityState: 'hidden'
    },
    nativeNotification: {
      body: 'Generic answer ready text',
      enabled: true,
      NotificationRef: createNotificationApi(),
      title: 'Answer completed'
    },
    storage
  })

  assert.equal(handleTerminalRun(visible).inAppNotificationRequested, false)
  assert.equal(handleTerminalRun(hidden).nativeNotificationShown, false)
})

test('polls server run state until it reaches a terminal status', async () => {
  const statuses = ['running', 'done']
  const requested = []
  let sleeps = 0

  const run = await pollRunUntilTerminal({
    getRun: async (runUuid) => {
      requested.push(runUuid)
      return { status: statuses.shift(), uuid: runUuid }
    },
    initialRun: { status: 'queued', uuid: 'run-1' },
    maxAttempts: 4,
    runUuid: 'run-1',
    sleep: async () => {
      sleeps += 1
    }
  })

  assert.equal(run.status, 'done')
  assert.deepEqual(requested, ['run-1', 'run-1'])
  assert.equal(sleeps, 2)
})

test('stops background polling without producing a terminal result', async () => {
  const run = await pollRunUntilTerminal({
    getRun: async () => {
      throw new Error('should not poll after cancellation')
    },
    initialRun: { status: 'queued', uuid: 'run-1' },
    isStopped: () => true,
    maxAttempts: 4,
    runUuid: 'run-1',
    sleep: async () => {}
  })

  assert.equal(run, null)
})

for (const status of [401, 403, 404]) {
  test(`stops completion polling when access is rejected with ${status}`, async () => {
    let polls = 0
    const run = await pollRunUntilTerminal({
      getRun: async () => {
        polls += 1
        throw { response: { status } }
      },
      initialRun: { status: 'running', uuid: 'run-a' },
      maxAttempts: 4,
      runUuid: 'run-a',
      sleep: async () => {}
    })
    assert.equal(polls, 1)
    assert.equal(run, null)
  })
}
