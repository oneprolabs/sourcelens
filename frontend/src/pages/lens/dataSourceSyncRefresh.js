const SYNC_STATUS_REFRESH_BASE_DELAY_MS = 5000
const SYNC_STATUS_REFRESH_MAX_DELAY_MS = 30000
const TERMINAL_TASK_STATUSES = new Set(['SUCCESS', 'FAILURE', 'REVOKED'])

export function mergeDataSourceSyncStatuses(rows, statuses) {
  const statusByUuid = new Map(
    (Array.isArray(statuses) ? statuses : []).map((status) => [
      status.uuid,
      status
    ])
  )
  return rows.map((row) => {
    const status = statusByUuid.get(row.uuid)
    if (!status) return row
    return {
      ...row,
      current_sync: status.current_sync,
      last_task: status.last_task,
      sync_state: status.sync_state,
      last_synced_at: status.last_synced_at,
      last_error: status.last_error
    }
  })
}

/**
 * Keep the terminal result of tasks that belong to this page session.
 *
 * A task is remembered once it is either seen active (`watchedTaskIds`) or
 * created after the page loaded (`sessionStartedAt`), so even a fast upload
 * that finishes between polls still shows its result. Nothing is persisted, so
 * a page reload starts with an empty set and shows no stale result.
 */
export function collectCompletedTaskResults(
  statuses,
  watchedTaskIds,
  previous = {},
  sessionStartedAt = null
) {
  const next = { ...previous }
  ;(Array.isArray(statuses) ? statuses : []).forEach((status) => {
    const uuid = status?.uuid
    if (!uuid) return
    const active = status.current_sync
    if (active?.task_id) {
      watchedTaskIds.add(active.task_id)
      delete next[uuid]
      return
    }
    const lastTask = status.last_task
    if (!lastTask?.task_id) return
    const createdInSession =
      sessionStartedAt != null &&
      lastTask.created_at != null &&
      new Date(lastTask.created_at).getTime() >= sessionStartedAt
    if (
      (watchedTaskIds.has(lastTask.task_id) || createdInSession) &&
      TERMINAL_TASK_STATUSES.has(String(lastTask.status || '').toUpperCase())
    ) {
      next[uuid] = lastTask
    }
  })
  return next
}

export function nextSyncStatusRefreshDelay(currentDelay, changed) {
  if (changed) return SYNC_STATUS_REFRESH_BASE_DELAY_MS
  return Math.min(
    Math.max(currentDelay, SYNC_STATUS_REFRESH_BASE_DELAY_MS) * 2,
    SYNC_STATUS_REFRESH_MAX_DELAY_MS
  )
}
