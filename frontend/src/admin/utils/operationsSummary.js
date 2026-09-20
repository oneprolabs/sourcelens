const RUN_STATUSES = ['running', 'streaming', 'queued', 'failed', 'done']
const FLEET_HEALTH_STATUSES = ['online', 'unresponsive', 'offline', 'draining']
const FLEET_WORKLOAD_FIELDS = [
  ['active_run_count', 'active_runs'],
  ['queued_run_count', 'queued_runs'],
  ['awaiting_resume_count', 'awaiting_resume']
]

function metricValue(value) {
  if (value === null || value === undefined || value === '') return null
  const number = Number(value)
  return Number.isFinite(number) && number >= 0 ? number : null
}

function hasCompletePage(payload, rows) {
  const total = metricValue(payload?.total ?? payload?.count ?? rows.length)
  return total === rows.length
}

export function resolveRunSummary(payload) {
  const rows = Array.isArray(payload?.results) ? payload.results : []
  const serverSummary = payload?.summary
  const completePage = hasCompletePage(payload, rows)
  const summary = {
    total: metricValue(serverSummary?.total ?? payload?.total ?? rows.length)
  }

  for (const status of RUN_STATUSES) {
    const serverValue = metricValue(serverSummary?.[status])
    summary[status] =
      serverValue ??
      (completePage
        ? rows.filter((row) => row?.status === status).length
        : null)
  }

  return summary
}

export function resolveFleetSummary(payload) {
  const rows = Array.isArray(payload?.results) ? payload.results : []
  const serverSummary = payload?.fleet_summary
  const completePage = hasCompletePage(payload, rows)
  const summary = {}

  for (const status of FLEET_HEALTH_STATUSES) {
    const serverValue = metricValue(serverSummary?.[status])
    summary[status] =
      serverValue ??
      (completePage
        ? rows.filter((row) => row?.status === status).length
        : null)
  }

  FLEET_WORKLOAD_FIELDS.forEach(([rowField, summaryField]) => {
    const serverValue = metricValue(serverSummary?.[summaryField])
    const rowValues = rows.map((row) => metricValue(row?.[rowField]))
    summary[summaryField] =
      serverValue ??
      (completePage && rowValues.every((value) => value !== null)
        ? rowValues.reduce((total, value) => total + value, 0)
        : null)
  })

  return summary
}

export function hasOperationMetric(value) {
  return metricValue(value) !== null
}

export function formatOperationMetric(value) {
  const metric = metricValue(value)
  return metric === null ? '—' : metric.toLocaleString()
}
