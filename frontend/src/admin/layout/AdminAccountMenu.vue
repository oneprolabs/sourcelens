<template>
  <div ref="menuRef" class="admin-account">
    <button
      type="button"
      class="admin-account-trigger"
      :aria-expanded="open"
      @click="open = !open"
      @keydown.esc="open = false"
    >
      <span class="admin-avatar">{{ displayName.charAt(0) }}</span>
      <span class="admin-account-identity">
        <strong>{{ displayName }}</strong>
        <small>{{ identity }}</small>
      </span>
      <MoreHorizontal :size="16" aria-hidden="true" />
    </button>
    <div v-if="open" class="admin-account-menu" @keydown.esc="open = false">
      <div class="admin-account-menu-identity">
        <strong>{{ displayName }}</strong>
        <small>{{ userStore.userInfo?.email }}</small>
      </div>
      <button type="button" @click="openSettings">
        <Settings :size="18" aria-hidden="true" />{{ t('common.settings') }}
      </button>
      <div class="admin-account-language"><LanguageSwitcher /></div>
      <button type="button" @click="logout">
        <LogOut :size="18" aria-hidden="true" />{{ t('common.logout') }}
      </button>
    </div>
  </div>
</template>

<script setup>
import { LogOut, MoreHorizontal, Settings } from '@lucide/vue'
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute, useRouter } from 'vue-router'
import LanguageSwitcher from '@/components/ui/LanguageSwitcher.vue'
import { useUiStore } from '@/store/ui'
import { useUserStore } from '@/store/user'

const { t } = useI18n()
const router = useRouter()
const route = useRoute()
const userStore = useUserStore()
const uiStore = useUiStore()
const open = ref(false)
const menuRef = ref(null)
const displayName = computed(() => {
  const user = userStore.userInfo || {}
  return (
    user.display_name ||
    [user.first_name, user.last_name].filter(Boolean).join(' ') ||
    user.username ||
    'User'
  )
})
const identity = computed(() => {
  if (route.meta.console === 'platform') return t('management.platformConsole')
  const user = userStore.userInfo || {}
  const role = t(
    user.tenant_role === 'tenant_admin'
      ? 'management.tenantAdminRole'
      : 'management.memberRole'
  )
  return [role, userStore.delegation?.tenant?.name || user.tenant?.name]
    .filter(Boolean)
    .join(' · ')
})
function openSettings() {
  open.value = false
  uiStore.openSettings()
}
async function logout() {
  try {
    await userStore.logout()
  } finally {
    open.value = false
    router.push('/login')
  }
}
function dismiss(event) {
  if (!menuRef.value?.contains(event.target)) open.value = false
}
onMounted(() => document.addEventListener('click', dismiss))
onBeforeUnmount(() => document.removeEventListener('click', dismiss))
</script>
