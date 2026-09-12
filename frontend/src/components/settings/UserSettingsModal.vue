<template>
  <Transition
    enter-active-class="transition duration-200 ease-out"
    enter-from-class="opacity-0"
    enter-to-class="opacity-100"
    leave-active-class="transition duration-150 ease-in"
    leave-from-class="opacity-100"
    leave-to-class="opacity-0"
  >
    <div
      v-if="show"
      class="fixed inset-0 z-50 flex items-center justify-center p-0 sm:p-6"
    >
      <div
        class="absolute inset-0 bg-black/30 backdrop-blur-[2px]"
        @click="emit('close')"
      />

      <Transition
        enter-active-class="transition duration-200 ease-out"
        enter-from-class="opacity-0 translate-y-3 scale-[0.98]"
        enter-to-class="opacity-100 translate-y-0 scale-100"
        leave-active-class="transition duration-150 ease-in"
        leave-from-class="opacity-100 translate-y-0 scale-100"
        leave-to-class="opacity-0 translate-y-3 scale-[0.98]"
      >
        <section
          v-if="show"
          role="dialog"
          aria-modal="true"
          aria-labelledby="settings-modal-title"
          class="settings-modal relative flex h-[100dvh] max-h-none w-full max-w-none flex-col overflow-hidden rounded-none border-0 border-line bg-surface shadow-soft-lg sm:h-[526px] sm:max-h-[82vh] sm:max-w-[700px] sm:rounded-2xl sm:border"
        >
          <div class="settings-layout flex min-h-0 flex-1 flex-col sm:flex-row">
            <!-- Left nav -->
            <nav
              class="settings-nav flex w-full shrink-0 flex-row border-b border-line bg-surface-sunken px-2 py-2 sm:w-48 sm:flex-col sm:border-b-0 sm:border-r sm:px-0 sm:py-4"
            >
              <div class="hidden px-3 pb-3 sm:block sm:px-4">
                <div
                  class="settings-nav-title text-sm font-semibold text-theme-subtle"
                >
                  {{ t('settings.modal.label') }}
                </div>
              </div>

              <div
                class="settings-nav-tabs settings-nav-scrollbar flex min-w-0 flex-1 gap-1 overflow-x-auto sm:block sm:space-y-0.5 sm:overflow-visible sm:px-2"
              >
                <button
                  v-for="section in sections"
                  :key="section.key"
                  type="button"
                  class="flex min-h-11 w-auto shrink-0 items-center justify-center gap-2 rounded-lg px-3 py-2 text-left text-sm transition-colors sm:min-h-0 sm:w-full sm:justify-start"
                  :aria-label="section.label"
                  :aria-current="
                    activeSection === section.key ? 'page' : undefined
                  "
                  :class="
                    activeSection === section.key
                      ? 'settings-nav-active bg-surface font-medium text-theme shadow-sm'
                      : 'text-theme-secondary hover:bg-surface-hover hover:text-theme'
                  "
                  @click="activeSection = section.key"
                >
                  <component :is="section.icon" class="h-4 w-4 shrink-0" />
                  <span
                    class="whitespace-nowrap text-sm sm:min-w-0 sm:flex-1 sm:truncate"
                  >
                    {{ section.label }}
                  </span>
                  <span
                    v-if="
                      section.key === 'release-notes' &&
                      uiStore.hasUnreadReleaseNotes
                    "
                    class="h-2 w-2 shrink-0 rounded-full bg-primary-500"
                  >
                    <span class="sr-only">
                      {{ t('settings.modal.releaseNotesUnread') }}
                    </span>
                  </span>
                </button>
              </div>

              <div
                class="settings-logout-wrap hidden border-t border-line px-2 pt-2 sm:block"
              >
                <button
                  type="button"
                  class="settings-logout flex min-h-11 w-full items-center justify-start gap-2.5 rounded-lg px-3 py-2 text-left text-sm text-red-500 transition-colors hover:bg-red-50 hover:text-red-600 sm:min-h-0"
                  :aria-label="t('common.logout')"
                  @click="handleLogout"
                >
                  <svg
                    class="h-4 w-4 shrink-0"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    stroke-width="2"
                    aria-hidden="true"
                  >
                    <path
                      d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"
                      stroke-linecap="round"
                      stroke-linejoin="round"
                    />
                    <polyline
                      points="16 17 21 12 16 7"
                      stroke-linecap="round"
                      stroke-linejoin="round"
                    />
                    <line
                      x1="21"
                      y1="12"
                      x2="9"
                      y2="12"
                      stroke-linecap="round"
                    />
                  </svg>
                  <span class="truncate text-sm">{{ t('common.logout') }}</span>
                </button>
              </div>
            </nav>

            <!-- Right content -->
            <div class="settings-content-shell flex min-w-0 flex-1 flex-col">
              <header
                class="settings-header flex min-h-14 items-center justify-between border-b border-line px-4 py-2 sm:min-h-0 sm:px-6 sm:pb-2 sm:pt-5"
              >
                <h2
                  id="settings-modal-title"
                  class="text-base font-semibold text-theme"
                >
                  {{ sections.find((s) => s.key === activeSection)?.label }}
                </h2>
                <button
                  ref="closeButtonRef"
                  type="button"
                  class="flex h-11 w-11 items-center justify-center rounded-lg text-theme-subtle transition-colors hover:bg-surface-hover hover:text-theme-secondary sm:h-8 sm:w-8"
                  :aria-label="t('common.close')"
                  @click="emit('close')"
                >
                  <svg
                    class="h-4 w-4"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    stroke-width="2.25"
                    aria-hidden="true"
                  >
                    <path
                      d="M6 18L18 6M6 6l12 12"
                      stroke-linecap="round"
                      stroke-linejoin="round"
                    />
                  </svg>
                </button>
              </header>

              <div
                class="settings-content min-h-0 flex-1 overflow-y-auto px-4 pb-[calc(1.5rem+env(safe-area-inset-bottom))] pt-4 sm:px-6 sm:pb-6 sm:pt-3"
              >
                <!-- Profile section -->
                <div v-if="activeSection === 'profile'" class="space-y-5">
                  <div class="flex items-center gap-4">
                    <div
                      class="flex h-14 w-14 shrink-0 items-center justify-center rounded-2xl text-xl font-semibold text-white"
                      :class="avatarBgColor"
                    >
                      {{ userInitials }}
                    </div>
                    <div class="min-w-0">
                      <div class="truncate text-base font-semibold text-theme">
                        {{ displayName }}
                      </div>
                      <div class="mt-0.5 truncate text-sm text-theme-muted">
                        {{
                          userStore.userInfo?.email ||
                          t('settings.modal.noEmail')
                        }}
                      </div>
                    </div>
                  </div>

                  <div
                    class="divide-y divide-line rounded-xl border border-line"
                  >
                    <div class="space-y-1 px-3 py-3 sm:px-4">
                      <div class="text-xs font-medium text-theme-muted">
                        {{ t('settings.modal.username') }}
                      </div>
                      <div
                        class="break-all text-sm font-medium leading-snug text-theme"
                      >
                        {{ userStore.userInfo?.username || '—' }}
                      </div>
                    </div>
                    <div class="space-y-1 px-3 py-3 sm:px-4">
                      <div class="text-xs font-medium text-theme-muted">
                        {{ t('settings.modal.email') }}
                      </div>
                      <div
                        class="break-all text-sm font-medium leading-snug text-theme"
                      >
                        {{ userStore.userInfo?.email || '—' }}
                      </div>
                    </div>
                  </div>

                  <button
                    type="button"
                    class="settings-mobile-logout flex min-h-12 w-full items-center justify-center gap-2 rounded-xl border border-red-200 px-4 py-3 text-sm font-medium text-red-600 transition-colors hover:bg-red-50 sm:hidden"
                    :aria-label="t('common.logout')"
                    @click="handleLogout"
                  >
                    <svg
                      class="h-4 w-4 shrink-0"
                      viewBox="0 0 24 24"
                      fill="none"
                      stroke="currentColor"
                      stroke-width="2"
                      aria-hidden="true"
                    >
                      <path
                        d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"
                        stroke-linecap="round"
                        stroke-linejoin="round"
                      />
                      <polyline
                        points="16 17 21 12 16 7"
                        stroke-linecap="round"
                        stroke-linejoin="round"
                      />
                      <line
                        x1="21"
                        y1="12"
                        x2="9"
                        y2="12"
                        stroke-linecap="round"
                      />
                    </svg>
                    {{ t('common.logout') }}
                  </button>
                </div>

                <!-- Language section -->
                <div v-if="activeSection === 'language'" class="space-y-3">
                  <p class="text-sm text-theme-muted">
                    {{ t('settings.modal.languageDesc') }}
                  </p>
                  <BaseSelect
                    id="ui-language"
                    :model-value="locale"
                    @update:model-value="selectLanguage"
                  >
                    <option
                      v-for="lang in languages"
                      :key="lang.value"
                      :value="lang.value"
                    >
                      {{ lang.flag }} {{ lang.label }}
                    </option>
                  </BaseSelect>
                </div>

                <!-- Appearance section -->
                <div v-if="activeSection === 'appearance'" class="space-y-3">
                  <p class="text-sm text-theme-muted">
                    {{ t('settings.modal.appearanceDesc') }}
                  </p>
                  <div class="grid gap-2 sm:grid-cols-2" role="radiogroup">
                    <label
                      v-for="option in themeOptions"
                      :key="option.value"
                      class="flex min-h-20 cursor-pointer items-center gap-3 rounded-xl border px-3 py-3 text-sm text-theme transition-colors"
                      :class="
                        preferencesStore.themeMode === option.value
                          ? 'border-primary-500 bg-surface-selected'
                          : 'border-line/80 bg-surface-raised hover:border-line-strong hover:bg-surface-hover'
                      "
                    >
                      <span
                        class="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-surface-hover text-theme-secondary"
                      >
                        <component :is="option.icon" class="h-4 w-4" />
                      </span>
                      <span class="min-w-0 flex-1">
                        <span class="block font-medium">{{
                          option.label
                        }}</span>
                        <span class="mt-0.5 block text-xs text-theme-muted">
                          {{ option.description }}
                        </span>
                      </span>
                      <input
                        v-model="preferencesStore.themeMode"
                        type="radio"
                        name="appearance-mode"
                        :value="option.value"
                        class="h-4 w-4 shrink-0 accent-primary-500"
                        @change="preferencesStore.setThemeMode(option.value)"
                      />
                    </label>
                  </div>
                  <p
                    v-if="preferencesStore.themeMode === 'scheduled'"
                    class="text-sm text-theme-muted"
                  >
                    {{ t('settings.modal.themeScheduleDescription') }}
                  </p>
                  <p class="text-xs text-theme-subtle">
                    {{ t('settings.modal.themeAdminNote') }}
                  </p>
                </div>

                <AnswerNotificationSettings
                  v-if="activeSection === 'notifications'"
                />

                <ReleaseNotesSettings
                  v-if="activeSection === 'release-notes'"
                />

                <PasswordChangeSettings v-if="activeSection === 'security'" />
              </div>
            </div>
          </div>
        </section>
      </Transition>
    </div>
  </Transition>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRouter } from 'vue-router'
