<template>
  <div
    class="layout-admin fixed inset-0 flex w-full overflow-hidden"
    :class="{
      'platform-console': isPlatformConsole,
      'admin-sidebar-collapsed': desktopSidebarCollapsed
    }"
  >
    <component
      :is="isPlatformConsole ? PlatformSidebar : AdminSidebar"
      :show-mobile-menu="showMobileMenu"
      @close="showMobileMenu = false"
    />
    <div class="admin-main-column flex-1 flex min-w-0 flex-col overflow-hidden">
      <AdminHeader
        :show-mobile-menu="isMobile ? showMobileMenu : !desktopSidebarCollapsed"
        @open-menu="toggleNavigation"
      />
      <div
        v-if="userStore.delegation"
        class="flex flex-wrap items-center gap-3 bg-danger-600 px-4 py-2 text-sm text-white"
      >
        <span class="flex-1">
          {{
            t('management.delegationBanner', {
              tenant: userStore.delegation.tenant?.name || ''
            })
          }}
        </span>
        <button
          type="button"
          class="rounded-md border border-white/40 px-3 py-1 text-xs font-medium hover:bg-white/10"
          @click="exitDelegation"
        >
          {{ t('management.exitDelegation') }}
        </button>
      </div>
      <main class="admin-main flex-1 min-w-0 overflow-y-auto">
        <div :key="route.path" class="admin-page-content h-full min-h-0">
          <slot />
        </div>
      </main>
    </div>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute, useRouter } from 'vue-router'

import { endTenantDelegation } from '@/api/platform'
import { useUserStore } from '@/store/user'
import { useIsMobile } from '@/composables/useIsMobile'
import AdminHeader from './AdminHeader.vue'
import AdminSidebar from './AdminSidebar.vue'
import PlatformSidebar from './PlatformSidebar.vue'

const showMobileMenu = ref(false)
const desktopSidebarCollapsed = ref(false)
const { isMobile } = useIsMobile(821)
function toggleNavigation() {
  if (isMobile.value) showMobileMenu.value = !showMobileMenu.value
  else desktopSidebarCollapsed.value = !desktopSidebarCollapsed.value
}
const route = useRoute()
const router = useRouter()
const { t } = useI18n()
const userStore = useUserStore()
const isPlatformConsole = computed(() => route.meta.console === 'platform')

async function exitDelegation() {
  const delegationId = userStore.delegation?.delegation_id
  try {
    if (delegationId) {
      await endTenantDelegation(delegationId)
    }
  } finally {
    userStore.clearDelegation()
    router.push('/management/platform/tenants')
  }
}
</script>
