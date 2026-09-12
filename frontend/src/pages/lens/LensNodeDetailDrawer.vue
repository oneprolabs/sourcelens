<template>
  <BaseDrawer
    :show="show"
    :title="t('lensAdmin.detail.title')"
    :subtitle="node?.name || ''"
    @close="$emit('close')"
  >
    <div v-if="node" class="space-y-6">
      <!-- Header: id + runtime status -->
      <div class="flex items-center justify-between">
        <span class="font-mono text-xs text-ink-400">
          {{ compactUuid(node.uuid) }}
        </span>
        <StatusBadge :status="node.status" />
      </div>

      <section class="space-y-3" data-testid="lensnode-resources">
        <h3 class="text-sm font-semibold text-ink-900">
          {{ t('lensAdmin.detail.resources') }}
        </h3>
        <div class="grid grid-cols-3 gap-2">
          <div
            v-for="item in resourceCards"
            :key="item.key"
            class="rounded-lg border border-line bg-surface-sunken px-2 py-3 text-center"
          >
            <component :is="item.icon" class="mx-auto h-4 w-4 text-brand-600" />
            <div class="mt-1 truncate text-sm font-semibold text-ink-900">
              {{ item.value }}
            </div>
            <div class="mt-0.5 text-[11px] text-ink-500">{{ item.label }}</div>
          </div>
        </div>
      </section>

      <section
        v-if="supportedTasks.length"
        class="space-y-3"
        data-testid="lensnode-capabilities"
      >
        <h3 class="text-sm font-semibold text-ink-900">
          {{ t('lensAdmin.detail.supportedTasks') }}
        </h3>
        <div class="flex flex-wrap gap-1.5">
          <span
            v-for="task in supportedTasks"
            :key="task"
            class="rounded border border-primary-200 bg-primary-50 px-2 py-1 text-xs text-primary-700"
          >
            {{ task }}
          </span>
        </div>
      </section>

      <section
        v-if="activeDatasourceOperations.length"
        class="space-y-3"
        data-testid="lensnode-datasources"
      >
        <h3 class="text-sm font-semibold text-ink-900">
          {{ t('lensAdmin.detail.datasourceTasks') }}
        </h3>
        <ul class="detail-list">
          <li
            v-for="operation in activeDatasourceOperations"
            :key="operation.task_id"
            class="space-y-1.5 px-3 py-2.5"
          >
            <div class="flex items-center justify-between gap-3">
              <span class="min-w-0 truncate text-sm text-ink-700">
                {{
                  operation.datasource_name ||
                  operation.datasource_uuid ||
                  t('lensAdmin.detail.nodeEmpty')
                }}
              </span>
              <StatusBadge status="running" />
            </div>
            <div
              class="flex items-center justify-between gap-3 text-[11px] text-ink-500"
            >
              <span>{{
                operation.operation || t('lensAdmin.detail.datasourceOperation')
              }}</span>
              <span>{{ operationProgress(operation) }}</span>
            </div>
          </li>
        </ul>
      </section>

      <section class="space-y-3">
        <div class="grid grid-cols-2 gap-3 sm:grid-cols-3">
          <div
            v-for="item in nodeMetricCards"
            :key="item.label"
            class="rounded-lg border border-line bg-surface-sunken px-3 py-2.5"
          >
            <div class="text-xs text-ink-500">{{ item.label }}</div>
            <div class="mt-1 text-lg font-semibold tabular-nums text-ink-900">
              {{ item.value }}
            </div>
          </div>
        </div>
        <div class="space-y-3">
          <div class="rounded-lg border border-line bg-surface px-4 py-3">
            <div class="flex items-center justify-between gap-3">
              <span class="text-xs font-medium text-ink-500">
                {{ nodeInfoRows[0].label }}
              </span>
              <span class="text-[11px] text-ink-400">
                {{ t('lensAdmin.detail.runtimeLocation') }}
              </span>
            </div>
            <div class="mt-2 break-all font-mono text-sm text-ink-900">
              {{ nodeInfoRows[0].value }}
            </div>
          </div>
          <div class="grid grid-cols-2 gap-3">
            <div
              v-for="item in nodeInfoRows.slice(1)"
              :key="item.label"
              class="rounded-lg border border-line bg-surface px-3 py-3"
            >
              <div class="text-xs text-ink-500">{{ item.label }}</div>
              <div class="mt-1.5 break-words text-sm font-medium text-ink-800">
                {{ item.value }}
              </div>
            </div>
          </div>
        </div>
      </section>

      <section class="space-y-3" data-testid="lensnode-existing-datasources">
        <h3 class="text-sm font-semibold text-ink-900">
          {{ t('lensAdmin.detail.existingDatasources') }}
        </h3>
        <ul v-if="node.datasources?.length" class="detail-list">
          <li v-for="datasource in node.datasources" :key="datasource.uuid" class="px-3 py-2.5">
            <div class="flex items-center justify-between gap-3">
              <span class="min-w-0 truncate text-sm font-medium text-ink-800">{{ datasource.name }}</span>
              <StatusBadge :status="datasource.status" />
            </div>
            <div class="mt-1 text-xs text-ink-500">{{ datasource.source_type }}</div>
          </li>
        </ul>
        <p v-else class="detail-empty">{{ t('lensAdmin.detail.noDatasources') }}</p>
      </section>
    </div>
  </BaseDrawer>