import AnswerNotificationSettings from '@/components/settings/AnswerNotificationSettings.vue'
import PasswordChangeSettings from '@/components/settings/PasswordChangeSettings.vue'
import ReleaseNotesSettings from '@/components/settings/ReleaseNotesSettings.vue'
import BaseSelect from '@/components/ui/BaseSelect.vue'
import { getPasswordManagementText } from '@/locales/passwordManagement'
import { getUiLanguageOptions } from '@/utils/languages'
import { usePreferencesStore } from '@/store/preferences'
import { useUiStore } from '@/store/ui'
import { useUserStore } from '@/store/user'

const props = defineProps({
  show: { type: Boolean, default: false }
})

const emit = defineEmits(['close'])

const { t, locale } = useI18n()
const router = useRouter()
const userStore = useUserStore()
const uiStore = useUiStore()
const preferencesStore = usePreferencesStore()
const languages = computed(() => getUiLanguageOptions())
const activeSection = ref('profile')
const closeButtonRef = ref(null)

const UserIcon = {
  template: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><circle cx="12" cy="8" r="4"/><path d="M4 20c0-4 3.6-7 8-7s8 3 8 7" stroke-linecap="round"/></svg>`
}
const GlobeIcon = {
  template: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M3.6 9h16.8M3.6 15h16.8M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18" stroke-linecap="round"/></svg>`
}
const AppearanceIcon = {
  template: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M12 3v18" stroke-linecap="round"/><path d="M12 3a9 9 0 0 1 0 18z" fill="currentColor" fill-opacity="0.25"/></svg>`
}
const BellIcon = {
  template: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9" stroke-linecap="round" stroke-linejoin="round"/><path d="M10 21h4" stroke-linecap="round"/></svg>`
}
const ReleaseNotesIcon = {
  template: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M5 4h14v16H5z"/><path d="M8 8h8M8 12h8M8 16h5" stroke-linecap="round"/></svg>`
}
const LockIcon = {
  template: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><rect x="5" y="10" width="14" height="10" rx="2"/><path d="M8 10V7a4 4 0 0 1 8 0v3" stroke-linecap="round"/></svg>`
}
const SunIcon = {
  template: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.93 4.93l1.42 1.42M17.66 17.66l1.41 1.41M2 12h2M20 12h2M4.93 19.07l1.42-1.42M17.66 6.34l1.41-1.41" stroke-linecap="round"/></svg>`
}
const MoonIcon = {
  template: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z" stroke-linecap="round" stroke-linejoin="round"/></svg>`
}
const SystemIcon = {
  template: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M12 3v18" stroke-linecap="round"/><path d="M12 3a9 9 0 0 1 0 18z" fill="currentColor" fill-opacity="0.25"/></svg>`
}
const ClockIcon = {
  template: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2" stroke-linecap="round" stroke-linejoin="round"/></svg>`
}

