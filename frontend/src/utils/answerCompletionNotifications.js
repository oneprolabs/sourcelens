import { isRunAccessDenied } from './runAccess.js'

const UNREAD_STORAGE_KEY = 'sourcelens.answerCompletion.unreadSessions'
const IN_APP_NOTIFIED_RUNS_KEY = 'sourcelens.answerCompletion.inAppNotifiedRuns'
const NATIVE_NOTIFIED_RUNS_KEY =
  'sourcelens.answerCompletion.nativeNotifiedRuns'
const NOTIFICATION_SUPPRESSED_RUNS_KEY =
  'sourcelens.answerCompletion.notificationSuppressedRuns'
const UNREAD_DECIDED_RUNS_KEY = 'sourcelens.answerCompletion.unreadDecidedRuns'
const TERMINAL_RUN_STATUSES = new Set(['done', 'failed', 'cancelled'])
const MAX_NOTIFIED_RUNS = 100

function readObject(storage, key) {
  try {
    const value = JSON.parse(storage?.getItem(key) || '{}')
    return value && typeof value === 'object' && !Array.isArray(value)
      ? value
      : {}
  } catch {
    return {}
  }
}

function writeObject(storage, key, value) {
  storage?.setItem(key, JSON.stringify(value))
}

function readArray(storage, key) {
  try {
    const value = JSON.parse(storage?.getItem(key) || '[]')
    return Array.isArray(value) ? value : []
  } catch {
    return []
  }
}

export function readUnreadSessions(storage) {
  return readObject(storage, UNREAD_STORAGE_KEY)
}

export function answerCompletionTitle({
  baseTitle,
  completionLabel,
  hasUnread
}) {
  return hasUnread ? `${completionLabel} · ${baseTitle}` : baseTitle
}

export function shouldReviewUnreadSession({
  documentRef,
  selectedSessionUuid,
  unreadSessions
}) {
  return (
    documentRef?.visibilityState === 'visible' &&
    Boolean(selectedSessionUuid && unreadSessions?.[selectedSessionUuid])
  )
}

function markUnreadSession(storage, sessionUuid, runUuid) {
  const unread = readUnreadSessions(storage)
  if (unread[sessionUuid] === runUuid) {
    return false
  }
  unread[sessionUuid] = runUuid
  writeObject(storage, UNREAD_STORAGE_KEY, unread)
  return true
}

function reserveChannelNotification(storage, key, otherKey, runUuid) {
  const notifiedRuns = readArray(storage, key)
  if (
    notifiedRuns.includes(runUuid) ||
    readArray(storage, otherKey).includes(runUuid) ||
    readArray(storage, NOTIFICATION_SUPPRESSED_RUNS_KEY).includes(runUuid)
  ) {
    return false
  }
  try {
    storage?.setItem(
      key,
      JSON.stringify([...notifiedRuns, runUuid].slice(-MAX_NOTIFIED_RUNS))
    )
    return Boolean(storage)
  } catch {
    return false
  }
}

function reserveSuppressedNotification(storage, runUuid) {
  const suppressedRuns = readArray(storage, NOTIFICATION_SUPPRESSED_RUNS_KEY)
  if (
    suppressedRuns.includes(runUuid) ||
    readArray(storage, IN_APP_NOTIFIED_RUNS_KEY).includes(runUuid) ||
    readArray(storage, NATIVE_NOTIFIED_RUNS_KEY).includes(runUuid)
  ) {
    return false
  }
  try {
    storage?.setItem(
      NOTIFICATION_SUPPRESSED_RUNS_KEY,
      JSON.stringify([...suppressedRuns, runUuid].slice(-MAX_NOTIFIED_RUNS))
    )
    return Boolean(storage)
  } catch {
    return false
  }
}

function reserveUnreadDecision(storage, runUuid) {
  const decidedRuns = readArray(storage, UNREAD_DECIDED_RUNS_KEY)
  if (decidedRuns.includes(runUuid)) {
    return false
  }
  try {
    storage?.setItem(
      UNREAD_DECIDED_RUNS_KEY,
      JSON.stringify([...decidedRuns, runUuid].slice(-MAX_NOTIFIED_RUNS))
    )
    return Boolean(storage)
  } catch {
    return false
  }
}

export function clearUnreadSession(storage, sessionUuid) {
  const unread = readUnreadSessions(storage)
  if (!(sessionUuid in unread)) {
    return false
  }
  delete unread[sessionUuid]
  writeObject(storage, UNREAD_STORAGE_KEY, unread)
  return true
}

