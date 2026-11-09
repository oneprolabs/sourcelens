<template>
  <AdminLayout>
    <div class="flex max-w-full flex-col gap-4 py-4">
      <section class="admin-data-panel overflow-hidden">
        <div
          class="flex flex-col gap-4 border-b border-line px-5 py-4 lg:flex-row lg:items-start lg:justify-between"
        >
          <div class="min-w-0">
            <router-link
              v-if="run?.dataset_uuid"
              :to="`/management/lens/evaluation/datasets/${run.dataset_uuid}`"
              class="text-xs font-medium text-ink-500 hover:text-primary-600"
            >
              ← {{ t('evaluation.report.backToDataset') }}
            </router-link>
            <h1 class="admin-page-title mt-1">
              {{ t('evaluation.report.title') }}
            </h1>
            <p class="admin-page-subtitle">
              <span v-if="run">
                {{ run.config_snapshot?.dataset?.name || run.dataset_name }} · v{{ run.config_snapshot?.dataset?.version || run.dataset_version }} ·
                <span v-if="run.config_snapshot?.dataset?.revision">{{ t('evaluation.revisions.title') }} #{{ run.config_snapshot.dataset.revision }} ·</span>
                <span class="font-mono text-xs">{{ shortUuid(run.uuid) }}</span>
              </span>
            </p>
            <div
              v-if="run"
              class="mt-2 flex flex-wrap items-center gap-3 text-xs text-ink-500"
            >
              <span class="eval-status" :class="runStatusClass(run.status)">
                {{ t(`evaluation.runStatus.${run.status}`) }}
              </span>
              <span v-if="run.started_at">
                {{ t('evaluation.report.startedAt') }}:
                {{ formatDate(run.started_at) }}
              </span>
              <span v-if="run.finished_at">
                {{ t('evaluation.report.finishedAt') }}:
                {{ formatDate(run.finished_at) }}
              </span>
              <span v-if="platformVersion">
                {{ t('evaluation.report.platformVersion') }}:
                {{ platformVersion }}
              </span>
              <span v-if="lensnodeVersion">
                {{ t('evaluation.report.lensnodeVersion') }}:
                {{ lensnodeVersion }}
              </span>
            </div>
          </div>
          <div class="flex flex-wrap items-center gap-2">
            <BaseButton
              variant="outline"
              size="sm"
              :loading="loading"
              @click="reload"
            >
              {{ t('common.refresh') }}
            </BaseButton>
            <BaseButton variant="outline" size="sm" @click="comparisonOpen = true">
              {{ t('evaluation.comparison.action') }}
            </BaseButton>
            <BaseButton
              v-if="isActive"
              variant="outline"
              size="sm"
              :loading="actionLoading"
              @click="cancelRun"
            >
              {{ t('evaluation.report.cancel') }}
            </BaseButton>
            <BaseButton
              v-if="isTerminal"
              size="sm"
              :loading="actionLoading"
              @click="retryRun"
            >
              {{ t('evaluation.report.retry') }}
            </BaseButton>
          </div>
        </div>

        <div class="px-5 py-4">
          <BaseLoading v-if="loading && !report" />

          <template v-else-if="report">
            <div
              v-if="run?.error"
              class="mb-4 rounded-lg bg-error-50 px-4 py-3 text-sm text-error-700"
            >
              {{ run.error }}
            </div>
            <div
              v-else-if="waitingReason"
              class="mb-4 rounded-lg bg-warning-50 px-4 py-3 text-sm text-warning-700"
            >
              {{ waitingMessage }}
            </div>

            <div class="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
              <div class="admin-metric-card">
                <p
                  class="text-xs font-medium uppercase tracking-wide text-ink-500"
                >
                  {{ t('evaluation.report.totalCases') }}
                </p>
                <p class="admin-metric-value mt-1">{{ summary.total_cases }}</p>
                <p class="mt-1 text-xs text-ink-500">
                  {{
                    t('evaluation.report.completedCases', {
                      count: summary.completed_cases
                    })
                  }}
                </p>
              </div>
              <div class="admin-metric-card">
                <p
                  class="text-xs font-medium uppercase tracking-wide text-ink-500"
                >
                  {{ t('evaluation.report.passed') }}
                </p>
                <p class="admin-metric-value mt-1 text-success-700">
                  {{ summary.passed_cases }}
                </p>
                <p class="mt-1 text-xs text-ink-500">
                  {{ t('evaluation.report.passedHint') }}
                </p>
                <p class="mt-1 text-xs text-ink-500">
                  {{ t('evaluation.report.failed') }}:
                  {{ summary.failed_cases }} ·
                  {{ t('evaluation.report.inconclusive') }}:
                  {{ summary.inconclusive_cases }}
                </p>
              </div>
              <div class="admin-metric-card">
                <p
                  class="text-xs font-medium uppercase tracking-wide text-ink-500"
                >
                  {{ t('evaluation.report.score') }}
                </p>
                <p class="admin-metric-value mt-1">
                  {{ formatScore(summary.aggregate_score) }}
                </p>
                <p class="mt-1 text-xs text-ink-500">
                  {{
                    summary.coverage?.aggregate_suppressed
                      ? t('evaluation.report.scoreSuppressed')
                      : t('evaluation.report.weightedHint')
                  }}
                </p>
              </div>
              <div class="admin-metric-card">
                <p
                  class="text-xs font-medium uppercase tracking-wide text-ink-500"
                >
                  {{ t('evaluation.report.coverage') }}
                </p>
                <p class="admin-metric-value mt-1">
                  {{ formatPercent(summary.coverage?.case_ratio) }}
                </p>
                <p class="mt-1 text-xs text-ink-500">
                  {{
                    t('evaluation.report.coverageHint', {
                      scored: summary.coverage?.scored_metrics ?? 0,
                      total: summary.coverage?.expected_metrics ?? 0,
                      casePercent: formatPercent(summary.coverage?.case_ratio)
                    })
                  }}
                </p>
              </div>
            </div>

            <div class="mt-6 grid gap-4 lg:grid-cols-2">
              <div class="rounded-lg border border-line">
                <div class="border-b border-line px-4 py-3">
                  <h2 class="text-sm font-semibold text-ink-900">
                    {{ t('evaluation.report.metrics') }}
                  </h2>
                </div>
                <div class="overflow-x-auto">
                  <table class="w-full table-fixed divide-y divide-line">
                    <colgroup>
                      <col style="width: 38%" />
                      <col style="width: 18%" />
                      <col style="width: 22%" />
                      <col style="width: 22%" />
                    </colgroup>
                    <thead class="bg-surface-sunken">
                      <tr>
                        <th class="table-head">
                          {{ t('evaluation.report.metric') }}
                        </th>
                        <th class="table-head">
                          {{ t('evaluation.report.weight') }}
                        </th>
                        <th class="table-head">
                          {{ t('evaluation.report.meanScore') }}
                        </th>
                        <th class="table-head">
                          {{ t('evaluation.report.verdicts') }}
                        </th>
                      </tr>
                    </thead>
                    <tbody class="divide-y divide-line bg-surface">
                      <tr v-for="(metric, name) in report.metrics" :key="name">
                        <td class="table-cell">
                          <p class="font-medium text-ink-900">{{ name }}</p>
                          <p class="text-xs text-ink-500">
                            {{ metric.category }}
                          </p>
                        </td>
                        <td class="table-cell tabular-nums">
                          {{ metric.weight }}
                        </td>
                        <td class="table-cell tabular-nums">
                          {{ formatScore(metric.mean_score) }}
                          <p
                            v-if="metric.coverage_ratio !== undefined"
                            class="text-xs text-ink-500"
                          >
                            {{
                              t('evaluation.report.metricCoverage', {
                                percent: formatPercent(metric.coverage_ratio)
                              })
                            }}
                          </p>
                        </td>
                        <td class="table-cell text-xs tabular-nums">
                          <span class="text-success-700">{{
                            metric.passed
                          }}</span>
                          /
                          <span class="text-error-600">{{
                            metric.failed
                          }}</span>
                          · {{ metric.scored }}/{{ summary.total_cases }}
                        </td>
                      </tr>
                    </tbody>
                  </table>
                </div>
              </div>

              <div class="rounded-lg border border-line">
                <div class="border-b border-line px-4 py-3">
                  <h2 class="text-sm font-semibold text-ink-900">
                    {{ t('evaluation.report.failureClusters') }}
                  </h2>
                </div>
                <div
                  v-if="!report.failure_clusters?.length"
                  class="px-4 py-10 text-center text-sm text-ink-500"
                >
                  {{ t('evaluation.report.noFailures') }}
                </div>
                <ul v-else class="divide-y divide-line">
                  <li
                    v-for="cluster in report.failure_clusters"
                    :key="cluster.code"
                    class="flex items-start justify-between gap-3 px-4 py-3"
                  >
                    <div class="min-w-0">
                      <p class="font-mono text-xs font-medium text-ink-900">
                        {{ cluster.code }}
                      </p>
                      <p class="mt-1 line-clamp-1 text-xs text-ink-500">
                        {{
                          cluster.cases.map((item) => item.input).join(' · ')
                        }}
                      </p>
                    </div>
                    <span
                      class="rounded-full bg-error-50 px-2 py-0.5 text-xs font-medium text-error-700"
                    >
                      {{ cluster.count }}
                    </span>
                  </li>
                </ul>
              </div>
            </div>

            <div class="mt-6 rounded-lg border border-line">
              <div
                class="flex flex-wrap items-center justify-between gap-3 border-b border-line px-4 py-3"
              >
                <h2 class="text-sm font-semibold text-ink-900">
                  {{ t('evaluation.report.results') }}
                </h2>
                <div class="flex flex-wrap items-center gap-2">
                  <button
                    v-for="filter in verdictFilters"
                    :key="filter.key"
                    type="button"
                    class="rounded-full px-3 py-1 text-xs font-medium transition-colors"
                    :class="
                      verdictFilter === filter.key
                        ? 'bg-primary-600 text-white'
                        : 'bg-surface-sunken text-ink-600 hover:text-ink-900'
                    "
                    @click="setVerdictFilter(filter.key)"
                  >
                    {{ filter.label }}
                  </button>
                </div>
              </div>

              <BaseLoading v-if="resultsLoading && !results.length" />

              <div
                v-else-if="!results.length"
                class="px-4 py-12 text-center text-sm text-ink-500"
              >
                {{ t('evaluation.report.noResults') }}
              </div>

              <div v-else class="overflow-x-auto">
                <table class="w-full table-fixed divide-y divide-line">
                  <colgroup>
                    <col style="width: 34%" />
                    <col style="width: 9rem" />
                    <col style="width: 12rem" />
                    <col style="width: 9rem" />
                    <col style="width: 8rem" />
                  </colgroup>
                  <thead class="bg-surface-sunken">
                    <tr>
                      <th class="table-head">
                        {{ t('evaluation.report.case') }}
                      </th>
                      <th class="table-head">
                        {{ t('evaluation.report.verdict') }}
                      </th>
                      <th class="table-head">
                        {{ t('evaluation.report.score') }}
                      </th>
                      <th class="table-head">
                        {{ t('evaluation.report.tokens') }}
                      </th>
                      <th class="table-head">{{ t('common.actions') }}</th>
                    </tr>
                  </thead>
                  <tbody class="divide-y divide-line bg-surface">
                    <tr
                      v-for="row in results"
                      :key="row.uuid"
                      class="cursor-pointer hover:bg-surface-hover"
                      @click="openResult(row)"
                    >
                      <td class="table-cell">
                        <p class="line-clamp-2 text-ink-900">
                          {{ row.case_input }}
                        </p>
                        <p v-if="row.error" class="mt-1 text-xs text-error-600">
                          {{ row.error }}
                        </p>
                      </td>
                      <td class="table-cell">
                        <span
                          class="eval-status"
                          :class="verdictClass(row.passed)"
                        >
                          {{ verdictLabel(row.passed) }}
                        </span>
                      </td>
                      <td class="table-cell tabular-nums">
                        {{ formatScore(resultScore(row)) }}
                      </td>
                      <td class="table-cell tabular-nums text-xs">
                        {{ row.execution_metrics?.total_tokens ?? '—' }}
                      </td>
                      <td class="table-cell">
                        <button
                          type="button"
                          class="text-sm font-medium text-primary-600 hover:text-primary-700"
                          @click.stop="openResult(row)"
                        >
                          {{ t('evaluation.report.detail') }}
                        </button>
                      </td>
                    </tr>
                  </tbody>
                </table>
              </div>

              <div class="px-4">
                <PaginationBar
                  :current-page="page"
                  :page-size="pageSize"
                  :total="resultsTotal"
                  @prev="goPrev"
                  @next="goNext"
                  @update:page-size="changePageSize"
                />
              </div>
            </div>
          </template>
        </div>
      </section>
    </div>

    <BaseDrawer :show="comparisonOpen" :title="t('evaluation.comparison.action')" width="2xl" @close="comparisonOpen = false">
      <div class="flex flex-col gap-4">
        <label class="text-sm font-medium">{{ t('evaluation.comparison.other') }}
          <input v-model="comparisonUuid" class="mt-1 w-full rounded-md border border-line bg-surface px-3 py-2" />
        </label>
        <BaseButton :disabled="!comparisonUuid.trim()" :loading="comparisonLoading" @click="loadComparison">{{ t('evaluation.comparison.action') }}</BaseButton>
        <template v-if="comparison">
          <p class="rounded-md bg-warning-50 p-3 text-sm text-warning-700">{{ t('evaluation.comparison.warning') }}</p>
          <ul class="text-xs text-ink-600"><li v-for="reason in comparison.limitations" :key="reason">{{ t(`evaluation.comparison.limitations.${reason}`) }}</li></ul>
          <div v-for="item in comparison.cases" :key="item.case_uuid" class="rounded-lg border border-line p-3">
            <div class="font-mono text-xs text-ink-500">{{ item.case_uuid }}</div>
            <div class="mt-2 grid gap-3 md:grid-cols-2">
              <div><h3 class="text-sm font-semibold">{{ t('evaluation.comparison.current') }} · {{ item.left ? verdictLabel(item.left.passed) : '—' }}</h3><pre class="mt-1 max-h-48 overflow-auto whitespace-pre-wrap text-xs">{{ item.left?.output || '—' }}</pre></div>
              <div><h3 class="text-sm font-semibold">{{ t('evaluation.comparison.otherLabel') }} · {{ item.right ? verdictLabel(item.right.passed) : '—' }}</h3><pre class="mt-1 max-h-48 overflow-auto whitespace-pre-wrap text-xs">{{ item.right?.output || '—' }}</pre></div>
            </div>
          </div>
        </template>
      </div>
    </BaseDrawer>

    <BaseDrawer
      :show="detailOpen"
      :title="t('evaluation.report.resultDetail')"
      :subtitle="detail?.case_input"
      width="2xl"
      @close="detailOpen = false"
    >
      <BaseLoading v-if="detailLoading" />
      <div v-else-if="detail" class="flex flex-col gap-4">
        <div class="flex flex-wrap items-center gap-3">
          <span class="eval-status" :class="verdictClass(detail.passed)">
            {{ verdictLabel(detail.passed) }}
          </span>
          <span class="text-xs text-ink-500">
            {{ t('evaluation.report.tokens') }}:
            {{ detail.execution_metrics?.total_tokens ?? '—' }}
          </span>
          <span class="text-xs text-ink-500">
            {{ t('evaluation.report.duration') }}:
            {{ formatDuration(detail.execution_metrics?.duration_seconds) }}
          </span>
        </div>

        <section v-if="detail.baseline">
          <h3 class="text-sm font-semibold text-ink-900">{{ t('evaluation.baseline.answer') }}</h3>
          <p class="mt-1 text-xs text-ink-500">Run: {{ detail.baseline.run_uuid }}</p>
          <details class="mt-2 text-xs text-ink-600">
            <summary>{{ t('evaluation.revisions.metadata') }}</summary>
            <pre class="mt-2 max-h-48 overflow-auto whitespace-pre-wrap">{{ prettyJson(detail.baseline.metadata || {}) }}</pre>
          </details>
          <pre class="mt-2 max-h-64 overflow-auto whitespace-pre-wrap rounded-lg bg-surface-sunken p-3 text-xs text-ink-700">{{ detail.baseline.answer }}</pre>
          <p class="mt-1 text-xs text-ink-500">{{ t('evaluation.baseline.manualComparison') }}</p>
        </section>

        <section>
          <h3 class="text-sm font-semibold text-ink-900">
            {{ t('evaluation.report.output') }}
          </h3>
          <pre
            class="mt-2 max-h-64 overflow-auto whitespace-pre-wrap rounded-lg bg-surface-sunken p-3 text-xs text-ink-700"
            >{{ detail.output_snapshot || '—' }}</pre
          >
        </section>

        <section class="rounded-lg border border-line p-3">
          <h3 class="text-sm font-semibold">{{ t('evaluation.calibration.title') }}</h3>
          <p class="mt-1 text-xs text-ink-500">{{ t('evaluation.calibration.description') }}</p>
          <div class="mt-3 grid gap-3 md:grid-cols-2">
            <BaseSelect v-model="calibrationAnswer" :label="t('evaluation.calibration.answer')">
              <option value="">{{ t('evaluation.calibration.select') }}</option>
              <option value="pass">{{ t('evaluation.report.passed') }}</option>
              <option value="fail">{{ t('evaluation.report.failed') }}</option>
              <option value="inconclusive">{{ t('evaluation.report.inconclusive') }}</option>
            </BaseSelect>
            <BaseSelect v-model="calibrationEvidence" :label="t('evaluation.calibration.evidence')">
              <option value="">{{ t('evaluation.calibration.select') }}</option>
              <option value="pass">{{ t('evaluation.report.passed') }}</option>
              <option value="fail">{{ t('evaluation.report.failed') }}</option>
              <option value="inconclusive">{{ t('evaluation.report.inconclusive') }}</option>
            </BaseSelect>
          </div>
          <label class="mt-3 block text-sm">{{ t('evaluation.calibration.reason') }}
            <textarea v-model="calibrationReason" maxlength="4000" class="mt-1 w-full rounded-md border border-line bg-surface px-3 py-2" />
          </label>
          <BaseButton class="mt-2" size="sm" :disabled="!calibrationAnswer || !calibrationEvidence || !calibrationReason.trim()" :loading="calibrationSaving" @click="saveCalibration">{{ t('evaluation.calibration.save') }}</BaseButton>
          <div v-for="row in calibrations" :key="row.id" class="mt-3 border-t border-line pt-2 text-xs">
            {{ row.reviewer || '—' }} · {{ formatDate(row.created_at) }} · {{ t('evaluation.calibration.answer') }}: {{ t(`evaluation.calibration.verdict.${row.answer_verdict}`) }} · {{ t('evaluation.calibration.evidence') }}: {{ t(`evaluation.calibration.verdict.${row.evidence_verdict}`) }}
            <p class="mt-1 whitespace-pre-wrap">{{ row.explanation }}</p>
          </div>
        </section>

        <section v-if="detail.issues?.length">
          <h3 class="text-sm font-semibold text-ink-900">
            {{ t('evaluation.report.issues') }}
          </h3>
          <ul class="mt-2 space-y-2">
            <li
              v-for="issue in detail.issues"
              :key="issue.code"
              class="rounded-lg bg-error-50 px-3 py-2 text-xs text-error-700"
            >
              <span class="font-mono font-medium">{{ issue.code }}</span>
              <span class="ml-2">{{ issue.metric }}</span>
            </li>
          </ul>
        </section>

        <section>
          <h3 class="text-sm font-semibold text-ink-900">
            {{ t('evaluation.report.scores') }}
          </h3>
          <pre
            class="mt-2 max-h-72 overflow-auto rounded-lg bg-surface-sunken p-3 text-xs text-ink-700"
            >{{ prettyJson(detail.scores) }}</pre
          >
        </section>

        <section>
          <h3 class="text-sm font-semibold text-ink-900">
            {{ t('evaluation.report.executionMetrics') }}
          </h3>
          <pre
            class="mt-2 max-h-64 overflow-auto rounded-lg bg-surface-sunken p-3 text-xs text-ink-700"
            >{{ prettyJson(detail.execution_metrics) }}</pre
          >
        </section>

        <section v-if="detail.citations_snapshot?.length">
          <h3 class="text-sm font-semibold text-ink-900">
            {{ t('evaluation.report.citations') }}
          </h3>
          <ul class="mt-2 space-y-1 text-xs text-ink-700">
            <li
              v-for="(citation, index) in detail.citations_snapshot"
              :key="index"
              class="font-mono"
            >
              {{ citation.path }}
              <span v-if="citation.start_line" class="text-ink-500">
                :{{ citation.start_line }}-{{ citation.end_line }}
              </span>
            </li>
          </ul>
        </section>
      </div>
    </BaseDrawer>
  </AdminLayout>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'

