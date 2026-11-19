<template>
  <AdminLayout>
    <div class="flex h-full min-h-0 max-w-full flex-col gap-4 py-4">
      <section
        class="flex max-h-full min-h-0 w-full flex-col overflow-hidden rounded-lg border border-line bg-surface shadow-sm"
      >
        <div class="flex flex-shrink-0 items-start justify-between gap-3 border-b border-line px-5 py-4">
          <div class="min-w-0">
            <h1 class="text-xl font-semibold text-ink-900">
              {{ t('management.platformStatsTitle') }}
            </h1>
            <p class="mt-2 max-w-3xl text-sm leading-6 text-ink-500">
              {{ t('management.platformStatsSubtitle') }}
            </p>
          </div>
          <BaseButton variant="outline" size="sm" @click="exportCsv">
            {{ t('management.platformStatsExport') }}
          </BaseButton>
        </div>

        <BaseLoading v-if="loading && !rows.length" />

        <div v-else class="min-h-0 flex-1 overflow-auto px-5 py-4">
          <div v-if="error" class="mb-4 rounded-md border border-danger-200 bg-danger-50 px-4 py-3 text-sm text-danger-700">
            {{ error }}
          </div>

          <div class="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <article
              v-for="card in cards"
              :key="card.key"
              class="rounded-lg border border-line bg-surface p-4"
            >
              <p class="text-xs font-medium uppercase tracking-wide text-ink-500">
                {{ card.label }}
              </p>
              <p class="mt-2 text-2xl font-semibold text-ink-900">{{ card.value }}</p>
              <p v-if="card.hint" class="mt-1 text-xs text-ink-500">{{ card.hint }}</p>
            </article>
          </div>

          <div
            v-if="highFailureRows.length"
            class="mt-4 rounded-md border border-danger-200 bg-danger-50 px-4 py-3 text-sm text-danger-700"
          >
            {{ t('management.platformStatsHighFailure', {
              count: highFailureRows.length,
              tenants: highFailureNames
            }) }}
          </div>

          <div class="mt-4 flex flex-wrap items-end gap-3">
            <label class="min-w-0 flex-1 sm:max-w-xs">
              <span class="mb-1 block text-sm font-medium text-ink-700">
                {{ t('common.search') }}
              </span>
              <input
                v-model="search"
                type="text"
                class="form-input"
                :placeholder="t('management.platformStatsSearch')"
              />
            </label>
            <div class="flex flex-wrap gap-1 rounded-lg border border-line bg-surface-sunken p-1">
              <button
                v-for="segment in segments"
                :key="segment.value"
                type="button"
                class="rounded-md px-3 py-1.5 text-sm font-medium"
                :class="statusFilter === segment.value ? 'bg-surface text-ink-900 shadow-sm' : 'text-ink-500 hover:text-ink-700'"
                @click="statusFilter = segment.value"
              >
                {{ segment.label }} · {{ segment.count }}
              </button>
            </div>
          </div>

          <div class="relative mt-3 overflow-auto rounded-lg border border-line bg-surface">
            <table class="w-full min-w-[1100px] divide-y divide-line">
              <thead class="sticky top-0 z-10 bg-surface-sunken">
                <tr>
                  <th class="table-head">{{ t('management.platformTenant') }}</th>
                  <th class="table-head text-right">{{ t('management.statsActiveMembers') }}</th>
                  <th class="table-head text-right">{{ t('management.statsAssistantsData') }}</th>
                  <th
                    v-for="column in sortableColumns"
                    :key="column.key"
                    class="table-head cursor-pointer select-none text-right"
                    @click="toggleSort(column.key)"
                  >
                    <span class="inline-flex items-center gap-1">
                      {{ t(column.label) }}
                      <span v-if="sortKey === column.key" class="text-[10px] text-ink-500">
                        {{ sortDir === 'asc' ? '▲' : '▼' }}
                      </span>
                    </span>
                  </th>
                  <th class="table-head text-right">{{ t('common.actions') }}</th>
                </tr>
              </thead>
              <tbody class="divide-y divide-line bg-surface">
                <tr v-for="row in visibleRows" :key="row.uuid" class="hover:bg-line-soft">
                  <td class="px-4 py-3">
                    <div class="flex items-center gap-3">
                      <span class="flex h-9 w-9 items-center justify-center rounded-lg bg-brand-50 text-sm font-semibold text-brand-700">
                        {{ tenantInitial(row) }}
                      </span>
                      <div class="min-w-0">
                        <p class="truncate text-sm font-medium text-ink-900">{{ row.name }}</p>
                        <span
                          class="mt-0.5 inline-flex rounded-full px-2 py-0.5 text-xs font-medium"
                          :class="tenantStatusClass(row.status)"
                        >
                          {{ tenantStatusLabel(row.status) }}
                        </span>
                      </div>
                    </div>
                  </td>
                  <td class="px-4 py-3 text-right font-mono text-sm text-ink-700">
                    {{ row.active_member_count }} / {{ row.member_count }}
                  </td>
                  <td class="px-4 py-3 text-right font-mono text-sm text-ink-700">
                    {{ row.assistant_count }} / {{ row.datasource_count }}
                  </td>
                  <td class="px-4 py-3 text-right font-mono text-sm text-ink-700">
                    {{ formatNumber(row.run_count) }}
                  </td>
                  <td class="px-4 py-3 text-right font-mono text-sm text-ink-700">
                    {{ formatNumber(row.tokens) }}
                  </td>
                  <td class="px-4 py-3 text-right font-mono text-sm text-ink-700">
                    {{ formatCosts(row.costs) }}
                  </td>
                  <td class="px-4 py-3 text-right font-mono text-sm" :class="failureClass(row)">
                    {{ row.run_count ? `${row.fail_rate}%` : '—' }}
                  </td>
                  <td class="px-4 py-3 text-right font-mono text-sm text-ink-700">
                    {{ formatDuration(row.p95_duration_ms) }}
                  </td>
                  <td class="px-4 py-3 text-right font-mono text-sm text-ink-700">
                    {{ row.like_rate == null ? '—' : `${row.like_rate}%` }}
                  </td>
                  <td class="px-4 py-3 text-right font-mono text-sm text-ink-700">
                    {{ row.running_count }}
                  </td>
                  <td class="px-4 py-3 text-right">
                    <BaseButton
                      size="sm"
                      variant="outline"
                      :loading="startingTenantId === row.uuid"
                      :disabled="!!startingTenantId"
                      @click="enterTenant(row.uuid)"
                    >
                      {{ t('management.enterTenant') }}
                    </BaseButton>
                  </td>
                </tr>
              </tbody>
            </table>
            <p v-if="!visibleRows.length" class="px-4 py-10 text-center text-sm text-ink-500">
              {{ t('management.platformStatsEmpty') }}
            </p>
          </div>

          <section class="mt-6 rounded-lg border border-line bg-surface">
            <div class="border-b border-line px-5 py-3">
              <h2 class="text-sm font-semibold text-ink-900">{{ t('management.statsMetricsTitle') }}</h2>
              <p class="mt-1 text-xs text-ink-500">{{ t('management.statsMetricsHint') }}</p>
            </div>
            <table class="w-full divide-y divide-line">
              <thead class="bg-surface-sunken">
                <tr>
                  <th class="table-head">{{ t('management.statsMetric') }}</th>
                  <th class="table-head">{{ t('management.statsDefinition') }}</th>
                  <th class="table-head">{{ t('management.statsSource') }}</th>
                  <th class="table-head">{{ t('management.statsStatus') }}</th>
                </tr>
              </thead>
              <tbody class="divide-y divide-line">
                <tr v-for="metric in metrics" :key="metric.key">
                  <td class="px-4 py-3 text-sm font-medium text-ink-800">{{ t(metric.name) }}</td>
                  <td class="px-4 py-3 text-sm text-ink-600">{{ t(metric.definition) }}</td>
                  <td class="px-4 py-3 font-mono text-xs text-ink-500">{{ metric.source }}</td>
                  <td class="px-4 py-3">
                    <span
                      class="inline-flex rounded-full px-2 py-0.5 text-xs font-medium"
                      :class="metric.ready ? 'bg-success-50 text-success-700' : 'bg-warning-50 text-warning-700'"
                    >
                      {{ metric.ready ? t('management.statsReady') : t('management.statsPending') }}
                    </span>
                  </td>
                </tr>
              </tbody>
            </table>
          </section>
        </div>
      </section>
    </div>
  </AdminLayout>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'

