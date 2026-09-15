<template>
  <div
    class="rounded-md border border-line bg-surface p-4 shadow-sm"
    data-testid="task-summary-card"
  >
    <div class="mb-3 flex flex-wrap items-center gap-3">
      <StatusBadge :status="statusKey" />
      <div v-if="hasProgress" class="flex items-center gap-2">
        <div class="h-1.5 w-32 overflow-hidden rounded-full bg-surface-sunken">
          <div
            class="h-full rounded-full bg-primary-500 transition-all"
            :style="{ width: progressWidth }"
          />
        </div>
        <span class="font-mono text-xs text-ink-600">{{ progressValue }}%</span>
      </div>
      <span v-if="progressText" class="text-xs text-ink-600">
        {{ progressText }}
      </span>
    </div>

    <div
      v-if="syncStats.length"
      class="mb-3 grid grid-cols-2 gap-2 sm:grid-cols-4"
    >
      <div
        v-for="item in syncStats"
        :key="item.label"
        class="rounded border border-line bg-surface-sunken px-3 py-2"
      >
        <div
          class="text-[10px] font-medium uppercase tracking-wider text-ink-500"
        >
          {{ item.label }}
        </div>
        <div class="mt-0.5 text-base font-semibold text-ink-900">
          {{ item.value }}
        </div>
      </div>
    </div>

    <div
      v-if="errorText"
      class="rounded border border-red-200 bg-red-50 p-2 text-xs text-red-700"
    >
      <span class="font-semibold">{{ t('taskManagement.list.error') }}:</span>
      <span class="ml-1 break-words">{{ errorText }}</span>
    </div>

    <div class="mt-3 flex justify-end">
      <a
        v-if="taskId"
        :href="`/management/task-management/list?execution_id=${taskId}`"
        target="_blank"
        rel="noopener"
        class="text-xs font-medium text-primary-600 hover:text-primary-700 hover:underline"
      >
        {{ t('lensAdmin.datasourceDetail.details.openInTaskManager') }} →
      </a>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import StatusBadge from '@/components/ui/StatusBadge.vue'

const props = defineProps({
  task: { type: Object, default: null }
})

const { t } = useI18n()

const SYNC_ERROR_KEYS = {
  LENS_SOURCE_RESOURCE_LIMIT_EXCEEDED: 'datasourceResourceLimit',
  LENS_SOURCE_SYNC_TIMEOUT: 'datasourceSyncTimeout',
  LENS_SOURCE_CREDENTIAL_INVALID: 'datasourceCredentialInvalid',
  LENS_SOURCE_CONFIG_INVALID: 'datasourceConfigInvalid',
  LENS_SOURCE_TARGET_PATH_INVALID: 'datasourceTargetPathInvalid',
  LENS_SOURCE_TARGET_PATH_REQUIRED: 'datasourceTargetPathRequired',
  LENS_SOURCE_GIT_LAYOUT_MIGRATION_REQUIRED: 'datasourceGitLayoutMigration'
}

const STATUS_MAP = {
  PENDING: 'pending',
  STARTED: 'processing',
  SUCCESS: 'success',
  FAILURE: 'failed',
  RETRY: 'processing',
  REVOKED: 'cancelled'
}

function readField(obj, path) {
  if (!obj) return undefined
  return path.split('.').reduce((acc, k) => (acc == null ? acc : acc[k]), obj)
}

const taskId = computed(() => props.task?.id ?? null)
const statusKey = computed(() => {
  const s = props.task?.status
  if (!s) return 'pending'
  return STATUS_MAP[s] || String(s).toLowerCase() || 'pending'
})
const progressValue = computed(() => {
  const v = readField(props.task, 'metadata.progress_percent')
  if (v == null) return null
  const n = Number(v)
  return Number.isFinite(n) ? n : null
})
const hasProgress = computed(() => progressValue.value != null)
const progressWidth = computed(() => {
  const n = progressValue.value
  if (n == null) return '0%'
  return `${Math.max(0, Math.min(100, n))}%`
})
const progressText = computed(() => {
  const step = readField(props.task, 'metadata.progress_step')
  const msg = readField(props.task, 'metadata.progress_message')
  if (step && msg) return `${step} · ${msg}`
  return msg || step || ''
})
const errorText = computed(() => {
  const raw = props.task?.error || readField(props.task, 'metadata.error') || ''
  const code = String(raw).split(':', 1)[0].trim().toUpperCase()
  const key = SYNC_ERROR_KEYS[code]
  return key ? t(`lensNodeErrors.${key}`) : raw
})
const itemResults = computed(() => {
  const steps = readField(props.task, 'metadata.steps')
  const logs = readField(props.task, 'metadata.logs')
  const list =
    Array.isArray(steps) && steps.length > 0
      ? steps
      : Array.isArray(logs)
        ? logs
        : []
  return list.filter((entry) =>
    ['item_done', 'item_failed'].includes(entry.name || entry.step)
  )
})

const syncStats = computed(() => {
  const m = readField(props.task, 'metadata') || {}
  if (!m.sync_summary && !m.type && !itemResults.value.length) return []
  const summary = m.sync_summary || {}
  const done = itemResults.value.filter((it) => it.status === 'done').length
  const failed = itemResults.value.filter((it) => it.status === 'failed').length
  return [
    {
      label: t('lensAdmin.datasourceDetail.details.statSuccess'),
      value: summary.documents ?? done
    },
    {
      label: t('lensAdmin.datasourceDetail.details.statFailed'),
      value: summary.failed ?? failed
    },
    {
      label: t('lensAdmin.datasourceDetail.details.statFolders'),
      value: summary.folders ?? 0
    },
    {
      label: t('lensAdmin.datasourceDetail.details.statFiles'),
      value: summary.files ?? done
    }
  ]
})
</script>
