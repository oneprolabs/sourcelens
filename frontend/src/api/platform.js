import api from '@/api'

function unwrapResponse(response) {
  return response?.data?.data ?? response?.data ?? null
}

export async function getPlatformTenants(params = {}) {
  const response = await api.get('/v1/platform/tenants', { params })
  return unwrapResponse(response)
}

export async function startTenantDelegation(tenantUuid) {
  const response = await api.post(`/v1/platform/tenants/${tenantUuid}/delegate`)
  return unwrapResponse(response)
}

export async function endTenantDelegation(delegationId) {
  const response = await api.post(
    `/v1/platform/delegations/${delegationId}/end`
  )
  return unwrapResponse(response)
}

export async function getPlatformOverview() {
  const response = await api.get('/v1/platform/overview')
  return unwrapResponse(response)
}

export async function getPlatformAudit(params = {}) {
  const response = await api.get('/v1/platform/audit', { params })
  return unwrapResponse(response)
}

export async function getPlatformTenantStats(params = {}) {
  const response = await api.get('/v1/platform/tenant-stats', { params })
  return unwrapResponse(response)
}

export async function getPlatformMembers(params = {}) {
  const response = await api.get('/v1/platform/members', { params })
  return unwrapResponse(response)
}

export async function getPlatformApiKeys(params = {}) {
  const response = await api.get('/v1/platform/api-keys', { params })
  return unwrapResponse(response)
}

export async function platformApiKeyAction(uuid, action) {
  const response = await api.post(`/v1/platform/api-keys/${uuid}/${action}`)
  return unwrapResponse(response)
}

export async function platformBulkApiKeyAction(payload) {
  const response = await api.post('/v1/platform/api-keys/bulk', payload)
  return unwrapResponse(response)
}

export async function getPlatformApiKeyNotices(params = {}) {
  const response = await api.get('/v1/platform/api-key-notices', { params })
  return unwrapResponse(response)
}

export async function retryPlatformApiKeyNotice(uuid) {
  const response = await api.post(`/v1/platform/api-key-notices/${uuid}/retry`)
  return unwrapResponse(response)
}
