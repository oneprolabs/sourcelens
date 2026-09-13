<template>
  <AdminLayout>
    <div class="flex max-w-full flex-col gap-4 py-4">
      <section
        class="overflow-hidden rounded-lg border border-line bg-surface shadow-sm"
      >
        <div
          class="flex flex-col gap-4 border-b border-line px-5 py-4 lg:flex-row lg:items-start lg:justify-between"
        >
          <div class="min-w-0">
            <div class="flex flex-wrap items-center gap-2">
              <h1 class="text-xl font-semibold text-ink-900">
                {{ t('lensAdmin.pages.resourceSettings.title') }}
              </h1>
            </div>
          </div>
          <div class="flex flex-wrap items-center gap-2">
            <BaseButton
              variant="outline"
              size="sm"
              :loading="loading"
              @click="load"
            >
              {{ t('common.refresh') }}
            </BaseButton>
          </div>
        </div>

        <div class="px-5 py-4">
          <BaseLoading v-if="loading && settingDefinitions.length === 0" />

          <template v-else>
            <div class="space-y-4">
              <div
                v-for="grp in groupedSettings"
                :key="grp.group"
                class="overflow-hidden rounded-lg border border-line"
              >
                <div class="border-b border-line bg-surface-sunken px-4 py-2.5">
                  <h3 class="text-sm font-semibold text-ink-900">
                    {{ grp.title }}
                  </h3>
                </div>
                <table
                  class="settings-table min-w-full table-fixed divide-y divide-line"
                >
                  <colgroup>
                    <col class="w-[48%]" />
                    <col class="w-[52%]" />
                  </colgroup>
                  <tbody class="divide-y divide-line bg-surface">
                    <tr
                      v-for="setting in grp.items"
                      :key="setting.key"
                      class="align-top transition-colors hover:bg-line-soft"
                    >
                      <td class="table-cell">
                        <div class="text-sm font-semibold text-ink-900">
                          {{ setting.label }}
                        </div>
                        <p class="mt-1 text-sm leading-6 text-ink-500">
                          {{ setting.description }}
                        </p>
                        <p
                          class="setting-key mt-1 font-mono text-xs text-ink-400"
                        >
                          {{ setting.key }}
                        </p>
                      </td>
                      <td class="table-cell">
                        <div
                          class="settings-control flex w-full items-center justify-end gap-3"
                        >
                          <input
                            v-if="setting.type === 'number'"
                            v-model.number="settingsForm[setting.key]"
                            type="number"
                            min="1"
                            class="settings-input w-full max-w-40 rounded-md border border-line bg-surface px-3 py-2 text-sm text-ink-900 focus:border-brand-500 focus:outline-none focus:ring-2 focus:ring-brand-500/20"
                          />
                          <BaseSelect
                            v-else-if="setting.type === 'model_ref'"
                            v-model="settingsForm[setting.key]"
                          >
                            <option value="">
                              {{ t('lensAdmin.placeholders.noModel') }}
                            </option>
                            <option
                              v-for="config in llmConfigOptions"
                              :key="config.uuid || config.id"
                              :value="config.uuid || config.id"
                            >
                              {{ formatLLMConfigLabel(config) }}
                            </option>
                          </BaseSelect>
                          <input
                            v-else
                            v-model="settingsForm[setting.key]"
                            type="text"
                            class="settings-input w-full rounded-md border border-line bg-surface px-3 py-2 text-sm text-ink-900 focus:border-brand-500 focus:outline-none focus:ring-2 focus:ring-brand-500/20"
                            :placeholder="setting.placeholder"
                          />
                          <span class="settings-unit w-16 text-sm text-ink-500">
                            {{ setting.unit }}
                          </span>
                        </div>
                      </td>
                    </tr>
                  </tbody>
                </table>
              </div>
            </div>

            <div
              class="mt-4 flex items-center justify-end gap-3 border-t border-line pt-4"
            >
              <BaseButton
                variant="secondary"
                size="sm"
                :disabled="saving"
                @click="resetSettingsForm"
              >
                {{ t('lensAdmin.settings.reset') }}
              </BaseButton>
              <BaseButton
                variant="primary"
                size="sm"
                :loading="saving"
                @click="saveSettings"
              >
                {{ t('lensAdmin.settings.saveChanges') }}
              </BaseButton>
            </div>
            <p v-if="formError" class="mt-2 text-sm text-danger-700">
              {{ formError }}
            </p>
          </template>
        </div>
      </section>
    </div>
  </AdminLayout>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'

import { llmAdminApi } from '@/admin/api/llmAdmin'
import {
  createGlobalSetting,
  listGlobalSettings,
  updateGlobalSetting
} from '@/api/lens'
import { extractErrorMessage } from '@/utils/api'
import AdminLayout from '@/admin/layout/AdminLayout.vue'
import { useToast } from '@/composables/useToast'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseLoading from '@/components/ui/BaseLoading.vue'
import BaseSelect from '@/components/ui/BaseSelect.vue'

import { formatLLMConfigLabel, normalizeList } from './adminHelpers'

const { t } = useI18n()
const { showSuccess, showError } = useToast()

const loading = ref(false)
const saving = ref(false)
const formError = ref('')
const globalSettings = ref([])
const llmConfigOptions = ref([])

const defaultSettings = {
  'lens.datasource_upload.max_bytes': 52428800,
  'lens.datasource_upload.max_extracted_bytes': 104857600,
  'lens.datasource_upload.max_extracted_files': 300,
  'lens.datasource_sync.timeout_s': 360,
  'lens.datasource_sync.workers': 4,
  'lens.datasource_conversion.vision_model_ref': '',
  'lens.datasource_conversion.document_model_ref': ''
}

