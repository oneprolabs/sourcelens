<template>
  <div ref="container" class="turnstile-widget"></div>
</template>

<script setup>
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { getTurnstileConfig } from '@/config/runtime'

const SCRIPT_SRC =
  'https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit'

const emit = defineEmits(['verified', 'expired', 'error'])

const { siteKey } = getTurnstileConfig(import.meta.env.VITE_TURNSTILE_SITE_KEY)
const container = ref(null)
let widgetId = null
let bypassed = false
let disposed = false

/**
 * Load the Cloudflare Turnstile script once and resolve when ready.
 */
function loadScript() {
  if (window.turnstile) {
    return Promise.resolve()
  }
  if (window.__turnstileLoading) {
    return window.__turnstileLoading
  }
  window.__turnstileLoading = new Promise((resolve, reject) => {
    const script = document.createElement('script')
    script.src = SCRIPT_SRC
    script.async = true
    script.defer = true
    script.onload = () => resolve()
    script.onerror = () => reject(new Error('Turnstile script failed'))
    document.head.appendChild(script)
  })
  return window.__turnstileLoading
}

onMounted(async () => {
  try {
    const response = await fetch('/api/v1/auth/turnstile/status', {
      cache: 'no-store',
      signal: AbortSignal.timeout(5000)
    })
    const policy = await response.json()
    if (disposed) return
    if (response.ok && policy.data?.required === false) {
      bypassed = true
      emit('verified', '')
      return
    }
  } catch (err) {
    // Fall through to the normal Turnstile challenge when policy discovery fails.
  }

  if (disposed) return
  if (!siteKey) {
    emit('error', new Error('Turnstile site key is not configured'))
    return
  }
  try {
    await loadScript()
    if (disposed) return
    widgetId = window.turnstile.render(container.value, {
      sitekey: siteKey,
      callback: (token) => emit('verified', token),
      'expired-callback': () => emit('expired'),
      'error-callback': () => emit('error')
    })
  } catch (err) {
    emit('error', err)
  }
})

/**
 * Reset the widget so the user can solve the challenge again.
 */
function reset() {
  if (widgetId !== null && window.turnstile) {
    window.turnstile.reset(widgetId)
  } else if (bypassed) {
    // Bypass mode: verification always passes, so re-emit a fresh token
    // to restore the consumer's state after a failed submit.
    emit('verified', '')
  }
}

onBeforeUnmount(() => {
  disposed = true
  if (widgetId !== null && window.turnstile) {
    window.turnstile.remove(widgetId)
  }
})

defineExpose({ reset, hasSiteKey: !!siteKey })
</script>

<style scoped>
.turnstile-widget {
  display: flex;
  justify-content: center;
  min-height: 0;
}
</style>
