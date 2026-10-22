<template>
  <header
    class="flex items-center justify-between gap-2 border-b border-line bg-surface px-4 py-2 sm:gap-3 sm:px-6 sm:py-3"
  >
    <router-link to="/" class="flex min-w-0 shrink-0 items-center no-underline">
      <BrandLogo variant="responsive" />
    </router-link>
    <div class="flex min-w-0 items-center gap-2">
      <router-link
        v-if="showQaLink && assistantSlug"
        :to="`/lens/assistants/${assistantSlug}/qa`"
        :aria-label="t('lens.qa.publicListLink')"
        class="inline-flex min-h-10 min-w-10 shrink-0 items-center justify-center gap-1.5 whitespace-nowrap rounded-md border border-line px-2.5 py-2 text-sm font-medium text-theme-secondary no-underline transition-colors hover:border-line-strong hover:bg-surface-sunken hover:text-theme sm:min-h-0 sm:min-w-0 sm:px-3 sm:py-1.5"
      >
        <MessagesSquare :size="15" :stroke-width="2" aria-hidden="true" />
        <span class="hidden sm:inline">{{ t('lens.qa.publicListLink') }}</span>
      </router-link>
      <router-link
        v-if="assistantSlug"
        :to="`/lens/assistants/${assistantSlug}/chat`"
        class="inline-flex min-h-10 shrink-0 items-center justify-center whitespace-nowrap rounded-md bg-primary-600 px-3 py-2 text-sm font-medium text-white no-underline transition-colors hover:bg-primary-700 sm:min-h-0 sm:py-1.5"
      >
        <span class="sm:hidden">{{ t('lens.qa.backToChat') }}</span>
        <span class="hidden sm:inline">{{ ctaLabel }}</span>
      </router-link>
      <router-link
        v-if="!isAuthenticated"
        :to="loginTarget"
        class="inline-flex min-h-10 shrink-0 items-center justify-center whitespace-nowrap rounded-md px-2.5 py-2 text-sm font-medium text-theme-secondary no-underline transition-colors hover:bg-surface-sunken sm:min-h-0 sm:px-3 sm:py-1.5"
      >
        {{ t('auth.signIn') }}
      </router-link>
    </div>
  </header>
</template>

<script setup>
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute } from 'vue-router'
import { MessagesSquare } from '@lucide/vue'

import BrandLogo from '@/components/layout/BrandLogo.vue'
import { useUserStore } from '@/store/user'

const props = defineProps({
  assistantName: { type: String, default: '' },
  assistantSlug: { type: String, default: '' },
  showQaLink: { type: Boolean, default: false }
})

const { t } = useI18n()
const route = useRoute()
const userStore = useUserStore()
const isAuthenticated = computed(() => userStore.isAuthenticated)
const loginTarget = computed(() => ({
  path: '/login',
  query: { next: route.fullPath }
}))
const ctaLabel = computed(() =>
  props.assistantName
    ? t('lens.qa.ctaAction', { name: props.assistantName })
    : t('lens.qa.backToChat')
)
</script>
