<template>
  <div class="relative" ref="menuRef">
    <button
      type="button"
      class="quick-menu-trigger"
      :class="open ? 'quick-menu-trigger-open' : ''"
      @click="toggleMenu"
    >
      <div class="quick-avatar" :class="avatarBgColor">
        <span>{{ userInitials }}</span>
      </div>
      <div class="min-w-0 flex-1">
        <div class="truncate text-sm font-medium text-theme">
          {{ displayName }}
        </div>
        <div class="truncate text-xs text-theme-muted">
          {{ currentPlatformLabel }}
        </div>
      </div>
      <svg
        class="h-4 w-4 shrink-0 text-theme-muted transition-transform"
        :class="{ 'rotate-180': open }"
        fill="none"
        stroke="currentColor"
        viewBox="0 0 24 24"
      >
        <path
          stroke-linecap="round"
          stroke-linejoin="round"
          stroke-width="2"
          d="M19 9l-7 7-7-7"
        />
      </svg>
    </button>

    <Transition
      enter-active-class="transition ease-out duration-100"
      enter-from-class="transform opacity-0 translate-y-1 scale-95"
      enter-to-class="transform opacity-100 translate-y-0 scale-100"
      leave-active-class="transition ease-in duration-75"
      leave-from-class="transform opacity-100 translate-y-0 scale-100"
      leave-to-class="transform opacity-0 translate-y-1 scale-95"
    >
      <div v-if="open" class="quick-menu-panel">
        <div class="quick-menu-section">
          <div class="quick-menu-label">
            {{ t('platforms.switchPlatform') }}
          </div>
          <div class="space-y-1">
            <router-link
              v-for="platform in platforms"
              :key="platform.key"
              :to="platform.defaultPath"
              class="quick-menu-link"
              @click="open = false"
            >
              <span class="truncate">{{ platform.label }}</span>
              <span
                v-if="platform.key === currentPlatformKey"
                class="quick-pill"
              >
                {{ t('common.current') }}
              </span>
            </router-link>
          </div>
        </div>

        <div class="quick-menu-section">
          <div
            v-if="userStore.userHasFeature('admin_console')"
            class="quick-menu-admin-separator mb-2 border-b border-line pb-2"
          >
            <router-link
              :to="getManagementEntry(userStore.userInfo)"
              class="quick-menu-link"
              @click="openAdminConsole"
            >
              <span class="truncate">{{
                t(
                  isPlatformAdmin(userStore.userInfo)
                    ? 'management.platformConsole'
                    : 'management.tenantManagement'
                )
              }}</span>
            </router-link>
          </div>
          <button type="button" class="quick-menu-link" @click="openSettings">
            <span class="truncate">{{ t('common.settings') }}</span>
            <span
              v-if="uiStore.hasUnreadReleaseNotes"
              class="ml-auto h-2 w-2 shrink-0 rounded-full bg-primary-500"
            >
              <span class="sr-only">
                {{ t('settings.modal.releaseNotesUnread') }}
              </span>
            </span>
          </button>
          <button type="button" class="quick-menu-link" @click="handleLogout">
            <span class="truncate">{{ t('common.logout') }}</span>
          </button>
        </div>
      </div>
    </Transition>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute, useRouter } from 'vue-router'

import { useUiStore } from '@/store/ui'
import { useUserStore } from '@/store/user'
import {
  getAvailablePlatforms,
  getCurrentPlatformKey,
  getPlatformByKey,
  isPlatformAdmin,
  getManagementEntry,
  saveAdminReturnPath
} from '@/utils/platformAccess'

const { t } = useI18n()
const route = useRoute()
const router = useRouter()
const userStore = useUserStore()
const uiStore = useUiStore()

const open = ref(false)
const menuRef = ref(null)

const platforms = computed(() => getAvailablePlatforms(userStore.userInfo, t))
const currentPlatformKey = computed(() => getCurrentPlatformKey(route.path))
const currentPlatform = computed(() =>
  getPlatformByKey(currentPlatformKey.value, t)
)

const displayName = computed(() => {
  const userInfo = userStore.userInfo
  if (!userInfo) return 'User'
  if (userInfo.display_name) return userInfo.display_name
  if (userInfo.first_name && userInfo.last_name) {
    return `${userInfo.first_name} ${userInfo.last_name}`
  }
  if (userInfo.first_name) return userInfo.first_name
  return userInfo.username || 'User'
})

const userInitials = computed(() => {
  const name = displayName.value.trim()
  return name.charAt(0).toUpperCase() || 'U'
})

const avatarBgColor = computed(() => {
  const colors = [
    'bg-blue-500',
    'bg-indigo-500',
    'bg-emerald-500',
    'bg-rose-500',
    'bg-amber-500',
    'bg-cyan-500'
  ]
  const charCode = userInitials.value.charCodeAt(0)
  return colors[charCode % colors.length]
})

const currentPlatformLabel = computed(() => {
  return currentPlatform.value?.label || t('platforms.workspace')
})

const toggleMenu = () => {
  open.value = !open.value
}

const openAdminConsole = () => {
  saveAdminReturnPath(route.fullPath)
  open.value = false
}

const openSettings = () => {
  uiStore.openSettings()
  open.value = false
}

const handleLogout = async () => {
  try {
    await userStore.logout()
  } finally {
    open.value = false
    router.push('/login')
  }
}

const handleClickOutside = (event) => {
  if (menuRef.value && !menuRef.value.contains(event.target)) {
    open.value = false
  }
}

onMounted(() => {
  document.addEventListener('click', handleClickOutside)
})

onUnmounted(() => {
  document.removeEventListener('click', handleClickOutside)
})
</script>

<style scoped>
.quick-menu-trigger {
  @apply flex w-full items-center gap-3 rounded-xl border border-line bg-surface px-3 py-2 text-left shadow-sm transition-colors;
}

.quick-menu-trigger:hover,
.quick-menu-trigger-open {
  @apply border-primary-200 bg-primary-50;
}

.quick-avatar {
  @apply flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-xs font-semibold text-white;
}

.quick-menu-panel {
  @apply absolute bottom-full left-0 z-50 mb-2 w-[18rem] overflow-hidden rounded-2xl border border-line bg-surface shadow-xl;
}

.quick-menu-section {
  @apply border-b border-line px-3 py-3 last:border-b-0;
}

.quick-menu-label {
  @apply mb-2 text-[11px] font-semibold uppercase tracking-wide text-theme-muted;
}

.quick-menu-link {
  @apply flex w-full items-center justify-between rounded-lg px-3 py-2 text-sm text-theme-secondary transition-colors;
}

.quick-menu-link:hover {
  @apply bg-line-soft text-theme;
}

.quick-pill {
  @apply rounded-full bg-line-soft px-2 py-0.5 text-xs font-medium text-theme-muted;
}

:global(:root[data-theme='dark'] .quick-menu-trigger:hover),
:global(:root[data-theme='dark'] .quick-menu-trigger-open) {
  border-color: var(--sl-border-strong);
  background: var(--sl-bg-hover);
}

:global(:root[data-theme='dark'] .quick-menu-admin-separator),
:global(:root[data-theme='dark'] .quick-menu-section) {
  border-bottom: 0;
}

:global(:root[data-theme='dark'] .quick-menu-link:hover) {
  background: var(--sl-bg-hover);
}
</style>
