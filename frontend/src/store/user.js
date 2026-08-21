import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { authApi } from '@/api/auth'
import { usePreferencesStore } from '@/store/preferences'
import {
  getAvailablePlatforms,
  getLandingPath,
  hasAnyPermission,
  hasFeature,
  hasPermission
} from '@/utils/platformAccess'

export const useUserStore = defineStore('user', () => {
  const preferencesStore = usePreferencesStore()

  // State
  const user = ref(null)
  const token = ref(localStorage.getItem('access_token'))
  const loading = ref(false)
  const error = ref(null)
  let pendingAuthCheck = Promise.resolve()

  // Getters
  const isAuthenticated = computed(() => !!token.value && !!user.value)
  const userInfo = computed(() => user.value)
  const availablePlatforms = computed(() => getAvailablePlatforms(user.value))
  const landingPath = computed(() => getLandingPath(user.value))

  const loadUserPreferences = async () => {
    const language = user.value?.profile?.language
    if (language && !localStorage.getItem('userLanguage')) {
      await preferencesStore.setLanguage(language)
    }
  }

  // Actions
  const login = async (credentials) => {
    loading.value = true
    error.value = null

    try {
      const response = await authApi.login(credentials)

      // Handle the actual response format from backend
      const data = response.data.data || response.data

      // Handle JWT response format
      if (data.access) {
        token.value = data.access
        user.value = data.user

        // Save tokens to localStorage
        localStorage.setItem('access_token', data.access)
        if (data.refresh) {
          localStorage.setItem('refresh_token', data.refresh)
        }
      } else {
        // Fallback for different response format
        token.value = data.token || data.access_token
        user.value = data.user
        localStorage.setItem('access_token', token.value)
      }

      await loadUserPreferences()

      return data
    } catch (err) {
      error.value =
        err.response?.data?.detail ||
        err.response?.data?.message ||
        'Login failed'
      throw err
    } finally {
      loading.value = false
    }
  }

  const loginWithCode = async ({ email, code }) => {
    loading.value = true
    error.value = null

    try {
      const response = await authApi.verifyLoginCode({ email, code })
      const data = response.data.data || response.data

      if (!data.access) {
        throw new Error('Invalid verify response')
      }

      token.value = data.access
      localStorage.setItem('access_token', data.access)
      if (data.refresh) {
        localStorage.setItem('refresh_token', data.refresh)
      }

      // Load the full profile (roles/features) for routing and gating.
      const profile = await authApi.getProfile()
      user.value = profile.data.data || profile.data
      await loadUserPreferences()

      return user.value
    } catch (err) {
      error.value =
        err.response?.data?.message ||
        err.response?.data?.detail ||
        'Login failed'
      throw err
    } finally {
      loading.value = false
    }
  }

  const clearAuthState = () => {
    user.value = null
    token.value = null
    pendingAuthCheck = Promise.resolve()
    localStorage.removeItem('access_token')
    localStorage.removeItem('refresh_token')
  }

  const logout = async () => {
    try {
      // Call backend logout API to invalidate token
      await authApi.logout()
    } catch (error) {
      console.error('Logout API call failed:', error)
      // Continue with local cleanup even if API call fails
    } finally {
      clearAuthState()
    }
  }

  const checkAuth = async () => {
    const storedToken = localStorage.getItem('access_token')
    if (!storedToken) {
      clearAuthState()
      return false
    }

    if (!token.value) {
      token.value = storedToken
    }

    if (user.value) {
      return true
    }

    await pendingAuthCheck
    if (user.value) {
      return true
    }

    pendingAuthCheck = (async () => {
      try {
        const response = await authApi.getProfile()
        const data = response.data.data || response.data
        user.value = data
        await loadUserPreferences()
        return true
      } catch (err) {
        clearAuthState()
        return false
      }
    })()

    return pendingAuthCheck
  }

  const checkAuthStatus = async () => {
    if (user.value) {
      return user.value
    }

    await pendingAuthCheck
    if (user.value) {
      return user.value
    }

    pendingAuthCheck = (async () => {
      try {
        const response = await authApi.getProfile()
        const data = response.data.data || response.data
        user.value = data
        if (!token.value && localStorage.getItem('access_token')) {
          token.value = localStorage.getItem('access_token')
        }
        await loadUserPreferences()
        return data
      } catch (err) {
        if (err?.response?.status !== 502 && err?.code !== 'ERR_BAD_RESPONSE') {
          console.error('Check auth status failed:', err)
        }
        return null
      }
    })()

    return pendingAuthCheck
  }

  const updateProfile = async (profileData) => {
    loading.value = true
    error.value = null

    try {
      const response = await authApi.updateProfile(profileData)
      // Handle the actual response format from backend
      const data = response.data.data || response.data
      user.value = data
      return data
    } catch (err) {
      error.value = err.response?.data?.message || 'Update failed'
      throw err
    } finally {
      loading.value = false
    }
  }

  const updateLanguage = async (language) => {
    if (isAuthenticated.value) {
      await updateProfile({ profile_language: language })
    }
    await preferencesStore.setLanguage(language)
  }

  const setUser = (userData) => {
    user.value = userData
  }

  const setToken = (tokenValue, refreshValue = null) => {
    token.value = tokenValue
    if (tokenValue) {
      localStorage.setItem('access_token', tokenValue)
    }
    if (refreshValue) {
      localStorage.setItem('refresh_token', refreshValue)
    }
  }

  const userHasFeature = (featureKey) => hasFeature(user.value, featureKey)
  const userHasPermission = (permission) => {
    return hasPermission(user.value, permission)
  }
  const userHasAnyPermission = (permissions) => {
    return hasAnyPermission(user.value, permissions)
  }
  const getUserLandingPath = () => getLandingPath(user.value)

  return {
    // State
    user,
    token,
    loading,
    error,
    // Getters
    isAuthenticated,
    userInfo,
    availablePlatforms,
    landingPath,
    // Actions
    login,
    loginWithCode,
    logout,
    checkAuth,
    checkAuthStatus,
    updateProfile,
    updateLanguage,
    setUser,
    setToken,
    userHasFeature,
    userHasPermission,
    userHasAnyPermission,
    getUserLandingPath,
    // Helper functions
    loadUserPreferences
  }
})
