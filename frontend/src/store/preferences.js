import { defineStore } from 'pinia'
import { detectTimezone, detectLanguage } from '@/utils/timezone'
import i18n, { normalizeUiLanguage } from '@/i18n'
import { toDocumentLang } from '@/utils/documentLang'
import {
  getNextThemeBoundary,
  normalizeThemeMode,
  resolveTheme
} from '@/utils/theme'

const COMPLETION_INDICATOR_KEY = 'answerCompletionIndicator'
const NATIVE_BROWSER_NOTIFICATIONS_KEY = 'nativeBrowserNotifications'
const THEME_MODE_KEY = 'userThemeMode'
const UNREAD_STORAGE_KEY = 'sourcelens.answerCompletion.unreadSessions'

const themeControllers = new WeakMap()

const getStorage = () => {
  try {
    return typeof localStorage === 'undefined' ? null : localStorage
  } catch {
    return null
  }
}

const readThemeMode = () => {
  try {
    return getStorage()?.getItem(THEME_MODE_KEY) ?? null
  } catch {
    return null
  }
}

const writeThemeMode = (mode) => {
  try {
    getStorage()?.setItem(THEME_MODE_KEY, mode)
  } catch {
    return
  }
}

const removeThemeMode = () => {
  try {
    getStorage()?.removeItem(THEME_MODE_KEY)
  } catch {
    return
  }
}

const disposeThemeController = (store) => {
  const controller = themeControllers.get(store)
  if (!controller) {
    return
  }

  if (controller.timer !== null) {
    controller.timerHost.clearTimeout(controller.timer)
  }
  if (
    controller.mediaQuery &&
    controller.listener &&
    typeof controller.mediaQuery.removeEventListener === 'function'
  ) {
    controller.mediaQuery.removeEventListener('change', controller.listener)
  }
  themeControllers.delete(store)
}

const getThemeController = (store) => {
  let controller = themeControllers.get(store)
  if (controller) {
    return controller
  }

  controller = {
    listener: null,
    mediaQuery: null,
    timer: null,
    timerHost: null
  }
  themeControllers.set(store, controller)

  const originalDispose = store.$dispose.bind(store)
  store.$dispose = () => {
    disposeThemeController(store)
    return originalDispose()
  }
  return controller
}

const clearThemeBoundary = (store) => {
  const controller = themeControllers.get(store)
  if (!controller || controller.timer === null) {
    return
  }

  controller.timerHost.clearTimeout(controller.timer)
  controller.timer = null
  controller.timerHost = null
}

const scheduleThemeBoundary = (store, now = new Date()) => {
  clearThemeBoundary(store)
  if (
    store.themeMode !== 'scheduled' ||
    typeof window === 'undefined' ||
    typeof window.setTimeout !== 'function'
  ) {
    return
  }

  const controller = getThemeController(store)
  const boundary = getNextThemeBoundary(now)
  const delay = Math.max(boundary.getTime() - now.getTime(), 0)
  controller.timerHost = window
  controller.timer = window.setTimeout(() => {
    controller.timer = null
    controller.timerHost = null
    store.applyTheme()
    scheduleThemeBoundary(store)
  }, delay)
}

const registerSystemThemeListener = (store) => {
  const controller = getThemeController(store)
  if (
    controller.listener !== null ||
    typeof window === 'undefined' ||
    typeof window.matchMedia !== 'function'
  ) {
    return
  }

  const mediaQuery = window.matchMedia('(prefers-color-scheme: dark)')
  if (typeof mediaQuery.addEventListener !== 'function') {
    return
  }

  controller.mediaQuery = mediaQuery
  controller.listener = () => {
    if (store.themeMode === 'system') {
      store.applyTheme()
    }
  }
  mediaQuery.addEventListener('change', controller.listener)
}

