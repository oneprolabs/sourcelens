<template>
  <div class="relative" ref="dropdownRef">
    <button
      @click="toggleDropdown"
      :class="[
        'flex min-h-11 min-w-11 items-center justify-center gap-1.5 rounded-lg px-2.5 py-1.5 text-sm transition-colors md:min-h-0 md:min-w-0',
        variant === 'dark'
          ? 'text-white/80 hover:bg-surface-hover/20 hover:text-white'
          : 'text-theme-muted hover:bg-line-soft hover:text-theme'
      ]"
      :title="t('common.language')"
    >
      <span class="text-base leading-none">{{ currentLanguageDisplay }}</span>
      <svg
        class="h-3 w-3 transition-transform"
        :class="showDropdown ? 'rotate-180' : ''"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        stroke-width="2.5"
      >
        <path d="m6 9 6 6 6-6" stroke-linecap="round" stroke-linejoin="round" />
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
      <div
        v-if="showDropdown"
        class="absolute right-0 z-50 mt-1.5 w-36 overflow-hidden rounded-xl border border-line bg-surface p-1 shadow-soft-md"
      >
        <button
          v-for="lang in languages"
          :key="lang.value"
          @click="selectLanguage(lang.value)"
          class="flex min-h-11 w-full items-center gap-2.5 rounded-lg px-3 py-2 text-sm transition-colors md:min-h-0"
          :class="
            locale === lang.value
              ? 'bg-line-soft font-medium text-theme'
              : 'text-theme-secondary hover:bg-line-soft hover:text-theme'
          "
        >
          <span class="text-base leading-none">{{ lang.flag }}</span>
          <span class="flex-1 text-left">{{ lang.label }}</span>
          <svg
            v-if="locale === lang.value"
            class="h-3.5 w-3.5 shrink-0 text-primary-600"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            stroke-width="2.5"
          >
            <path
              d="M20 6 9 17l-5-5"
              stroke-linecap="round"
              stroke-linejoin="round"
            />
          </svg>
        </button>
      </div>
    </Transition>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { useUserStore } from '@/store/user'
import { getUiLanguageOptions } from '@/utils/languages'

defineProps({
  variant: {
    type: String,
    default: 'default',
    validator: (v) => ['default', 'dark'].includes(v)
  }
})

const { t, locale } = useI18n()
const userStore = useUserStore()

const showDropdown = ref(false)
const dropdownRef = ref(null)

const languages = computed(() => getUiLanguageOptions())

const currentLanguageDisplay = computed(() => {
  const lang = languages.value.find((l) => l.value === locale.value)
  return lang ? lang.flag : '🌐'
})

const toggleDropdown = () => {
  showDropdown.value = !showDropdown.value
}

const selectLanguage = async (language) => {
  await userStore.updateLanguage(language)
  showDropdown.value = false
}

const handleClickOutside = (event) => {
  if (dropdownRef.value && !dropdownRef.value.contains(event.target)) {
    showDropdown.value = false
  }
}

onMounted(() => {
  document.addEventListener('click', handleClickOutside)
})

onUnmounted(() => {
  document.removeEventListener('click', handleClickOutside)
})
</script>
