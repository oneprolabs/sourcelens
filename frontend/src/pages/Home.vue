<template>
  <div class="home-shell">
    <div v-if="loading" class="home-loading">
      <BaseLoading />
    </div>
    <AssistantEmptyState v-else :variant="variant" />
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import AssistantEmptyState from '@/components/lens/AssistantEmptyState.vue'
import BaseLoading from '@/components/ui/BaseLoading.vue'
import { useUserStore } from '@/store/user'
import { listAssistants } from '@/api/lens'
import { readRecentChat, pickRecentAssistant } from '@/utils/recentChat'

const router = useRouter()
const userStore = useUserStore()

const loading = ref(true)
const variant = ref('visitor')

onMounted(async () => {
  // The router guard guarantees an authenticated user here. Every user
  // resolves a default assistant and enters its chat. With none available,
  // fall back to a role-aware guide: admins see the create-first-assistant
  // guide, regular users the no-assistant notice.
  try {
    const assistants = await listAssistants()
    const target = pickRecentAssistant(
      assistants || [],
      readRecentChat(userStore.user)
    )
    if (target) {
      await router.replace(`/lens/assistants/${target.slug}/chat`)
      return
    }
  } catch {
    // Fall through to the guide below.
  }
  variant.value = userStore.userHasFeature('admin_console')
    ? 'admin'
    : 'visitor'
  loading.value = false
})
</script>

<style scoped>
.home-shell {
  @apply h-screen w-full bg-surface;
}

.home-loading {
  @apply flex h-full w-full items-center justify-center;
}
</style>
