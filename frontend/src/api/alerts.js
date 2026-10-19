import api from '@/api'
import { collectPaginatedResults } from './pagination'
const unwrap = (response) => response?.data?.data ?? response?.data
export const personalAlertsApi = {
  rules() {
    return collectPaginatedResults((page) =>
      api
        .get('/lens/alert-subscriptions/rules/', {
          params: { page, page_size: 100 }
        })
        .then(unwrap)
    )
  },
  subscriptions() {
    return collectPaginatedResults((page) =>
      api
        .get('/lens/alert-subscriptions/', { params: { page, page_size: 100 } })
        .then(unwrap)
    )
  },
  subscribe(rule_uuid) {
    return api
      .post('/lens/alert-subscriptions/', { rule_uuid, enabled: true })
      .then(unwrap)
  },
  update(uuid, enabled) {
    return api
      .patch(`/lens/alert-subscriptions/${uuid}/`, { enabled })
      .then(unwrap)
  },
  unsubscribe(uuid) {
    return api.delete(`/lens/alert-subscriptions/${uuid}/`)
  }
}
