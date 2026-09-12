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
                {{ t('lensAdmin.pages.settings.title') }}
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
                            class="min-w-0 max-w-lg"
                          >
                            <option value="">
                              {{ t('lensAdmin.placeholders.noModel') }}
                            </option>
                            <option
                              v-for="config in llmConfigOptions"
                              :key="config.uuid"
                              :value="config.uuid"
                            >
                              {{ formatLLMConfigLabel(config) }}
                            </option>
                          </BaseSelect>
                          <input
                            v-else-if="setting.type === 'text'"
                            v-model="settingsForm[setting.key]"
                            type="text"
                            :placeholder="setting.placeholder"
                            class="settings-input min-w-0 w-full max-w-lg rounded-md border border-line bg-surface px-3 py-2 text-sm text-ink-900 focus:border-brand-500 focus:outline-none focus:ring-2 focus:ring-brand-500/20"
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
              class="scheduled-tasks mt-6 overflow-hidden rounded-lg border border-line"
            >
              <div class="border-b border-line px-4 py-3">
                <h3 class="text-sm font-semibold text-ink-900">
                  {{ t('lensAdmin.tasks.title') }}
                </h3>
                <p class="mt-1 text-sm text-ink-500">
                  {{ t('lensAdmin.tasks.description') }}
                </p>
              </div>
              <table
                class="scheduled-tasks-table min-w-full divide-y divide-line"
              >
                <thead class="bg-surface-sunken">
                  <tr>
                    <th class="table-head">
                      {{ t('lensAdmin.tasks.task') }}
                    </th>
                    <th class="table-head">
                      {{ t('lensAdmin.tasks.enabled') }}
                    </th>
                    <th class="table-head">
                      {{ t('lensAdmin.tasks.lastRun') }}
                    </th>
                    <th class="table-head">
                      {{ t('lensAdmin.tasks.lastStatus') }}
                    </th>
                  </tr>
                </thead>
                <tbody class="divide-y divide-line bg-surface">
                  <tr
                    v-for="task in defaultScheduledTasks"
                    :key="task.task_type"
                    class="align-top transition-colors hover:bg-line-soft"
                  >
                    <td class="table-cell">
                      <div class="text-sm font-semibold text-ink-900">
                        {{ task.label }}
                      </div>
                      <p class="mt-1 text-sm leading-6 text-ink-500">
                        {{ task.description }}
                      </p>
                      <p class="task-key mt-1 font-mono text-xs text-ink-400">
                        {{ task.task_type }}
                      </p>
                    </td>
                    <td
                      class="task-detail table-cell"
                      :data-label="t('lensAdmin.tasks.enabled')"
                    >
                      <label class="inline-flex items-center gap-2">
                        <input
                          :checked="task.enabled"
                          :disabled="taskSaving[task.task_type]"
                          type="checkbox"
                          class="h-4 w-4 rounded border-line text-brand-600 focus:ring-brand-500"
                          @change="
                            updateScheduledTaskEnabled(
                              task.task_type,
                              $event.target.checked
                            )
                          "
                        />
                        <span class="text-sm text-ink-600">
                          {{
                            task.enabled
                              ? t('common.status.enabled')
                              : t('common.status.disabled')
                          }}
                        </span>
                      </label>
                    </td>
                    <td
                      class="task-detail table-cell text-sm text-ink-600"
                      :data-label="t('lensAdmin.tasks.lastRun')"
                    >
                      {{ formatDateTime(task.last_run_at) }}
                    </td>
                    <td
                      class="task-detail table-cell"
                      :data-label="t('lensAdmin.tasks.lastStatus')"
                    >
                      <StatusBadge :status="task.last_status || 'unknown'" />
                    </td>
                  </tr>
                </tbody>
              </table>
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
import { extractErrorMessage } from '@/utils/api'
import AdminLayout from '@/admin/layout/AdminLayout.vue'
import {
  createGlobalSetting,
  getSystemHealth,
  listGlobalSettings,
  updateGlobalSetting,
  updateSystemTaskEnabled
} from '@/api/lens'
import { useToast } from '@/composables/useToast'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseLoading from '@/components/ui/BaseLoading.vue'
import BaseSelect from '@/components/ui/BaseSelect.vue'
import StatusBadge from '@/components/ui/StatusBadge.vue'

