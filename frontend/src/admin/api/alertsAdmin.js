/**
 * Alert admin API: rules CRUD and triggered alert events.
 * Base path: /api/lens/admin/alert-rules/ and /api/lens/admin/alert-events/
 */
import apiClient from '@/api/index'
import { extractResponseData } from '@/utils/api'

export const alertsAdminApi = {
  getChannels(params = {}) {
    return apiClient
      .get('/lens/admin/alert-rules/channels/', { params })
      .then(extractResponseData)
  },
  getMessages(params = {}) {
    return apiClient
      .get('/lens/admin/alert-messages/', { params })
      .then(extractResponseData)
  },
  publishMessage(body) {
    return apiClient
      .post('/lens/admin/alert-messages/', body)
      .then(extractResponseData)
  },
  getCatalog() {
    return apiClient
      .get('/lens/admin/alert-rules/catalog/')
      .then(extractResponseData)
  },
  retryEvent(uuid) {
    return apiClient
      .post(`/lens/admin/alert-events/${uuid}/retry/`)
      .then(extractResponseData)
  },
  getRules(params = {}) {
    return apiClient
      .get('/lens/admin/alert-rules/', { params })
      .then(extractResponseData)
  },
  createRule(body) {
    return apiClient
      .post('/lens/admin/alert-rules/', body)
      .then(extractResponseData)
  },
  updateRule(uuid, body) {
    return apiClient
      .patch(`/lens/admin/alert-rules/${uuid}/`, body)
      .then(extractResponseData)
  },
  deleteRule(uuid) {
    return apiClient.delete(`/lens/admin/alert-rules/${uuid}/`)
  },
  getEvents(params = {}) {
    return apiClient
      .get('/lens/admin/alert-events/', { params })
      .then(extractResponseData)
  },
  getEvent(uuid) {
    return apiClient
      .get(`/lens/admin/alert-events/${uuid}/`)
      .then(extractResponseData)
  }
}
