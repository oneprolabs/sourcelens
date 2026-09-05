import api from '@/api'
import { citationSourceUrl } from '@/pages/lens/codeCitations'

import { createInFlightRequestCache } from './inFlight'
import { collectPaginatedResults } from './pagination'

const assistantListRequests = createInFlightRequestCache()

function unwrapResponse(response) {
  return response?.data?.data ?? response?.data ?? null
}

function unwrapList(payload) {
  if (Array.isArray(payload)) {
    return payload
  }
  if (Array.isArray(payload?.results)) {
    return payload.results
  }
  return []
}

export async function listAssistants(params = {}) {
  const key = JSON.stringify(params)
  return assistantListRequests.run(key, () =>
    collectPaginatedResults(async (page) => {
      const response = await api.get('/lens/assistants/', {
        params: { page_size: 100, ...params, page }
      })
      return unwrapResponse(response)
    })
  )
}

export async function getPublicAssistant(slug) {
  const response = await api.get(`/lens/public/assistants/${slug}/`)
  return unwrapResponse(response)
}

export async function getAssistant(uuid) {
  const response = await api.get(`/lens/assistants/${uuid}/`)
  return unwrapResponse(response)
}

export async function createAssistant(payload) {
  const response = await api.post('/lens/assistants/', payload)
  return unwrapResponse(response)
}

export async function updateAssistant(uuid, payload) {
  const response = await api.patch(`/lens/assistants/${uuid}/`, payload)
  return unwrapResponse(response)
}

export async function archiveAssistant(uuid) {
  const response = await api.post(`/lens/assistants/${uuid}/archive/`)
  return unwrapResponse(response)
}

export async function restoreAssistant(uuid) {
  const response = await api.post(`/lens/assistants/${uuid}/restore/`)
  return unwrapResponse(response)
}

export async function listLensNodes() {
  return collectPaginatedResults(async (page) =>
    listLensNodePage({ page, page_size: 1000 })
  )
}

export async function listLensNodePage(params = {}) {
  const response = await api.get('/lens/admin/lensnodes/', { params })
  return unwrapResponse(response)
}

export async function getAdminRuns(params = {}) {
  const response = await api.get('/lens/admin/runs/', { params })
  return unwrapResponse(response)
}

export async function getAdminRun(uuid) {
  const response = await api.get(`/lens/admin/runs/${uuid}/`)
  return unwrapResponse(response)
}

export async function getAdminRunTrajectory(runUuid, params = {}) {
  const response = await api.get(`/lens/admin/runs/${runUuid}/trajectory/`, {
    params
  })
  return unwrapResponse(response)
}

export async function streamAdminRunTrajectory(
  runUuid,
  {
    cursor = '',
    revision = '',
    sequence = 0,
    q = '',
    category = '',
    signal,
    onEvent
  } = {}
) {
  const params = new URLSearchParams()
  if (cursor) params.set('cursor', cursor)
  if (revision) params.set('revision', revision)
  if (sequence) params.set('sequence', String(sequence))
  if (q) params.set('q', q)
  if (category) params.set('category', category)
  const query = params.toString()
  const baseUrl = String(api.defaults.baseURL || '/api').replace(/\/$/, '')
  const token = localStorage.getItem('access_token')
  const headers = { Accept: 'text/event-stream' }
  if (token) headers.Authorization = `Bearer ${token}`
  const response = await fetch(
    `${baseUrl}/lens/admin/runs/${runUuid}/trajectory/stream/${query ? `?${query}` : ''}`,
    {
      credentials: 'include',
      headers,
      signal
    }
  )
  if (!response.ok || !response.body) {
    const error = new Error('Trajectory stream failed')
    error.response = { status: response.status }
    throw error
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true }).replace(/\r\n/g, '\n')
    const frames = buffer.split('\n\n')
    buffer = frames.pop() || ''
    for (const frame of frames) {
      const data = frame
        .split('\n')
        .filter((line) => line.startsWith('data:'))
        .map((line) => line.slice(5).trimStart())
        .join('\n')
      if (data) onEvent?.(JSON.parse(data))
    }
  }
}

