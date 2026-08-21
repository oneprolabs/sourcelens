<template>
  <AdminLayout>
    <div
      class="flex h-auto min-h-0 w-full max-w-full flex-col p-0 md:h-full md:p-6"
    >
      <div class="mb-4 flex-shrink-0">
        <h1 class="text-lg font-semibold text-gray-900">
          {{ t('lensRuns.title') }}
        </h1>
        <p class="mt-1 text-sm text-gray-500">
          {{ t('lensRuns.subtitle') }}
        </p>
      </div>

      <div
        class="flex min-h-0 flex-col overflow-visible rounded-lg border-0 border-gray-200 bg-transparent shadow-none md:overflow-hidden md:border md:bg-white md:shadow-sm"
      >
        <div class="flex min-h-0 flex-col p-0 md:p-6">
          <div
            class="mb-4 flex flex-shrink-0 flex-col items-stretch gap-3 rounded-lg border border-gray-200 bg-white p-3 md:mb-6 md:flex-row md:flex-nowrap md:items-center md:justify-between md:border-0 md:p-0"
          >
            <div
              class="flex w-full min-w-0 flex-col items-stretch gap-3 md:flex-1 md:flex-row md:flex-nowrap md:items-center"
            >
              <input
                v-model="filters.q"
                type="text"
                :placeholder="t('lensRuns.filterKeyword')"
                class="min-h-11 w-full min-w-0 rounded-md border border-gray-300 px-2.5 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-primary-500 md:min-h-0 md:w-40"
                @input="onFiltersChanged"
              />
              <input
                v-model="filters.username"
                type="text"
                :placeholder="t('lensRuns.filterUsername')"
                class="min-h-11 w-full min-w-0 rounded-md border border-gray-300 px-2.5 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-primary-500 md:min-h-0 md:w-28"
                @input="onUsernameChanged"
              />
              <BaseSelect
                v-model="filters.assistant"
                class="md:w-48"
                mobile-touch
                @change="onFiltersChanged"
              >
                <option value="">{{ t('lensRuns.assistantAll') }}</option>
                <option v-for="a in assistants" :key="a.slug" :value="a.slug">
                  {{ a.name }}
                </option>
              </BaseSelect>
              <BaseSelect
                v-model="filters.status"
                class="md:w-28"
                mobile-touch
                @change="onFiltersChanged"
              >
                <option value="">{{ t('lensRuns.statusAll') }}</option>
                <option value="done">{{ t('lensRuns.statusDone') }}</option>
                <option value="failed">{{ t('lensRuns.statusFailed') }}</option>
                <option value="streaming">
                  {{ t('lensRuns.statusRunning') }}
                </option>
                <option value="queued">{{ t('lensRuns.statusQueued') }}</option>
                <option value="cancelled">
                  {{ t('lensRuns.statusCancelled') }}
                </option>
              </BaseSelect>
              <div class="flex w-full shrink-0 items-center gap-2 md:w-auto">
                <BaseDateInput
                  v-model="filters.start_date"
                  compact
                  :max="filters.end_date || undefined"
                  @change="onFiltersChanged"
                />
                <span class="text-gray-400">–</span>
                <BaseDateInput
                  v-model="filters.end_date"
                  compact
                  :min="filters.start_date || undefined"
                  @change="onFiltersChanged"
                />
              </div>
            </div>
            <div class="flex w-full shrink-0 items-center gap-2 md:w-auto">
              <BaseButton
                variant="outline"
                size="sm"
                :loading="loading"
                :title="t('common.refresh')"
                class="flex-1 md:flex-none"
                @click="fetchRuns"
              >
                {{ t('common.refresh') }}
              </BaseButton>
              <BaseButton
                variant="outline"
                size="sm"
                class="flex-1 md:flex-none"
                @click="resetFilters"
              >
                {{ t('lensRuns.resetFilters') }}
              </BaseButton>
            </div>
          </div>

          <BaseLoading v-if="loading && runs.length === 0" />

          <div
            v-else-if="!loading && runs.length === 0"
            class="rounded-lg border border-gray-200 bg-gray-50 py-16 text-center"
          >
            <p class="text-sm font-medium text-gray-600">
              {{ t('lensRuns.noRuns') }}
            </p>
          </div>

          <div v-else class="flex flex-col md:min-h-0">
            <div
              data-testid="mobile-run-observation-list"
              class="space-y-3 md:hidden"
            >
              <button
                v-for="r in runs"
                :key="`mobile-${r.uuid}`"
                type="button"
                class="block w-full rounded-lg border border-gray-200 bg-white p-4 text-left shadow-sm transition-colors hover:border-primary-200 hover:bg-primary-50/30 focus:outline-none focus-visible:ring-2 focus-visible:ring-primary-500/30"
                :aria-label="`${t('common.viewDetails')}: ${r.question || '-'}`"
                @click="openDetail(r.uuid)"
              >
                <div class="flex items-start justify-between gap-3">
                  <div class="min-w-0 flex-1">
                    <h2
                      class="line-clamp-3 text-sm font-semibold leading-5 text-gray-900"
                    >
                      {{ r.question || '-' }}
                    </h2>
                    <p class="mt-2 text-xs text-gray-500">
                      {{ formatDate(r.created_at) }}
                    </p>
                  </div>
                  <span :class="statusClass(r.status)">{{ r.status }}</span>
                </div>

                <dl class="mt-4 grid grid-cols-2 gap-x-4 gap-y-3 text-sm">
                  <div>
                    <dt class="text-xs text-gray-500">
                      {{ t('lensRuns.colUser') }}
                    </dt>
                    <dd class="mt-0.5 truncate font-medium text-gray-800">
                      {{ r.username || '-' }}
                    </dd>
                  </div>
                  <div>
                    <dt class="text-xs text-gray-500">
                      {{ t('lensRuns.colAssistant') }}
                    </dt>
                    <dd class="mt-0.5 truncate font-medium text-gray-800">
                      {{ r.assistant_name || '-' }}
                    </dd>
                  </div>
                  <div>
                    <dt class="text-xs text-gray-500">
                      {{ t('lensRuns.colDuration') }}
                    </dt>
                    <dd class="mt-0.5 font-medium tabular-nums text-gray-800">
                      {{ durationText(r.duration_seconds) }}
                    </dd>
                  </div>
                  <div>
                    <dt class="text-xs text-gray-500">
                      {{ t('lensRuns.colSteps') }}
                    </dt>
                    <dd class="mt-0.5 font-medium tabular-nums text-gray-800">
                      {{ r.event_count }}
                      <span
                        v-if="r.subagent_count > 0"
                        class="text-xs text-indigo-600"
                      >
                        · {{ t('lensRuns.subagents', { n: r.subagent_count }) }}
                      </span>
                    </dd>
                  </div>
                </dl>

                <div
                  class="mt-4 flex min-h-11 items-center justify-between border-t border-gray-100 pt-2"
                >
                  <span
                    v-if="r.feedback === 'positive'"
                    class="feedback-pill feedback-pill-positive"
                  >
                    <ThumbsUp :size="13" />
                    {{ t('lensRuns.feedbackHelpful') }}
                  </span>
                  <span
                    v-else-if="r.feedback === 'negative'"
                    class="feedback-pill feedback-pill-negative"
                  >
                    <ThumbsDown :size="13" />
                    {{ t('lensRuns.feedbackUnhelpful') }}
                  </span>
                  <span v-else class="text-xs text-gray-400">
                    {{ t('lensRuns.colFeedback') }}: —
                  </span>
                  <span
                    class="inline-flex items-center gap-1 text-sm font-medium text-primary-700"
                  >
                    {{ t('common.viewDetails') }}
                    <svg
                      class="h-4 w-4"
                      fill="none"
                      stroke="currentColor"
                      viewBox="0 0 24 24"
                      aria-hidden="true"
                    >
                      <path
                        stroke-linecap="round"
                        stroke-linejoin="round"
                        stroke-width="2"
                        d="M9 5l7 7-7 7"
                      />
                    </svg>
                  </span>
                </div>
              </button>
            </div>

            <div
              data-testid="desktop-run-observation-table"
              class="relative hidden max-h-full overflow-auto rounded-lg border border-gray-200 bg-white shadow-sm md:block"
            >
              <table class="min-w-full divide-y divide-gray-200">
                <thead class="sticky top-0 z-10 bg-gray-50">
                  <tr>
                    <th class="th">{{ t('lensRuns.colTime') }}</th>
                    <th class="th">{{ t('lensRuns.colUser') }}</th>
                    <th class="th">{{ t('lensRuns.colAssistant') }}</th>
                    <th class="th">{{ t('lensRuns.colQuestion') }}</th>
                    <th class="th">{{ t('lensRuns.colStatus') }}</th>
                    <th class="th">{{ t('lensRuns.colFeedback') }}</th>
                    <th class="th">{{ t('lensRuns.colDuration') }}</th>
                    <th class="th">{{ t('lensRuns.colSteps') }}</th>
                  </tr>
                </thead>
                <tbody class="bg-white divide-y divide-gray-100">
                  <tr
                    v-for="r in runs"
                    :key="r.uuid"
                    class="hover:bg-gray-50 cursor-pointer transition-colors"
                    @click="openDetail(r.uuid)"
                  >
                    <td class="td text-gray-600 whitespace-nowrap">
                      {{ formatDate(r.created_at) }}
                    </td>
                    <td class="td text-gray-900 whitespace-nowrap">
                      {{ r.username || '-' }}
                    </td>
                    <td class="td text-gray-600 whitespace-nowrap">
                      {{ r.assistant_name || '-' }}
                    </td>
                    <td class="td text-gray-700 max-w-md truncate">
                      {{ r.question || '-' }}
                    </td>
                    <td class="td whitespace-nowrap">
                      <span :class="statusClass(r.status)">{{ r.status }}</span>
                    </td>
                    <td class="td whitespace-nowrap">
                      <span
                        v-if="r.feedback === 'positive'"
                        class="feedback-pill feedback-pill-positive"
                      >
                        <ThumbsUp :size="13" />
                        {{ t('lensRuns.feedbackHelpful') }}
                      </span>
                      <span
                        v-else-if="r.feedback === 'negative'"
                        class="feedback-pill feedback-pill-negative"
                      >
                        <ThumbsDown :size="13" />
                        {{ t('lensRuns.feedbackUnhelpful') }}
                      </span>
                      <span v-else class="text-gray-400">—</span>
                    </td>
                    <td class="td text-gray-600 whitespace-nowrap tabular-nums">
                      {{ durationText(r.duration_seconds) }}
                    </td>
                    <td class="td text-gray-600 whitespace-nowrap tabular-nums">
                      {{ r.event_count }}
                      <span
                        v-if="r.subagent_count > 0"
                        class="ml-1 text-xs text-indigo-600"
                      >
                        · {{ t('lensRuns.subagents', { n: r.subagent_count }) }}
                      </span>
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>

            <PaginationBar
              v-model:page-size="pageSize"
              :current-page="page"
              :total="total"
              @page-size-change="handlePageSizeChange"
              @prev="goPrevPage"
              @next="goNextPage"
            />
          </div>
        </div>
      </div>

      <!-- Run detail right panel -->
      <Transition
        enter-active-class="transition-opacity duration-200"
        enter-from-class="opacity-0"
        enter-to-class="opacity-100"
        leave-active-class="transition-opacity duration-150"
        leave-from-class="opacity-100"
        leave-to-class="opacity-0"
      >
        <div
          v-if="detailVisible"
          class="fixed inset-0 bg-gray-900 bg-opacity-50 z-40"
          aria-hidden="true"
          @click="closeDetail"
        />
      </Transition>
      <Transition
        enter-active-class="transition-transform duration-300 ease-out"
        enter-from-class="translate-x-full"
        enter-to-class="translate-x-0"
        leave-active-class="transition-transform duration-250 ease-in"
        leave-from-class="translate-x-0"
        leave-to-class="translate-x-full"
      >
        <div
          v-if="detailVisible"
          class="fixed inset-y-0 right-0 z-50 flex w-full max-w-6xl flex-col bg-white shadow-xl"
          role="dialog"
          aria-modal="true"
        >
          <div
            class="flex items-center justify-between px-6 py-4 border-b border-gray-200 bg-gradient-to-r from-gray-50 to-gray-100 flex-shrink-0"
          >
            <h2 class="min-w-0 text-lg font-semibold text-gray-900">
              <span>{{ t('lensRuns.detailTitle') }}</span>
              <span
                v-if="selectedUuid"
                data-testid="run-detail-id"
                class="run-detail-id"
              >
                {{ selectedUuid }}
              </span>
            </h2>
            <div class="flex shrink-0 items-center gap-2">
              <BaseButton
                v-if="canDiagnoseRun"
                data-testid="generate-run-diagnosis"
                size="sm"
                variant="outline"
                :disabled="!canGenerateDiagnosis"
                @click="generateDiagnosis"
              >
                {{ t('lensRuns.generateDiagnosis') }}
              </BaseButton>
              <button
                data-testid="close-run-detail"
                type="button"
                class="rounded-md p-2 text-gray-400 hover:bg-gray-200 hover:text-gray-600"
                :aria-label="t('lensRuns.closeDetail')"
                @click="closeDetail"
              >
                <svg
                  class="w-5 h-5"
                  fill="none"
                  stroke="currentColor"
                  viewBox="0 0 24 24"
                >
                  <path
                    stroke-linecap="round"
                    stroke-linejoin="round"
                    stroke-width="2"
                    d="M6 18L18 6M6 6l12 12"
                  />
                </svg>
              </button>
            </div>
          </div>

          <div class="flex-1 overflow-y-auto">
            <BaseLoading v-if="detailLoading" class="m-6" />
            <div v-else-if="detail">
              <div
                class="sticky top-0 z-10 flex gap-5 border-b border-gray-200 bg-white px-6"
              >
                <button
                  class="detail-tab"
                  :class="
                    activeDetailTab === 'overview' ? 'detail-tab-active' : ''
                  "
                  @click="activeDetailTab = 'overview'"
                >
                  {{ t('lensRuns.tabOverview') }}
                </button>
                <button
                  class="detail-tab"
                  data-testid="run-diagnosis-tab"
                  :class="
                    activeDetailTab === 'diagnosis' ? 'detail-tab-active' : ''
                  "
                  @click="activeDetailTab = 'diagnosis'"
                >
                  {{ t('lensRuns.tabDiagnosis') }}
                </button>
                <button
                  class="detail-tab"
                  :class="
                    activeDetailTab === 'trace' ? 'detail-tab-active' : ''
                  "
                  @click="activeDetailTab = 'trace'"
                >
                  {{ t('lensRuns.tabTrace') }}
                  <span class="ml-1 text-xs text-gray-400">{{
                    detail.trace_event_count ?? detail.event_count
                  }}</span>
                </button>
                <button
                  class="detail-tab"
                  data-testid="run-files-tab"
                  :class="
                    activeDetailTab === 'files' ? 'detail-tab-active' : ''
                  "
                  @click="activeDetailTab = 'files'"
                >
                  {{ t('lensRuns.tabFiles') }}
                  <span class="ml-1 text-xs text-gray-400">{{
                    (detail.output_files || []).length
                  }}</span>
                </button>
              </div>

              <!-- Overview tab -->
              <div
                v-show="activeDetailTab === 'overview'"
                class="space-y-4 px-6 py-5"
              >
                <section
                  data-testid="run-overview-summary"
                  class="overview-section"
                >
                  <h3 class="overview-title">
                    {{ t('lensRuns.overviewSummary') }}
                  </h3>
                  <dl class="overview-grid">
                    <div>
                      <dt class="overview-label">
                        {{ t('lensRuns.executorStatus') }}
                      </dt>
                      <dd class="mt-1">
                        <span :class="statusClass(detail.executor_status)">{{
                          detail.executor_status || detail.status
                        }}</span>
                      </dd>
                    </div>
                    <div>
                      <dt class="overview-label">
                        {{ t('lensRuns.businessOutcome') }}
                      </dt>
                      <dd class="mt-1">
                        <span :class="statusClass(detail.outcome)">
                          {{ detail.outcome || '-' }}
                        </span>
                      </dd>
                    </div>
                    <div>
                      <dt class="overview-label">
                        {{ t('lensRuns.colFeedback') }}
                      </dt>
                      <dd class="mt-1">
                        <span
                          v-if="detail.feedback === 'positive'"
                          class="feedback-pill feedback-pill-positive"
                        >
                          <ThumbsUp :size="13" />
                          {{ t('lensRuns.feedbackHelpful') }}
                        </span>
                        <span
                          v-else-if="detail.feedback === 'negative'"
                          class="feedback-pill feedback-pill-negative"
                        >
                          <ThumbsDown :size="13" />
                          {{ t('lensRuns.feedbackUnhelpful') }}
                        </span>
                        <span v-else class="text-sm text-gray-400">—</span>
                      </dd>
                    </div>
                    <div
                      v-if="Object.keys(detail.termination_detail || {}).length"
                      class="col-span-2"
                    >
                      <dt class="overview-label">
                        {{ t('lensRuns.terminationDetail') }}
                      </dt>
                      <dd class="overview-value">
                        {{ detail.termination_detail.reason || '-' }}
                        <span
                          v-if="detail.termination_detail.capability"
                          class="font-normal text-gray-500"
                        >
                          · {{ detail.termination_detail.capability }}
                        </span>
                        <span
                          v-if="detail.termination_detail.error_type"
                          class="font-normal text-gray-500"
                        >
                          · {{ detail.termination_detail.error_type }}
                        </span>
                      </dd>
                    </div>
                    <div
                      v-if="hasFailureSummary"
                      class="col-span-2"
                      data-testid="run-failure-summary"
                    >
                      <dt class="overview-label">
                        {{ t('lensRuns.failureScope') }}
                      </dt>
                      <dd class="mt-1 flex flex-wrap gap-2">
                        <span
                          v-if="detail.failure_summary.unresolved_failure_count"
                          class="failure-pill failure-pill-error"
                        >
                          {{
                            t('lensRuns.failureUnresolved', {
                              n: detail.failure_summary.unresolved_failure_count
                            })
                          }}
                        </span>
                        <span
                          v-if="detail.failure_summary.recovered_failure_count"
                          class="failure-pill failure-pill-recovered"
                        >
                          {{
                            t('lensRuns.failureRecovered', {
                              n: detail.failure_summary.recovered_failure_count
                            })
                          }}
                        </span>
                        <span
                          v-if="detail.failure_summary.warning_count"
                          class="failure-pill failure-pill-warning"
                        >
                          {{
                            t('lensRuns.failureWarning', {
                              n: detail.failure_summary.warning_count
                            })
                          }}
                        </span>
                      </dd>
                    </div>
                    <div>
                      <dt class="overview-label">
                        {{ t('lensRuns.colUser') }}
                      </dt>
                      <dd class="overview-value">
                        {{ detail.username || '-' }}
                      </dd>
                    </div>
                    <div>
                      <dt class="overview-label">
                        {{ t('lensRuns.colSteps') }}
                      </dt>
                      <dd class="overview-value tabular-nums">
                        {{ detail.event_count }}
                        <span
                          v-if="detail.subagent_count > 0"
                          class="font-normal text-indigo-600"
                        >
                          ·
                          {{
                            t('lensRuns.subagents', {
                              n: detail.subagent_count
                            })
                          }}
                        </span>
                        <span
                          v-if="detail.subagent_denied_count > 0"
                          class="font-normal text-amber-600"
                        >
                          ·
                          {{
                            t('lensRuns.subagentsDenied', {
                              n: detail.subagent_denied_count
                            })
                          }}
                        </span>
                      </dd>
                    </div>
                    <div class="col-span-2">
                      <dt class="overview-label">{{ t('lensRuns.tokens') }}</dt>
                      <dd class="overview-value tabular-nums">
                        {{
                          detail.total_tokens
                            ? detail.total_tokens.toLocaleString()
                            : '-'
                        }}
                        <span
                          v-if="detail.llm_calls"
                          class="font-normal text-gray-500"
                        >
                          ·
                          {{ t('lensRuns.llmCalls', { n: detail.llm_calls }) }}
                        </span>
                        <span
                          v-if="detail.total_cost != null"
                          class="font-normal text-gray-500"
                        >
                          · ${{ detail.total_cost }}
                        </span>
                      </dd>
                    </div>
                    <div v-if="detail.structured_analysis_calls">
                      <dt class="overview-label">
                        {{ t('lensRuns.structuredAnalysisCalls') }}
                      </dt>
                      <dd class="overview-value tabular-nums">
                        {{ detail.structured_analysis_calls }}
                      </dd>
                    </div>
                    <div v-if="detail.structured_validation_calls">
                      <dt class="overview-label">
                        {{ t('lensRuns.structuredValidationCalls') }}
                      </dt>
                      <dd class="overview-value tabular-nums">
                        {{ detail.structured_validation_calls }}
                      </dd>
                    </div>
                    <div v-if="detail.transform_calls">
                      <dt class="overview-label">
                        {{ t('lensRuns.transformCalls') }}
                      </dt>
                      <dd class="overview-value tabular-nums">
                        {{ detail.transform_calls }}
                      </dd>
                    </div>
                  </dl>
                </section>

                <section
                  data-testid="run-overview-execution"
                  class="overview-section"
                >
                  <h3 class="overview-title">
                    {{ t('lensRuns.overviewExecution') }}
                  </h3>
                  <dl class="overview-grid">
                    <div>
                      <dt class="overview-label">
                        {{ t('lensRuns.colAssistant') }}
                      </dt>
                      <dd class="overview-value">
                        {{ detail.assistant_name || '-' }}
                      </dd>
                    </div>
                    <div>
                      <dt class="overview-label">
                        {{ t('lensRuns.modelsUsed') }}
                      </dt>
                      <dd
                        data-testid="run-models-used"
                        class="overview-value break-all"
                      >
                        {{ detail.models_used?.join(', ') || '-' }}
                      </dd>
                    </div>
                    <div>
                      <dt class="overview-label">
                        {{ t('lensAdmin.fields.agentRounds') }}
                      </dt>
                      <dd class="mt-1">
                        <span
                          data-testid="run-analysis-depth"
                          class="analysis-depth-pill"
                        >
                          {{ agentRoundsLabel }}
                        </span>
                      </dd>
                    </div>
                    <div>
                      <dt class="overview-label">{{ t('lensRuns.task') }}</dt>
                      <dd class="overview-value">
                        {{ detail.execution?.task || '-' }}
                      </dd>
                    </div>
                    <div>
                      <dt class="overview-label">
                        {{ t('lensRuns.lensnode') }}
                      </dt>
                      <dd class="overview-value">
                        {{ detail.lensnode_name || '-' }}
                      </dd>
                    </div>
                  </dl>
                </section>

                <section
                  data-testid="run-overview-timing"
                  class="overview-section"
                >
                  <h3 class="overview-title">
                    {{ t('lensRuns.overviewTiming') }}
                  </h3>
                  <dl class="overview-grid">
                    <div>
                      <dt class="overview-label">
                        {{ t('lensRuns.submittedAt') }}
                      </dt>
                      <dd class="overview-value tabular-nums">
                        {{ formatDateTime(detail.created_at) }}
                      </dd>
                    </div>
                    <div>
                      <dt class="overview-label">
                        {{ t('lensRuns.queueTime') }}
                      </dt>
                      <dd class="overview-value tabular-nums">
                        {{ queueText }}
                      </dd>
                    </div>
                    <div>
                      <dt class="overview-label">
                        {{ t('lensRuns.execTime') }}
                      </dt>
                      <dd class="overview-value tabular-nums">
                        {{ durationText(detail.duration_seconds) }}
                      </dd>
                    </div>
                    <div class="col-span-2">
                      <dt class="overview-label">
                        {{ t('lensRuns.execWindow') }}
                      </dt>
                      <dd class="overview-value tabular-nums">
                        {{ formatDateTime(detail.started_at) }}
                        <span class="font-normal text-gray-400">→</span>
                        {{ formatDateTime(detail.finished_at) }}
                      </dd>
                    </div>
                  </dl>
                </section>

                <section
                  v-if="detail.execution"
                  data-testid="run-overview-resources"
                  class="overview-section"
                >
                  <h3 class="overview-title">
                    {{ t('lensRuns.overviewResources') }}
                  </h3>
                  <dl class="overview-grid">
                    <div class="col-span-2">
                      <dt class="overview-label">
                        {{ t('lensRuns.resources') }}
                      </dt>
                      <dd class="overview-value">
                        {{ (detail.execution.loaded_skills || []).length }}
                        skills ·
                        {{ (detail.execution.loaded_mcps || []).length }} mcps
                      </dd>
                    </div>
                    <div class="col-span-2">
                      <dt class="overview-label">
                        {{ t('lensRuns.targetDirs') }}
                      </dt>
                      <dd class="overview-value break-all">
                        {{
                          (detail.execution.target_dirs || [])
                            .map((d) => d.path || d)
                            .join(', ') || '-'
                        }}
                      </dd>
                    </div>
                  </dl>
                </section>

                <section
                  v-if="hasPlannedEvidence"
                  data-testid="run-planned-evidence"
                  class="overview-section"
                >
                  <h3 class="overview-title">
                    {{ t('lensRuns.plannedEvidence') }}
                  </h3>
                  <p
                    v-if="plannedEvidence.planner_status === 'fallback'"
                    data-testid="planned-evidence-fallback"
                    class="mb-2 rounded-md border border-amber-300 bg-amber-50 px-2 py-1 text-xs text-amber-800"
                  >
                    {{ t('lensRuns.plannedEvidenceFallback') }}
                    <span
                      v-if="plannedEvidence.planner_rejection_reason"
                      class="mt-0.5 block font-mono"
                      :title="plannedEvidence.planner_rejection_reason"
                    >
                      {{ plannedEvidence.planner_rejection_reason }}
                    </span>
                  </p>
                  <dl class="overview-grid">
                    <div>
                      <dt class="overview-label">
                        {{ t('lensRuns.plannedEvidencePlannerStatus') }}
                      </dt>
                      <dd
                        class="overview-value"
                        :data-testid="`planner-status-${plannedEvidence.planner_status || 'none'}`"
                      >
                        {{ plannerStatusLabel }}
                      </dd>
                    </div>
                    <div>
                      <dt class="overview-label">
                        {{ t('lensRuns.plannedEvidenceModelCalls') }}
                      </dt>
                      <dd class="overview-value tabular-nums">
                        {{ plannedEvidence.model_call_count ?? '-' }}
                      </dd>
                    </div>
                    <div>
                      <dt class="overview-label">
                        {{ t('lensRuns.plannedEvidenceRetrievalCalls') }}
                      </dt>
                      <dd class="overview-value tabular-nums">
                        {{ plannedEvidence.retrieval_call_count ?? '-' }}
                      </dd>
                    </div>
                    <div>
                      <dt class="overview-label">
                        {{ t('lensRuns.plannedEvidenceTokens') }}
                      </dt>
                      <dd class="overview-value tabular-nums">
                        {{ plannedEvidence.evidence_tokens ?? '-' }}
                      </dd>
                    </div>
                    <div>
                      <dt class="overview-label">
                        {{ t('lensRuns.plannedEvidenceCitations') }}
                      </dt>
                      <dd class="overview-value tabular-nums">
                        {{ plannedEvidence.citation_count ?? '-' }}
                      </dd>
                    </div>
                  </dl>
                </section>

                <section>
                  <h3 class="text-sm font-semibold text-gray-700 mb-2">
                    {{ t('lensRuns.question') }}
                  </h3>
                  <div
                    class="rounded-md border border-gray-200 bg-gray-50 p-3 text-sm text-gray-800 whitespace-pre-wrap"
                  >
                    {{ detail.question || '-' }}
                  </div>
                </section>

                <section v-if="detail.attachments && detail.attachments.length">
                  <h3 class="text-sm font-semibold text-gray-700 mb-2">
                    {{ t('lensRuns.attachments') }}
                  </h3>
                  <div class="flex flex-wrap gap-3">
                    <div
                      v-for="img in detail.attachments.filter(
                        (item) => item.kind !== 'document'
                      )"
                      :key="img.uuid"
                      class="flex flex-col gap-1"
                    >
                      <AuthImage
                        :src="img.url"
                        :alt="img.original_name || 'image'"
                        class="run-attachment"
                        zoomable
                      />
                      <span class="text-xs text-gray-500">
                        {{
                          img.source === 'inherited'
                            ? t('lensRuns.inheritedAttachment')
                            : t('lensRuns.directAttachment')
                        }}
                      </span>
                    </div>
                    <button
                      v-for="file in detail.attachments.filter(
                        (item) => item.kind === 'document'
                      )"
                      :key="file.uuid"
                      type="button"
                      class="run-document-attachment"
                      @click="
                        downloadOutputFile({
                          ...file,
                          filename: file.original_name
                        })
                      "
                    >
                      <FileText :size="20" aria-hidden="true" />
                      <span>{{ file.original_name }}</span>
                      <span class="text-xs text-gray-500">
                        {{
                          file.source === 'inherited'
                            ? t('lensRuns.inheritedAttachment')
                            : t('lensRuns.directAttachment')
                        }}
                      </span>
                      <Download :size="16" aria-hidden="true" />
                    </button>
                  </div>
                  <p v-if="visionQuery" class="mt-2 text-xs text-gray-500">
                    {{ t('lensRuns.visionQuery') }}: {{ visionQuery }}
                  </p>
                  <p
                    v-if="visionFailureReason"
                    class="mt-2 text-xs text-red-600"
                  >
                    {{ t('lensRuns.visionFailureReason') }}:
                    {{ visionFailureReason }}
                  </p>
                </section>

                <section v-if="detail.answer">
                  <h3 class="text-sm font-semibold text-gray-700 mb-2">
                    {{ t('lensRuns.answer') }}
                  </h3>
                  <div class="rounded-md border border-gray-200 p-3">
                    <MarkdownRenderer :content="detail.answer" />
                  </div>
                </section>

                <section v-if="detail.error">
                  <h3 class="text-sm font-semibold text-red-600 mb-2">
                    {{ t('lensRuns.error') }}
                  </h3>
                  <pre
                    class="rounded-md border border-red-200 bg-red-50 p-3 text-xs text-red-700 whitespace-pre-wrap"
                  >
                    {{ detail.error }}</pre
                  >
                </section>
              </div>

              <!-- Diagnosis tab -->
              <RunDiagnosisPanel
                v-show="activeDetailTab === 'diagnosis'"
                ref="diagnosisPanel"
                :run-uuid="selectedUuid"
                :active="activeDetailTab === 'diagnosis'"
                @navigate="navigateFromEvidence"
              />

              <!-- Trace tab -->
              <div
                v-show="activeDetailTab === 'trace'"
                class="px-6 py-5 space-y-6"
              >
                <section
                  v-if="detail.llm_calls"
                  data-testid="run-token-summary"
                  class="rounded-lg border border-indigo-100 bg-indigo-50/60 p-4"
                >
                  <div class="flex items-start justify-between gap-4">
                    <div>
                      <h3 class="text-xs font-medium text-indigo-700">
                        {{ t('lensRuns.totalTokens') }}
                      </h3>
                      <p
                        class="mt-1 text-2xl font-semibold tracking-tight text-gray-900 tabular-nums"
                      >
                        {{ (detail.total_tokens || 0).toLocaleString() }}
                      </p>
                    </div>
                    <div
                      v-if="detail.total_cost != null"
                      class="rounded-md bg-white/80 px-3 py-2 text-right ring-1 ring-inset ring-indigo-100"
                    >
                      <p class="text-xs text-gray-500">
                        {{ t('lensRuns.cost') }}
                      </p>
                      <p class="mt-0.5 font-medium text-gray-900 tabular-nums">
                        ${{ detail.total_cost }}
                      </p>
                    </div>
                  </div>

                  <dl
                    class="mt-4 grid grid-cols-2 gap-px overflow-hidden rounded-md border border-indigo-100 bg-indigo-100 sm:grid-cols-4"
                  >
                    <div class="bg-white px-3 py-2.5">
                      <dt class="text-xs text-gray-500">
                        {{ t('lensRuns.promptTokens') }}
                      </dt>
                      <dd class="mt-1 font-medium text-gray-900 tabular-nums">
                        {{ (detail.prompt_tokens || 0).toLocaleString() }}
                      </dd>
                    </div>
                    <div class="bg-white px-3 py-2.5">
                      <dt class="text-xs text-gray-500">
                        {{ t('lensRuns.completionTokens') }}
                      </dt>
                      <dd class="mt-1 font-medium text-gray-900 tabular-nums">
                        {{ (detail.completion_tokens || 0).toLocaleString() }}
                      </dd>
                    </div>
                    <div class="bg-white px-3 py-2.5">
                      <dt class="text-xs text-gray-500">
                        {{ t('lensRuns.cachedTokens') }}
                      </dt>
                      <dd class="mt-1 font-medium text-gray-900 tabular-nums">
                        {{ (detail.cached_tokens || 0).toLocaleString() }}
                      </dd>
                    </div>
                    <div class="bg-white px-3 py-2.5">
                      <dt class="text-xs text-gray-500">
                        {{ t('lensRuns.reasoningTokens') }}
                      </dt>
                      <dd class="mt-1 font-medium text-gray-900 tabular-nums">
                        {{ (detail.reasoning_tokens || 0).toLocaleString() }}
                      </dd>
                    </div>
                  </dl>

                  <div class="mt-3 flex flex-wrap gap-2">
                    <span class="token-summary-pill">
                      {{ t('lensRuns.llmCalls', { n: detail.llm_calls }) }}
                    </span>
                    <span
                      v-if="detail.structured_analysis_calls"
                      class="token-summary-pill"
                    >
                      {{
                        t('lensRuns.structuredAnalysisCallsCount', {
                          n: detail.structured_analysis_calls
                        })
                      }}
                    </span>
                    <span
                      v-if="detail.structured_validation_calls"
                      class="token-summary-pill"
                    >
                      {{
                        t('lensRuns.structuredValidationCallsCount', {
                          n: detail.structured_validation_calls
                        })
                      }}
                    </span>
                    <span
                      v-if="detail.transform_calls"
                      class="token-summary-pill"
                    >
                      {{
                        t('lensRuns.transformCallsCount', {
                          n: detail.transform_calls
                        })
                      }}
                    </span>
                    <span
                      v-if="detail.subagent_model_calls"
                      class="token-summary-pill token-summary-pill-accent"
                    >
                      {{
                        t('lensRuns.subagentModelCalls', {
                          n: detail.subagent_model_calls
                        })
                      }}
                    </span>
                  </div>
                </section>

                <RunTrajectoryPanel
                  :run-uuid="selectedUuid"
                  :active="activeDetailTab === 'trace'"
                />
              </div>

              <!-- Files tab -->
              <div v-show="activeDetailTab === 'files'" class="px-6 py-5">
                <div
                  v-if="detail.output_files && detail.output_files.length"
                  class="space-y-3"
                >
                  <div
                    v-for="file in detail.output_files"
                    :key="file.uuid"
                    class="rounded-lg border border-gray-200 bg-white p-4"
                  >
                    <div class="flex items-start gap-3">
                      <span
                        class="flex h-10 w-10 shrink-0 items-center justify-center rounded-md bg-gray-100 text-gray-500"
                      >
                        <FileText :size="20" aria-hidden="true" />
                      </span>
                      <div class="min-w-0 flex-1">
                        <p
                          class="truncate text-sm font-medium text-gray-900"
                          :title="file.filename"
                        >
                          {{ file.filename }}
                        </p>
                        <dl
                          class="mt-2 grid gap-x-4 gap-y-1 text-xs text-gray-500 sm:grid-cols-3"
                        >
                          <div>
                            <dt class="sr-only">
                              {{ t('lensRuns.fileType') }}
                            </dt>
                            <dd>{{ file.content_type || '-' }}</dd>
                          </div>
                          <div>
                            <dt class="sr-only">
                              {{ t('lensRuns.fileSize') }}
                            </dt>
                            <dd>{{ formatBytes(file.byte_size) }}</dd>
                          </div>
                          <div>
                            <dt class="sr-only">
                              {{ t('lensRuns.fileCreated') }}
                            </dt>
                            <dd data-testid="output-file-created">
                              {{ formatDateTime(file.created_at) }}
                            </dd>
                          </div>
                        </dl>
                      </div>
                      <div class="flex shrink-0 items-center gap-1">
                        <button
                          v-if="isPreviewable(file)"
                          type="button"
                          data-testid="preview-output-file"
                          class="rounded-md p-2 text-gray-400 transition-colors hover:bg-gray-100 hover:text-primary-600"
                          :aria-label="
                            t('lensRuns.previewFile', { name: file.filename })
                          "
                          @click="openPreview(file)"
                        >
                          <Eye :size="18" aria-hidden="true" />
                        </button>
                        <button
                          type="button"
                          data-testid="download-output-file"
                          class="rounded-md p-2 text-gray-400 transition-colors hover:bg-gray-100 hover:text-primary-600"
                          :aria-label="
                            t('lensRuns.downloadFile', { name: file.filename })
                          "
                          @click="downloadOutputFile(file)"
                        >
                          <Download :size="18" aria-hidden="true" />
                        </button>
                      </div>
                    </div>
                  </div>
                </div>
                <p
                  v-else
                  class="py-12 text-center text-sm text-gray-400"
                  data-testid="run-files-empty"
                >
                  {{ t('lensRuns.noFiles') }}
                </p>
              </div>
            </div>
          </div>
        </div>
      </Transition>
      <FilePreviewModal
        :file="previewFile"
        @close="closePreview"
        @download="downloadOutputFile"
      />
    </div>
  </AdminLayout>
