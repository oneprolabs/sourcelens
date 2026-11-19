export function isRunAccessDenied(error) {
  return [401, 403, 404].includes(error?.response?.status ?? error?.status)
}

export function runStreamHeaders(
  tokenStorage = globalThis.localStorage,
  delegationStorage = globalThis.sessionStorage
) {
  const headers = { Accept: 'text/event-stream' }
  const token = tokenStorage?.getItem('access_token')
  if (token) headers.Authorization = `Bearer ${token}`
  try {
    const delegation = JSON.parse(
      delegationStorage?.getItem('tenant_delegation') || 'null'
    )
    if (delegation?.delegation_id) {
      headers['X-Delegation-ID'] = delegation.delegation_id
    }
  } catch {
    // Invalid local state must not prevent the server from rejecting access.
  }
  return headers
}
