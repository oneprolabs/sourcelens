<template>
  <AdminLayout>
    <div class="flex h-full min-h-0 max-w-full flex-col gap-4 py-4">
      <section
        class="flex max-h-full min-h-0 w-full flex-col overflow-hidden rounded-lg border border-line bg-surface shadow-sm"
      >
        <div
          class="flex flex-wrap items-start justify-between gap-3 border-b border-line px-5 py-4"
        >
          <div class="min-w-0 flex-1">
            <h1 class="text-xl font-semibold text-ink-900">
              {{ t('management.platformOverviewTitle') }}
            </h1>
            <p class="mt-2 max-w-3xl text-sm leading-6 text-ink-500">
              {{ t('management.platformOverviewSubtitle') }}
            </p>
          </div>
          <div class="flex shrink-0 flex-col items-end gap-2">
            <BaseButton
              variant="outline"
              size="sm"
              :loading="loading"
              @click="fetchOverview"
            >
              {{ t('common.refresh') }}
            </BaseButton>
            <p v-if="overview?.generated_at" class="text-xs text-ink-500">
              {{ t('platformOverview.generatedAt') }}
              {{ formatDate(overview.generated_at) }}
            </p>
          </div>
        </div>

        <BaseLoading v-if="loading" />

        <div v-else class="min-h-0 flex-1 overflow-auto px-5 py-5">
          <div
            v-if="error"
            class="mb-4 rounded-md border border-danger-200 bg-danger-50 px-4 py-3 text-sm text-danger-700"
          >
            {{ error }}
          </div>

          <div v-if="overview" class="grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
            <article
              v-for="card in cards"
              :key="card.key"
              class="rounded-lg border border-line bg-surface p-4"
            >
              <p class="text-xs font-medium text-ink-500">
                {{ card.label }}
              </p>
              <p class="mt-2 break-words text-2xl font-semibold text-ink-900">
                {{ card.value }}
              </p>
              <p v-if="card.hint" class="mt-1 text-xs text-ink-500">
                {{ card.hint }}
              </p>
              <p v-if="card.change != null" class="mt-1 text-xs text-ink-600">
                {{ card.change > 0 ? '↑ +' : card.change < 0 ? '↓ ' : ''
                }}{{ card.change }}%
                {{ t('platformOverview.previousWeek') }}
              </p>
            </article>
          </div>

          <PlatformOverviewActivity
            v-if="overview"
            :overview="overview"
            class="mt-5"
          />

          <section
            v-if="overview"
            class="mt-5 overflow-x-auto rounded-lg border border-line bg-surface"
          >
            <div
              class="flex items-center justify-between border-b border-line px-5 py-3"
            >
              <div>
                <h2 class="text-sm font-semibold text-ink-900">
                  {{ t('management.platformRecentAudit') }}
                </h2>
                <p class="mt-1 text-xs text-ink-500">
                  {{ t('management.platformRecentAuditHint') }}
                </p>
              </div>
              <router-link
                to="/management/platform/audit"
                class="text-sm font-medium text-brand-700 hover:text-brand-800"
              >
                {{ t('common.viewMore') }}
              </router-link>
            </div>
            <div
              v-if="!overview?.recent_audit?.length"
              class="px-5 py-8 text-center text-sm text-ink-500"
            >
              {{ t('management.platformNoAudit') }}
            </div>
            <table v-else class="w-full min-w-[600px] divide-y divide-line">
              <thead class="bg-surface-sunken">
                <tr>
                  <th class="table-head">{{ t('management.auditTime') }}</th>
                  <th class="table-head">{{ t('management.auditAction') }}</th>
                  <th class="table-head">
                    {{ t('management.platformTenant') }}
                  </th>
                  <th class="table-head">{{ t('management.auditActor') }}</th>
                  <th class="table-head">{{ t('management.auditResult') }}</th>
                </tr>
              </thead>
              <tbody class="divide-y divide-line">
                <tr v-for="row in overview.recent_audit" :key="row.uuid">
                  <td class="px-4 py-3 text-sm text-ink-500">
                    {{ formatDate(row.created_at) }}
                  </td>
                  <td class="px-4 py-3 text-sm font-medium text-ink-800">
                    {{ actionLabel(row.action) }}
                  </td>
                  <td class="px-4 py-3 text-sm text-ink-600">
                    {{ row.tenant?.name || '—' }}
                  </td>
                  <td class="px-4 py-3 text-sm text-ink-600">
                    {{ row.actor }}
                  </td>
                  <td class="px-4 py-3 text-sm">
                    <span
                      :class="
                        row.result === 'success'
                          ? 'text-success-700'
                          : 'text-danger-700'
                      "
                    >
                      {{ resultLabel(row.result) }}
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
import BaseLoading from '@/components/ui/BaseLoading.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import { getPlatformOverview } from '@/api/platform'
import PlatformOverviewActivity from './PlatformOverviewActivity.vue'

const { t, locale } = useI18n()
const overview = ref(null)
const loading = ref(true)
const error = ref('')

const cards = computed(() => {
  const data = overview.value || {}
  return [
    {
      key: 'tenants',
      label: t('management.platformTenantCount'),
      value: data.tenant_count ?? 0,
      hint: t('management.platformTenantBreakdown', {
        active: data.active_tenant_count ?? 0,
        pending: data.pending_tenant_count ?? 0,
        suspended: data.suspended_tenant_count ?? 0
      })
    },
    {
      key: 'runs',
      label: t('management.platformRuns7d'),
      value: formatNumber(data.run_count_7d ?? 0),
      change: data.run_change_percent
    },
    {
      key: 'tokens',
      label: t('management.platformTokens7d'),
      value: data.tokens_7d == null ? '—' : formatNumber(data.tokens_7d)
    },
    {
      key: 'costs',
      label: t('platformOverview.modelCosts'),
      value:
        Object.entries(data.costs_7d || {})
          .map(([currency, amount]) => {
            try {
              return new Intl.NumberFormat(locale.value, {
                style: 'currency',
                currency,
                maximumFractionDigits: 4
              }).format(amount)
            } catch {
              return `${currency} ${formatNumber(amount)}`
            }
          })
          .join(' · ') || '—',
      hint: data.unpriced_calls_7d
        ? t('platformOverview.unpricedCalls', { count: data.unpriced_calls_7d })
        : t('platformOverview.costsHint')
    },
    {
      key: 'nodes',
      label: t('management.platformNodeOnline'),
      value: `${data.node_online_count ?? 0} / ${data.node_total_count ?? 0}`
    }
  ]
})

function formatNumber(value) {
  return new Intl.NumberFormat(locale.value).format(value || 0)
}

function formatDate(value) {
  if (!value) return '—'
  return new Date(value).toLocaleString(locale.value)
}

function actionLabel(action) {
  return t(
    `management.auditActions.${String(action || '').replaceAll('.', '_')}`,
    action
  )
}

function resultLabel(result) {
  if (result === 'denied') return t('management.auditDenied')
  if (result === 'failed') return t('management.auditFailed')
  return t('management.auditSuccess')
}

async function fetchOverview() {
  loading.value = true
  error.value = ''
  try {
    overview.value = await getPlatformOverview()
  } catch (err) {
    error.value =
      err?.response?.data?.detail || t('management.platformOverviewLoadFailed')
  } finally {
    loading.value = false
  }
}

onMounted(fetchOverview)
</script>