const settingsForm = ref({ ...defaultSettings })
const initialSettings = ref({ ...defaultSettings })

const settingDefinitions = computed(() => {
  return [
    ...['max_bytes', 'max_extracted_bytes', 'max_extracted_files'].map(
      (key) => ({
        key: `lens.datasource_upload.${key}`,
        group: 'datasource',
        label: t(`lensAdmin.resourceSettings.${key}`),
        description: t(`lensAdmin.resourceSettings.${key}Desc`),
        type: 'number',
        unit: key === 'max_extracted_files' ? '' : 'bytes'
      })
    ),
    {
      key: 'lens.datasource_sync.timeout_s',
      group: 'datasource',
      label: t('lensAdmin.resourceSettings.syncTimeoutTitle'),
      description: t('lensAdmin.resourceSettings.syncTimeoutDesc'),
      type: 'number',
      unit: t('lensAdmin.resourceSettings.minutesUnit'),
      storage: 'seconds'
    },
    {
      key: 'lens.datasource_sync.workers',
      group: 'datasource',
      label: t('lensAdmin.resourceSettings.syncWorkersTitle'),
      description: t('lensAdmin.resourceSettings.syncWorkersDesc'),
      type: 'number',
      unit: t('lensAdmin.resourceSettings.workersUnit')
    },
    {
      key: 'lens.datasource_conversion.vision_model_ref',
      group: 'datasource',
      label: t('lensAdmin.resourceSettings.visionModelTitle'),
      description: t('lensAdmin.resourceSettings.visionModelDesc'),
      type: 'model_ref',
      unit: ''
    },
    {
      key: 'lens.datasource_conversion.document_model_ref',
      group: 'datasource',
      label: t('lensAdmin.resourceSettings.documentModelTitle'),
      description: t('lensAdmin.resourceSettings.documentModelDesc'),
      type: 'model_ref',
      unit: ''
    }
  ]
})

const settingGroupOrder = ['datasource']

const groupedSettings = computed(() =>
  settingGroupOrder
    .map((group) => ({
      group,
      title: t(`lensAdmin.resourceSettings.groups.${group}`),
      items: settingDefinitions.value.filter((item) => item.group === group)
    }))
    .filter((entry) => entry.items.length)
)

async function load() {
  loading.value = true
  formError.value = ''
  try {
    const [settingRows, llmRows] = await Promise.all([
      listGlobalSettings(),
      llmAdminApi.getLLMConfigAll({ scope: 'global' }).catch(() => [])
    ])
    globalSettings.value = normalizeList(settingRows)
    llmConfigOptions.value = normalizeList(llmRows)
    hydrateSettingsForm()
  } catch (error) {
    showError(extractErrorMessage(error, t('lensAdmin.messages.loadFailed')))
  } finally {
    loading.value = false
  }
}

function hydrateSettingsForm() {
  const next = { ...defaultSettings }
  globalSettings.value.forEach((setting) => {
    if (setting.key in next) {
      const definition = settingDefinitions.value.find(
        (item) => item.key === setting.key
      )
      if (setting.value === null || setting.value === undefined) {
        return
      }
      if (['text', 'model_ref'].includes(definition?.type)) {
        next[setting.key] = String(setting.value || '')
        return
      }
      const value = Number(setting.value)
      if (Number.isFinite(value) && value > 0) {
        next[setting.key] =
          definition?.storage === 'seconds' ? Math.ceil(value / 60) : value
      }
    }
  })
  settingsForm.value = { ...next }
  initialSettings.value = { ...next }
}

function resetSettingsForm() {
  settingsForm.value = { ...initialSettings.value }
  formError.value = ''
}

async function saveSettings() {
  saving.value = true
  formError.value = ''
  try {
    for (const setting of settingDefinitions.value) {
      const value =
        setting.type === 'text' || setting.type === 'model_ref'
          ? String(settingsForm.value[setting.key] || '').trim()
          : numericSettingValue(setting)
      const payload = {
        key: setting.key,
        value,
        description: setting.description
      }
      const exists = globalSettings.value.some((row) => row.key === setting.key)
      if (exists) {
        await updateGlobalSetting(setting.key, payload)
      } else {
        await createGlobalSetting(payload)
      }
    }
    showSuccess(t('lensAdmin.messages.saveSuccess'))
    await load()
  } catch (error) {
    formError.value = extractErrorMessage(
      error,
      t('lensAdmin.messages.saveFailed')
    )
    showError(formError.value)
  } finally {
    saving.value = false
  }
}

function numericSettingValue(setting) {
  const formValue = Math.max(
    1,
    Math.round(Number(settingsForm.value[setting.key]) || 1)
  )
  return setting.storage === 'seconds' ? formValue * 60 : formValue
}

onMounted(load)
</script>

<style scoped>
.table-cell {
  @apply px-4 py-3 align-top text-sm text-ink-700;
}

@media (max-width: 767px) {
  .settings-table,
  .settings-table tbody {
    display: block;
    width: 100%;
  }

  .settings-table {
    min-width: 100% !important;
    table-layout: auto;
  }

  .settings-table colgroup {
    display: none;
  }

  .settings-table tr,
  .settings-table .table-cell {
    display: block;
    width: 100%;
  }

  .settings-table .table-cell:first-child {
    @apply pb-2;
  }

  .settings-table .table-cell:last-child {
    @apply pt-0;
  }

  .setting-key {
    overflow-wrap: anywhere;
  }

  .settings-control {
    @apply flex-col items-stretch gap-2;
  }

  .settings-control .settings-input {
    min-width: 0;
    max-width: none;
  }

  .settings-unit {
    @apply w-auto;
  }
}
</style>