export async function getAdminRunTrajectoryExport(runUuid) {
  const events = []
  let afterSequence = 0
  let summary = null
  let hasMore = true

  while (hasMore) {
    const payload = await getAdminRunTrajectory(runUuid, {
      after_sequence: afterSequence,
      page_size: 500
    })
    const rows = unwrapList(payload)
    events.push(...rows)
    summary ||= payload?.summary ?? null
    hasMore = Boolean(payload?.has_more) && rows.length > 0
    if (!hasMore) break
    afterSequence = payload.next_after_sequence
  }

  return { summary, events }
}

export async function cancelAdminRun(runUuid) {
  const response = await api.post(`/lens/admin/runs/${runUuid}/cancel/`)
  return unwrapResponse(response)
}

export async function retryAdminRun(runUuid, idempotencyKey) {
  const response = await api.post(`/lens/admin/runs/${runUuid}/retry/`, {
    idempotency_key: idempotencyKey
  })
  return unwrapResponse(response)
}

export async function resumeAdminRun(runUuid) {
  const response = await api.post(`/lens/admin/runs/${runUuid}/resume/`)
  return unwrapResponse(response)
}

export async function getAdminRunDiagnostics(runUuid) {
  const response = await api.get(`/lens/admin/runs/${runUuid}/diagnostics/`)
  return unwrapList(unwrapResponse(response))
}

export async function generateAdminRunDiagnosis(runUuid) {
  const response = await api.post(
    `/lens/admin/runs/${runUuid}/diagnostics/`,
    {}
  )
  return unwrapResponse(response)
}

export async function createAdminRunDiagnosticTurn(
  runUuid,
  diagnosticUuid,
  question
) {
  const response = await api.post(
    `/lens/admin/runs/${runUuid}/diagnostics/${diagnosticUuid}/turns/`,
    { question }
  )
  return unwrapResponse(response)
}

export async function getAdminUserAccessDetail(userId) {
  const response = await api.get(`/lens/admin/access/users/${userId}/`)
  return unwrapResponse(response)
}

export async function getAdminGroupAccessDetail(groupId, params = {}) {
  const response = await api.get(`/lens/admin/access/groups/${groupId}/`, {
    params
  })
  return unwrapResponse(response)
}

export async function createLensNode(payload) {
  const response = await api.post('/lens/admin/lensnodes/', payload)
  return unwrapResponse(response)
}

export async function updateLensNode(uuid, payload) {
  const response = await api.patch(`/lens/admin/lensnodes/${uuid}/`, payload)
  return unwrapResponse(response)
}

export async function deleteLensNode(uuid) {
  const response = await api.delete(`/lens/admin/lensnodes/${uuid}/`)
  return unwrapResponse(response)
}

export async function scanLensNodeDirs(uuid, paths) {
  const response = await api.post(`/lens/admin/lensnodes/${uuid}/list-dirs/`, {
    paths
  })
  return unwrapResponse(response)
}

export async function checkLensNodeDataSourcePath(uuid, payload) {
  const response = await api.post(
    `/lens/admin/lensnodes/${uuid}/check-datasource-path/`,
    payload
  )
  return unwrapResponse(response)
}

export async function testLensNodeDataSourceConnection(uuid, payload) {
  const response = await api.post(
    `/lens/admin/lensnodes/${uuid}/test-datasource-connection/`,
    payload,
    { timeout: 120000 }
  )
  return unwrapResponse(response)
}

export async function approveLensNode(uuid) {
  const response = await api.post(`/lens/admin/lensnodes/${uuid}/approve/`)
  return unwrapResponse(response)
}

export async function rejectLensNode(uuid) {
  const response = await api.post(`/lens/admin/lensnodes/${uuid}/reject/`)
  return unwrapResponse(response)
}