import {
  DEFAULT_NODE_IMAGE,
  formatLLMConfigLabel,
  normalizeList
} from './adminHelpers'
import { useShortDateTime } from './useShortDateTime'

const { t } = useI18n()
const { showSuccess, showError } = useToast()
const formatDateTime = useShortDateTime()

const loading = ref(false)
const saving = ref(false)
const formError = ref('')
const llmConfigOptions = ref([])
const globalSettings = ref([])
const systemHealth = ref([])
const taskSaving = ref({})

const defaultSettings = {
  public_base_url: '',
  'lensnode.image': '',
  'lensnode.defaults.timeout': 600,
  'retention.run_days': 90,
  'lensnode.health.offline_threshold_s': 120,
  'lensnode_cleanup.interval_seconds': 3600,
  'lensnode_health.interval_seconds': 60,
  'run_retention.interval_seconds': 86400,
  'lens.skills.generator_model_ref': '',
  'lens.smart_collaboration.model_ref': ''
}

const settingsForm = ref({ ...defaultSettings })
const initialSettings = ref({ ...defaultSettings })

const defaultScheduledTaskMeta = {
  lensnode_cleanup: {
    label: () => t('lensAdmin.tasks.cleanup'),
    description: () => t('lensAdmin.tasks.cleanupDesc')
  },
  lensnode_health: {
    label: () => t('lensAdmin.tasks.health'),
    description: () => t('lensAdmin.tasks.healthDesc')
  },
  run_retention: {
    label: () => t('lensAdmin.tasks.retention'),
    description: () => t('lensAdmin.tasks.retentionDesc')
  }
}

const defaultScheduledTasks = computed(() => {
  const taskTypes = ['lensnode_cleanup', 'lensnode_health', 'run_retention']
  return taskTypes.map((taskType) => {
    const existing =
      systemHealth.value.find((row) => row.task_type === taskType) || {}
    const meta = defaultScheduledTaskMeta[taskType]
    return {
      task_type: taskType,
      label: meta.label(),
      description: meta.description(),
      enabled: existing.enabled !== false,
      last_run_at: existing.last_run_at || null,
      last_status: existing.last_status || null
    }
  })
})

const settingDefinitions = computed(() => {
  return [
    {
      key: 'public_base_url',
      group: 'deploy',
      label: t('lensAdmin.settings.publicBaseUrlTitle'),
      description: t('lensAdmin.settings.publicBaseUrlDesc'),
      type: 'text',
      unit: '',
      placeholder: window.location.origin
    },
    {
      key: 'lensnode.image',
      group: 'deploy',
      label: t('lensAdmin.settings.nodeImageTitle'),
      description: t('lensAdmin.settings.nodeImageDesc'),
      type: 'text',
      unit: '',
      placeholder: DEFAULT_NODE_IMAGE
    },
    {
      key: 'lensnode.defaults.timeout',
      group: 'runtime',
      label: t('lensAdmin.settings.timeoutTitle'),
      description: t('lensAdmin.settings.timeoutDesc'),
      type: 'number',
      unit: t('lensAdmin.settings.secondsUnit')
    },
    {
      key: 'lens.smart_collaboration.model_ref',
      group: 'runtime',
      label: t('lensAdmin.settings.smartCollaborationModelTitle'),
      description: t('lensAdmin.settings.smartCollaborationModelDesc'),
      type: 'model_ref',
      unit: ''
    },
    {
      key: 'retention.run_days',
      group: 'runtime',
      label: t('lensAdmin.settings.retentionTitle'),
      description: t('lensAdmin.settings.retentionDesc'),
      type: 'number',
      unit: t('lensAdmin.settings.daysUnit')
    },
    {
      key: 'lensnode.health.offline_threshold_s',
      group: 'health',
      label: t('lensAdmin.settings.offlineTitle'),
      description: t('lensAdmin.settings.offlineDesc'),
      type: 'number',
      unit: t('lensAdmin.settings.secondsUnit')
    },
    {
      key: 'lensnode_cleanup.interval_seconds',
      group: 'health',
      label: t('lensAdmin.settings.cleanupIntervalTitle'),
      description: t('lensAdmin.settings.cleanupIntervalDesc'),
      type: 'number',
      unit: t('lensAdmin.settings.secondsUnit')
    },
    {
      key: 'lensnode_health.interval_seconds',
      group: 'health',
      label: t('lensAdmin.settings.healthIntervalTitle'),
      description: t('lensAdmin.settings.healthIntervalDesc'),
      type: 'number',
      unit: t('lensAdmin.settings.secondsUnit')
    },
    {
      key: 'run_retention.interval_seconds',
      group: 'health',
      label: t('lensAdmin.settings.retentionIntervalTitle'),
      description: t('lensAdmin.settings.retentionIntervalDesc'),
      type: 'number',
      unit: t('lensAdmin.settings.secondsUnit')
    },
    {
      key: 'lens.skills.generator_model_ref',
      group: 'advanced',
      label: t('lensAdmin.settings.skillGeneratorModelTitle'),
      description: t('lensAdmin.settings.skillGeneratorModelDesc'),
      type: 'model_ref',
      unit: ''
    }
  ]
})

