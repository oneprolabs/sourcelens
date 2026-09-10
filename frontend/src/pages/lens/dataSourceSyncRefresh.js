const SYNC_STATUS_REFRESH_BASE_DELAY_MS = 5000
const SYNC_STATUS_REFRESH_MAX_DELAY_MS = 30000

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
      sync_state: status.sync_state,
      last_synced_at: status.last_synced_at,
      last_error: status.last_error
    }
  })
}

export function nextSyncStatusRefreshDelay(currentDelay, changed) {
  if (changed) return SYNC_STATUS_REFRESH_BASE_DELAY_MS
  return Math.min(
    Math.max(currentDelay, SYNC_STATUS_REFRESH_BASE_DELAY_MS) * 2,
    SYNC_STATUS_REFRESH_MAX_DELAY_MS
  )
}