import AdminLayout from '@/admin/layout/AdminLayout.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseDrawer from '@/components/ui/BaseDrawer.vue'
import BaseLoading from '@/components/ui/BaseLoading.vue'
import BaseSelect from '@/components/ui/BaseSelect.vue'
import PaginationBar from '@/components/ui/PaginationBar.vue'
import {
  cancelEvaluationRun,
  compareEvaluationRuns,
  createEvaluationCalibration,
  getEvaluationCalibrations,
  getEvaluationReport,
  getEvaluationResult,
  getEvaluationRun,
  listEvaluationResults,
  retryEvaluationRun
} from '@/api/evaluation'
import { useToast } from '@/composables/useToast'
import { formatDate } from '@/utils/formatting'

const route = useRoute()
const { t } = useI18n()
const { showSuccess, showError } = useToast()

const runUuid = computed(() => route.params.uuid)
const run = ref(null)
const report = ref(null)
const loading = ref(true)
const actionLoading = ref(false)
const results = ref([])
const resultsTotal = ref(0)
const resultsLoading = ref(false)
const page = ref(1)
const pageSize = ref(20)
const verdictFilter = ref('all')
const detailOpen = ref(false)
const detail = ref(null)
const detailLoading = ref(false)
const calibrations = ref([])
const calibrationAnswer = ref('')
const calibrationEvidence = ref('')
const calibrationReason = ref('')
const calibrationSaving = ref(false)

