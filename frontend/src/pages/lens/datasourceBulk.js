/** Return only reasons supported by the datasource's actual state. */
export function dataSourceBulkSkipReason(row, action) {
  if (
    ['sync', 'retry', 'policy'].includes(action) &&
    (['upload', 'managed_workspace'].includes(row.source_type) ||
      row.plugin_key === 'file_upload')
  )
    return 'manualSource'
  if (['sync', 'retry'].includes(action) && row.status === 'disabled')
    return 'disabled'
  if (['sync', 'retry', 'delete'].includes(action) && row.current_sync?.task_id)
    return 'running'
  if (action === 'retry' && row.sync_state?.last_status !== 'failed')
    return 'notFailed'
  if (action === 'pause' && row.status === 'disabled') return 'alreadyDisabled'
  if (action === 'resume' && row.status !== 'disabled') return 'alreadyEnabled'
  return ''
}

/** Freeze the exact selected identities and the state used for the confirmation. */
export function freezeDataSourceTargets(rows) {
  const seen = new Set()
  return rows
    .filter((row) => row.uuid && !seen.has(row.uuid) && seen.add(row.uuid))
    .map((row) => ({
      uuid: row.uuid,
      name: row.name,
      source_type: row.source_type,
      plugin_key: row.plugin_key,
      status: row.status,
      current_sync: row.current_sync?.task_id
        ? { task_id: row.current_sync.task_id }
        : null,
      sync_state: { last_status: row.sync_state?.last_status || '' }
    }))
}

/** Execute once per frozen target; one failed request does not hide other results. */
export async function executeDataSourceBatch({
  targets,
  action,
  policy = {},
  api,
  canContinue = () => true,
  onResult = () => {}
}) {
  const frozen = freezeDataSourceTargets(targets)
  const schedule = { ...policy }
  const results = []
  for (const row of frozen) {
    if (!canContinue()) break
    const result = { uuid: row.uuid, name: row.name, status: 'success' }
    try {
      let reason = dataSourceBulkSkipReason(row, action)
      if (action === 'retry' && !reason) {
        const current = await api.get(row.uuid)
        if (!canContinue()) break
        reason = dataSourceBulkSkipReason(current, action)
      }
      if (reason) Object.assign(result, { status: 'skipped', reason })
      else if (action === 'sync' || action === 'retry') {
        const response = await api.sync(row.uuid)
        result.taskId = response?.task_id || ''
      } else if (action === 'policy')
        await api.setPolicy(row.uuid, { ...schedule })
      else if (action === 'pause' || action === 'resume')
        await api.setEnabled(row.uuid, action === 'resume')
      else if (action === 'delete') await api.remove(row.uuid)
      else throw new Error('Unsupported datasource operation')
    } catch (error) {
      Object.assign(result, { status: 'failed', error })
    }
    results.push(result)
    if (canContinue()) onResult(result)
  }
  return results
}
