<template>
  <AdminLayout>
    <div class="tenant-overview-page">
      <header class="admin-page-heading tenant-summary-heading">
        <div class="admin-heading-copy">
          <h1>{{ t('tenantOverview.title') }}</h1>
          <p>{{ overviewSubtitle }}</p>
        </div>
        <div class="tenant-summary-refresh">
          <BaseButton
            variant="ghost"
            size="sm"
            :loading="loading"
            @click="load"
          >
            <RefreshCw :size="14" aria-hidden="true" />{{ t('common.refresh') }}
          </BaseButton>
          <p v-if="overview">
            {{ t('platformOverview.generatedAt') }}
            {{ formatTime(overview.generated_at) }}
          </p>
        </div>
      </header>
      <div v-if="error" role="alert" class="tenant-summary-error">
        {{ error }}
        <button class="ml-3 underline" @click="load">
          {{ t('common.tryAgain') }}
        </button>
      </div>
      <BaseLoading v-if="loading && !overview" />
      <template v-if="overview">
        <router-link
          v-if="!overview.setup.complete"
          to="/management/setup"
          class="tenant-setup-link"
        >
          <Zap :size="16" aria-hidden="true" />
          <span>{{ t('tenantOverview.continueSetup') }} ›</span>
        </router-link>
        <dl class="tenant-overview-kpis">
          <div
            v-for="card in cards"
            :key="card.key"
            class="tenant-overview-kpi"
          >
            <dt>
              <component :is="card.icon" :size="14" aria-hidden="true" />{{
                t(card.label)
              }}
            </dt>
            <dd>{{ formatNumber(card.value) }}</dd>
            <p
              v-if="card.key === 'runs' && overview.run_change_percent !== null"
              class="tenant-kpi-change"
              :data-direction="overview.run_change_percent >= 0 ? 'up' : 'down'"
            >
              {{ overview.run_change_percent > 0 ? '+' : ''
              }}{{ overview.run_change_percent }}%
              {{ t('platformOverview.previousWeek') }}
            </p>
          </div>
        </dl>
        <div class="tenant-overview-grid">
          <section class="tenant-overview-panel">
            <header class="tenant-panel-heading">
              <div>
                <h2>{{ t('tenantOverview.attention') }}</h2>
                <p>{{ t('tenantOverview.attentionHint') }}</p>
              </div>
              <span
                v-if="attentionItems.length"
                class="tenant-attention-count"
                >{{ attentionItems.length }}</span
              >
            </header>
            <div v-if="attentionItems.length" class="tenant-overview-todos">
              <router-link
                v-for="item in attentionItems"
                :key="item.label"
                :to="`/management/lens/${item.to}`"
                class="tenant-overview-todo"
              >
                <span
                  class="tenant-todo-icon"
                  :data-tone="item.to === 'shares' ? 'info' : 'warning'"
                  ><component :is="item.icon" :size="17" aria-hidden="true"
                /></span>
                <strong>{{
                  t(`tenantOverview.${item.label}`, { count: item.count })
                }}</strong>
                <ChevronRight :size="14" aria-hidden="true" />
              </router-link>
            </div>
            <div v-else class="tenant-overview-empty">
              <CircleCheck
                :size="28"
                aria-hidden="true"
                class="tenant-empty-success"
              />
              <p>{{ t('tenantOverview.noAttention') }}</p>
            </div>
          </section>
          <section class="tenant-overview-panel">
            <header class="tenant-panel-heading">
              <div>
                <h2>{{ t('tenantOverview.sourceHealth') }}</h2>
                <p>{{ t('tenantOverview.sourceHealthHint') }}</p>
              </div>
              <router-link to="/management/lens/datasources">{{
                t('tenantOverview.viewSources')
              }}</router-link>
            </header>
            <ul v-if="overview.datasource_count" class="tenant-source-health">
              <li
                v-for="row in sourceHealthRows"
                :key="row.state"
                :data-state="row.state"
              >
                <span class="tenant-source-icon"
                  ><component :is="row.icon" :size="17" aria-hidden="true"
                /></span>
                <span class="tenant-source-label"
                  ><b>{{ t(`tenantOverview.sourceStates.${row.state}`) }}</b
                  ><small
                    >{{ formatNumber(row.count) }} /
                    {{ formatNumber(overview.datasource_count) }}</small
                  ></span
                >
                <span class="tenant-source-bar"
                  ><i
                    :style="{
                      width: `${(row.count / overview.datasource_count) * 100}%`
                    }"
                /></span>
              </li>
            </ul>
            <div v-else class="tenant-overview-empty">
              <Database :size="28" aria-hidden="true" />
              <p>{{ t('tenantOverview.noSources') }}</p>
            </div>
          </section>
          <section class="tenant-overview-panel">
            <header class="tenant-panel-heading">
              <div>
                <h2>{{ t('tenantOverview.questions') }}</h2>
                <p>{{ t('tenantOverview.questionsHint') }}</p>
              </div>
            </header>
            <div v-if="hasQuestions" class="tenant-question-chart">
              <Bar
                :data="questionData"
                :options="chartOptions"
                role="img"
                :aria-label="t('tenantOverview.questions')"
              />
            </div>
            <table v-if="hasQuestions" class="sr-only">
              <caption>
                {{
                  t('tenantOverview.questions')
                }}
              </caption>
              <thead>
                <tr>
                  <th>{{ t('platformOverview.date') }}</th>
                  <th>{{ t('platformOverview.questionCount') }}</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="row in overview.question_series_7d" :key="row.date">
                  <th>{{ row.date }}</th>
                  <td>{{ row.count }}</td>
                </tr>
              </tbody>
            </table>
            <div v-else class="tenant-overview-empty">
              <ChartColumn :size="28" aria-hidden="true" />
              <p>{{ t('tenantOverview.noQuestions') }}</p>
            </div>
          </section>
          <section class="tenant-overview-panel">
            <header class="tenant-panel-heading">
              <div>
                <h2>{{ t('tenantOverview.activity') }}</h2>
                <p>{{ t('tenantOverview.activityHint') }}</p>
              </div>
            </header>
            <ol
              v-if="overview.recent_activity.length"
              class="tenant-overview-activity"
            >
              <li
                v-for="(event, index) in overview.recent_activity"
                :key="index"
              >
                <i aria-hidden="true" :data-kind="event.kind" />
                <p>
                  {{
                    t(`tenantOverview.events.${event.kind}`, {
                      name: event.name
                    })
                  }}
                </p>
                <time :datetime="event.at">{{ formatTime(event.at) }}</time>
              </li>
            </ol>
            <div v-else class="tenant-overview-empty">
              <Activity :size="28" aria-hidden="true" />
              <p>{{ t('tenantOverview.noActivity') }}</p>
            </div>
          </section>
        </div>
      </template>
    </div>
  </AdminLayout>
