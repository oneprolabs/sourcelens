<template>
  <div ref="container" class="turnstile-widget"></div>
</template>

<script setup>
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { getTurnstileConfig } from '@/config/runtime'

const SCRIPT_SRC =
  'https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit'

const emit = defineEmits(['verified', 'expired', 'error'])

const { enabled, siteKey } = getTurnstileConfig(
  import.meta.env.VITE_TURNSTILE_SITE_KEY
)
const container = ref(null)
let widgetId = null

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
    const response = await fetch('/api/v1/auth/turnstile/status')
    const policy = await response.json()
    if (policy.bypassed) {
      emit('verified', '')
      return
    }
  } catch (err) {
    // Fall through to the normal Turnstile challenge when policy discovery fails.
  }

  // Bypass when Turnstile is disabled or no site key is configured.
  if (!enabled || !siteKey) {
    emit('verified', '')
    return
  }

  try {
    await loadScript()
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
  if (enabled && siteKey && widgetId !== null && window.turnstile) {
    window.turnstile.reset(widgetId)
  } else if (!enabled || !siteKey) {
    // Bypass mode: verification always passes, so re-emit a fresh token
    // to restore the consumer's state after a failed submit.
    emit('verified', '')
  }
}

onBeforeUnmount(() => {
  if (enabled && siteKey && widgetId !== null && window.turnstile) {
    window.turnstile.remove(widgetId)
  }
})

defineExpose({ reset, hasSiteKey: enabled && !!siteKey })
</script>

<style scoped>
.turnstile-widget {
  display: flex;
  justify-content: center;
  min-height: 0;
}
</style>
