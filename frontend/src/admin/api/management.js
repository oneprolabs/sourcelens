/**
 * Management portal API (admin-only): user list, etc.
 */
import apiClient from '@/api/index'
import { collectPaginatedResults } from '@/api/pagination'

function extractData(res) {
  const body = res?.data
  if (body && typeof body === 'object' && 'data' in body) return body.data
  return body ?? res
}

export const managementApi = {
  getUsers(params = {}, config = {}) {
    return apiClient
      .get('/v1/management/users/', { ...config, params })
      .then(extractData)
  },

  getAllUsers(params = {}, config = {}) {
    // Selectors only need identity fields; the full payload expands per-user
    // access profiles and is roughly eight times slower to serialize.
    return collectPaginatedResults((page) =>
      apiClient
        .get('/v1/management/users/', {
          ...config,
          params: { page_size: 1000, compact: true, ...params, page }
        })
        .then(extractData)
    )
  },

  createUser(body) {
    return apiClient.post('/v1/management/users/', body).then(extractData)
  },

  updateUser(userId, body) {
    return apiClient
      .patch(`/v1/management/users/${userId}/`, body)
      .then(extractData)
  },

  bulkUpdateUsers(body) {
    return apiClient
      .post('/v1/management/users/bulk-status/', body)
      .then(extractData)
  },

  getGroups(params = {}, config = {}) {
    return apiClient
      .get('/v1/management/groups/', { ...config, params })
      .then(extractData)
  },

  getAllGroups(params = {}, config = {}) {
    // Selectors only need id/name; skip the group detail aggregation.
    return collectPaginatedResults((page) =>
      apiClient
        .get('/v1/management/groups/', {
          ...config,
          params: { page_size: 1000, compact: true, ...params, page }
        })
        .then(extractData)
    )
  },

  createGroup(body) {
    return apiClient.post('/v1/management/groups/', body).then(extractData)
  },

  updateGroup(groupId, body) {
    return apiClient
      .patch(`/v1/management/groups/${groupId}/`, body)
      .then(extractData)
  },

  deleteGroup(groupId) {
    return apiClient.delete(`/v1/management/groups/${groupId}/`)
  },

  bulkDeleteGroups(groupIds) {
    return apiClient
      .post('/v1/management/groups/bulk-delete/', {
        group_ids: groupIds
      })
      .then(extractData)
  },

  getRoles(params = {}) {
    return apiClient.get('/v1/management/roles/', { params }).then(extractData)
  },

  createRole(body) {
    return apiClient.post('/v1/management/roles/', body).then(extractData)
  },

  updateRole(roleId, body) {
    return apiClient
      .patch(`/v1/management/roles/${roleId}/`, body)
      .then(extractData)
  },

  bulkUpdateRoles(body) {
    return apiClient
      .post('/v1/management/roles/bulk-status/', body)
      .then(extractData)
  }
}