async function saveCalibration() {
  calibrationSaving.value = true
  try {
    calibrations.value = await createEvaluationCalibration(runUuid.value, detail.value.uuid, {
      answer_verdict: calibrationAnswer.value,
      evidence_verdict: calibrationEvidence.value,
      explanation: calibrationReason.value.trim()
    })
    calibrationReason.value = ''
    showSuccess(t('evaluation.calibration.saved'))
  } catch (error) {
    showError(errorMessage(error))
  } finally {
    calibrationSaving.value = false
  }
}

const comparisonOpen = ref(false)
const comparisonLoading = ref(false)
const comparisonUuid = ref('')
const comparison = ref(null)

async function loadComparison() {
  comparisonLoading.value = true
  comparison.value = null
  try {
    comparison.value = await compareEvaluationRuns(runUuid.value, comparisonUuid.value.trim())
  } catch (error) {
    showError(errorMessage(error))
  } finally {
    comparisonLoading.value = false
  }
}
let pollTimer = null
let resultsRequestVersion = 0
let disposed = false

const ACTIVE_STATUSES = ['queued', 'running', 'paused']
const TERMINAL_STATUSES = ['completed', 'cancelled', 'failed']

const isActive = computed(() => ACTIVE_STATUSES.includes(run.value?.status))
const isTerminal = computed(() => TERMINAL_STATUSES.includes(run.value?.status))
const summary = computed(() => report.value?.summary || {})
const waitingReason = computed(
  () => run.value?.config_snapshot?.waiting?.reason || ''
)
const waitingMessage = computed(() =>
  waitingReason.value === 'EVALUATION_NODE_BUSY'
    ? t('evaluation.report.waitingBusy')
    : t('evaluation.report.waitingNode')
)
const platformVersion = computed(
  () =>
    run.value?.config_snapshot?.platform_version ||
    report.value?.provenance?.platform_version ||
    ''
)
const lensnodeVersion = computed(
  () =>
    run.value?.config_snapshot?.lensnode_agent_version ||
    report.value?.provenance?.lensnode?.agent_version ||
    ''
)

