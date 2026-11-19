<template>
  <div class="grid min-w-0 gap-4 xl:grid-cols-2">
    <section class="min-w-0 rounded-lg border border-line bg-surface p-5">
      <h2 class="text-sm font-semibold text-ink-900">
        {{ t('platformOverview.questionsTitle') }}
      </h2>
      <p class="mt-1 text-xs text-ink-500">
        {{ t('platformOverview.questionsHint') }}
      </p>
      <div v-if="hasQuestions" class="mt-5 h-56">
        <Bar
          :data="questionData"
          :options="barOptions"
          :aria-label="t('platformOverview.questionsTitle')"
          role="img"
        />
      </div>
      <table v-if="hasQuestions" class="sr-only">
        <caption>
          {{
            t('platformOverview.questionsTitle')
          }}
        </caption>
        <thead>
          <tr>
            <th>{{ t('platformOverview.date') }}</th>
            <th>{{ t('platformOverview.questionCount') }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in questionSeries" :key="row.date">
            <th>{{ row.date }}</th>
            <td>{{ row.count }}</td>
          </tr>
        </tbody>
      </table>
      <p v-if="!hasQuestions" class="py-20 text-center text-sm text-ink-500">
        {{ t('platformOverview.questionsEmpty') }}
      </p>
    </section>

    <section class="min-w-0 rounded-lg border border-line bg-surface p-5">
      <div class="flex flex-wrap items-start justify-between gap-2">
        <div>
          <h2 class="text-sm font-semibold text-ink-900">
            {{ t('platformOverview.topTenantsTitle') }}
          </h2>
          <p class="mt-1 text-xs text-ink-500">
            {{ t('management.platformRuns7d') }}
          </p>
        </div>
        <router-link
          to="/management/platform/tenants"
          class="text-sm font-medium text-brand-700 hover:underline"
        >
          {{ t('platformOverview.allTenants') }}
        </router-link>
      </div>
      <ol v-if="overview.top_tenants_7d?.length" class="mt-5 space-y-4">
        <li v-for="tenant in overview.top_tenants_7d" :key="tenant.uuid">
          <div class="mb-1.5 flex min-w-0 justify-between gap-3 text-sm">
            <span class="truncate text-ink-700">{{ tenant.name }}</span>
            <span class="shrink-0 font-mono text-ink-900">{{
              formatNumber(tenant.run_count)
            }}</span>
          </div>
          <div class="h-1.5 overflow-hidden rounded-full bg-surface-sunken">
            <div
              class="h-full rounded-full bg-brand-500"
              :style="{ width: `${(tenant.run_count / topCount) * 100}%` }"
            />
          </div>
        </li>
      </ol>
      <p v-else class="py-20 text-center text-sm text-ink-500">
        {{ t('platformOverview.tenantsEmpty') }}
      </p>
    </section>

    <section class="min-w-0 rounded-lg border border-line bg-surface p-5">
      <h2 class="text-sm font-semibold text-ink-900">
        {{ t('platformOverview.attentionTitle') }}
      </h2>
      <p class="mt-1 text-xs text-ink-500">
        {{ t('platformOverview.attentionHint') }}
      </p>
      <div v-if="attention.length" class="mt-4 divide-y divide-line">
        <router-link
          v-for="item in attention"
          :key="item.key"
          :to="item.to"
          class="flex min-h-20 items-center gap-3 rounded-md py-3 hover:bg-surface-hover focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-500"
        >
          <component
            :is="item.icon"
            class="h-5 w-5 shrink-0 text-warning-700"
            aria-hidden="true"
          />
          <div class="min-w-0 flex-1">
            <p class="text-sm font-medium text-ink-900">
              {{ t(item.title, { count: item.count }) }}
            </p>
            <p class="mt-1 text-xs leading-5 text-ink-500">
              {{ t(item.hint) }}
            </p>
          </div>
          <ChevronRight
            class="h-4 w-4 shrink-0 text-ink-400"
            aria-hidden="true"
          />
        </router-link>
      </div>
      <div v-else class="py-16 text-center">
        <CircleCheck
          class="mx-auto h-7 w-7 text-success-700"
          aria-hidden="true"
        />
        <p class="mt-3 text-sm font-medium text-ink-800">
          {{ t('platformOverview.allClear') }}
        </p>
        <p class="mt-1 text-xs text-ink-500">
          {{ t('platformOverview.allClearHint') }}
        </p>
      </div>
    </section>

    <section class="min-w-0 rounded-lg border border-line bg-surface p-5">
      <h2 class="text-sm font-semibold text-ink-900">
        {{ t('platformOverview.modelsTitle') }}
      </h2>
      <p class="mt-1 text-xs text-ink-500">
        {{ t('platformOverview.modelsHint') }}
      </p>
      <div
        v-if="models.length"
        class="mt-5 flex flex-col items-center gap-5 sm:flex-row"
      >
        <div class="h-44 w-44 shrink-0">
          <Doughnut
            :data="modelData"
            :options="donutOptions"
            :aria-label="t('platformOverview.modelsTitle')"
            role="img"
          />
        </div>
        <ul class="w-full min-w-0 flex-1 space-y-3">
          <li
            v-for="(model, index) in models"
            :key="index"
            class="flex min-w-0 items-center gap-2 text-sm"
          >
            <span
              class="h-2 w-2 shrink-0 rounded-full"
              :style="{ backgroundColor: colors[index] }"
            />
            <span class="min-w-0 flex-1 break-words text-ink-700">{{
              modelLabel(model)
            }}</span>
            <span class="shrink-0 font-mono text-ink-900">{{
              formatNumber(model.tokens)
            }}</span>
          </li>
        </ul>
      </div>
      <p v-else class="py-20 text-center text-sm text-ink-500">
        {{ t('platformOverview.modelsEmpty') }}
      </p>
    </section>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { ChevronRight, CircleCheck, Clock, Send, WifiOff } from '@lucide/vue'
import { Bar, Doughnut } from 'vue-chartjs'
import {
  ArcElement,
  BarElement,
  CategoryScale,
  Chart as ChartJS,
  LinearScale,
  Tooltip
} from 'chart.js'

ChartJS.register(ArcElement, BarElement, CategoryScale, LinearScale, Tooltip)
const props = defineProps({ overview: { type: Object, required: true } })
const { t, locale } = useI18n()
const styles = getComputedStyle(document.documentElement)
const colors = [
  '--sl-accent',
  '--sl-success',
  '--sl-text-secondary',
  '--sl-brand-strong',
  '--sl-text-muted',
  '--sl-text-subtle'
].map((token) => styles.getPropertyValue(token).trim())
const questionSeries = computed(() => props.overview.question_series_30d || [])
const hasQuestions = computed(() =>
  questionSeries.value.some((row) => row.count > 0)
)
const models = computed(() => props.overview.model_usage_7d || [])
const topCount = computed(() =>
  Math.max(
    1,
    ...(props.overview.top_tenants_7d || []).map((row) => row.run_count)
  )
)
const questionData = computed(() => ({
  labels: questionSeries.value.map((row) =>
    new Intl.DateTimeFormat(locale.value, {
      month: 'numeric',
      day: 'numeric',
      timeZone: 'UTC'
    }).format(new Date(`${row.date}T00:00:00Z`))
  ),
  datasets: [
    {
      label: t('platformOverview.questionCount'),
      data: questionSeries.value.map((row) => row.count),
      backgroundColor: colors[0],
      borderRadius: 3
    }
  ]
}))
const modelData = computed(() => ({
  labels: models.value.map(modelLabel),
  datasets: [
    {
      data: models.value.map((model) => model.tokens),
      backgroundColor: colors,
      borderWidth: 0
    }
  ]
}))
const barOptions = {
  responsive: true,
  maintainAspectRatio: false,
  plugins: { legend: { display: false } },
  scales: {
    x: { grid: { display: false }, ticks: { maxTicksLimit: 8 } },
    y: { beginAtZero: true, ticks: { precision: 0 } }
  }
}
const donutOptions = {
  responsive: true,
  maintainAspectRatio: false,
  cutout: '72%',
  plugins: { legend: { display: false } }
}
const attention = computed(() => {
  const data = props.overview.attention || {}
  return [
    {
      key: 'nodes',
      count: data.offline_node_count,
      icon: WifiOff,
      title: 'platformOverview.offlineNodes',
      hint: 'platformOverview.offlineNodesHint',
      to: '/management/platform/nodes'
    },
    {
      key: 'notifications',
      count: data.notification_failure_count_7d,
      icon: Send,
      title: 'platformOverview.failedNotifications',
      hint: 'platformOverview.failedNotificationsHint',
      to: {
        path: '/management/platform/notifications/records',
        query: {
          status: 'failed',
          start_date: data.notification_start_at,
          end_date: data.notification_end_at
        }
      }
    },
    {
      key: 'tenants',
      count: data.stale_pending_tenant_count,
      icon: Clock,
      title: 'platformOverview.pendingTenants',
      hint: 'platformOverview.pendingTenantsHint',
      to: '/management/platform/tenants?status=pending'
    }
  ].filter((item) => item.count > 0)
})
function formatNumber(value) {
  return new Intl.NumberFormat(locale.value).format(value || 0)
}
function modelLabel(model) {
  return model.other
    ? t('platformOverview.otherModels')
    : model.model || t('platformOverview.unknownModel')
}
</script>
