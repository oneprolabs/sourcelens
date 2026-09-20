/**
 * Pure datasource helpers extracted from DataSources.vue.
 *
 * Every function here depends only on its arguments (no refs, no i18n, no
 * component state), so it can be shared between the page and its drawer
 * sub-components.
 */

import { EMPTY_VALUE } from './adminHelpers.js'

export function formatDocIds(docIds) {
  if (Array.isArray(docIds)) {
    return docIds.join(', ')
  }
  return docIds || EMPTY_VALUE
}

export function dataSourceRepository(row) {
  const config = row.config || {}
  const datasourceConfig = row.datasource_config || {}
  const resources = Array.isArray(datasourceConfig.repositories)
    ? datasourceConfig.repositories
    : Array.isArray(datasourceConfig.projects)
      ? datasourceConfig.projects
      : []
  const firstResource = resources[0]
  if (
    row.source_type === 'git' ||
    datasourceConfig.repository ||
    datasourceConfig.project ||
    firstResource
  ) {
    return (
      datasourceConfig.repository ||
      datasourceConfig.project ||
      firstResource ||
      config.organization_url ||
      config.repo_url ||
      EMPTY_VALUE
    )
  }
  return (
    datasourceConfig.resource_urls?.[0] ||
    config.folder_url ||
    config.folder_token ||
    config.document_url ||
    EMPTY_VALUE
  )
}

export function dataSourceRepositoryUrl(row, connectionEndpoint = '') {
  const resource = dataSourceRepository(row)
  if (resource === EMPTY_VALUE || /^[a-z][a-z\d+.-]*:\/\//i.test(resource)) {
    return resource
  }
  if (!row.plugin_key || !connectionEndpoint) {
    return resource
  }
  return `${String(connectionEndpoint).replace(/\/$/, '')}/${resource.replace(
    /^\//,
    ''
  )}`
}

export function dataSourceRepositories(row) {
  const datasourceConfig = row?.datasource_config || {}
  const pluginResources = Array.isArray(datasourceConfig.repositories)
    ? datasourceConfig.repositories
    : Array.isArray(datasourceConfig.projects)
      ? datasourceConfig.projects
      : []
  if (pluginResources.length) {
    return pluginResources.map((resource) =>
      typeof resource === 'string'
        ? {
            name: resource,
            path: resource,
            branch: datasourceConfig.branch || ''
          }
        : resource
    )
  }
  const repositories = row?.config?.repositories
  return Array.isArray(repositories) ? repositories : []
}

export function isOrganizationDataSource(row) {
  return dataSourceRepositories(row).length > 1
}

export function dataSourceBranch(row) {
  if (row.source_type === 'git') {
    return row.datasource_config?.branch || row.config?.branch || 'main'
  }
  return EMPTY_VALUE
}

export function syncTagClass(status) {
  const classes = {
    success: 'border-success-200 bg-success-50 text-success-700',
    failed: 'border-danger-200 bg-danger-50 text-danger-700',
    running: 'border-warning-200 bg-warning-50 text-warning-700',
    not_synced: 'border-line bg-surface-sunken text-ink-600',
    disabled: 'border-line bg-surface-sunken text-ink-500',
    available: 'border-success-200 bg-success-50 text-success-700',
    unavailable: 'border-warning-200 bg-warning-50 text-warning-700',
    error: 'border-danger-200 bg-danger-50 text-danger-700',
    unknown: 'border-line bg-surface-sunken text-ink-500',
    policy: 'border-primary-200 bg-primary-50 text-primary-700'
  }
  return classes[status] || classes.not_synced
}

export function formatDataSourcePolicyLine(syncPolicy) {
  if (syncPolicy?.mode === 'crontab') {
    const cron = syncPolicy.cron || EMPTY_VALUE
    const timezone = syncPolicy.timezone || 'UTC'
    return `Crontab: ${cron} - ${timezone}`
  }
  const interval = syncPolicy?.interval_seconds || 86400
  return `Interval: ${interval}s`
}

export function isDataSourceSyncing(row) {
  return Boolean(row?.current_sync?.task_id)
}

export function isDataSourceEnabled(row) {
  return row?.status !== 'disabled'
}

const FAILED_UPLOAD_STATUSES = new Set(['FAILURE', 'REVOKED', 'CANCELLING'])

export function latestUploadTasksByFilename(tasks) {
  const latestFiles = new Map()
  const deletedNames = new Set()
  ;(Array.isArray(tasks) ? tasks : []).forEach((task) => {
    const metadata = task?.metadata || {}
    const name = metadata.filename
    if (!name || latestFiles.has(name) || deletedNames.has(name)) return
    // Tasks arrive newest first: the first task per filename is the latest
    // version. A deleted latest version means the file is gone, so never
    // fall back to an older upload of the same name.
    if (metadata.deleted) {
      deletedNames.add(name)
      return
    }
    // A deduplicated re-upload stored nothing: keep the row (and processing
    // state) of the upload that actually put the file on disk.
    if (metadata.duplicate) return
    // A failed upload stored nothing; fall back to the previous version that
    // actually stored a file. The is_latest_version flag goes stale after a
    // deduplicated retry, so rely on ordering instead.
    if (FAILED_UPLOAD_STATUSES.has(String(task?.status || '').toUpperCase())) {
      return
    }
    latestFiles.set(name, task)
  })
  return latestFiles
}