import AdminLayout from '@/admin/layout/AdminLayout.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseLoading from '@/components/ui/BaseLoading.vue'
import { getPlatformTenantStats } from '@/api/platform'
import { useEnterTenant } from './useEnterTenant'

const { t } = useI18n()
const { enterTenant, startingTenantId } = useEnterTenant()

const rows = ref([])
const loading = ref(false)
const error = ref('')
const search = ref('')
const statusFilter = ref('')
const sortKey = ref('run_count')
const sortDir = ref('desc')

const sortableColumns = [
  { key: 'run_count', label: 'management.statsRuns' },
  { key: 'tokens', label: 'management.statsTokens' },
  { key: 'fail_rate', label: 'management.statsFailRate' },
  { key: 'p95_duration_ms', label: 'management.statsP95' },
  { key: 'like_rate', label: 'management.statsLikeRate' },
  { key: 'running_count', label: 'management.statsRunning' }
]

const metrics = [
  {
    key: 'runs',
    name: 'management.metricRunsName',
    definition: 'management.metricRunsDefinition',
    source: 'Run.created_at',
    ready: true
  },
  {
    key: 'failure',
    name: 'management.metricFailureName',
    definition: 'management.metricFailureDefinition',
    source: 'Run.status / Run.outcome',
    ready: true
  },
  {
    key: 'duration',
    name: 'management.metricDurationName',
    definition: 'management.metricDurationDefinition',
    source: 'Run.started_at / finished_at',
    ready: true
  },
  {
    key: 'feedback',
    name: 'management.metricFeedbackName',
    definition: 'management.metricFeedbackDefinition',
    source: 'Run.feedback',
    ready: true
  },
  {
    key: 'tokens',
    name: 'management.metricTokensName',
    definition: 'management.metricTokensDefinition',
    source: 'LLMUsage / LLMUsageScope',
    ready: true
  },
  {
    key: 'active',
    name: 'management.metricActiveName',
    definition: 'management.metricActiveDefinition',
    source: 'Run / Session.user',
    ready: true
  },
  {
    key: 'storage',
    name: 'management.metricStorageName',
    definition: 'management.metricStorageDefinition',
    source: 'LensNode.last_metrics',
    ready: false
  },
  {
    key: 'keyusage',
    name: 'management.metricKeyUsageName',
    definition: 'management.metricKeyUsageDefinition',
    source: 'TenantApiKey.last_used_at',
    ready: false
  }
]