const verdictFilters = computed(() => [
  { key: 'all', label: t('evaluation.report.filterAll') },
  { key: 'true', label: t('evaluation.report.passed') },
  { key: 'false', label: t('evaluation.report.failed') },
  { key: 'null', label: t('evaluation.report.inconclusive') }
])

const RUN_STATUS_CLASSES = {
  queued: 'bg-surface-sunken text-ink-600',
  running: 'bg-primary-50 text-primary-700',
  paused: 'bg-warning-50 text-warning-700',
  cancelled: 'bg-surface-sunken text-ink-500',
  failed: 'bg-error-50 text-error-700',
  completed: 'bg-success-50 text-success-700'
}

function runStatusClass(status) {
  return RUN_STATUS_CLASSES[status] || RUN_STATUS_CLASSES.queued
}

function verdictClass(passed) {
  if (passed === true) return 'bg-success-50 text-success-700'
  if (passed === false) return 'bg-error-50 text-error-700'
  return 'bg-warning-50 text-warning-700'
}

function verdictLabel(passed) {
  if (passed === true) return t('evaluation.report.passed')
  if (passed === false) return t('evaluation.report.failed')
  return t('evaluation.report.inconclusive')
}

function errorMessage(error) {
  return (
    error?.response?.data?.data?.detail ||
    error?.response?.data?.message ||
    error?.message ||
    String(error)
  )
}