function isInactive(documentRef) {
  return (
    documentRef?.visibilityState !== 'visible' ||
    documentRef?.hasFocus?.() === false
  )
}

export async function requestNativeNotificationPermission(NotificationRef) {
  if (!NotificationRef) return null
  if (NotificationRef.permission !== 'default') {
    return NotificationRef.permission
  }
  try {
    return await NotificationRef.requestPermission()
  } catch {
    return null
  }
}

function showNativeCompletionNotification(options) {
  const nativeNotification = options.nativeNotification
  const NotificationRef = nativeNotification?.NotificationRef
  if (
    !nativeNotification?.enabled ||
    !isInactive(options.documentRef) ||
    NotificationRef?.permission !== 'granted' ||
    !reserveChannelNotification(
      options.storage,
      NATIVE_NOTIFIED_RUNS_KEY,
      IN_APP_NOTIFIED_RUNS_KEY,
      options.run.uuid
    )
  ) {
    return false
  }

  try {
    const notification = new NotificationRef(nativeNotification.title, {
      body: nativeNotification.body,
      renotify: false,
      tag: `sourcelens-answer-${options.run.uuid}`
    })
    notification.onclick = () => {
      try {
        notification.close()
        nativeNotification.windowRef?.focus?.()
        const opening = nativeNotification.onOpenConversation?.(
          options.sessionUuid
        )
        opening?.catch?.(() => {})
      } catch {
        return
      }
    }
    return true
  } catch {
    return false
  }
}

export function handleTerminalRun(options) {
  if (options.run?.status !== 'done') {
    return {
      inAppNotificationRequested: false,
      nativeNotificationShown: false,
      unreadChanged: false
    }
  }

  const isAnotherConversation =
    options.sessionUuid !== options.selectedSessionUuid
  const inactive = isInactive(options.documentRef)
  let unreadChanged = false
  if (
    reserveUnreadDecision(options.storage, options.run.uuid) &&
    options.indicatorEnabled &&
    (isAnotherConversation || inactive)
  ) {
    unreadChanged = markUnreadSession(
      options.storage,
      options.sessionUuid,
      options.run.uuid
    )
  }

  const nativeNotificationShown = inactive
    ? showNativeCompletionNotification(options)
    : false
  const inAppNotificationRequested =
    options.indicatorEnabled &&
    isAnotherConversation &&
    !inactive &&
    reserveChannelNotification(
      options.storage,
      IN_APP_NOTIFIED_RUNS_KEY,
      NATIVE_NOTIFIED_RUNS_KEY,
      options.run.uuid
    )
  if (
    (!isAnotherConversation && !inactive) ||
    (inactive && !nativeNotificationShown) ||
    (isAnotherConversation && !inactive && !options.indicatorEnabled)
  ) {
    reserveSuppressedNotification(options.storage, options.run.uuid)
  }

  return {
    inAppNotificationRequested,
    nativeNotificationShown,
    unreadChanged
  }
}

export function answerSummary(content, limit = 120) {
  const summary = String(content || '')
    .replace(/```[\s\S]*?```/g, ' ')
    .replace(/!\[[^\]]*\]\([^)]*\)/g, ' ')
    .replace(/\[([^\]]+)\]\([^)]*\)/g, '$1')
    .replace(/<[^>]+>/g, ' ')
    .replace(/[#>*_~`|]/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
  if (summary.length <= limit) {
    return summary
  }
  return `${summary.slice(0, limit).trimEnd()}…`
}

export async function pollRunUntilTerminal({
  getRun,
  initialRun,
  isStopped = () => false,
  maxAttempts,
  runUuid,
  sleep
}) {
  let run = initialRun
  for (let attempt = 0; attempt < maxAttempts; attempt += 1) {
    if (TERMINAL_RUN_STATUSES.has(run?.status)) {
      return run
    }
    if (isStopped()) return null
    await sleep()
    if (isStopped()) return null
    try {
      run = await getRun(runUuid)
    } catch (error) {
      if (isRunAccessDenied(error)) return null
      continue
    }
  }
  return TERMINAL_RUN_STATUSES.has(run?.status) ? run : null
}

export {
  IN_APP_NOTIFIED_RUNS_KEY,
  NATIVE_NOTIFIED_RUNS_KEY,
  NOTIFICATION_SUPPRESSED_RUNS_KEY,
  UNREAD_DECIDED_RUNS_KEY,
  UNREAD_STORAGE_KEY
}