const cards = computed(() => {
  const activeTenants = rows.value.filter((row) => row.status === 'active').length
  const runs = rows.value.reduce((total, row) => total + row.run_count, 0)
  const tokens = rows.value.reduce((total, row) => total + row.tokens, 0)
  const activeMembers = rows.value.reduce((total, row) => total + row.active_member_count, 0)
  return [
    {
      key: 'tenants',
      label: t('management.platformStatsActiveTenants'),
      value: `${activeTenants} / ${rows.value.length}`
    },
    {
      key: 'runs',
      label: t('management.statsRuns'),
      value: formatNumber(runs),
      hint: t('management.platformStatsWindowHint')
    },
    {
      key: 'tokens',
      label: t('management.statsTokens'),
      value: formatNumber(tokens),
      hint: t('management.platformStatsWindowHint')
    },
    {
      key: 'members',
      label: t('management.platformStatsActiveMembers'),
      value: formatNumber(activeMembers),
      hint: t('management.platformStatsWindowHint')
    }
  ]
})

const highFailureRows = computed(() =>
  rows.value.filter((row) => row.run_count >= 5 && row.fail_rate >= 5)
)

const highFailureNames = computed(() =>
  highFailureRows.value
    .slice(0, 3)
    .map((row) => row.name)
    .join('、')
)