function shortUuid(value) {
  return String(value || '').slice(0, 8)
}

function formatScore(value) {
  if (value === null || value === undefined || value === '') return '—'
  return Number(value).toFixed(3)
}

function formatPercent(value) {
  if (value === null || value === undefined) return '—'
  return `${Math.round(Number(value) * 100)}%`
}

function formatDuration(value) {
  if (value === null || value === undefined) return '—'
  const seconds = Number(value)
  if (!Number.isFinite(seconds)) return '—'
  if (seconds < 60) return `${seconds.toFixed(1)}s`
  return `${Math.floor(seconds / 60)}m ${Math.round(seconds % 60)}s`
}

function prettyJson(value) {
  try {
    return JSON.stringify(value ?? {}, null, 2)
  } catch {
    return String(value)
  }
}

function resultScore(row) {
  const scores = row?.scores || {}
  const weights = Object.values(scores).filter(
    (entry) => typeof entry?.score === 'number' && entry?.weight
  )
  if (!weights.length) return null
  const totalWeight = weights.reduce((sum, entry) => sum + entry.weight, 0)
  const weighted = weights.reduce(
    (sum, entry) => sum + entry.score * entry.weight,
    0
  )
  return totalWeight ? weighted / totalWeight : null
}

async function loadRun() {
  run.value = await getEvaluationRun(runUuid.value)
}

