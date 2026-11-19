<template>
  <div :aria-busy="loading">
    <BaseLoading v-if="loading" />
    <div
      v-else-if="loadError"
      role="alert"
      class="space-y-3 text-sm text-danger-700"
    >
      <p>{{ loadError }}</p>
      <BaseButton variant="secondary" @click="loadConfig">{{
        t('common.retry')
      }}</BaseButton>
    </div>
    <form v-else @submit.prevent="saveConfig">
      <h2 class="mb-6 text-base font-semibold text-ink-900">
        {{ t('taskManagement.settings.taskManagementSection') }}
      </h2>
      <fieldset :disabled="saving" class="space-y-6 disabled:opacity-60">
        <section
          v-for="field in numericFields"
          :key="field.key"
          class="grid gap-4 md:grid-cols-3"
        >
          <div class="md:col-span-2">
            <label :for="field.key" class="text-sm font-semibold text-ink-900">
              {{ t(`taskManagement.settings.${field.title}Title`) }}
            </label>
            <p class="mt-1 text-sm text-ink-500">
              {{ t(`taskManagement.settings.${field.title}Desc`) }}
            </p>
          </div>
          <div class="flex items-center gap-2 md:justify-end">
            <input
              :id="field.key"
              v-model.number="form[field.key]"
              type="number"
              required
              min="1"
              :max="field.max"
              step="1"
              class="w-24 rounded-md border border-line bg-surface px-3 py-2 text-sm text-ink-900 focus:ring-2 focus:ring-brand-500"
            />
            <span class="text-sm text-ink-500">{{
              t(`taskManagement.settings.${field.unit}Unit`)
            }}</span>
          </div>
        </section>
        <section
          v-for="item in schedules"
          :key="item.key"
          class="border-t border-line pt-6"
        >
          <div class="flex items-start justify-between gap-4">
            <div>
              <h3 class="text-sm font-semibold text-ink-900">
                {{ t(`taskManagement.settings.${item.title}Title`) }}
              </h3>
              <p class="mt-1 text-sm text-ink-500">
                {{ t(`taskManagement.settings.${item.title}Desc`) }}
              </p>
            </div>
            <input
              v-model="form[`${item.key}_enabled`]"
              type="checkbox"
              :aria-label="t(`taskManagement.settings.${item.title}Title`)"
              :disabled="!availability[item.key]"
              class="mt-1 h-5 w-5 accent-brand-500"
            />
          </div>
          <p v-if="!availability[item.key]" class="mt-2 text-sm text-ink-500">
            {{ t('taskManagement.settings.runtimeDisabled') }}
          </p>
          <label
            :for="`${item.key}_crontab`"
            class="mt-4 block text-sm font-medium text-ink-700"
          >
            {{ t(`taskManagement.settings.${item.title}Crontab`) }}
          </label>
          <input
            :id="`${item.key}_crontab`"
            v-model.trim="form[`${item.key}_crontab`]"
            type="text"
            required
            class="mt-2 w-full rounded-md border border-line bg-surface px-3 py-2 font-mono text-sm text-ink-900 focus:ring-2 focus:ring-brand-500 md:max-w-sm"
          />
          <p class="mt-1 text-xs text-ink-500">
            {{ t('taskManagement.settings.crontabHelp') }}
          </p>
        </section>
      </fieldset>
      <p v-if="saveError" role="alert" class="mt-4 text-sm text-danger-700">
        {{ saveError }}
      </p>
      <div
        class="mt-6 flex flex-wrap items-center justify-end gap-3 border-t border-line pt-6"
      >
        <span v-if="dirty" class="mr-auto text-sm text-ink-500">{{
          t('management.unsavedChanges')
        }}</span>
        <BaseButton
          variant="secondary"
          size="sm"
          :disabled="saving || !dirty"
          @click="resetForm"
        >
          {{ t('taskManagement.settings.reset') }}
        </BaseButton>
        <BaseButton
          type="submit"
          size="sm"
          :loading="saving"
          :disabled="!dirty"
        >
          {{ t('taskManagement.settings.saveChanges') }}
        </BaseButton>
      </div>
    </form>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { onBeforeRouteLeave } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { taskManagementApi } from '@/admin/api'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseLoading from '@/components/ui/BaseLoading.vue'
import { useToast } from '@/composables/useToast'
import { extractErrorMessage, extractResponseData } from '@/utils/api'

const { t } = useI18n()
const { showSuccess } = useToast()
const loading = ref(true)
const saving = ref(false)
const loadError = ref('')
const saveError = ref('')
const form = reactive({})
const initialValues = ref({})
const availability = reactive({ cleanup: true, mark_timeout: true })
const dirty = computed(
  () => JSON.stringify(form) !== JSON.stringify(initialValues.value)
)
const numericFields = [
  { key: 'timeout_minutes', title: 'timeout', max: 1440, unit: 'minutes' },
  { key: 'retention_days', title: 'retention', max: 3650, unit: 'days' }
]
const schedules = [
  { key: 'cleanup', title: 'cleanup' },
  { key: 'mark_timeout', title: 'markTimeout' }
]
const keys = [
  'timeout_minutes',
  'retention_days',
  'cleanup_crontab',
  'mark_timeout_crontab',
  'cleanup_enabled',
  'mark_timeout_enabled'
]

function applyConfig(data) {
  for (const key of keys) form[key] = data[key]
  for (const { key } of schedules) availability[key] = data[`${key}_available`]
  initialValues.value = { ...form }
}
function resetForm() {
  Object.assign(form, initialValues.value)
  saveError.value = ''
}
async function loadConfig() {
  loading.value = true
  loadError.value = ''
  try {
    applyConfig(extractResponseData(await taskManagementApi.getConfig()))
  } catch (error) {
    loadError.value = extractErrorMessage(error, t('common.error'))
  } finally {
    loading.value = false
  }
}
async function saveConfig() {
  if (saving.value || !dirty.value) return
  saving.value = true
  saveError.value = ''
  try {
    const changes = Object.fromEntries(
      keys
        .filter((key) => form[key] !== initialValues.value[key])
        .map((key) => [key, form[key]])
    )
    applyConfig(
      extractResponseData(await taskManagementApi.updateConfig(changes))
    )
    showSuccess(t('taskManagement.settings.saveSuccess'))
  } catch (error) {
    saveError.value = extractErrorMessage(
      error,
      t('taskManagement.settings.saveFailed')
    )
  } finally {
    saving.value = false
  }
}
function beforeUnload(event) {
  if (!dirty.value) return
  event.preventDefault()
  event.returnValue = ''
}
onBeforeRouteLeave(
  () =>
    !dirty.value || window.confirm(t('taskManagement.settings.confirmLeave'))
)
onMounted(() => {
  loadConfig()
  window.addEventListener('beforeunload', beforeUnload)
})
onBeforeUnmount(() => window.removeEventListener('beforeunload', beforeUnload))
</script>
