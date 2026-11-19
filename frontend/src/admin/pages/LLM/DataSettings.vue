<template>
  <AdminLayout>
    <div class="w-full max-w-full p-6">
      <div class="mb-4">
        <h1 class="text-lg font-semibold text-gray-900">
          {{ t('llm.dataSettings.title') }}
        </h1>
        <p class="mt-1 text-sm text-gray-500">
          {{ t('llm.dataSettings.subtitle') }}
        </p>
      </div>

      <div class="bg-white rounded-lg border border-gray-200 shadow-sm">
        <div class="p-6">
          <BaseLoading v-if="loading" />
          <p v-else-if="loadError" role="alert" class="text-sm text-danger-700">
            {{ loadError }}
            <button type="button" class="underline" @click="loadConfig">
              {{ t('common.refresh') }}
            </button>
          </p>
          <template v-else>
            <h2 class="text-base font-semibold text-gray-900 mb-6">
              {{ t('llm.dataSettings.sectionTitle') }}
            </h2>

            <section
              class="grid grid-cols-1 md:grid-cols-3 gap-4 items-start pb-6"
            >
              <div class="md:col-span-2">
                <h3 class="text-sm font-semibold text-gray-900 mb-1">
                  {{ t('llm.dataSettings.retentionTitle') }}
                </h3>
                <p class="text-sm text-gray-600">
                  {{ t('llm.dataSettings.retentionDesc') }}
                </p>
              </div>
              <div class="md:col-span-1 flex justify-end">
                <div class="w-full md:w-64 flex items-center justify-end gap-2">
                  <input
                    v-model.number="form.retention_days"
                    :aria-label="t('llm.dataSettings.retentionTitle')"
                    type="number"
                    min="1"
                    max="3650"
                    :placeholder="
                      t('llm.dataSettings.retentionDaysPlaceholder')
                    "
                    class="rounded-md border border-gray-300 px-3 py-2 text-sm w-24 focus:outline-none focus:ring-1 focus:ring-primary-500 focus:border-primary-500"
                  />
                  <span class="text-sm text-gray-500 w-14">{{
                    t('llm.dataSettings.daysUnit')
                  }}</span>
                </div>
              </div>
            </section>

            <section class="pt-6 pb-6 border-t border-gray-200">
              <div class="grid grid-cols-1 md:grid-cols-3 gap-4 items-start">
                <div class="md:col-span-2">
                  <h3 class="text-sm font-semibold text-gray-900 mb-1">
                    {{ t('llm.dataSettings.cleanupTitle') }}
                  </h3>
                  <p class="text-sm text-gray-600">
                    {{ t('llm.dataSettings.cleanupDesc') }}
                  </p>
                </div>
                <div class="md:col-span-1 flex justify-end">
                  <div class="w-full md:w-64 flex justify-end">
                    <label
                      class="relative inline-flex items-center cursor-pointer"
                    >
                      <input
                        v-model="form.cleanup_enabled"
                        :disabled="!cleanupAvailable"
                        :aria-label="t('llm.dataSettings.cleanupTitle')"
                        type="checkbox"
                        class="sr-only peer"
                      />
                      <div
                        class="w-11 h-6 bg-gray-200 peer-focus:outline-none peer-focus:ring-4 peer-focus:ring-primary-300 rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-primary-600"
                      />
                    </label>
                  </div>
                </div>
              </div>
              <div
                class="grid grid-cols-1 md:grid-cols-3 gap-4 items-center mt-4"
              >
                <div class="md:col-span-2">
                  <label class="text-sm font-medium text-gray-700">{{
                    t('llm.dataSettings.cleanupCrontab')
                  }}</label>
                  <p class="text-xs text-gray-500 mt-0.5">
                    {{ t('llm.dataSettings.crontabHelp') }}
                  </p>
                </div>
                <div class="md:col-span-1 flex justify-end">
                  <div class="w-full md:w-64">
                    <input
                      v-model="form.cleanup_crontab"
                      :aria-label="t('llm.dataSettings.cleanupCrontab')"
                      type="text"
                      :placeholder="
                        t('llm.dataSettings.cleanupCrontabPlaceholder')
                      "
                      :disabled="!form.cleanup_enabled"
                      class="rounded-md border border-gray-300 px-3 py-2 text-sm w-full font-mono focus:outline-none focus:ring-1 focus:ring-primary-500 focus:border-primary-500 disabled:bg-gray-100 disabled:text-gray-500 disabled:cursor-not-allowed"
                    />
                  </div>
                </div>
              </div>
            </section>

            <section class="pt-6 pb-6 border-t border-gray-200">
              <div class="grid grid-cols-1 md:grid-cols-3 gap-4 items-start">
                <div class="md:col-span-2">
                  <h3 class="text-sm font-semibold text-gray-900 mb-1">
                    {{ t('llm.dataSettings.aggregationCrontab') }}
                  </h3>
                  <p class="text-sm text-gray-600">
                    {{ t('llm.dataSettings.aggregationCrontabDesc') }}
                  </p>
                  <p class="text-xs text-gray-500 mt-0.5">
                    {{ t('llm.dataSettings.crontabHelp') }}
                  </p>
                </div>
                <div class="md:col-span-1 flex justify-end">
                  <div class="w-full md:w-64">
                    <input
                      v-model="form.aggregation_crontab"
                      :aria-label="t('llm.dataSettings.aggregationCrontab')"
                      type="text"
                      :placeholder="
                        t('llm.dataSettings.aggregationCrontabPlaceholder')
                      "
                      class="rounded-md border border-gray-300 px-3 py-2 text-sm w-full font-mono focus:outline-none focus:ring-1 focus:ring-primary-500 focus:border-primary-500"
                    />
                  </div>
                </div>
              </div>
            </section>

            <div
              class="flex items-center justify-end gap-3 border-t border-gray-200 pt-6"
            >
              <BaseButton
                variant="secondary"
                size="sm"
                :disabled="saving || !dirty"
                @click="resetForm"
              >
                {{ t('llm.dataSettings.reset') }}
              </BaseButton>
              <BaseButton
                variant="primary"
                size="sm"
                :loading="saving"
                :disabled="!dirty"
                @click="saveConfig"
              >
                {{ t('llm.dataSettings.saveChanges') }}
              </BaseButton>
            </div>
            <p
              v-if="saveError"
              role="alert"
              class="mt-2 text-sm text-danger-700"
            >
              {{ saveError }}
            </p>
            <p v-if="saveSuccess" class="mt-2 text-sm text-green-700">
              {{ t('llm.dataSettings.saveSuccess') }}
            </p>
          </template>
        </div>
      </div>
    </div>
  </AdminLayout>