async function loadReport() {
  report.value = await getEvaluationReport(runUuid.value)
}

async function loadResults({ quiet = false } = {}) {
  const version = ++resultsRequestVersion
  resultsLoading.value = true
  try {
    const params = { page: page.value, page_size: pageSize.value }
    if (verdictFilter.value !== 'all') {
      params.passed = verdictFilter.value
    }
    const data = await listEvaluationResults(runUuid.value, params)
    if (version !== resultsRequestVersion || disposed) return
    results.value = Array.isArray(data) ? data : data?.results || []
    resultsTotal.value = Array.isArray(data)
      ? data.length
      : Number(data?.count ?? results.value.length)
  } catch (error) {
    if (version !== resultsRequestVersion || disposed) return
    if (!quiet) {
      results.value = []
      resultsTotal.value = 0
      showError(errorMessage(error))
    }
  } finally {
    if (version === resultsRequestVersion && !disposed) resultsLoading.value = false
  }
}

async function reload() {
  loading.value = true
  try {
    await Promise.all([loadRun(), loadReport(), loadResults()])
  } catch (error) {
    showError(errorMessage(error))
  } finally {
    loading.value = false
  }
}

function schedulePoll() {
  stopPoll()
  if (disposed || !isActive.value) return
  pollTimer = window.setTimeout(async () => {
    try {
      await Promise.all([loadRun(), loadReport(), loadResults({ quiet: true })])
    } catch {
      // Keep polling; a transient error should not stop the view.
    }
    schedulePoll()
  }, 4000)
}