</template>

<script setup>
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute, useRouter } from 'vue-router'
import { format } from 'date-fns'
import { useDebounceFn } from '@vueuse/core'
import { Download, Eye, FileText, ThumbsDown, ThumbsUp } from '@lucide/vue'
import { useToast } from '@/composables/useToast'
import { extractErrorMessage } from '@/utils/api'
import { fetchDeliverableBlob, isPreviewable } from '@/utils/filePreview'
import { getAdminRuns, getAdminRun, listAssistants } from '@/api/lens'
import AdminLayout from '@/admin/layout/AdminLayout.vue'
import RunDiagnosisPanel from '@/admin/pages/lens/RunDiagnosisPanel.vue'
import RunTrajectoryPanel from '@/admin/pages/lens/RunTrajectoryPanel.vue'
import FilePreviewModal from '@/components/lens/FilePreviewModal.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseDateInput from '@/components/ui/BaseDateInput.vue'
import BaseLoading from '@/components/ui/BaseLoading.vue'
import BaseSelect from '@/components/ui/BaseSelect.vue'
import PaginationBar from '@/components/ui/PaginationBar.vue'
import MarkdownRenderer from '@/components/ui/MarkdownRenderer.vue'
import AuthImage from '@/components/ui/AuthImage.vue'
import { useUserStore } from '@/store/user'

