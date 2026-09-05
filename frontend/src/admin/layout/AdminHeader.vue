<template>
  <header
    class="layout-admin-header z-30 flex-shrink-0 border-b border-ink-800 bg-ink-900/95 shadow-sm backdrop-blur"
  >
    <div class="px-2 sm:px-6 lg:px-8">
      <div class="flex h-14 items-center justify-between">
        <div class="flex min-w-0 flex-1 items-center gap-1 sm:gap-3">
          <button
            type="button"
            aria-controls="admin-sidebar"
            :aria-expanded="showMobileMenu"
            :aria-label="`${t('common.expand')} ${t('management.logoTitle')}`"
            @click="$emit('open-menu')"
            class="h-11 w-11 shrink-0 rounded-md p-2 text-slate-400 hover:bg-slate-700 hover:text-slate-100 focus:outline-none focus:ring-2 focus:ring-indigo-500 lg:hidden"
          >
            <svg
              class="w-6 h-6"
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
            >
              <path
                stroke-linecap="round"
                stroke-linejoin="round"
                stroke-width="2"
                d="M4 6h16M4 12h16M4 18h16"
              />
            </svg>
          </button>
          <h1
            class="min-w-0 truncate whitespace-nowrap text-sm font-semibold text-white lg:hidden sm:text-lg"
          >
            {{ pageTitle }}
          </h1>
        </div>

        <div
          class="ml-1 flex shrink-0 items-center space-x-0.5 sm:ml-2 sm:space-x-4"
        >
          <div class="hidden min-[430px]:block">
            <LanguageSwitcher variant="dark" />
          </div>
          <router-link
            :to="assistantReturnPath"
            @click="clearAssistantReturnPath"
            :aria-label="t('management.backToUserPlatform')"
            class="flex min-h-11 min-w-11 shrink-0 items-center justify-center gap-2 rounded-lg border border-slate-600 bg-slate-800 px-2 py-2 text-sm font-medium text-slate-100 shadow-sm transition-colors hover:border-slate-500 hover:bg-slate-700 sm:px-3"
          >
            <svg
              class="h-4 w-4"
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
            >
              <path
                stroke-linecap="round"
                stroke-linejoin="round"
                stroke-width="2"
                d="M10 19l-7-7m0 0l7-7m-7 7h18"
              />
            </svg>
            <span class="hidden sm:inline">
              {{ t('management.backToUserPlatform') }}
            </span>
          </router-link>
          <div class="relative" ref="userMenuRef">
            <button
              @click="toggleUserMenu"
              class="flex min-h-11 items-center gap-2 rounded-lg px-2 py-1 text-sm text-ink-300 hover:bg-white/10 hover:text-white focus:outline-none focus:ring-2 focus:ring-brand-500/40"
            >
              <div
                :class="avatarBgColor"
                class="w-8 h-8 rounded-full flex items-center justify-center"
              >
                <span class="text-white font-medium text-sm">{{
                  userInitials
                }}</span>
              </div>
              <span class="hidden sm:block">{{ displayName }}</span>
              <svg
                class="w-4 h-4 transition-transform"
                :class="{ 'rotate-180': showUserMenu }"
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
              enter-from-class="transform opacity-0 scale-95"
              enter-to-class="transform opacity-100 scale-100"
              leave-active-class="transition ease-in duration-75"
              leave-from-class="transform opacity-100 scale-100"
              leave-to-class="transform opacity-0 scale-95"
            >
              <div
                v-if="showUserMenu"
                class="absolute right-2 z-50 mt-2 w-[calc(100vw-2rem)] max-w-80 rounded-lg border border-line bg-surface py-2 shadow-lg sm:right-0"
              >
                <div class="border-b border-line px-4 py-2">
                  <div class="truncate font-semibold text-ink-900">
                    {{ displayName }}
                  </div>
                </div>
                <div class="contents">
                  <button
                    type="button"
                    class="my-2 flex min-h-11 w-full items-center gap-2 px-4 py-1.5 text-left text-sm text-ink-700 transition-colors hover:bg-line-soft hover:text-ink-900"
                    @click="openSettings"
                  >
                    <svg
                      class="h-4 w-4 text-ink-400"
                      fill="none"
                      stroke="currentColor"
                      viewBox="0 0 24 24"
                    >
                      <path
                        stroke-linecap="round"
                        stroke-linejoin="round"
                        stroke-width="2"
                        d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z"
                      />
                      <path
                        stroke-linecap="round"
                        stroke-linejoin="round"
                        stroke-width="2"
                        d="M15 12a3 3 0 11-6 0 3 3 0 016 0z"
                      />
                    </svg>
                    <span>{{ t('common.settings') }}</span>
                    <span
                      v-if="uiStore.hasUnreadReleaseNotes"
                      class="ml-auto h-2 w-2 shrink-0 rounded-full bg-brand-500"
                    >
                      <span class="sr-only">
                        {{ t('settings.modal.releaseNotesUnread') }}
                      </span>
                    </span>
                  </button>
                </div>
                <div class="border-t border-line px-4 py-2 min-[430px]:hidden">
                  <LanguageSwitcher />
                </div>
                <div class="my-1 border-t border-line"></div>
                <button
                  @click="handleLogout"
                  class="block min-h-11 w-full px-4 py-2 text-left text-sm text-ink-700 hover:bg-line-soft"
                >
                  {{ t('common.logout') }}
                </button>
              </div>
            </Transition>
          </div>
        </div>
      </div>
    </div>
  </header>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { useUserStore } from '@/store/user'