export async function issueLensNodeToken(uuid) {
  const response = await api.post(`/lens/admin/lensnodes/${uuid}/issue-token/`)
  return unwrapResponse(response)
}

export async function revokeLensNodeToken(uuid) {
  const response = await api.post(`/lens/admin/lensnodes/${uuid}/revoke-token/`)
  return unwrapResponse(response)
}

export async function listSessions(assistantSlug = '', options = {}) {
  const params = {
    ...(assistantSlug ? { assistant_slug: assistantSlug } : {}),
    ...(options.archived ? { archived: true } : {}),
    ...(options.routingMode ? { routing_mode: options.routingMode } : {})
  }
  const response = await api.get('/lens/sessions/', { params })
  return unwrapList(unwrapResponse(response))
}

export async function getSession(uuid) {
  const response = await api.get(`/lens/sessions/${uuid}/`)
  return unwrapResponse(response)
}

export async function listMessages(sessionUuid) {
  const response = await api.get(`/lens/sessions/${sessionUuid}/messages/`)
  return unwrapList(unwrapResponse(response))
}

export async function createSession(payload) {
  const response = await api.post('/lens/sessions/', payload)
  return unwrapResponse(response)
}

export async function updateSession(uuid, payload) {
  const response = await api.patch(`/lens/sessions/${uuid}/`, payload)
  return unwrapResponse(response)
}

export async function deleteSession(uuid) {
  await api.delete(`/lens/sessions/${uuid}/`)
}

export async function pinSession(uuid) {
  const response = await api.post(`/lens/sessions/${uuid}/pin/`)
  return unwrapResponse(response)
}

export async function unpinSession(uuid) {
  const response = await api.post(`/lens/sessions/${uuid}/unpin/`)
  return unwrapResponse(response)
}

export async function archiveSession(uuid) {
  const response = await api.post(`/lens/sessions/${uuid}/archive/`)
  return unwrapResponse(response)
}

export async function restoreSession(uuid) {
  const response = await api.post(`/lens/sessions/${uuid}/restore/`)
  return unwrapResponse(response)
}

export async function createRun(sessionUuid, payload) {
  const response = await api.post(
    `/lens/sessions/${sessionUuid}/runs/`,
    payload
  )
  return unwrapResponse(response)
}

export async function uploadAttachment(sessionUuid, file) {
  const form = new FormData()
  form.append('file', file)
  const response = await api.post(
    `/lens/sessions/${sessionUuid}/attachments/`,
    form,
    { headers: { 'Content-Type': 'multipart/form-data' } }
  )
  return unwrapResponse(response)
}

export async function deleteAttachment(uuid) {
  await api.delete(`/lens/attachments/${uuid}/`)
}

export async function getRun(uuid) {
  const response = await api.get(`/lens/runs/${uuid}/`)
  return unwrapResponse(response)
}

export async function getRunCitationSource(runUuid, citationId) {
  const response = await api.get(citationSourceUrl(runUuid, citationId))
  return unwrapResponse(response)
}

export async function getRunPdf(uuid) {
  return api.get(`/lens/runs/${uuid}/pdf/`, { responseType: 'blob' })
}

export async function cancelRun(runUuid) {
  const response = await api.post(`/lens/runs/${runUuid}/cancel/`)
  return unwrapResponse(response)
}

export async function answerRunClarification(runUuid, requestId, answer) {
  const response = await api.post(`/lens/runs/${runUuid}/clarification/`, {
    request_id: requestId,
    answer
  })
  return unwrapResponse(response)
}

export async function updateRunFeedback(runUuid, feedback) {
  const response = await api.patch(`/lens/runs/${runUuid}/feedback/`, {
    feedback
  })
  return unwrapResponse(response)
}

export async function listDataSources(params = {}) {
  const response = await api.get('/lens/admin/datasources/', { params })
  const payload = unwrapResponse(response)
  return Object.keys(params || {}).length ? payload : unwrapList(payload)
}

export async function createDataSource(payload) {
  const response = await api.post('/lens/admin/datasources/', payload)
  return unwrapResponse(response)
}