export const usePreferencesStore = defineStore('preferences', {
  state: () => ({
    language: detectLanguage(),
    timezone: detectTimezone(),
    detectedLanguage: detectLanguage(),
    detectedTimezone: detectTimezone(),
    answerCompletionIndicator: true,
    nativeBrowserNotifications: false,
    themeMode: 'system',
    themeOverride: null,
    resolvedTheme: 'light',
    isLoaded: false
  }),

  getters: {
    currentLanguage: (state) => state.language,
    currentTimezone: (state) => state.timezone,
    isAutoDetected: (state) => {
      return (
        state.language === state.detectedLanguage &&
        state.timezone === state.detectedTimezone
      )
    }
  },

  actions: {
    async setLanguage(language) {
      const normalizedLanguage = normalizeUiLanguage(language)
      this.language = normalizedLanguage
      i18n.global.locale.value = normalizedLanguage
      document.documentElement.lang = toDocumentLang(normalizedLanguage)
      localStorage.setItem('userLanguage', normalizedLanguage)
    },

    setTimezone(timezone) {
      this.timezone = timezone
      localStorage.setItem('userTimezone', timezone)
    },

    setAnswerCompletionIndicator(enabled) {
      this.answerCompletionIndicator = enabled
      localStorage.setItem(COMPLETION_INDICATOR_KEY, String(enabled))
      if (!enabled) {
        localStorage.removeItem(UNREAD_STORAGE_KEY)
      }
    },

    setNativeBrowserNotifications(enabled) {
      this.nativeBrowserNotifications = enabled
      localStorage.setItem(NATIVE_BROWSER_NOTIFICATIONS_KEY, String(enabled))
    },

    applyTheme(now = new Date()) {
      const mediaQuery =
        typeof window !== 'undefined' && typeof window.matchMedia === 'function'
          ? window.matchMedia('(prefers-color-scheme: dark)')
          : null
      const preferredTheme = resolveTheme(
        this.themeMode,
        now,
        mediaQuery?.matches ?? false
      )
      const resolvedTheme = this.themeOverride ?? preferredTheme
      this.resolvedTheme = resolvedTheme

      if (typeof document !== 'undefined') {
        document.documentElement.dataset.theme = resolvedTheme
        document.documentElement.style.colorScheme = resolvedTheme
      }
    },

    setThemeMode(mode) {
      const normalizedThemeMode = normalizeThemeMode(mode)
      this.themeMode = normalizedThemeMode
      writeThemeMode(normalizedThemeMode)
      this.applyTheme()
      scheduleThemeBoundary(this)
    },

    setThemeOverride(theme) {
      this.themeOverride = theme === 'light' ? 'light' : null
      this.applyTheme()
    },

    loadFromLocalStorage() {
      const savedLanguage = localStorage.getItem('userLanguage')
      const savedTimezone = localStorage.getItem('userTimezone')
      const savedIndicator = localStorage.getItem(COMPLETION_INDICATOR_KEY)
      const savedNativeNotifications = localStorage.getItem(
        NATIVE_BROWSER_NOTIFICATIONS_KEY
      )
      const savedThemeMode = readThemeMode()
      const normalizedThemeMode = normalizeThemeMode(savedThemeMode)

      if (savedLanguage) {
        const normalizedLanguage = normalizeUiLanguage(savedLanguage)
        this.language = normalizedLanguage
        i18n.global.locale.value = normalizedLanguage
        document.documentElement.lang = toDocumentLang(normalizedLanguage)
        if (savedLanguage !== normalizedLanguage) {
          localStorage.setItem('userLanguage', normalizedLanguage)
        }
      }

      if (savedTimezone) {
        this.timezone = savedTimezone
      }
      if (savedIndicator !== null) {
        this.answerCompletionIndicator = savedIndicator === 'true'
      }
      if (savedNativeNotifications !== null) {
        this.nativeBrowserNotifications = savedNativeNotifications === 'true'
      }
      this.themeMode = normalizedThemeMode
      if (savedThemeMode !== normalizedThemeMode) {
        writeThemeMode(normalizedThemeMode)
      }
      this.applyTheme()
      registerSystemThemeListener(this)
      scheduleThemeBoundary(this)
      this.isLoaded = true
    },

    loadFromBackend(preferences) {
      if (preferences.language && !localStorage.getItem('userLanguage')) {
        this.setLanguage(preferences.language)
      }

      if (preferences.scene) {
        // Store scene preference if needed
        localStorage.setItem('userScene', preferences.scene)
      }

      this.isLoaded = true
    },

    reset() {
      const normalizedLanguage = normalizeUiLanguage(this.detectedLanguage)
      this.language = normalizedLanguage
      this.timezone = this.detectedTimezone
      this.answerCompletionIndicator = true
      this.nativeBrowserNotifications = false
      this.themeMode = 'system'
      i18n.global.locale.value = normalizedLanguage
      document.documentElement.lang = toDocumentLang(normalizedLanguage)
      localStorage.removeItem('userLanguage')
      localStorage.removeItem('userTimezone')
      localStorage.removeItem(COMPLETION_INDICATOR_KEY)
      localStorage.removeItem(NATIVE_BROWSER_NOTIFICATIONS_KEY)
      removeThemeMode()
      localStorage.removeItem(UNREAD_STORAGE_KEY)
      clearThemeBoundary(this)
      this.applyTheme()
    }
  }
})