const segments = computed(() => [
  { value: '', label: t('management.allStatuses'), count: rows.value.length },
  {
    value: 'active',
    label: t('management.tenantActive'),
    count: rows.value.filter((row) => row.status === 'active').length
  },
  {
    value: 'pending',
    label: t('management.tenantPending'),
    count: rows.value.filter((row) => row.status === 'pending').length
  },
  {
    value: 'suspended',
    label: t('management.tenantSuspended'),
    count: rows.value.filter((row) => row.status === 'suspended').length
  }
])

const visibleRows = computed(() => {
  const keyword = search.value.trim().toLowerCase()
  const filtered = rows.value.filter((row) => {
    if (statusFilter.value && row.status !== statusFilter.value) return false
    if (!keyword) return true
    return (
      row.name.toLowerCase().includes(keyword) ||
      row.slug.toLowerCase().includes(keyword)
    )
  })
  const multiplier = sortDir.value === 'asc' ? 1 : -1
  return [...filtered].sort((left, right) => {
    const leftValue = left[sortKey.value] ?? -1
    const rightValue = right[sortKey.value] ?? -1
    if (leftValue === rightValue) return left.name.localeCompare(right.name)
    return leftValue > rightValue ? multiplier : -multiplier
  })
})

function tenantInitial(row) {
  return (row.name || row.slug || 'T').trim().charAt(0).toUpperCase()
}

function tenantStatusLabel(status) {
  if (status === 'suspended') return t('management.tenantSuspended')
  if (status === 'pending') return t('management.tenantPending')
  return t('management.tenantActive')
}

function tenantStatusClass(status) {
  if (status === 'suspended') return 'bg-surface-sunken text-ink-600'
  if (status === 'pending') return 'bg-warning-50 text-warning-700'
  return 'bg-success-50 text-success-700'
}

function failureClass(row) {
  if (row.run_count && row.fail_rate >= 5) return 'font-semibold text-danger-700'
  return 'text-ink-700'
}

function formatNumber(value) {
  return new Intl.NumberFormat().format(value || 0)
}

function formatCosts(costs) {
  const entries = Object.entries(costs || {})
  if (!entries.length) return '—'
  return entries.map(([currency, value]) => `${currency} ${value.toFixed(2)}`).join(' / ')
}

function formatDuration(value) {
  if (value == null) return '—'
  if (value < 1000) return `${value} ms`
  return `${(value / 1000).toFixed(1)} s`
}

function toggleSort(key) {
  if (sortKey.value === key) {
    sortDir.value = sortDir.value === 'asc' ? 'desc' : 'asc'
    return
  }
  sortKey.value = key
  sortDir.value = 'desc'
}

function exportCsv() {
  const header = [
    'tenant',
    'status',
    'active_members',
    'members',
    'assistants',
    'datasources',
    'runs',
    'tokens',
    'costs',
    'fail_rate',
    'p95_duration_ms',
    'like_rate',
    'running'
  ]
  const lines = visibleRows.value.map((row) => [
    row.name,
    row.status,
    row.active_member_count,
    row.member_count,
    row.assistant_count,
    row.datasource_count,
    row.run_count,
    row.tokens,
    formatCosts(row.costs),
    row.fail_rate ?? '',
    row.p95_duration_ms ?? '',
    row.like_rate ?? '',
    row.running_count
  ])
  const csv = [header, ...lines]
    .map((line) => line.map((value) => `"${String(value).replaceAll('"', '""')}"`).join(','))
    .join('\n')
  const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = 'tenant-stats.csv'
  link.click()
  URL.revokeObjectURL(url)
}

async function fetchStats() {
  loading.value = true
  error.value = ''
  try {
    const data = await getPlatformTenantStats({ days: 7 })
    rows.value = data?.results || []
  } catch (err) {
    error.value =
      err?.response?.data?.detail || t('management.platformStatsLoadFailed')
  } finally {
    loading.value = false
  }
}

onMounted(fetchStats)
</script>
