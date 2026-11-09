import api from '@/api'

function unwrapResponse(response) {
  return response?.data?.data ?? response?.data ?? null
}

export async function listEvaluationDatasets(params = {}) {
  const response = await api.get('/lens/admin/evaluation/datasets/', { params })
  return unwrapResponse(response)
}

export async function createEvaluationDataset(payload) {
  const response = await api.post('/lens/admin/evaluation/datasets/', payload)
  return unwrapResponse(response)
}

export async function getEvaluationDataset(uuid) {
  const response = await api.get(`/lens/admin/evaluation/datasets/${uuid}/`)
  return unwrapResponse(response)
}

export async function updateEvaluationDataset(uuid, payload) {
  const response = await api.patch(
    `/lens/admin/evaluation/datasets/${uuid}/`,
    payload
  )
  return unwrapResponse(response)
}

export async function deleteEvaluationDataset(uuid) {
  const response = await api.delete(`/lens/admin/evaluation/datasets/${uuid}/`)
  return unwrapResponse(response)
}

export async function listEvaluationCases(datasetUuid, params = {}) {
  const response = await api.get(
    `/lens/admin/evaluation/datasets/${datasetUuid}/cases/`,
    { params }
  )
  return unwrapResponse(response)
}

export async function createEvaluationCase(datasetUuid, payload) {
  const response = await api.post(
    `/lens/admin/evaluation/datasets/${datasetUuid}/cases/`,
    payload
  )
  return unwrapResponse(response)
}

export async function updateEvaluationCase(uuid, payload) {
  const response = await api.patch(
    `/lens/admin/evaluation/cases/${uuid}/`,
    payload
  )
  return unwrapResponse(response)
}

export async function deleteEvaluationCase(uuid) {
  const response = await api.delete(`/lens/admin/evaluation/cases/${uuid}/`)
  return unwrapResponse(response)
}

export async function importEvaluationCases(datasetUuid, { text, file, filename } = {}) {
  const url = `/lens/admin/evaluation/datasets/${datasetUuid}/import/`
  if (file) {
    const formData = new FormData()
    formData.append('file', file)
    const response = await api.post(url, formData, {
      headers: { 'Content-Type': 'multipart/form-data' }
    })
    return unwrapResponse(response)
  }
  const response = await api.post(url, { text, filename })
  return unwrapResponse(response)
}

export async function listDatasetRuns(datasetUuid, params = {}) {
  const response = await api.get(
    `/lens/admin/evaluation/datasets/${datasetUuid}/runs/`,
    { params }
  )
  return unwrapResponse(response)
}

export async function startEvaluationRun(datasetUuid, payload = {}) {
  const response = await api.post(
    `/lens/admin/evaluation/datasets/${datasetUuid}/runs/`,
    payload
  )
  return unwrapResponse(response)
}

export async function getEvaluationRun(uuid) {
  const response = await api.get(`/lens/admin/evaluation/runs/${uuid}/`)
  return unwrapResponse(response)
}

export async function cancelEvaluationRun(uuid) {
  const response = await api.post(`/lens/admin/evaluation/runs/${uuid}/cancel/`)
  return unwrapResponse(response)
}

export async function pauseEvaluationRun(uuid, payload = {}) {
  const response = await api.post(
    `/lens/admin/evaluation/runs/${uuid}/pause/`,
    payload
  )
  return unwrapResponse(response)
}

export async function resumeEvaluationRun(uuid) {
  const response = await api.post(`/lens/admin/evaluation/runs/${uuid}/resume/`)
  return unwrapResponse(response)
}

export async function retryEvaluationRun(uuid) {
  const response = await api.post(`/lens/admin/evaluation/runs/${uuid}/retry/`)
  return unwrapResponse(response)
}

export async function listEvaluationResults(runUuid, params = {}) {
  const response = await api.get(
    `/lens/admin/evaluation/runs/${runUuid}/results/`,
    { params }
  )
  return unwrapResponse(response)
}

export async function getEvaluationResult(runUuid, resultUuid) {
  const response = await api.get(
    `/lens/admin/evaluation/runs/${runUuid}/results/${resultUuid}/`
  )
  return unwrapResponse(response)
}

export async function getEvaluationReport(runUuid) {
  const response = await api.get(
    `/lens/admin/evaluation/runs/${runUuid}/report/`
  )
  return unwrapResponse(response)
}

export async function listEvaluationMetrics() {
  const response = await api.get('/lens/admin/evaluation/metrics/')
  return unwrapResponse(response)
}

export async function createEvaluationBaseline(datasetUuid, runUuid) {
  const response = await api.post(
    `/lens/admin/evaluation/datasets/${datasetUuid}/baseline/`,
    { run_uuid: runUuid }
  )
  return unwrapResponse(response)
}

export async function getEvaluationRevisions(datasetUuid, revision = null) {
  const response = await api.get(`/lens/admin/evaluation/datasets/${datasetUuid}/revisions/`, {
    params: revision ? { revision } : {}
  })
  return unwrapResponse(response)
}

export async function compareEvaluationRuns(runUuid, otherUuid) {
  const response = await api.get(`/lens/admin/evaluation/runs/${runUuid}/compare/`, {
    params: { other: otherUuid }
  })
  return unwrapResponse(response)
}

export async function getEvaluationCalibrations(runUuid, resultUuid) {
  const response = await api.get(`/lens/admin/evaluation/runs/${runUuid}/results/${resultUuid}/calibrations/`)
  return unwrapResponse(response)
}

export async function createEvaluationCalibration(runUuid, resultUuid, payload) {
  const response = await api.post(`/lens/admin/evaluation/runs/${runUuid}/results/${resultUuid}/calibrations/`, payload)
  return unwrapResponse(response)
}