import { useUiStore } from '@/store/ui'
import LanguageSwitcher from '@/components/ui/LanguageSwitcher.vue'
import {
  consumeAdminReturnPath,
  getAdminReturnPath
} from '@/utils/platformAccess'

defineProps({
  showMobileMenu: {
    type: Boolean,
    default: false
  }
})

defineEmits(['open-menu'])

const { t } = useI18n()
const route = useRoute()
const router = useRouter()
const userStore = useUserStore()
const uiStore = useUiStore()
const assistantReturnPath = ref(getAdminReturnPath())

const clearAssistantReturnPath = () => {
  consumeAdminReturnPath()
}

const showUserMenu = ref(false)
const userMenuRef = ref(null)

const pageTitle = computed(() => {
  const routeNames = {
    ManagementUsers: t('management.userManagement'),
    ManagementGroups: t('management.groupManagement'),
    LensAssistants: t('lensAdmin.pages.assistants.title'),
    LensNodes: t('lensAdmin.pages.lensnodes.title'),
    LensDataSources: t('lensAdmin.pages.datasources.title'),
    LensConnections: t('lensAdmin.pages.connections.title'),
    LensCredentials: t('lensAdmin.pages.credentials.title'),
    LensSkills: t('lensAdmin.pages.skills.title'),
    LensMcp: t('lensAdmin.pages.mcp.title'),
    LensResourceSettings: t('lensAdmin.pages.resourceSettings.title'),
    LensSettings: t('lensAdmin.pages.settings.title'),
    LensRunObservation: t('lensRuns.title'),
    LensShareReview: t('lens.qa.adminTitle'),
    LLMStats: t('llm.stats.title'),
    LLMUsage: t('llm.usage.title'),
    LLMConfig: t('llm.config.title'),
    LLMDataSettings: t('llm.dataSettings.title'),
    TaskManagementList: t('taskManagement.list.title'),
    TaskManagementStats: t('taskManagement.stats.title'),
    TaskManagementSettings: t('taskManagement.settings.title'),
    AdminNotificationsStats: t('notificationManagement.stats.title'),
    AdminNotificationsRecords: t('notificationManagement.records.title'),
    AdminNotificationsChannels: t('notificationManagement.channels.menuTitle'),
    AdminNotificationsSettings: t('notificationManagement.settings.menuTitle')
  }
  return routeNames[route.name] || t('management.logoTitle')
})

const displayName = computed(() => {
  const userInfo = userStore.userInfo
  if (!userInfo) return 'User'
  if (userInfo.display_name) return userInfo.display_name
  if (userInfo.first_name && userInfo.last_name)
    return `${userInfo.first_name} ${userInfo.last_name}`
  if (userInfo.first_name) return userInfo.first_name
  return userInfo.username || 'User'
})

const userInitials = computed(() => {
  const name = displayName.value
  return name.trim().charAt(0).toUpperCase() || 'U'
})

const avatarBgColor = computed(() => {
  const colors = [
    'bg-indigo-500',
    'bg-slate-500',
    'bg-violet-500',
    'bg-purple-500'
  ]
  const charCode = userInitials.value.charCodeAt(0)
  return colors[charCode % colors.length]
})

const toggleUserMenu = () => {
  showUserMenu.value = !showUserMenu.value
}

const openSettings = () => {
  uiStore.openSettings()
  showUserMenu.value = false
}

const handleLogout = async () => {
  try {
    await userStore.logout()
  } catch (error) {
    console.error('Logout failed:', error)
  } finally {
    showUserMenu.value = false
    router.push('/login')
  }
}

const handleClickOutside = (event) => {
  if (userMenuRef.value && !userMenuRef.value.contains(event.target)) {
    showUserMenu.value = false
  }
}

onMounted(() => {
  document.addEventListener('click', handleClickOutside)
})

onUnmounted(() => {
  document.removeEventListener('click', handleClickOutside)
})
</script>
