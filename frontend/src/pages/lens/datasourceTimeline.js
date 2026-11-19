/** Describe task results without inventing missing counters or failure causes. */
export function dataSourceTaskOutcome(task) {
  const status = String(task?.status || '').toUpperCase()
  if (status === 'FAILURE') return 'failed'
  if (status === 'REVOKED') return 'cancelled'
  if (status === 'CANCELLING') return 'cancelling'
  if (status === 'RETRY') return 'retrying'
  if (status === 'STARTED') return 'running'
  if (status !== 'SUCCESS') return 'pending'
  const metadata = task?.metadata || {}
  const summary = metadata.sync_summary || {}
  const conversion = metadata.conversion_summary || {}
  return summary.partial_success ||
    Number(summary.failed) > 0 ||
    Number(conversion.failed) > 0
    ? 'partial'
    : 'completed'
}

export function dataSourceTaskSummary(task, t) {
  const metadata = task?.metadata || {}
  const summary =
    task?.module === 'lens_datasource_conversion'
      ? metadata.conversion_summary || {}
      : metadata.sync_summary || {}
  const counts = ['changed', 'deleted', 'skipped', 'failed']
    .filter(
      (key) =>
        summary[key] != null &&
        Number.isFinite(Number(summary[key])) &&
        Number(summary[key]) >= 0
    )
    .map((key) =>
      t(
        `lensAdmin.datasourceDetail.timeline.${key === 'failed' ? 'failedCount' : key}`,
        { count: Number(summary[key]) }
      )
    )
  const error = task?.error || metadata.error
  return [counts.join(' · '), error || metadata.progress_message || '']
    .filter(Boolean)
    .join(' · ')
}