const themeOptions = computed(() => [
  {
    value: 'light',
    label: t('settings.modal.themeLight'),
    description: t('settings.modal.themeLightDesc'),
    icon: SunIcon
  },
  {
    value: 'dark',
    label: t('settings.modal.themeDark'),
    description: t('settings.modal.themeDarkDesc'),
    icon: MoonIcon
  },
  {
    value: 'system',
    label: t('settings.modal.themeSystem'),
    description: t('settings.modal.themeSystemDesc'),
    icon: SystemIcon
  },
  {
    value: 'scheduled',
    label: t('settings.modal.themeScheduled'),
    description: t('settings.modal.themeScheduledDesc'),
    icon: ClockIcon
  }
])

const sections = computed(() => [
  { key: 'profile', label: t('settings.modal.title'), icon: UserIcon },
  {
    key: 'release-notes',
    label: t('settings.modal.releaseNotes'),
    icon: ReleaseNotesIcon
  },
  { key: 'language', label: t('settings.modal.language'), icon: GlobeIcon },
  {
    key: 'appearance',
    label: t('settings.modal.appearance'),
    icon: AppearanceIcon
  },
  {
    key: 'notifications',
    label: t('settings.modal.notifications'),
    icon: BellIcon
  },
  {
    key: 'security',
    label: getPasswordManagementText(locale.value).security.title,
    icon: LockIcon
  }
])