function stopPoll() {
  if (pollTimer) {
    window.clearTimeout(pollTimer)
    pollTimer = null
  }
}

function setVerdictFilter(key) {
  verdictFilter.value = key
  page.value = 1
  loadResults()
}

function goPrev() {
  if (page.value <= 1) return
  page.value -= 1
  loadResults()
}

function goNext() {
  if (page.value * pageSize.value >= resultsTotal.value) return
  page.value += 1
  loadResults()
}

function changePageSize(value) {
  pageSize.value = Number(value) || 20
  page.value = 1
  loadResults()
}

async function openResult(row) {
  detailOpen.value = true
  detail.value = null
  calibrations.value = []
  calibrationAnswer.value = ''
  calibrationEvidence.value = ''
  calibrationReason.value = ''
  detailLoading.value = true
  try {
    detail.value = await getEvaluationResult(runUuid.value, row.uuid)
    calibrations.value = await getEvaluationCalibrations(runUuid.value, row.uuid)
  } catch (error) {
    showError(errorMessage(error))
  } finally {
    detailLoading.value = false
  }
}

async function cancelRun() {
  actionLoading.value = true
  try {
    await cancelEvaluationRun(runUuid.value)
    showSuccess(t('evaluation.report.cancelled'))
    await reload()
  } catch (error) {
    showError(errorMessage(error))
  } finally {
    actionLoading.value = false
  }
}

async function retryRun() {
  actionLoading.value = true
  try {
    await retryEvaluationRun(runUuid.value)
    showSuccess(t('evaluation.report.retried'))
    page.value = 1
    await reload()
    schedulePoll()
  } catch (error) {
    showError(errorMessage(error))
  } finally {
    actionLoading.value = false
  }
}

watch(isActive, (active) => {
  if (active) schedulePoll()
  else stopPoll()
})

onMounted(async () => {
  await reload()
  schedulePoll()
})

onBeforeUnmount(() => {
  disposed = true
  stopPoll()
})
</script>

<style scoped>
.table-head {
  @apply border-b border-line px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-ink-500;
}

.table-cell {
  @apply px-4 py-3 align-top text-sm text-ink-700;
}

.eval-status {
  @apply inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium;
}
</style>
