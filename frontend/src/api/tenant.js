import api from '@/api'

function unwrapResponse(response) {
  return response?.data?.data ?? response?.data ?? null
}

export async function getTenantOverview() {
  const response = await api.get('/v1/tenant/overview')
  return unwrapResponse(response)
}

export async function getCurrentTenant() {
  const response = await api.get('/v1/tenant')
  return unwrapResponse(response)
}

export async function updateCurrentTenant(payload) {
  const response = await api.patch('/v1/tenant', payload)
  return unwrapResponse(response)
}

export async function uploadTenantLogo(file) {
  const form = new FormData()
  form.append('logo', file)
  const response = await api.post('/v1/tenant/logo', form, {
    headers: { 'Content-Type': 'multipart/form-data' }
  })
  return unwrapResponse(response)
}

export async function getTenantMembers(params = {}) {
  const response = await api.get('/v1/tenant/members', { params })
  return unwrapResponse(response)
}

export async function bulkUpdateTenantMembers(payload) {
  const response = await api.post('/v1/tenant/members/bulk', payload)
  return unwrapResponse(response)
}

export async function updateTenantMember(userId, payload) {
  const response = await api.patch(`/v1/tenant/members/${userId}`, payload)
  return unwrapResponse(response)
}

export async function unlockTenantMember(userId, version) {
  const response = await api.post(`/v1/tenant/members/${userId}/unlock`, {
    version
  })
  return unwrapResponse(response)
}

export async function resetTenantMemberPassword(userId, version) {
  const response = await api.post(
    `/v1/tenant/members/${userId}/password-reset`,
    {
      version
    }
  )
  return unwrapResponse(response)
}

export async function deleteTenantMembers(payload) {
  const response = await api.post('/v1/tenant/members/delete', payload)
  return unwrapResponse(response)
}

export async function previewTenantInvitations(payload) {
  const response = await api.post(
    '/v1/tenant/invitations/bulk/preview',
    payload
  )
  return unwrapResponse(response)
}

export async function bulkInviteTenantMembers(payload) {
  const response = await api.post('/v1/tenant/invitations/bulk', payload)
  return unwrapResponse(response)
}

export async function inviteTenantMember(payload) {
  const response = await api.post('/v1/tenant/invitations', payload)
  return unwrapResponse(response)
}

export async function resendTenantInvitation(userId) {
  const response = await api.post(`/v1/tenant/invitations/${userId}/resend`, {})
  return unwrapResponse(response)
}

export async function getTenantGroups(params = {}) {
  const response = await api.get('/v1/tenant/groups', { params })
  return unwrapResponse(response)
}

export async function createTenantGroup(payload) {
  const response = await api.post('/v1/tenant/groups', payload)
  return unwrapResponse(response)
}

export async function getTenantGroup(groupUuid) {
  const response = await api.get(`/v1/tenant/groups/${groupUuid}`)
  return unwrapResponse(response)
}

export async function updateTenantGroup(groupUuid, payload) {
  const response = await api.patch(`/v1/tenant/groups/${groupUuid}`, payload)
  return unwrapResponse(response)
}

export async function deleteTenantGroups(payload) {
  const response = await api.post('/v1/tenant/groups/delete', payload)
  return unwrapResponse(response)
}

export async function deleteTenantGroup(groupUuid) {
  const response = await api.delete(`/v1/tenant/groups/${groupUuid}`)
  return unwrapResponse(response)
}

export async function addTenantGroupMember(groupUuid, userId) {
  const response = await api.post(`/v1/tenant/groups/${groupUuid}/members`, {
    user_id: userId
  })
  return unwrapResponse(response)
}

export async function removeTenantGroupMember(groupUuid, userId) {
  const response = await api.delete(
    `/v1/tenant/groups/${groupUuid}/members/${userId}`
  )
  return unwrapResponse(response)
}

export async function getTenantApiKeys(params = {}) {
  const response = await api.get('/v1/tenant/api-keys', { params })
  return unwrapResponse(response)
}

export async function createTenantApiKey(payload) {
  const response = await api.post('/v1/tenant/api-keys', payload)
  return unwrapResponse(response)
}

export async function bulkUpdateTenantApiKeys(payload) {
  const response = await api.post('/v1/tenant/api-keys/bulk', payload)
  return unwrapResponse(response)
}

export async function updateTenantApiKey(keyUuid, payload) {
  const response = await api.patch(`/v1/tenant/api-keys/${keyUuid}`, payload)
  return unwrapResponse(response)
}

export async function getTenantApiKey(keyUuid) {
  const response = await api.get(`/v1/tenant/api-keys/${keyUuid}`)
  return unwrapResponse(response)
}

export async function rotateTenantApiKey(keyUuid, payload = {}) {
  const response = await api.post(`/v1/tenant/api-keys/${keyUuid}`, payload)
  return unwrapResponse(response)
}

export async function revokeTenantApiKey(keyUuid) {
  const response = await api.delete(`/v1/tenant/api-keys/${keyUuid}`)
  return unwrapResponse(response)
}