export async function updateDataSource(uuid, payload) {
  const response = await api.patch(`/lens/admin/datasources/${uuid}/`, payload)
  return unwrapResponse(response)
}

export async function deleteDataSource(uuid) {
  const response = await api.delete(`/lens/admin/datasources/${uuid}/`)
  return unwrapResponse(response)
}

export async function listCredentials(params = {}) {
  return collectPaginatedResults(async (page) => {
    const response = await api.get('/lens/admin/credentials/', {
      params: { page_size: 10000, ...params, page }
    })
    return unwrapResponse(response)
  })
}

export async function createCredential(payload) {
  const response = await api.post('/lens/admin/credentials/', payload)
  return unwrapResponse(response)
}

export async function updateCredential(uuid, payload) {
  const response = await api.patch(`/lens/admin/credentials/${uuid}/`, payload)
  return unwrapResponse(response)
}

export async function revealCredential(uuid) {
  const response = await api.post(`/lens/admin/credentials/${uuid}/reveal/`)
  return unwrapResponse(response)
}

export async function validateCredential(uuid) {
  const response = await api.post(`/lens/admin/credentials/${uuid}/validate/`)
  return unwrapResponse(response)
}

export async function deleteCredential(uuid) {
  const response = await api.delete(`/lens/admin/credentials/${uuid}/`)
  return unwrapResponse(response)
}

export async function listPlugins() {
  const response = await api.get('/lens/admin/plugins/')
  return unwrapList(unwrapResponse(response))
}

export async function getPluginManifest(key) {
  const response = await api.get(`/lens/admin/plugins/${key}/manifest/`)
  return unwrapResponse(response)
}

export async function getPluginIcon(key) {
  const response = await api.get(`/lens/admin/plugins/${key}/icon/`, {
    responseType: 'blob'
  })
  return response.data
}

export async function listConnections(params = {}) {
  return collectPaginatedResults(async (page) => {
    const response = await api.get('/lens/admin/connections/', {
      params: { page_size: 1000, ...params, page }
    })
    return unwrapResponse(response)
  })
}

export async function createConnection(payload) {
  const response = await api.post('/lens/admin/connections/', payload)
  return unwrapResponse(response)
}

export async function updateConnection(uuid, payload) {
  const response = await api.patch(`/lens/admin/connections/${uuid}/`, payload)
  return unwrapResponse(response)
}

export async function deleteConnection(uuid) {
  const response = await api.delete(`/lens/admin/connections/${uuid}/`)
  return unwrapResponse(response)
}

export async function validateConnection(uuid) {
  const response = await api.post(`/lens/admin/connections/${uuid}/validate/`)
  return unwrapResponse(response)
}

export async function validateConnectionDatasource(uuid, payload) {
  const response = await api.post(
    `/lens/admin/connections/${uuid}/validate-datasource/`,
    payload,
    { headers: { 'Cache-Control': 'no-store' } }
  )
  return unwrapResponse(response)
}

export async function getConnectionResources(uuid, params = {}) {
  const response = await api.get(`/lens/admin/connections/${uuid}/resources/`, {
    params
  })
  return unwrapResponse(response)
}

export async function getConnectionResourceCandidates(uuid, params = {}) {
  const response = await api.get(
    `/lens/admin/connections/${uuid}/resource-candidates/`,
    {
      params,
      headers: { 'Cache-Control': 'no-store' }
    }
  )
  return unwrapResponse(response)
}

export async function previewConnectionResources(payload) {
  const response = await api.post(
    '/lens/admin/connections/resource-preview/',
    payload,
    { headers: { 'Cache-Control': 'no-store' } }
  )
  return unwrapResponse(response)
}

export async function listEnvironmentVariableSets() {
  return collectPaginatedResults(async (page) => {
    const response = await api.get('/lens/admin/environment-variable-sets/', {
      params: { page_size: 1000, page }
    })
    return unwrapResponse(response)
  })
}

