import axios from 'axios'
import apiConfig from '@/config/api'

function getCookie(name) {
  const value = `; ${document.cookie}`
  const parts = value.split(`; ${name}=`)
  if (parts.length === 2) return parts.pop().split(';').shift()
  return null
}

const api = axios.create({
  baseURL: apiConfig.apiBaseUrl,
  timeout: apiConfig.timeout,
  headers: {
    'Content-Type': 'application/json'
  },
  withCredentials: true
})

// Track if token refresh is in progress to avoid concurrent refreshes
let isRefreshing = false
let failedQueue = []

const processQueue = (error, token = null) => {
  failedQueue.forEach((prom) => {
    if (error) {
      prom.reject(error)
    } else {
      prom.resolve(token)
    }
  })
  failedQueue = []
}

api.interceptors.request.use(
  (config) => {
    if (window.location.pathname.startsWith('/management/platform/catalog/')) {
      config.headers['X-Catalog-Scope'] = 'official'
    }
    if (
      window.location.pathname.startsWith('/management/platform/notifications/')
    ) {
      config.headers['X-Notification-Scope'] = 'platform'
    }
    if (window.location.pathname.startsWith('/management/platform/')) {
      config.headers['X-Model-Scope'] = 'platform'
    }
    // Don't add Authorization header for token refresh endpoint
    if (!config.url?.includes('/token/refresh')) {
      const token = localStorage.getItem('access_token')
      if (token) {
        config.headers.Authorization = `Bearer ${token}`
      }
    }

    const csrfToken = getCookie('csrftoken')
    if (csrfToken) {
      config.headers['X-CSRFToken'] = csrfToken
    }

    const uiLanguage = localStorage.getItem('userLanguage') || 'en'
    config.headers['Accept-Language'] = uiLanguage

    try {
      const delegation = JSON.parse(
        sessionStorage.getItem('tenant_delegation') || 'null'
      )
      if (delegation?.delegation_id) {
        config.headers['X-Delegation-ID'] = delegation.delegation_id
      }
    } catch {
      sessionStorage.removeItem('tenant_delegation')
    }

    return config
  },
  (error) => {
    return Promise.reject(error)
  }
)

// Response interceptor
api.interceptors.response.use(
  (response) => {
    return response
  },
  async (error) => {
    const originalRequest = error.config

    // Handle 401 Unauthorized errors
    if (error.response?.status === 401 && !originalRequest._retry) {
      // If refresh endpoint returns 401, token is invalid, redirect to login
      if (originalRequest.url?.includes('/token/refresh')) {
        localStorage.removeItem('access_token')
        localStorage.removeItem('refresh_token')
        window.location.href = '/login'
        return Promise.reject(error)
      }

      // If already refreshing, queue this request
      // This prevents multiple concurrent refresh attempts
      if (isRefreshing) {
        return new Promise((resolve, reject) => {
          failedQueue.push({ resolve, reject })
        })
          .then((token) => {
            // Update request with new token and retry
            originalRequest.headers.Authorization = `Bearer ${token}`
            return api(originalRequest)
          })
          .catch((err) => {
            return Promise.reject(err)
          })
      }

      originalRequest._retry = true
      isRefreshing = true

      // Try to refresh token
      const refreshToken = localStorage.getItem('refresh_token')
      if (refreshToken) {
        try {
          // Create a new axios instance without interceptors for refresh
          const refreshApi = axios.create({
            baseURL: apiConfig.apiBaseUrl,
            timeout: apiConfig.timeout,
            headers: {
              'Content-Type': 'application/json'
            },
            withCredentials: true
          })

          const response = await refreshApi.post('/v1/auth/token/refresh', {
            refresh: refreshToken
          })

          // Handle unified response format: {code, message, data}
          const responseData = response.data.data || response.data
          const newAccessToken = responseData.access

          if (newAccessToken) {
            // Update localStorage
            localStorage.setItem('access_token', newAccessToken)

            // Update Pinia store if available
            try {
              const { useUserStore } = await import('@/store/user')
              const userStore = useUserStore()
              userStore.setToken(newAccessToken)
            } catch (storeError) {
              // Store might not be available, continue anyway
              console.warn('Failed to update store token:', storeError)
            }

            // Process queued requests
            processQueue(null, newAccessToken)

            // Retry original request with new token
            originalRequest.headers.Authorization = `Bearer ${newAccessToken}`
            return api(originalRequest)
          } else {
            throw new Error('No access token in refresh response')
          }
        } catch (refreshError) {
          // Refresh failed, clear tokens and redirect to login
          processQueue(refreshError, null)
          localStorage.removeItem('access_token')
          localStorage.removeItem('refresh_token')
          window.location.href = '/login'
          return Promise.reject(refreshError)
        } finally {
          isRefreshing = false
        }
      } else {
        // No refresh token, redirect to login
        isRefreshing = false
        localStorage.removeItem('access_token')
        localStorage.removeItem('refresh_token')
        window.location.href = '/login'
      }
    }

    return Promise.reject(error)
  }
)

export default api