</template>

<script setup>
import { ref, reactive, computed, onMounted, onBeforeUnmount } from 'vue'
import { onBeforeRouteLeave } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { useToast } from '@/composables/useToast'
import { extractErrorMessage } from '@/utils/api'
import { llmAdminApi } from '@/admin/api'
import AdminLayout from '@/admin/layout/AdminLayout.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseLoading from '@/components/ui/BaseLoading.vue'

const { t } = useI18n()
const { showSuccess, showError } = useToast()

const loading = ref(false)
const saving = ref(false)
const cleanupAvailable = ref(true)
const loadError = ref('')
const saveError = ref('')
const saveSuccess = ref(false)

const form = reactive({
  retention_days: 365,
  cleanup_enabled: true,
  cleanup_crontab: '0 2 * * *',
  aggregation_crontab: '5 * * * *'
})

const initialValues = reactive({
  retention_days: 365,
  cleanup_enabled: true,
  cleanup_crontab: '0 2 * * *',
  aggregation_crontab: '5 * * * *'
})

const dirty = computed(() =>
  Object.keys(form).some((key) => form[key] !== initialValues[key])
)

function resetForm() {
  Object.assign(form, initialValues)
  saveError.value = ''
  saveSuccess.value = false
}

function assignFromValue(raw) {
  for (const key of Object.keys(form)) {
    if (raw?.[key] !== undefined) form[key] = raw[key]
  }
  cleanupAvailable.value = raw?.cleanup_available !== false
  Object.assign(initialValues, form)
}

async function loadConfig() {
  loading.value = true
  loadError.value = ''
  try {
    assignFromValue(await llmAdminApi.getMeteringConfig())
  } catch (error) {
    loadError.value = extractErrorMessage(error, t('common.error'))
  } finally {
    loading.value = false
  }
}

async function saveConfig() {
  saveError.value = ''
  saveSuccess.value = false
  const payload = Object.fromEntries(
    Object.keys(form)
      .filter((key) => form[key] !== initialValues[key])
      .map((key) => [key, form[key]])
  )
  if (!Object.keys(payload).length) return
  saving.value = true
  try {
    assignFromValue(await llmAdminApi.updateMeteringConfig(payload))
    saveSuccess.value = true
    showSuccess(t('llm.dataSettings.saveSuccess'))
  } catch (error) {
    saveError.value = extractErrorMessage(
      error,
      t('llm.dataSettings.saveFailed')
    )
    showError(saveError.value)
  } finally {
    saving.value = false
  }
}

onBeforeRouteLeave(
  () => !dirty.value || window.confirm(t('llm.dataSettings.confirmLeave'))
)
function beforeUnload(event) {
  if (!dirty.value) return
  event.preventDefault()
  event.returnValue = ''
}
if (typeof window !== 'undefined')
  window.addEventListener('beforeunload', beforeUnload)
onBeforeUnmount(() => {
  if (typeof window !== 'undefined')
    window.removeEventListener('beforeunload', beforeUnload)
})

onMounted(() => {
  loadConfig()
})
</script>