export async function createEnvironmentVariableSet(payload) {
  const response = await api.post(
    '/lens/admin/environment-variable-sets/',
    payload
  )
  return unwrapResponse(response)
}

export async function updateEnvironmentVariableSet(uuid, payload) {
  const response = await api.patch(
    `/lens/admin/environment-variable-sets/${uuid}/`,
    payload
  )
  return unwrapResponse(response)
}

export async function revealEnvironmentVariableSet(uuid) {
  const response = await api.post(
    `/lens/admin/environment-variable-sets/${uuid}/reveal/`
  )
  return unwrapResponse(response)
}

export async function deleteEnvironmentVariableSet(uuid) {
  const response = await api.delete(
    `/lens/admin/environment-variable-sets/${uuid}/`
  )
  return unwrapResponse(response)
}

export async function syncDataSource(uuid, payload = {}) {
  const response = await api.post(
    `/lens/admin/datasources/${uuid}/sync/`,
    payload
  )
  return unwrapResponse(response)
}

export async function setDataSourceEnabled(uuid, enabled) {
  const response = await api.post(
    `/lens/admin/datasources/${uuid}/set-enabled/`,
    { enabled }
  )
  return unwrapResponse(response)
}

export async function refreshDataSourceAvailability(uuid) {
  const response = await api.post(
    `/lens/admin/datasources/${uuid}/refresh-availability/`
  )
  return unwrapResponse(response)
}

export async function uploadDataSourceFile(uuid, file) {
  const formData = new FormData()
  formData.append('file', file)
  const response = await api.post(
    `/lens/admin/datasources/${uuid}/upload/`,
    formData,
    { headers: { 'Content-Type': 'multipart/form-data' } }
  )
  return unwrapResponse(response)
}

export async function cancelDataSourceSync(uuid) {
  const response = await api.post(
    `/lens/admin/datasources/${uuid}/cancel-sync/`
  )
  return unwrapResponse(response)
}

export async function listSkills() {
  return collectPaginatedResults(async (page) => {
    const response = await api.get('/lens/admin/skills/', {
      params: { page_size: 1000, page }
    })
    return unwrapResponse(response)
  })
}

export async function createSkill(payload) {
  const response = await api.post('/lens/admin/skills/', payload)
  return unwrapResponse(response)
}

export async function updateSkill(uuid, payload) {
  const response = await api.patch(`/lens/admin/skills/${uuid}/`, payload)
  return unwrapResponse(response)
}

export async function deleteSkill(uuid) {
  const response = await api.delete(`/lens/admin/skills/${uuid}/`)
  return unwrapResponse(response)
}

export async function getSkillDeleteImpact(uuid) {
  const response = await api.get(`/lens/admin/skills/${uuid}/delete-impact/`)
  return unwrapResponse(response)
}

export async function forceDeleteSkill(uuid, confirmationName) {
  const response = await api.post(`/lens/admin/skills/${uuid}/force-delete/`, {
    confirmation_name: confirmationName
  })
  return unwrapResponse(response)
}

export async function uploadSkill(file, environment) {
  const payload = new FormData()
  payload.append('file', file)
  if (environment !== undefined) {
    payload.append('environment', JSON.stringify(environment))
  }
  const response = await api.post('/lens/admin/skills/upload/', payload, {
    headers: { 'Content-Type': 'multipart/form-data' }
  })
  return unwrapResponse(response)
}

export async function updateUploadedSkill(uuid, file, environment) {
  const payload = new FormData()
  payload.append('file', file)
  if (environment !== undefined) {
    payload.append('environment', JSON.stringify(environment))
  }
  const response = await api.post(
    `/lens/admin/skills/${uuid}/update-upload/`,
    payload,
    { headers: { 'Content-Type': 'multipart/form-data' } }
  )
  return unwrapResponse(response)
}

export async function importSkillFromGithub(url) {
  const response = await api.post('/lens/admin/skills/import-github/', {
    url
  })
  return unwrapResponse(response)
}