const { t } = useI18n()
const { showError } = useToast()
const route = useRoute()
const router = useRouter()
const userStore = useUserStore()

const loading = ref(false)
const runs = ref([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)
const assistants = ref([])

const detailVisible = ref(false)
const detailLoading = ref(false)
const detail = ref(null)
const selectedUuid = ref(null)
const activeDetailTab = ref('overview')
const previewFile = ref(null)
const diagnosisPanel = ref(null)

const canDiagnoseRun = computed(() => {
  const user = userStore.userInfo
  return Boolean(
    user?.is_staff ||
      user?.is_superuser ||
      userStore.userHasPermission('lens.run_diagnostics')
  )
})

const canGenerateDiagnosis = computed(
  () =>
    Boolean(detail.value) &&
    ['done', 'failed', 'cancelled'].includes(detail.value.status)
)

const filters = ref({
  q: '',
  username: '',
  user_id: '',
  group_id: '',
  assistant: '',
  status: '',
  start_date: '',
  end_date: ''
})

const totalPages = computed(() =>
  total.value > 0 ? Math.ceil(total.value / pageSize.value) : 1
)

const visionQuery = computed(() => {
  const step = (detail.value?.steps || []).find(
    (item) => item.step_type === 'multimodal'
  )
  return step?.multimodal?.query || ''
})

const visionFailureReason = computed(() => {
  const step = (detail.value?.steps || []).find(
    (item) => item.step_type === 'multimodal'
  )
  return step?.failure_reason || ''
})

const plannedEvidence = computed(() => detail.value?.planned_evidence || {})
const hasPlannedEvidence = computed(
  () => Object.keys(plannedEvidence.value).length > 0
)
const plannerStatusLabel = computed(() => {
  const status = plannedEvidence.value.planner_status
  if (!status) return '-'
  const key = { valid: 'valid', repaired: 'repaired', fallback: 'fallback' }[
    status
  ]
  return key ? t(`lensRuns.plannedEvidencePlannerStatus.${key}`) : status
})

const AGENT_ROUNDS_KEYS = {
  flash: 'flash',
  fast: 'fast',
  balanced: 'balanced',
  deep: 'deep',
  max: 'max'
}

const agentRoundsLabel = computed(() => {
  const value = detail.value?.agent_rounds
  const key = AGENT_ROUNDS_KEYS[value]
  return key ? t(`lensAdmin.agentRounds.${key}`) : value || '-'
})

const hasFailureSummary = computed(() => {
  const summary = detail.value?.failure_summary
  return Boolean(
    summary &&
      (summary.unresolved_failure_count ||
        summary.recovered_failure_count ||
        summary.warning_count)
  )
})

function formatDate(val) {
  if (!val) return '-'
  try {
    return format(new Date(val), 'yyyy-MM-dd HH:mm')
  } catch {
    return String(val)
  }
}

function durationText(sec) {
  if (sec === null || sec === undefined) return '-'
  if (sec < 60) return `${Math.round(sec)}s`
  return `${Math.floor(sec / 60)}m ${Math.round(sec % 60)}s`
}

function statusClass(status) {
  const s = (status || '').toLowerCase()
  const base = 'text-xs font-medium px-2 py-0.5 rounded'
  if (['completed', 'done'].includes(s)) {
    return `${base} bg-green-100 text-green-800`
  }
  if (['blocked', 'failed'].includes(s)) {
    return `${base} bg-red-100 text-red-800`
  }
  if (s === 'partial') return `${base} bg-amber-100 text-amber-800`
  if (s === 'cancelled') return `${base} bg-gray-100 text-gray-600`
  if (['running', 'streaming', 'queued'].includes(s))
    return `${base} bg-blue-100 text-blue-800`
  return `${base} bg-gray-100 text-gray-600`
}

function formatDateTime(val) {
  if (!val) return '-'
  try {
    return format(new Date(val), 'MM-dd HH:mm:ss')
  } catch {
    return String(val)
  }
}

function formatBytes(size) {
  if (size === null || size === undefined) return '-'
  if (size < 1024) return `${size} B`
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`
  return `${(size / (1024 * 1024)).toFixed(1)} MB`
}

function openPreview(file) {
  previewFile.value = file
}

function closePreview() {
  previewFile.value = null
}

async function downloadOutputFile(file) {
  if (!file?.url) return
  try {
    const blob = await fetchDeliverableBlob(file)
    const objectUrl = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = objectUrl
    link.download = file.filename || 'download'
    document.body.appendChild(link)
    link.click()
    document.body.removeChild(link)
    URL.revokeObjectURL(objectUrl)
  } catch {
    showError(t('lensRuns.downloadFailed'))
  }
}

const queueText = computed(() => {
  const d = detail.value
  if (!d?.created_at || !d?.started_at) return '-'
  const sec = (new Date(d.started_at) - new Date(d.created_at)) / 1000
  if (sec < 0) return '-'
  return sec < 1 ? '<1s' : durationText(sec)
})

function onFiltersChanged() {
  page.value = 1
  debouncedFetch()
}

function onUsernameChanged() {
  filters.value.user_id = ''
  filters.value.group_id = ''
  onFiltersChanged()
}

const debouncedFetch = useDebounceFn(() => fetchRuns(), 300)

function resetFilters() {
  filters.value = {
    q: '',
    username: '',
    user_id: '',
    group_id: '',
    assistant: '',
    status: '',
    start_date: '',
    end_date: ''
  }
  page.value = 1
  router.replace({ path: route.path })
  fetchRuns()
}

function handlePageSizeChange() {
  page.value = 1
  fetchRuns()
}

function goPrevPage() {
  if (page.value <= 1) return
  page.value -= 1
  fetchRuns()
}

function goNextPage() {
  if (page.value >= totalPages.value) return
  page.value += 1
  fetchRuns()
}

function openDetail(uuid) {
  selectedUuid.value = uuid
  detailVisible.value = true
  detail.value = null
  activeDetailTab.value = 'overview'
  previewFile.value = null
}

function closeDetail() {
  detailVisible.value = false
  selectedUuid.value = null
  detail.value = null
  previewFile.value = null
}

async function generateDiagnosis() {
  if (!canGenerateDiagnosis.value) return
  activeDetailTab.value = 'diagnosis'
  await nextTick()
  diagnosisPanel.value?.generate()
}

function navigateFromEvidence(evidenceRef) {
  if (String(evidenceRef).startsWith('E-FILE-')) {
    activeDetailTab.value = 'files'
  } else if (evidenceRef === 'E-RUN') {
    activeDetailTab.value = 'overview'
  } else {
    activeDetailTab.value = 'trace'
  }
}

async function fetchRuns() {
  loading.value = true
  try {
    const params = { page: page.value, page_size: pageSize.value }
    for (const [k, v] of Object.entries(filters.value)) {
      if (v) params[k] = v
    }
    const data = await getAdminRuns(params)
    runs.value = data?.results ?? []
    total.value = data?.total ?? 0
  } catch (e) {
    showError(extractErrorMessage(e, t('common.error')))
    runs.value = []
    total.value = 0
  } finally {
    loading.value = false
  }
}

async function fetchDetail() {
  if (!selectedUuid.value) return
  detailLoading.value = true
  detail.value = null
  try {
    detail.value = await getAdminRun(selectedUuid.value)
  } catch (e) {
    showError(extractErrorMessage(e, t('common.error')))
    detail.value = null
  } finally {
    detailLoading.value = false
  }
}

onMounted(async () => {
  filters.value.user_id = String(route.query.user_id || '')
  filters.value.group_id = String(route.query.group_id || '')
  filters.value.username = String(route.query.username || '')
  filters.value.assistant = String(route.query.assistant || '')
  try {
    assistants.value = await listAssistants()
  } catch {
    assistants.value = []
  }
  fetchRuns()
})

watch(detailVisible, (visible) => {
  if (visible && selectedUuid.value) fetchDetail()
})
</script>

<style scoped>
.run-attachment :deep(.auth-image) {
  max-width: 180px;
  max-height: 180px;
  object-fit: cover;
  border: 1px solid #e5e7eb;
}
.run-document-attachment {
  @apply flex max-w-sm items-center gap-2 rounded-lg border border-gray-200
    bg-white px-3 py-2 text-left text-sm text-gray-700;
}
.run-document-attachment span {
  @apply truncate;
}
.run-detail-id {
  @apply ml-2 break-all font-mono text-xs font-normal text-gray-500;
}
.overview-section {
  @apply rounded-lg border border-gray-200 bg-gray-50/70 p-4;
}
.overview-title {
  @apply text-sm font-semibold text-gray-800;
}
.overview-grid {
  @apply mt-3 grid grid-cols-2 gap-x-4 gap-y-3 text-sm;
}
.overview-label {
  @apply text-xs text-gray-500;
}
.overview-value {
  @apply mt-1 text-sm font-medium text-gray-900;
}
.analysis-depth-pill {
  @apply inline-flex rounded-full border border-primary-200 bg-primary-50 px-2.5
    py-1 text-xs font-semibold text-primary-700;
}
.failure-pill {
  @apply inline-flex rounded-full px-2.5 py-1 text-xs font-semibold;
}
.failure-pill-error {
  @apply bg-red-100 text-red-800;
}
.failure-pill-recovered {
  @apply bg-green-100 text-green-800;
}
.failure-pill-warning {
  @apply bg-amber-100 text-amber-800;
}
.token-summary-pill {
  @apply inline-flex rounded-full border border-indigo-100 bg-white px-2.5 py-1
    text-xs font-medium text-gray-600;
}
.token-summary-pill-accent {
  @apply text-indigo-700;
}
.th {
  @apply px-4 py-3 text-left text-xs font-semibold text-gray-700 uppercase tracking-wider;
}
.td {
  @apply px-4 py-3 text-sm;
}

.feedback-pill {
  @apply inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-semibold;
}

.feedback-pill-positive {
  @apply bg-green-100 text-green-800;
}

.feedback-pill-negative {
  @apply bg-red-100 text-red-800;
}

.detail-tab {
  @apply py-3 text-sm font-medium border-b-2 border-transparent text-gray-500 transition-colors;
}
.detail-tab:hover {
  @apply text-gray-700;
}
.detail-tab-active {
  @apply border-primary-500 text-primary-600;
}

.timeline {
  @apply pl-1;
}
.timeline-item {
  @apply relative pl-5 pb-4;
  border-left: 1.5px solid #e5e7eb;
}
.timeline-item:last-child {
  @apply pb-0;
  border-left-color: transparent;
}
.timeline-dot {
  @apply absolute left-0 top-1 h-2.5 w-2.5 rounded-full ring-2 ring-white;
  transform: translateX(-50%);
}
.timeline-row {
  @apply flex items-baseline justify-between gap-3;
}
.timeline-text {
  @apply text-sm text-gray-800 break-words;
}
.timeline-time {
  @apply shrink-0 text-xs text-gray-400 tabular-nums;
}
.timeline-detail {
  @apply mt-0.5 text-xs text-gray-500 break-all;
}

.timeline-preview {
  @apply mt-1 rounded border border-gray-100 bg-gray-50 px-2 py-1 text-xs
    text-gray-600 break-all;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.dot-blue {
  background: #3b82f6;
}
.dot-purple {
  background: #8b5cf6;
}
.dot-green {
  background: #10b981;
}
.dot-amber {
  background: #f59e0b;
}
.dot-red {
  background: #ef4444;
}
.dot-gray {
  background: #9ca3af;
}
.dot-indigo {
  background: #6366f1;
}
</style>