</template>

<script setup>
import { computed } from 'vue'
import { Cpu, HardDrive, MemoryStick } from '@lucide/vue'
import { useI18n } from 'vue-i18n'

import { formatOperationMetric } from '@/admin/utils/operationsSummary'
import BaseDrawer from '@/components/ui/BaseDrawer.vue'
import StatusBadge from '@/components/ui/StatusBadge.vue'

import { compactUuid } from './adminHelpers'
import { useShortDateTime } from './useShortDateTime'

const props = defineProps({
  show: Boolean,
  node: {
    type: Object,
    default: null
  }
})

defineEmits(['close'])

const { t } = useI18n()
const formatDateTime = useShortDateTime()

const metricValue = (keys) => {
  const metrics = props.node?.last_metrics || {}
  const value = keys
    .map((key) => metrics[key])
    .find((item) => item !== undefined && item !== null)
  if (value === undefined) return t('lensAdmin.detail.notReported')
  return typeof value === 'number' ? `${Math.round(value)}%` : String(value)
}
const resourceCards = computed(() => [
  {
    key: 'cpu',
    label: t('lensAdmin.detail.cpu'),
    value: metricValue(['cpu_percent', 'cpu_usage']),
    icon: Cpu
  },
  {
    key: 'memory',
    label: t('lensAdmin.detail.memory'),
    value: metricValue(['memory_percent', 'memory_usage']),
    icon: MemoryStick
  },
  {
    key: 'disk',
    label: t('lensAdmin.detail.disk'),
    value: metricValue(['disk_percent', 'disk_usage']),
    icon: HardDrive
  }
])
const supportedTasks = computed(() => {
  const tasks = Array.isArray(props.node?.tasks) ? props.node.tasks : []
  return tasks
    .map((task) => (typeof task === 'string' ? task : task.name || task.title))
    .filter(Boolean)
})
const activeDatasourceOperations = computed(() => {
  const operations = props.node?.active_datasource_operations
  return Array.isArray(operations)
    ? operations.filter(
        (operation) => operation && typeof operation === 'object'
      )
    : []
})

function operationProgress(operation) {
  const progress = operation.last_progress || {}
  if (typeof progress.progress_percent === 'number') {
    return `${Math.round(progress.progress_percent)}%`
  }
  if (
    typeof progress.progress_current === 'number' &&
    typeof progress.progress_total === 'number' &&
    progress.progress_total > 0
  ) {
    return `${progress.progress_current}/${progress.progress_total}`
  }
  return operation.phase || t('lensAdmin.detail.notReported')
}

const nodeMetricCards = computed(() => {
  const node = props.node || {}
  return [
    {
      label: t('lensAdmin.detail.totalRuns'),
      value: formatOperationMetric(node.total_run_count)
    },
    {
      label: t('lensAdmin.detail.succeededRuns'),
      value: formatOperationMetric(node.succeeded_run_count)
    },
    {
      label: t('lensAdmin.detail.failedRuns'),
      value: formatOperationMetric(node.failed_run_count)
    },
    {
      label: t('lensAdmin.detail.totalTokens'),
      value: formatOperationMetric(node.total_tokens)
    },
    {
      label: t('lensAdmin.detail.activeRuns'),
      value: formatOperationMetric(node.active_run_count)
    },
    {
      label: t('lensAdmin.detail.queuedRuns'),
      value: formatOperationMetric(node.queued_run_count)
    }
  ]
})

const nodeInfoRows = computed(() => {
  const node = props.node || {}
  return [
    {
      label: t('lensAdmin.detail.workspace'),
      value: node.workspace_path || t('lensAdmin.detail.notReported')
    },
    {
      label: t('lensAdmin.detail.agentVersion'),
      value: node.agent_version || t('lensAdmin.detail.notReported')
    },
    {
      label: t('lensAdmin.detail.protocolVersion'),
      value: node.protocol_version || t('lensAdmin.detail.notReported')
    },
    {
      label: t('lensAdmin.detail.lastHeartbeat'),
      value: formatDateTime(node.last_heartbeat_at)
    },
    {
      label: t('lensAdmin.detail.lastRun'),
      value: formatDateTime(node.last_run_at)
    }
  ]
})

</script>

<style scoped>
.detail-list {
  @apply divide-y divide-line overflow-hidden rounded-lg border border-line;
}
</style>