export async function updateGithubSkill(uuid, url) {
  const response = await api.post(`/lens/admin/skills/${uuid}/update-github/`, {
    url
  })
  return unwrapResponse(response)
}

export async function checkSkillUpdates() {
  const response = await api.post('/lens/admin/skills/check-updates/')
  return unwrapResponse(response)
}

export async function downloadSkill(uuid) {
  const response = await api.get(`/lens/admin/skills/${uuid}/download/`, {
    responseType: 'blob'
  })
  return response
}

export async function previewSkillFile(uuid, path) {
  const response = await api.get(`/lens/admin/skills/${uuid}/file-preview/`, {
    params: { path }
  })
  return unwrapResponse(response)
}

export async function beautifySkill(payload) {
  const response = await api.post('/lens/admin/skills/beautify/', payload)
  return unwrapResponse(response)
}

export async function listMcpServers() {
  return collectPaginatedResults(async (page) => {
    const response = await api.get('/lens/admin/mcp-servers/', {
      params: { page_size: 1000, page }
    })
    return unwrapResponse(response)
  })
}

export async function createMcpServer(payload) {
  const response = await api.post('/lens/admin/mcp-servers/', payload)
  return unwrapResponse(response)
}

export async function updateMcpServer(uuid, payload) {
  const response = await api.patch(`/lens/admin/mcp-servers/${uuid}/`, payload)
  return unwrapResponse(response)
}

export async function deleteMcpServer(uuid) {
  const response = await api.delete(`/lens/admin/mcp-servers/${uuid}/`)
  return unwrapResponse(response)
}

export async function listGlobalSettings() {
  const response = await api.get('/lens/admin/global-settings/')
  return unwrapList(unwrapResponse(response))
}

export async function createGlobalSetting(payload) {
  const response = await api.post('/lens/admin/global-settings/', payload)
  return unwrapResponse(response)
}

export async function updateGlobalSetting(key, payload) {
  const response = await api.patch(
    `/lens/admin/global-settings/${encodeURIComponent(key)}/`,
    payload
  )
  return unwrapResponse(response)
}

export async function getSystemHealth() {
  const response = await api.get('/lens/admin/global-settings/system-health/')
  return unwrapList(unwrapResponse(response))
}

export async function updateSystemTaskEnabled(taskType, enabled) {
  const response = await api.patch(
    '/lens/admin/global-settings/system-health/',
    {
      task_type: taskType,
      enabled
    }
  )
  return unwrapResponse(response)
}

// Public shareable Q&A

export async function shareRun(runUuid, payload = {}) {
  const response = await api.post(`/lens/runs/${runUuid}/share/`, payload)
  return unwrapResponse(response)
}

export async function listMyShares() {
  const response = await api.get('/lens/shares/')
  return unwrapList(unwrapResponse(response))
}

export async function updateMyShare(uuid, payload) {
  const response = await api.patch(`/lens/shares/${uuid}/`, payload)
  return unwrapResponse(response)
}

export async function deleteShare(uuid) {
  await api.delete(`/lens/shares/${uuid}/`)
}

export async function getPublicQa(token) {
  const response = await api.get(`/lens/public/qa/${token}/`)
  return unwrapResponse(response)
}

export async function getPublicQaPdf(token) {
  return api.get(`/lens/public/qa/${token}/pdf/`, {
    responseType: 'blob'
  })
}

export async function getPublicAssistantQa(slug, params = {}) {
  const response = await api.get(`/lens/public/assistants/${slug}/qa/`, {
    params
  })
  return unwrapResponse(response)
}

export async function listAdminShares(params = {}) {
  const response = await api.get('/lens/admin/shares/', { params })
  return unwrapResponse(response)
}

export async function getAdminShare(uuid) {
  const response = await api.get(`/lens/admin/shares/${uuid}/`)
  return unwrapResponse(response)
}

export async function updateAdminShare(uuid, payload) {
  const response = await api.patch(`/lens/admin/shares/${uuid}/`, payload)
  return unwrapResponse(response)
}