</template>

<script setup>
import {
  Activity,
  Bot,
  ChartColumn,
  ChevronRight,
  CircleCheck,
  Database,
  MessageSquare,
  RefreshCw,
  Share2,
  TriangleAlert,
  Users,
  Zap
} from '@lucide/vue'
import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { Bar } from 'vue-chartjs'
import {
  Chart as ChartJS,
  BarElement,
  CategoryScale,
  LinearScale,
  Tooltip
} from 'chart.js'
import { getTenantOverview } from '@/api/tenant'
import AdminLayout from '@/admin/layout/AdminLayout.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseLoading from '@/components/ui/BaseLoading.vue'
import { useUserStore } from '@/store/user'

ChartJS.register(BarElement, CategoryScale, LinearScale, Tooltip)
const { t, locale } = useI18n()
const userStore = useUserStore()
const overview = ref(null)
const loading = ref(false)
const error = ref('')
const overviewSubtitle = computed(() => {
  const delegation = userStore.delegation
  const tenant = delegation?.tenant?.name || overview.value?.tenant?.name
  if (!tenant) return t('tenantOverview.subtitle')
  if (delegation) return t('tenantOverview.delegatedSubtitle', { tenant })

  const user = userStore.userInfo
  const name = user?.display_name || user?.username
  return name ? t('tenantOverview.welcomeSubtitle', { name, tenant }) : tenant
})
const cards = computed(() => [
  {
    icon: Users,
    key: 'members',
    label: 'tenantOverview.members',
    value: overview.value?.member_count || 0
  },
  {
    icon: Bot,
    key: 'assistants',
    label: 'tenantSetup.assistant',
    value: overview.value?.assistant_count || 0
  },
  {
    icon: Database,
    key: 'sources',
    label: 'tenantSetup.datasource',
    value: overview.value?.datasource_count || 0
  },
  {
    icon: MessageSquare,
    key: 'runs',
    label: 'tenantOverview.questions',
    value: overview.value?.run_count_7d || 0
  }
])
const sourceHealthRows = computed(() => {
  const health = overview.value?.datasource_health || {}
  return ['synced', 'running', 'failed', 'waiting', 'disabled']
    .filter(
      (state) =>
        ['synced', 'running', 'failed'].includes(state) || health[state] > 0
    )
    .map((state) => ({
      state,
      count: health[state] || 0,
      icon:
        state === 'synced'
          ? CircleCheck
          : state === 'failed'
            ? TriangleAlert
            : RefreshCw
    }))
})
const attentionItems = computed(() => {
  const attention = overview.value?.attention || {}
  return [
    {
      count: attention.failed_datasource_count,
      label: 'failedSources',
      to: 'datasources',
      icon: TriangleAlert
    },
    {
      count: attention.pending_share_count,
      label: 'pendingShares',
      to: 'shares',
      icon: Share2
    },
    {
      count: attention.assistant_attention_count,
      label: 'assistantAttention',
      to: 'assistants',
      icon: Bot
    }
  ].filter((item) => item.count > 0)
})
const hasQuestions = computed(() =>
  overview.value?.question_series_7d?.some((row) => row.count > 0)
)
const questionData = computed(() => ({
  labels:
    overview.value?.question_series_7d?.map((row) => row.date.slice(5)) || [],
  datasets: [
    {
      data: overview.value?.question_series_7d?.map((row) => row.count) || [],
      backgroundColor: getComputedStyle(document.documentElement)
        .getPropertyValue('--sl-accent')
        .trim(),
      borderRadius: 3
    }
  ]
}))
const chartOptions = {
  responsive: true,
  maintainAspectRatio: false,
  plugins: { legend: { display: false } },
  scales: {
    y: { beginAtZero: true, ticks: { precision: 0 } },
    x: { grid: { display: false } }
  }
}
function formatNumber(value) {
  return new Intl.NumberFormat(locale.value).format(value)
}
function formatTime(value) {
  return value ? new Date(value).toLocaleString(locale.value) : '—'
}
async function load() {
  loading.value = true
  try {
    overview.value = await getTenantOverview()
    error.value = ''
  } catch (e) {
    error.value =
      e?.response?.data?.detail || e?.message || t('tenantOverview.loadFailed')
  } finally {
    loading.value = false
  }
}
onMounted(load)
</script>
