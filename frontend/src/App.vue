<template>
  <div id="app" class="min-h-screen bg-surface-sunken">
    <ErrorBoundary>
      <router-view
        :key="$route.meta.notificationScope || $route.meta.modelScope"
      />
    </ErrorBoundary>
    <Teleport to="body">
      <UserSettingsModal
        :show="uiStore.settingsOpen"
        @close="uiStore.closeSettings()"
      />
    </Teleport>
    <ActivityNotificationStack />
    <Toast />
  </div>
</template>

<script setup>
import { onBeforeUnmount, onMounted, watch } from 'vue'

import { useSessionActivity } from '@/composables/useSessionActivity'
import { useUserStore } from '@/store/user'
import { useUiStore } from '@/store/ui'
import { usePreferencesStore } from '@/store/preferences'
import ErrorBoundary from '@/components/ui/ErrorBoundary.vue'
import ActivityNotificationStack from '@/components/ui/ActivityNotificationStack.vue'
import Toast from '@/components/ui/Toast.vue'
import UserSettingsModal from '@/components/settings/UserSettingsModal.vue'
import {
  readUnreadSessions,
  UNREAD_STORAGE_KEY
} from '@/utils/answerCompletionNotifications'
import { stopRunCompletionTracking } from '@/utils/runCompletionTracking'

const userStore = useUserStore()
const uiStore = useUiStore()
const preferencesStore = usePreferencesStore()
const sessionActivity = useSessionActivity()

function refreshUnreadSessions() {
  sessionActivity.setUnreadSessions(readUnreadSessions(window.localStorage))
}

function handleCompletionStorage(event) {
  if (event.key === UNREAD_STORAGE_KEY) {
    refreshUnreadSessions()
  }
  if (event.key === 'answerCompletionIndicator') {
    preferencesStore.answerCompletionIndicator = event.newValue !== 'false'
  }
  if (event.key === 'nativeBrowserNotifications') {
    preferencesStore.nativeBrowserNotifications = event.newValue === 'true'
  }
}

watch(
  () => preferencesStore.answerCompletionIndicator,
  () => {
    refreshUnreadSessions()
  }
)

watch(
  () => userStore.token,
  (token, previousToken) => {
    if (previousToken && token !== previousToken) {
      stopRunCompletionTracking()
      sessionActivity.clearSessionActivity()
    }
  }
)

// Initialize app
onMounted(() => {
  window.addEventListener('storage', handleCompletionStorage)
  refreshUnreadSessions()
  // Check if user is logged in, but only if we have a token and user is not loaded
  // This avoids duplicate calls with router guard
  const hasToken = !!localStorage.getItem('access_token')
  if (hasToken && !userStore.user) {
    userStore.checkAuth().catch(() => {
      // Error handling is done in checkAuth (clears auth state)
    })
  }
})

onBeforeUnmount(() => {
  window.removeEventListener('storage', handleCompletionStorage)
})
</script>
