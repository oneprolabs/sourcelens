import { createApp } from 'vue'
import { createPinia } from 'pinia'
import * as Sentry from '@sentry/vue'
import App from './App.vue'
import router from './router'
import i18n from './i18n'
import { toDocumentLang } from './utils/documentLang'
import { usePreferencesStore } from './store/preferences'
import { isManagementPath } from './utils/theme'
import './assets/css/main.css'
import './assets/css/admin-prototype.css'

// Google Analytics (gtag.js) — loaded only when a measurement ID is set,
// so dev builds without VITE_GA_ID never send traffic.
const gaId = import.meta.env.VITE_GA_ID
if (gaId) {
  const gaScript = document.createElement('script')
  gaScript.async = true
  gaScript.src = `https://www.googletagmanager.com/gtag/js?id=${gaId}`
  document.head.appendChild(gaScript)
  window.dataLayer = window.dataLayer || []
  window.gtag = function gtag() {
    window.dataLayer.push(arguments)
  }
  window.gtag('js', new Date())
  window.gtag('config', gaId)
}

const app = createApp(App)
const pinia = createPinia()

// Frontend Sentry (separate project from backend/lensnode). Disabled when
// no DSN is configured.
if (import.meta.env.VITE_SENTRY_DSN) {
  Sentry.init({
    app,
    dsn: import.meta.env.VITE_SENTRY_DSN,
    environment: import.meta.env.VITE_SENTRY_ENVIRONMENT || 'production',
    sendDefaultPii: import.meta.env.VITE_SENTRY_SEND_DEFAULT_PII === 'true',
    tracesSampleRate: Number(
      import.meta.env.VITE_SENTRY_TRACES_SAMPLE_RATE || 0.1
    )
  })
}

app.use(pinia)
app.use(router)
app.use(i18n)

const preferencesStore = usePreferencesStore()
preferencesStore.setThemeOverride(
  isManagementPath(window.location.pathname) ? 'light' : null
)
preferencesStore.loadFromLocalStorage()
document.documentElement.lang = toDocumentLang(i18n.global.locale.value)

router.afterEach((to) => {
  preferencesStore.setThemeOverride(isManagementPath(to.path) ? 'light' : null)
})

app.mount('#app')
