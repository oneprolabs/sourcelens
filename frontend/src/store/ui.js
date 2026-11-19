import { defineStore } from 'pinia'
import { computed, ref } from 'vue'

import releaseNotesManifest from '@/generated/release-notes.json'
import { useUserStore } from '@/store/user'
import {
  getReleaseNotesStorageKey,
  hasReleaseNotesForAudience,
  isReleaseNotesVersionUnread,
  markReleaseNotesViewed as persistReleaseNotesViewed
} from '@/utils/releaseNotes'

function readViewedReleaseNotesVersion(isAdmin) {
  if (typeof window === 'undefined') return ''
  return window.localStorage.getItem(getReleaseNotesStorageKey(isAdmin)) || ''
}

export const useUiStore = defineStore('ui', () => {
  const adminSidebarScrollTop = ref(0)
  const platformSidebarCollapsedGroups = ref(new Set())
  const settingsOpen = ref(false)
  const settingsTab = ref('profile')
  const userStore = useUserStore()
  const releaseNotesVersion = releaseNotesManifest.version || 'dev'
  const isAdmin = computed(() => userStore.userHasFeature('admin_console'))
  const viewedUserReleaseNotesVersion = ref(
    readViewedReleaseNotesVersion(false)
  )
  const viewedAdminReleaseNotesVersion = ref(
    readViewedReleaseNotesVersion(true)
  )
  const viewedReleaseNotesVersion = computed(() =>
    isAdmin.value
      ? viewedAdminReleaseNotesVersion.value
      : viewedUserReleaseNotesVersion.value
  )
  const hasUnreadReleaseNotes = computed(
    () =>
      hasReleaseNotesForAudience(releaseNotesManifest, isAdmin.value) &&
      isReleaseNotesVersionUnread(
        releaseNotesVersion,
        viewedReleaseNotesVersion.value
      )
  )

  const openSettings = (tab = 'profile') => {
    settingsTab.value = tab
    settingsOpen.value = true
  }

  const closeSettings = () => {
    settingsOpen.value = false
  }

  const markReleaseNotesViewed = () => {
    persistReleaseNotesViewed(releaseNotesVersion, undefined, isAdmin.value)
    const viewedVersion = isAdmin.value
      ? viewedAdminReleaseNotesVersion
      : viewedUserReleaseNotesVersion
    viewedVersion.value = releaseNotesVersion
  }

  return {
    adminSidebarScrollTop,
    platformSidebarCollapsedGroups,
    hasUnreadReleaseNotes,
    markReleaseNotesViewed,
    settingsOpen,
    settingsTab,
    openSettings,
    closeSettings
  }
})