const settingGroupOrder = ['deploy', 'runtime', 'health', 'advanced']

const groupedSettings = computed(() =>
  settingGroupOrder
    .map((group) => ({
      group,
      title: t(`lensAdmin.settings.groups.${group}`),
      items: settingDefinitions.value.filter((item) => item.group === group)
    }))
    .filter((entry) => entry.items.length)
)

async function load() {
  loading.value = true
  formError.value = ''
  try {
    const [settingRows, healthRows, llmRows] = await Promise.all([
      listGlobalSettings(),
      getSystemHealth(),
      llmAdminApi.getLLMConfigAll({ scope: 'global' }).catch(() => [])
    ])

    globalSettings.value = normalizeList(settingRows)
    systemHealth.value = normalizeList(healthRows)
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
      next[setting.key] =
        definition?.type === 'number'
          ? Number(setting.value ?? next[setting.key])
          : (setting.value ?? next[setting.key])
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
        setting.type === 'number'
          ? Math.max(1, Number(settingsForm.value[setting.key]) || 1)
          : settingsForm.value[setting.key] || ''
      const payload = {
        key: setting.key,
        value,
        description: setting.description
      }
      if (globalSettings.value.some((item) => item.key === setting.key)) {
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

async function updateScheduledTaskEnabled(taskType, enabled) {
  taskSaving.value = { ...taskSaving.value, [taskType]: true }
  try {
    await updateSystemTaskEnabled(taskType, enabled)
    await load()
    showSuccess(t('lensAdmin.messages.saveSuccess'))
  } catch (error) {
    showError(extractErrorMessage(error, t('lensAdmin.messages.saveFailed')))
  } finally {
    taskSaving.value = { ...taskSaving.value, [taskType]: false }
  }
}

onMounted(load)
</script>

<style scoped>
.table-head {
  @apply border-b border-line px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-ink-500;
}

.table-cell {
  @apply px-4 py-3 align-top text-sm text-ink-700;
}

@media (max-width: 767px) {
  .settings-table,
  .settings-table tbody,
  .scheduled-tasks-table,
  .scheduled-tasks-table tbody {
    display: block;
    width: 100%;
  }

  .settings-table,
  .scheduled-tasks-table {
    min-width: 100% !important;
    table-layout: auto;
  }

  .settings-table colgroup {
    display: none;
  }

  .scheduled-tasks-table thead {
    position: absolute;
    width: 1px;
    height: 1px;
    padding: 0;
    margin: -1px;
    overflow: hidden;
    clip: rect(0, 0, 0, 0);
    white-space: nowrap;
    border: 0;
  }

  .settings-table tr,
  .settings-table .table-cell,
  .scheduled-tasks-table tr {
    display: block;
    width: 100%;
  }

  .settings-table .table-cell:first-child {
    @apply pb-2;
  }

  .settings-table .table-cell:last-child {
    @apply pt-0;
  }

  .setting-key,
  .task-key {
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

  .scheduled-tasks {
    @apply overflow-x-hidden;
  }

  .scheduled-tasks-table .table-cell {
    display: block;
    width: 100%;
  }

  .scheduled-tasks-table .task-detail {
    display: grid;
    grid-template-columns: minmax(5rem, 36%) minmax(0, 1fr);
    @apply gap-3 border-t border-line py-3;
  }

  .scheduled-tasks-table .task-detail::before {
    content: attr(data-label);
    @apply text-xs font-semibold uppercase tracking-wide text-ink-500;
  }
}
</style>