const displayName = computed(() => {
  const userInfo = userStore.userInfo
  if (!userInfo) return 'User'
  if (userInfo.display_name) return userInfo.display_name
  if (userInfo.first_name && userInfo.last_name)
    return `${userInfo.first_name} ${userInfo.last_name}`
  if (userInfo.first_name) return userInfo.first_name
  return userInfo.username || 'User'
})

const userInitials = computed(
  () => displayName.value.trim().charAt(0).toUpperCase() || 'U'
)

const avatarBgColor = computed(() => {
  const colors = [
    'bg-blue-500',
    'bg-indigo-500',
    'bg-emerald-500',
    'bg-rose-500',
    'bg-amber-500',
    'bg-cyan-500'
  ]
  return colors[userInitials.value.charCodeAt(0) % colors.length]
})

const selectLanguage = async (language) => {
  await userStore.updateLanguage(language)
}

const handleLogout = async () => {
  await userStore.logout().catch(() => {})
  uiStore.closeSettings()
  await router.push('/login')
}

const handleKeydown = (event) => {
  if (event.key === 'Escape' && props.show && !event.defaultPrevented) {
    uiStore.closeSettings()
  }
}

watch(
  () => props.show,
  async (show) => {
    if (show) {
      activeSection.value = uiStore.settingsTab || 'profile'
      await nextTick()
      closeButtonRef.value?.focus()
    }
    if (typeof document !== 'undefined')
      document.body.style.overflow = show ? 'hidden' : ''
  },
  { immediate: true }
)

watch(
  [() => props.show, activeSection],
  ([show, section]) => {
    if (show && section === 'release-notes') {
      uiStore.markReleaseNotesViewed()
    }
  },
  { immediate: true }
)

if (typeof window !== 'undefined')
  window.addEventListener('keydown', handleKeydown)

onBeforeUnmount(() => {
  if (typeof document !== 'undefined') document.body.style.overflow = ''
  if (typeof window !== 'undefined')
    window.removeEventListener('keydown', handleKeydown)
})
</script>

<style scoped>
.settings-nav-scrollbar {
  scrollbar-width: none;
}

.settings-nav-scrollbar::-webkit-scrollbar {
  display: none;
}

:global(:root[data-theme='dark'] .settings-modal) {
  border-color: rgb(var(--sl-border-default-rgb) / 60%);
}

:global(:root[data-theme='dark'] .settings-nav),
:global(:root[data-theme='dark'] .settings-logout-wrap),
:global(:root[data-theme='dark'] .settings-header) {
  border-color: transparent;
}

:global(:root[data-theme='dark'] .settings-nav-title) {
  color: var(--sl-text-primary);
}

:global(:root[data-theme='dark'] .settings-nav-active) {
  background: var(--sl-bg-selected);
  box-shadow: none;
}

:global(:root[data-theme='dark'] .settings-logout:hover),
:global(:root[data-theme='dark'] .settings-mobile-logout:hover) {
  background: var(--sl-bg-hover);
}
</style>
