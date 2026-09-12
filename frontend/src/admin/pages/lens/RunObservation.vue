<template>
  <AdminLayout>
    <div class="flex max-w-full flex-col gap-4 py-4">
      <section class="admin-data-panel overflow-hidden">
        <div
          class="flex flex-col gap-4 border-b border-line px-5 py-4 lg:flex-row lg:items-start lg:justify-between"
        >
          <div class="min-w-0">
            <h1 class="admin-page-title">
              {{ t('lensRuns.title') }}
            </h1>
            <p class="admin-page-subtitle">
              {{ t('lensRuns.subtitle') }}
            </p>
          </div>
          <div class="flex flex-wrap items-center gap-2">
            <BaseButton
              variant="outline"
              size="sm"
              :loading="loading"
              :title="t('common.refresh')"
              @click="fetchRuns"
            >
              {{ t('common.refresh') }}
            </BaseButton>
          </div>
        </div>

        <div
          data-testid="run-status-summary"
          class="grid grid-cols-2 gap-3 border-b border-line bg-surface-sunken/50 px-5 py-4 md:grid-cols-5"
        >
          <button
            v-for="item in statusSummaryCards"
            :key="item.status"
            type="button"
            class="rounded-lg border bg-surface px-4 py-3 text-left transition-colors hover:border-primary-300"
            :class="
              filters.status === item.status
                ? 'border-primary-400 ring-1 ring-primary-200'
                : 'border-line'
            "
            @click="setStatusFilter(item.status)"
          >
            <p class="text-xs font-medium text-ink-500">{{ item.label }}</p>
            <p class="admin-metric-value mt-1">
              <span
                v-if="loading && !hasLoaded"
                class="metric-loading"
                aria-hidden="true"
              >
                …
              </span>
              <span v-else>{{ formatOperationMetric(item.count) }}</span>
            </p>
          </button>
        </div>

        <div class="flex min-h-0 flex-col px-5 py-4">
          <div class="mb-4 flex flex-col border-b border-line pb-4">
            <div
              class="flex w-full min-w-0 flex-col items-stretch gap-3 xl:flex-row xl:items-center"
            >
              <div
                class="grid min-w-0 flex-1 gap-3 sm:grid-cols-2 xl:grid-cols-4"
              >
                <input
                  v-model="filters.q"
                  type="text"
                  :placeholder="t('lensRuns.filterKeyword')"
                  class="min-h-11 w-full min-w-0 rounded-md border border-line bg-surface text-ink-900 px-2.5 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-primary-500 md:min-h-9"
                  @input="onFiltersChanged"
                />
                <input
                  v-model="filters.username"
                  type="text"
                  :placeholder="t('lensRuns.filterUsername')"
                  class="min-h-11 w-full min-w-0 rounded-md border border-line bg-surface text-ink-900 px-2.5 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-primary-500 md:min-h-9"
                  @input="onUsernameChanged"
                />
                <BaseSelect
                  v-model="filters.assistant"
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
                  mobile-touch
                  @change="onFiltersChanged"
                >
                  <option value="">{{ t('lensRuns.statusAll') }}</option>
                  <option value="done">{{ t('lensRuns.statusDone') }}</option>
                  <option value="failed">
                    {{ t('lensRuns.statusFailed') }}
                  </option>
                  <option value="active">
                    {{ t('lensRuns.statusRunning') }}
                  </option>
                  <option value="queued">
                    {{ t('lensRuns.statusQueued') }}
                  </option>
                  <option value="cancelled">
                    {{ t('lensRuns.statusCancelled') }}
                  </option>
                </BaseSelect>
              </div>
              <div class="flex w-full shrink-0 items-center gap-2 md:w-auto">
                <BaseButton
                  data-testid="toggle-run-advanced-filters"
                  variant="outline"
                  size="sm"
                  class="flex-1 md:flex-none"
                  :aria-expanded="advancedFiltersOpen"
                  aria-controls="run-advanced-filters"
                  @click="advancedFiltersOpen = !advancedFiltersOpen"
                >
                  {{ t('lensRuns.moreFilters') }}
                  <span
                    v-if="advancedFilterCount"
                    class="rounded-full bg-primary-100 px-1.5 text-xs text-primary-700"
                  >
                    {{ advancedFilterCount }}
                  </span>
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

            <div
              v-if="advancedFiltersOpen"
              id="run-advanced-filters"
              data-testid="run-advanced-filters"
              class="mt-3 grid gap-3 border-t border-line pt-3 md:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_auto] md:items-center"
            >
              <input
                v-model="filters.lensnode"
                type="text"
                :placeholder="t('lensRuns.filterLensNode')"
                class="min-h-11 w-full min-w-0 rounded-md border border-line bg-surface text-ink-900 px-2.5 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-primary-500 md:min-h-9"
                @input="onFiltersChanged"
              />
              <input
                v-model="filters.model"
                type="text"
                :placeholder="t('lensRuns.filterModel')"
                class="min-h-11 w-full min-w-0 rounded-md border border-line bg-surface text-ink-900 px-2.5 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-primary-500 md:min-h-9"
                @input="onFiltersChanged"
              />
              <div class="flex w-full shrink-0 items-center gap-2 md:w-auto">
                <BaseDateInput
                  v-model="filters.start_date"
                  compact
                  :max="filters.end_date || undefined"
                  @change="onFiltersChanged"
                />
                <span class="text-ink-400">–</span>
                <BaseDateInput
                  v-model="filters.end_date"
                  compact
                  :min="filters.start_date || undefined"
                  @change="onFiltersChanged"
                />
              </div>
            </div>
          </div>

          <template v-if="loading && !hasLoaded">
            <div
              data-testid="run-loading-state"
              class="flex min-h-52 items-center justify-center"
              role="status"
              aria-live="polite"
            >
              <BaseLoading size="sm" :text="t('lensRuns.loading')" />
            </div>
          </template>

          <template v-else>
            <div
              v-if="loading && hasLoaded"
              data-testid="run-refresh-loading"
              class="mb-4 flex min-h-10 items-center justify-center"
              role="status"
              aria-live="polite"
            >
              <BaseLoading size="sm" :text="t('lensRuns.loading')" />
            </div>

            <div
              v-if="loadError && hasLoaded"
              data-testid="run-refresh-error"
              class="mb-4 flex flex-wrap items-center justify-between gap-3 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800"
              role="alert"
            >
              <span>{{ loadError }}</span>
              <BaseButton size="sm" variant="outline" @click="fetchRuns">
                {{ t('lensRuns.retry') }}
              </BaseButton>
            </div>

            <div
              v-if="loadError && !hasLoaded"
              data-testid="run-error-state"
              class="flex min-h-52 flex-col items-center justify-center rounded-lg border border-red-200 bg-red-50 px-6 py-12 text-center"
              role="alert"
            >
              <p class="text-sm font-medium text-red-800">
                {{ t('lensRuns.loadErrorTitle') }}
              </p>
              <p class="mt-1 max-w-md text-sm text-red-700">
                {{ loadError }}
              </p>
              <BaseButton class="mt-4" size="sm" @click="fetchRuns">
                {{ t('lensRuns.retry') }}
              </BaseButton>
            </div>

            <div
              v-else-if="hasLoaded && !loadError && runs.length === 0"
              data-testid="run-empty-state"
              class="rounded-lg border border-line bg-surface-sunken py-16 text-center"
            >
              <p class="text-sm font-medium text-ink-600">
                {{ t('lensRuns.noRuns') }}
              </p>
            </div>

            <div v-else class="flex flex-col md:min-h-9">
              <div
                data-testid="mobile-run-observation-list"
                class="space-y-3 md:hidden"
              >
                <button
                  v-for="r in runs"
                  :key="`mobile-${r.uuid}`"
                  type="button"
                  class="block w-full rounded-lg border border-line bg-surface p-4 text-left shadow-sm transition-colors hover:border-primary-200 hover:bg-primary-50/30 focus:outline-none focus-visible:ring-2 focus-visible:ring-primary-500/30"
                  :aria-label="`${t('common.viewDetails')}: ${r.question || '-'}`"
                  @click="openDetail(r.uuid)"
                >
                  <div class="flex items-start justify-between gap-3">
                    <div class="min-w-0 flex-1">
                      <h2
                        class="line-clamp-3 text-sm font-semibold leading-5 text-ink-900"
                      >
                        {{ r.question || '-' }}
                      </h2>
                      <p class="mt-2 text-xs text-ink-500">
                        {{ formatDate(r.created_at) }}
                      </p>
                    </div>
                    <span :class="statusClass(r.status)">
                      {{ statusText(r.status) }}
                    </span>
                  </div>

                  <dl class="mt-4 grid grid-cols-2 gap-x-4 gap-y-3 text-sm">
                    <div>
                      <dt class="text-xs text-ink-500">
                        {{ t('lensRuns.colUser') }}
                      </dt>
                      <dd class="mt-0.5 truncate font-medium text-ink-800">
                        {{ r.username || '-' }}
                      </dd>
                    </div>
                    <div>
                      <dt class="text-xs text-ink-500">
                        {{ t('lensRuns.colAssistant') }}
                      </dt>
                      <dd class="mt-0.5 truncate font-medium text-ink-800">
                        {{ r.assistant_name || '-' }}
                      </dd>
                    </div>
                    <div>
                      <dt class="text-xs text-ink-500">
                        {{ t('lensRuns.colDuration') }}
                      </dt>
                      <dd class="mt-0.5 font-medium tabular-nums text-ink-800">
                        {{ durationText(r.duration_seconds) }}
                      </dd>
                    </div>
                    <div>
                      <dt class="text-xs text-ink-500">
                        {{ t('lensRuns.colSteps') }}
                      </dt>
                      <dd class="mt-0.5 font-medium tabular-nums text-ink-800">
                        {{ r.event_count }}
                        <span
                          v-if="r.subagent_count > 0"
                          class="text-xs text-indigo-600"
                        >
                          ·
                          {{ t('lensRuns.subagents', { n: r.subagent_count }) }}
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
                    <span v-else class="text-xs text-ink-400">
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
                class="relative hidden max-h-full overflow-auto rounded-lg border border-line bg-surface shadow-sm md:block"
              >
                <table class="min-w-full divide-y divide-line">
                  <thead class="sticky top-0 z-10 bg-surface-sunken">
                    <tr>
                      <th class="th">{{ t('lensRuns.colTime') }}</th>
                      <th class="th">{{ t('lensRuns.colUser') }}</th>
                      <th class="th">{{ t('lensRuns.colExecutor') }}</th>
                      <th class="th">{{ t('lensRuns.colQuestion') }}</th>
                      <th class="th">{{ t('lensRuns.colStatus') }}</th>
                      <th class="th">{{ t('lensRuns.colFeedback') }}</th>
                      <th class="th">{{ t('lensRuns.colDuration') }}</th>
                      <th class="th">{{ t('lensRuns.colOperations') }}</th>
                      <th class="th">{{ t('common.actions') }}</th>
                    </tr>
                  </thead>
                  <tbody class="bg-surface divide-y divide-gray-100">
                    <tr
                      v-for="r in runs"
                      :key="r.uuid"
                      class="hover:bg-surface-sunken cursor-pointer transition-colors"
                      @click="openDetail(r.uuid)"
                    >
                      <td class="td text-ink-600 whitespace-nowrap">
                        {{ formatDate(r.created_at) }}
                      </td>
                      <td class="td text-ink-900 whitespace-nowrap">
                        {{ r.username || '-' }}
                      </td>
                      <td class="td whitespace-nowrap text-ink-600">
                        <div>{{ r.assistant_name || '-' }}</div>
                        <div class="mt-1 text-xs text-ink-400">
                          {{ r.lensnode_name || '-' }}
                        </div>
                      </td>
                      <td class="td text-ink-700 max-w-md truncate">
                        {{ r.question || '-' }}
                      </td>
                      <td class="td whitespace-nowrap">
                        <span :class="statusClass(r.status)">
                          {{ statusText(r.status) }}
                        </span>
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
                        <span v-else class="text-ink-400">—</span>
                      </td>
                      <td
                        class="td text-ink-600 whitespace-nowrap tabular-nums"
                      >
                        {{ durationText(r.duration_seconds) }}
                      </td>
                      <td class="td whitespace-nowrap text-ink-600">
                        <div class="text-xs">
                          {{
                            t('lensRuns.toolCallsShort', {
                              n: r.tool_call_count
                            })
                          }}
                          ·
                          {{ t('lensRuns.retriesShort', { n: r.retry_count }) }}
                        </div>
                        <div class="mt-1 text-xs text-ink-400">
                          {{ r.model_ref || '-' }} ·
                          {{ budgetPercent(r.budget_consumption) }}
                        </div>
                      </td>
                      <td class="td whitespace-nowrap" @click.stop>
                        <div class="flex items-center gap-1">
                          <BaseButton
                            v-if="r.available_actions?.cancel"
                            size="sm"
                            variant="outline"
                            @click="requestRunAction('cancel', r)"
                          >
                            {{ t('lensRuns.cancelAction') }}
                          </BaseButton>
                          <BaseButton
                            v-if="r.available_actions?.retry"
                            size="sm"
                            variant="outline"
                            @click="requestRunAction('retry', r)"
                          >
                            {{ t('lensRuns.retryAction') }}
                          </BaseButton>
                          <BaseButton
                            size="sm"
                            variant="ghost"
                            @click="openDetail(r.uuid)"
                          >
                            {{ t('common.viewDetails') }}
                          </BaseButton>
                        </div>
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
          </template>
        </div>
      </section>

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
            class="flex flex-wrap items-center gap-3 border-b border-gray-200 bg-gradient-to-r from-gray-50 to-gray-100 px-6 py-4 flex-shrink-0"
          >
            <h2 class="min-w-0 flex-1 text-lg font-semibold text-gray-900">
              <span>{{ t('lensRuns.detailTitle') }}</span>
              <span
                v-if="selectedUuid"
                data-testid="run-detail-id"
                class="run-detail-id"
              >
                {{ selectedUuid }}
              </span>
            </h2>
            <div
              class="flex w-full shrink-0 items-center justify-end gap-2 md:w-auto"
            >
              <BaseButton
                v-if="detail && detail.available_actions?.resume"
                size="sm"
                variant="outline"
                @click="requestRunAction('resume', detail)"
              >
                {{ t('lensRuns.resumeAction') }}
              </BaseButton>
              <BaseButton
                v-if="detail?.available_actions?.cancel"
                size="sm"
                variant="outline"
                @click="requestRunAction('cancel', detail)"
              >
                {{ t('lensRuns.cancelAction') }}
              </BaseButton>
              <BaseButton
                v-if="detail?.available_actions?.retry"
                size="sm"
                variant="outline"
                @click="requestRunAction('retry', detail)"
              >
                {{ t('lensRuns.retryAction') }}
              </BaseButton>
              <RowActionMenu
                v-if="detail"
                data-testid="run-detail-more-actions"
                :actions="detailMoreActions"
                :label="t('common.moreActions')"
                @select="handleDetailMoreAction"
              />
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
                class="sticky top-0 z-10 flex gap-5 overflow-x-auto border-b border-gray-200 bg-white px-6"
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
                  data-testid="run-execution-tab"
                  :class="
                    activeDetailTab === 'execution' ? 'detail-tab-active' : ''
                  "
                  @click="activeDetailTab = 'execution'"
                >
                  {{ t('lensRuns.tabExecutionAnalysis') }}
                  <span class="ml-1 text-xs text-gray-400">{{
                    detail.trace_event_count ?? detail.event_count
                  }}</span>
                </button>
                <button
                  class="detail-tab"
                  data-testid="run-results-tab"
                  :class="
                    activeDetailTab === 'results' ? 'detail-tab-active' : ''
                  "
                  @click="activeDetailTab = 'results'"
                >
                  {{ t('lensRuns.tabResultsAndArtifacts') }}
                  <span class="ml-1 text-xs text-gray-400">{{
                    (detail.citations || []).length +
                    (detail.output_files || []).length
                  }}</span>
                </button>
              </div>

              <!-- Overview tab -->
              <div
                v-show="activeDetailTab === 'overview'"
                class="px-4 py-4 sm:px-6"
              >
                <div class="overview-dashboard">
                  <section
                    data-testid="run-overview-summary"
                    class="overview-section overview-hero"
                  >
                    <div class="overview-hero-header">
                      <div class="min-w-0">
                        <p class="overview-eyebrow">
                          {{ t('lensRuns.overviewSummary') }}
                        </p>
                        <h3 class="overview-hero-title truncate">
                          {{ detail.assistant_name || '-' }}
                        </h3>
                        <p class="overview-hero-context truncate">
                          {{ detail.execution?.task || '-' }}
                          <span aria-hidden="true">·</span>
                          {{ detail.lensnode_name || '-' }}
                        </p>
                      </div>
                      <div class="overview-status-group">
                        <div class="overview-status-item">
                          <span class="overview-label">
                            {{ t('lensRuns.executorStatus') }}
                          </span>
                          <span :class="statusClass(detail.executor_status)">
                            {{
                              statusText(
                                detail.executor_status || detail.status
                              )
                            }}
                          </span>
                        </div>
                        <div class="overview-status-item">
                          <span class="overview-label">
                            {{ t('lensRuns.businessOutcome') }}
                          </span>
                          <span :class="statusClass(detail.outcome)">
                            {{ statusText(detail.outcome) }}
                          </span>
                        </div>
                      </div>
                    </div>

                    <div class="overview-kpi-grid">
                      <div class="overview-kpi">
                        <span class="overview-label">
                          {{ t('lensRuns.colSteps') }}
                        </span>
                        <strong class="overview-kpi-value tabular-nums">
                          {{ detail.event_count }}
                        </strong>
                        <span
                          v-if="detail.subagent_count > 0"
                          class="overview-kpi-note"
                        >
                          {{
                            t('lensRuns.subagents', {
                              n: detail.subagent_count
                            })
                          }}
                        </span>
                      </div>
                      <div class="overview-kpi">
                        <span class="overview-label">
                          {{ t('lensRuns.totalTokens') }}
                        </span>
                        <strong class="overview-kpi-value tabular-nums">
                          {{ (detail.total_tokens || 0).toLocaleString() }}
                        </strong>
                      </div>
                      <div class="overview-kpi">
                        <span class="overview-label">
                          {{
                            t('lensRuns.toolCallsShort', {
                              n: detail.tool_call_count || 0
                            })
                          }}
                        </span>
                        <strong class="overview-kpi-value tabular-nums">
                          {{ detail.tool_call_count || 0 }}
                        </strong>
                      </div>
                      <div class="overview-kpi">
                        <span class="overview-label">
                          {{ t('lensRuns.execTime') }}
                        </span>
                        <strong class="overview-kpi-value tabular-nums">
                          {{ durationText(detail.duration_seconds) }}
                        </strong>
                      </div>
                    </div>

                    <div
                      data-testid="run-overview-execution"
                      class="overview-meta-row"
                    >
                      <div class="overview-meta-item">
                        <span class="overview-label">
                          {{ t('lensRuns.modelsUsed') }}
                        </span>
                        <span
                          data-testid="run-models-used"
                          class="overview-meta-value break-all"
                        >
                          {{ detail.models_used?.join(', ') || '-' }}
                        </span>
                      </div>
                      <div class="overview-meta-item">
                        <span class="overview-label">
                          {{ t('lensAdmin.fields.agentRounds') }}
                        </span>
                        <span
                          data-testid="run-analysis-depth"
                          class="analysis-depth-pill"
                        >
                          {{ agentRoundsLabel }}
                        </span>
                      </div>
                      <div class="overview-meta-item">
                        <span class="overview-label">
                          {{ t('lensRuns.colUser') }}
                        </span>
                        <span class="overview-meta-value">
                          {{ detail.username || '-' }}
                        </span>
                      </div>
                      <div class="overview-meta-item">
                        <span class="overview-label">
                          {{ t('lensRuns.colFeedback') }}
                        </span>
                        <span class="overview-meta-value">
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
                          <span v-else class="text-gray-400">—</span>
                        </span>
                      </div>
                    </div>

                    <div
                      data-testid="run-overview-timing"
                      class="overview-time-row"
                    >
                      <div>
                        <span class="overview-label">
                          {{ t('lensRuns.queueTime') }}
                        </span>
                        <span class="overview-time-value tabular-nums">
                          {{ queueText }}
                        </span>
                      </div>
                      <div>
                        <span class="overview-label">
                          {{ t('lensRuns.admissionWait') }}
                        </span>
                        <span class="overview-time-value tabular-nums">
                          {{ admissionWaitText }}
                        </span>
                      </div>
                      <div>
                        <span class="overview-label">
                          {{ t('lensRuns.execWindow') }}
                        </span>
                        <span class="overview-time-value tabular-nums">
                          {{ formatDateTime(detail.started_at) }}
                          <span class="font-normal text-gray-400">→</span>
                          {{ formatDateTime(detail.finished_at) }}
                        </span>
                      </div>
                    </div>

                    <div
                      v-if="
                        Object.keys(detail.termination_detail || {}).length ||
                        hasFailureSummary
                      "
                      class="overview-alert-row"
                    >
                      <div
                        v-if="
                          Object.keys(detail.termination_detail || {}).length
                        "
                      >
                        <span class="overview-label">
                          {{ t('lensRuns.terminationDetail') }}
                        </span>
                        <span class="overview-alert-value">
                          {{ detail.termination_detail.reason || '-' }}
                          <span v-if="detail.termination_detail.capability">
                            · {{ detail.termination_detail.capability }}
                          </span>
                          <span v-if="detail.termination_detail.error_type">
                            · {{ detail.termination_detail.error_type }}
                          </span>
                        </span>
                      </div>
                      <div
                        v-if="hasFailureSummary"
                        data-testid="run-failure-summary"
                        class="flex flex-wrap items-center gap-2"
                      >
                        <span class="overview-label">
                          {{ t('lensRuns.failureScope') }}
                        </span>
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
                      </div>
                    </div>
                  </section>

                  <section
                    v-if="detail.routing_mode === 'smart'"
                    data-testid="run-smart-collaboration"
                    class="overview-section"
                  >
                    <div class="overview-section-heading">
                      <div>
                        <h3 class="overview-title">
                          {{ t('lensRuns.smartCollaborationTitle') }}
                        </h3>
                        <p class="overview-section-description">
                          {{ t('lensRuns.smartCollaborationDescription') }}
                        </p>
                      </div>
                    </div>
                    <div class="mt-3 flex flex-wrap gap-2">
                      <span
                        v-for="assistant in detail.delegated_assistants || []"
                        :key="assistant.uuid"
                        class="rounded-full bg-indigo-50 px-2.5 py-1 text-xs font-medium text-indigo-700"
                      >
                        {{ assistant.name }}
                        <span class="ml-1 text-indigo-500"
                          >· {{ assistant.capability }}</span
                        >
                      </span>
                      <span
                        v-if="!(detail.delegated_assistants || []).length"
                        class="text-sm text-gray-400"
                      >
                        -
                      </span>
                    </div>
                  </section>

                  <section
                    data-testid="run-overview-usage"
                    class="overview-section overview-usage-section"
                  >
                    <div data-testid="run-token-summary">
                      <div class="overview-section-heading">
                        <div>
                          <h3 class="overview-title">
                            {{ t('lensRuns.overviewUsage') }}
                          </h3>
                          <p class="overview-section-description">
                            {{ t('lensRuns.overviewUsageDescription') }}
                          </p>
                        </div>
                        <span class="overview-usage-callout">
                          {{
                            t('lensRuns.llmCalls', { n: detail.llm_calls || 0 })
                          }}
                        </span>
                      </div>

                      <div class="overview-usage-metrics">
                        <div class="overview-usage-metric">
                          <dt class="overview-label">
                            {{
                              t('lensRuns.retriesShort', {
                                n: detail.retry_count || 0
                              })
                            }}
                          </dt>
                          <dd class="overview-usage-value">
                            {{ detail.retry_count || 0 }}
                          </dd>
                        </div>
                        <div class="overview-usage-metric">
                          <dt class="overview-label">
                            {{ t('lensRuns.cost') }}
                          </dt>
                          <dd class="overview-usage-value tabular-nums">
                            {{
                              detail.total_cost != null
                                ? `$${detail.total_cost}`
                                : '—'
                            }}
                          </dd>
                        </div>
                      </div>

                      <dl class="overview-token-breakdown">
                        <div class="bg-white px-3 py-2.5">
                          <dt class="text-xs text-gray-500">
                            {{ t('lensRuns.promptTokens') }}
                          </dt>
                          <dd
                            class="mt-1 font-medium text-gray-900 tabular-nums"
                          >
                            {{ (detail.prompt_tokens || 0).toLocaleString() }}
                          </dd>
                        </div>
                        <div class="bg-white px-3 py-2.5">
                          <dt class="text-xs text-gray-500">
                            {{ t('lensRuns.completionTokens') }}
                          </dt>
                          <dd
                            class="mt-1 font-medium text-gray-900 tabular-nums"
                          >
                            {{
                              (detail.completion_tokens || 0).toLocaleString()
                            }}
                          </dd>
                        </div>
                        <div class="bg-white px-3 py-2.5">
                          <dt class="text-xs text-gray-500">
                            {{ t('lensRuns.cachedTokens') }}
                          </dt>
                          <dd
                            class="mt-1 font-medium text-gray-900 tabular-nums"
                          >
                            {{ (detail.cached_tokens || 0).toLocaleString() }}
                          </dd>
                        </div>
                        <div class="bg-white px-3 py-2.5">
                          <dt class="text-xs text-gray-500">
                            {{ t('lensRuns.reasoningTokens') }}
                          </dt>
                          <dd
                            class="mt-1 font-medium text-gray-900 tabular-nums"
                          >
                            {{
                              (detail.reasoning_tokens || 0).toLocaleString()
                            }}
                          </dd>
                        </div>
                      </dl>

                      <div class="mt-3 flex flex-wrap gap-2">
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
                    </div>
                  </section>

                  <section
                    v-if="showLiveProgress"
                    data-testid="run-live-progress"
                    class="overview-section border-blue-200 bg-blue-50/60"
                  >
                    <h3 class="overview-title">
                      {{ t('lensRuns.liveProgressTitle') }}
                    </h3>
                    <dl class="overview-grid">
                      <div>
                        <dt class="overview-label">
                          {{ t('lensRuns.executorStatus') }}
                        </dt>
                        <dd class="mt-1">
                          <span :class="statusClass(detail.executor_status)">
                            {{
                              statusText(
                                detail.executor_status || detail.status
                              )
                            }}
                          </span>
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
                      <div>
                        <dt class="overview-label">
                          {{ t('lensRuns.colOperations') }}
                        </dt>
                        <dd class="overview-value">
                          {{
                            t('lensRuns.toolCallsShort', {
                              n: detail.tool_call_count || 0
                            })
                          }}
                          ·
                          {{
                            t('lensRuns.retriesShort', {
                              n: detail.retry_count || 0
                            })
                          }}
                        </dd>
                      </div>
                      <div>
                        <dt class="overview-label">
                          {{ t('lensRuns.resumeDeadline') }}
                        </dt>
                        <dd class="overview-value tabular-nums">
                          {{ formatDateTime(detail.resume_by) }}
                        </dd>
                      </div>
                    </dl>
                  </section>

                  <section
                    v-if="detail.execution"
                    data-testid="run-overview-resources"
                    class="overview-section"
                  >
                    <div class="overview-section-heading">
                      <div>
                        <h3 class="overview-title">
                          {{ t('lensRuns.overviewResources') }}
                        </h3>
                        <p class="overview-section-description">
                          {{ t('lensRuns.overviewResourcesDescription') }}
                        </p>
                      </div>
                    </div>

                    <div
                      data-testid="run-resource-ledger"
                      class="resource-ledger"
                    >
                      <div class="resource-ledger-summary">
                        <div class="resource-ledger-summary-item">
                          <span class="resource-ledger-summary-label">
                            {{ t('lensRuns.configuredResources') }}
                          </span>
                          <strong class="resource-ledger-summary-value">
                            {{
                              detail.execution.resource_usage
                                ?.configured_count || 0
                            }}
                          </strong>
                        </div>
                        <div class="resource-ledger-summary-item">
                          <span class="resource-ledger-summary-label">
                            {{ t('lensRuns.calledResources') }}
                          </span>
                          <strong class="resource-ledger-summary-value">
                            {{
                              detail.execution.resource_usage
                                ?.called_resource_count || 0
                            }}
                          </strong>
                        </div>
                        <div class="resource-ledger-summary-item">
                          <span class="resource-ledger-summary-label">
                            {{ t('lensRuns.totalResourceCalls') }}
                          </span>
                          <strong class="resource-ledger-summary-value">
                            {{
                              detail.execution.resource_usage?.total_calls || 0
                            }}
                          </strong>
                        </div>
                      </div>

                      <div class="resource-ledger-header">
                        <span>{{ t('lensRuns.resourceName') }}</span>
                        <span>{{ t('lensRuns.resourceStatus') }}</span>
                        <span class="text-right">
                          {{ t('lensRuns.resourceCallCountLabel') }}
                        </span>
                      </div>
                      <div class="resource-ledger-list">
                        <div
                          v-for="resource in detail.execution.resource_usage
                            ?.resources || []"
                          :key="`${resource.resource_type}-${resource.name}`"
                          class="resource-ledger-row"
                        >
                          <div class="resource-ledger-name">
                            <span
                              class="resource-ledger-type"
                              :class="`resource-ledger-type-${resource.resource_type}`"
                            >
                              {{ resourceTypeLabel(resource) }}
                            </span>
                            <span class="break-all">{{ resource.name }}</span>
                          </div>
                          <span
                            class="resource-ledger-status"
                            :class="{
                              'resource-ledger-status-used': resource.calls > 0,
                              'resource-ledger-status-idle':
                                resource.calls === 0
                            }"
                          >
                            {{
                              resource.configured
                                ? resource.calls > 0
                                  ? t('lensRuns.resourceUsed')
                                  : t('lensRuns.resourceNotCalled')
                                : t('lensRuns.runtimeResource')
                            }}
                          </span>
                          <strong class="resource-ledger-count">
                            {{ resource.calls || 0 }}
                          </strong>
                        </div>
                        <div
                          v-if="
                            !(detail.execution.resource_usage?.resources || [])
                              .length
                          "
                          class="resource-ledger-empty"
                        >
                          {{ t('lensRuns.noResourceCalls') }}
                        </div>
                      </div>
                    </div>

                    <dl class="overview-grid mt-4">
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

                  <section data-testid="run-question-section">
                    <div class="mb-2 flex items-center justify-between gap-3">
                      <h3 class="text-sm font-semibold text-gray-700">
                        {{ t('lensRuns.question') }}
                      </h3>
                      <button
                        v-if="questionCanExpand"
                        type="button"
                        data-testid="run-question-toggle"
                        class="question-toggle"
                        :aria-expanded="questionExpanded"
                        @click="questionExpanded = !questionExpanded"
                      >
                        {{
                          questionExpanded
                            ? t('common.collapse')
                            : t('common.expand')
                        }}
                      </button>
                    </div>
                    <div
                      ref="questionTextRef"
                      data-testid="run-question-content"
                      class="run-question-content"
                      :class="{
                        'run-question-collapsed':
                          questionCanExpand && !questionExpanded
                      }"
                    >
                      {{ detail.question || '-' }}
                    </div>
                  </section>

                  <section
                    v-if="detail.attachments && detail.attachments.length"
                  >
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
              </div>

              <!-- Execution analysis tab -->
              <div
                v-show="activeDetailTab === 'execution'"
                data-testid="run-execution-content"
                class="space-y-4 px-6 py-5"
              >
                <div
                  class="execution-view-tabs"
                  role="tablist"
                  :aria-label="t('lensRuns.executionViews')"
                >
                  <button
                    type="button"
                    role="tab"
                    data-testid="run-execution-trace-view"
                    class="execution-view-tab"
                    :class="
                      activeExecutionView === 'trace'
                        ? 'execution-view-tab-active'
                        : ''
                    "
                    :aria-selected="activeExecutionView === 'trace'"
                    @click="activeExecutionView = 'trace'"
                  >
                    {{ t('lensRuns.tabTrace') }}
                    <span class="text-xs text-gray-400">
                      {{ detail.trace_event_count ?? detail.event_count }}
                    </span>
                  </button>
                  <button
                    v-if="canDiagnoseRun"
                    type="button"
                    role="tab"
                    data-testid="run-execution-diagnosis-view"
                    class="execution-view-tab"
                    :class="
                      activeExecutionView === 'diagnosis'
                        ? 'execution-view-tab-active'
                        : ''
                    "
                    :aria-selected="activeExecutionView === 'diagnosis'"
                    @click="activeExecutionView = 'diagnosis'"
                  >
                    {{ t('lensRuns.diagnosisSection') }}
                  </button>
                </div>

                <RunTrajectoryPanel
                  v-show="activeExecutionView === 'trace'"
                  :run-uuid="selectedUuid"
                  :assistant-name="detail.assistant_name"
                  :run-status="detail.status"
                  :active="
                    activeDetailTab === 'execution' &&
                    activeExecutionView === 'trace'
                  "
                  @run-update="applyTrajectoryRunUpdate"
                />

                <RunDiagnosisPanel
                  v-if="canDiagnoseRun"
                  v-show="activeExecutionView === 'diagnosis'"
                  data-testid="run-execution-diagnosis"
                  :run-uuid="selectedUuid"
                  :active="
                    activeDetailTab === 'execution' &&
                    activeExecutionView === 'diagnosis'
                  "
                  :can-generate="canGenerateDiagnosis"
                  @navigate="navigateFromEvidence"
                />
              </div>

              <!-- Results and artifacts tab -->
              <div
                v-show="activeDetailTab === 'results'"
                data-testid="run-results-content"
                class="space-y-4 px-6 py-5"
              >
                <section v-if="detail.answer" class="overview-section">
                  <h3 class="overview-title">{{ t('lensRuns.answer') }}</h3>
                  <div
                    class="mt-3 rounded-md border border-gray-200 bg-white p-3"
                  >
                    <MarkdownRenderer :content="detail.answer" />
                  </div>
                </section>

                <section class="overview-section">
                  <h3 class="overview-title">
                    {{ t('lensRuns.evidenceTitle') }}
                  </h3>
                  <dl class="overview-grid">
                    <div>
                      <dt class="overview-label">
                        {{ t('lensRuns.plannedEvidenceCitations') }}
                      </dt>
                      <dd class="overview-value tabular-nums">
                        {{ plannedEvidence.citation_count ?? 0 }}
                      </dd>
                    </div>
                    <div>
                      <dt class="overview-label">
                        {{ t('lensRuns.plannedEvidenceRetrievalCalls') }}
                      </dt>
                      <dd class="overview-value tabular-nums">
                        {{ plannedEvidence.retrieval_call_count ?? 0 }}
                      </dd>
                    </div>
                  </dl>
                  <p class="mt-3 text-sm text-gray-600">
                    {{ t('lensRuns.evidenceBoundary') }}
                  </p>
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

                <section class="overview-section">
                  <h3 class="overview-title">
                    {{ t('lensRuns.verifiedCitations') }}
                  </h3>
                  <div v-if="detail.citations?.length" class="mt-3 space-y-3">
                    <article
                      v-for="citation in detail.citations"
                      :key="citation.id"
                      class="rounded-md border border-gray-200 bg-white p-3"
                    >
                      <p class="break-all font-mono text-xs text-gray-700">
                        {{ citation.path }}:{{ citation.start_line }}-{{
                          citation.end_line
                        }}
                      </p>
                      <p
                        v-if="citation.symbol"
                        class="mt-1 text-xs text-gray-500"
                      >
                        {{ citation.symbol }}
                      </p>
                      <p class="mt-2 text-sm text-gray-700">
                        {{ citation.supports }}
                      </p>
                    </article>
                  </div>
                  <p v-else class="mt-3 text-sm text-gray-500">
                    {{ t('lensRuns.noVerifiedCitations') }}
                  </p>
                </section>

                <section class="overview-section">
                  <h3 class="overview-title">{{ t('lensRuns.tabFiles') }}</h3>
                  <div
                    v-if="detail.output_files && detail.output_files.length"
                    class="mt-3 space-y-3"
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
                              t('lensRuns.downloadFile', {
                                name: file.filename
                              })
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
                    class="py-8 text-center text-sm text-gray-400"
                    data-testid="run-files-empty"
                  >
                    {{ t('lensRuns.noFiles') }}
                  </p>
                </section>
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
      <BaseModal
        :show="!!pendingAction"
        data-testid="run-action-confirm"
        :title="t(`lensRuns.confirm.${pendingAction?.type}.title`)"
        @close="pendingAction = null"
      >
        <p class="text-sm text-gray-600">
          {{ t(`lensRuns.confirm.${pendingAction?.type}.message`) }}
        </p>
        <template #footer>
          <div class="flex flex-row-reverse gap-2">
            <BaseButton
              :variant="pendingAction?.type === 'cancel' ? 'danger' : 'primary'"
              :loading="actionLoading"
              @click="confirmRunAction"
            >
              {{ t('common.confirm') }}
            </BaseButton>
            <BaseButton variant="outline" @click="pendingAction = null">
              {{ t('common.cancel') }}
            </BaseButton>
          </div>
        </template>
      </BaseModal>
    </div>
  </AdminLayout>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute, useRouter } from 'vue-router'
import { format } from 'date-fns'
import { useDebounceFn } from '@vueuse/core'
import { Download, Eye, FileText, ThumbsDown, ThumbsUp } from '@lucide/vue'
import { useToast } from '@/composables/useToast'
import { extractErrorMessage } from '@/utils/api'
import { fetchDeliverableBlob, isPreviewable } from '@/utils/filePreview'
import {
  cancelAdminRun,
  getAdminRun,
  getAdminRuns,
  getAdminRunTrajectoryExport,
  listAssistants,
  resumeAdminRun,
  retryAdminRun
} from '@/api/lens'
import AdminLayout from '@/admin/layout/AdminLayout.vue'
import RunDiagnosisPanel from '@/admin/pages/lens/RunDiagnosisPanel.vue'
import RunTrajectoryPanel from '@/admin/pages/lens/RunTrajectoryPanel.vue'
import {
  formatOperationMetric,
  resolveRunSummary
} from '@/admin/utils/operationsSummary'
import FilePreviewModal from '@/components/lens/FilePreviewModal.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseDateInput from '@/components/ui/BaseDateInput.vue'
import BaseLoading from '@/components/ui/BaseLoading.vue'
import BaseModal from '@/components/ui/BaseModal.vue'
import BaseSelect from '@/components/ui/BaseSelect.vue'
import PaginationBar from '@/components/ui/PaginationBar.vue'
import RowActionMenu from '@/components/ui/RowActionMenu.vue'
import MarkdownRenderer from '@/components/ui/MarkdownRenderer.vue'
import AuthImage from '@/components/ui/AuthImage.vue'
import { useUserStore } from '@/store/user'

const { t } = useI18n()
const { showError, showSuccess } = useToast()
const route = useRoute()
const router = useRouter()
const userStore = useUserStore()

const loading = ref(true)
const hasLoaded = ref(false)
const loadError = ref('')
const runs = ref([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)
const assistants = ref([])
const statusSummary = ref(null)

const detailVisible = ref(false)
const detailLoading = ref(false)
const detail = ref(null)
const selectedUuid = ref(null)
const activeDetailTab = ref('overview')
const activeExecutionView = ref('trace')
const advancedFiltersOpen = ref(false)
const previewFile = ref(null)
const pendingAction = ref(null)
const actionLoading = ref(false)
const questionTextRef = ref(null)
const questionCanExpand = ref(false)
const questionExpanded = ref(false)
let detailRefreshTimer = null

const ACTIVE_RUN_STATUSES = new Set(['queued', 'running', 'streaming'])

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
    canDiagnoseRun.value &&
    Boolean(detail.value) &&
    ['done', 'failed', 'cancelled'].includes(detail.value.status)
)

const detailMoreActions = computed(() => [
  {
    key: 'export',
    label: t('lensRuns.exportAction'),
    icon: Download
  }
])

const filters = ref({
  q: '',
  username: '',
  user_id: '',
  group_id: '',
  assistant: '',
  lensnode: '',
  model: '',
  status: '',
  start_date: '',
  end_date: ''
})

const advancedFilterCount = computed(
  () =>
    ['lensnode', 'model', 'start_date', 'end_date'].filter(
      (key) => filters.value[key]
    ).length
)

const statusSummaryCards = computed(() => [
  {
    status: '',
    label: t('lensRuns.summaryTotal'),
    count: statusSummary.value?.total
  },
  {
    status: 'active',
    label: t('lensRuns.statusRunning'),
    count:
      statusSummary.value?.running === null ||
      statusSummary.value?.streaming === null ||
      statusSummary.value?.running === undefined ||
      statusSummary.value?.streaming === undefined
        ? null
        : statusSummary.value.running + statusSummary.value.streaming
  },
  {
    status: 'queued',
    label: t('lensRuns.statusQueued'),
    count: statusSummary.value?.queued
  },
  {
    status: 'failed',
    label: t('lensRuns.statusFailed'),
    count: statusSummary.value?.failed
  },
  {
    status: 'done',
    label: t('lensRuns.statusDone'),
    count: statusSummary.value?.done
  }
])

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

const showLiveProgress = computed(() => {
  const status = (
    detail.value?.executor_status ||
    detail.value?.status ||
    ''
  ).toLowerCase()
  return (
    ['running', 'streaming', 'queued', 'awaiting_user_input'].includes(
      status
    ) || Boolean(detail.value?.resume_by)
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

function budgetPercent(value) {
  if (value === null || value === undefined) return '-'
  return t('lensRuns.budgetUsed', {
    value: Math.round(Number(value) * 100)
  })
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

function statusText(status) {
  const value = (status || '').toLowerCase()
  if (['completed', 'done'].includes(value)) return t('lensRuns.statusDone')
  if (value === 'failed') return t('lensRuns.statusFailed')
  if (['running', 'streaming'].includes(value)) {
    return t('lensRuns.statusRunning')
  }
  if (value === 'queued') return t('lensRuns.statusQueued')
  if (value === 'cancelled') return t('lensRuns.statusCancelled')
  if (value === 'blocked') return t('lensRuns.statusBlocked')
  if (value === 'partial') return t('lensRuns.statusPartial')
  if (value === 'awaiting_user_input') {
    return t('lensRuns.statusAwaitingInput')
  }
  return status || '-'
}

function resourceTypeLabel(resource) {
  if (resource.resource_type === 'plugin' && resource.plugin_name) {
    return t('lensRuns.pluginResource', { name: resource.plugin_name })
  }
  return t(`lensRuns.${resource.resource_type}Resource`)
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
  const sec = d?.control_queue_seconds
  if (sec === null || sec === undefined || sec < 0) return '-'
  return sec < 1 ? '<1s' : durationText(sec)
})

const admissionWaitText = computed(() => {
  const sec = detail.value?.admission_wait_seconds
  if (sec === null || sec === undefined || sec < 0) return '-'
  return sec < 1 ? '<1s' : durationText(sec)
})

function onFiltersChanged() {
  page.value = 1
  debouncedFetch()
}

function setStatusFilter(status) {
  filters.value.status = filters.value.status === status ? '' : status
  onFiltersChanged()
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
    lensnode: '',
    model: '',
    status: '',
    start_date: '',
    end_date: ''
  }
  advancedFiltersOpen.value = false
  page.value = 1
  router.replace({ path: route.path })
  fetchRuns()
}

function requestRunAction(type, run) {
  pendingAction.value = { type, run }
}

async function confirmRunAction() {
  const action = pendingAction.value
  if (!action || actionLoading.value) return
  actionLoading.value = true
  try {
    if (action.type === 'cancel') {
      await cancelAdminRun(action.run.uuid)
    } else if (action.type === 'retry') {
      const key = globalThis.crypto?.randomUUID?.() || String(Date.now())
      await retryAdminRun(action.run.uuid, key)
    } else if (action.type === 'resume') {
      await resumeAdminRun(action.run.uuid)
    }
    pendingAction.value = null
    showSuccess(t(`lensRuns.actionSuccess.${action.type}`))
    await fetchRuns()
    if (detailVisible.value && selectedUuid.value === action.run.uuid) {
      await fetchDetail()
    }
  } catch (error) {
    showError(extractErrorMessage(error, t('lensRuns.actionFailed')))
  } finally {
    actionLoading.value = false
  }
}

async function exportRun() {
  if (!selectedUuid.value || !detail.value) return
  try {
    const trajectory = await getAdminRunTrajectoryExport(selectedUuid.value)
    const blob = new Blob(
      [JSON.stringify({ run: detail.value, trajectory }, null, 2)],
      { type: 'application/json' }
    )
    const objectUrl = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = objectUrl
    link.download = `run-${selectedUuid.value}.json`
    document.body.appendChild(link)
    link.click()
    document.body.removeChild(link)
    URL.revokeObjectURL(objectUrl)
    showSuccess(t('lensRuns.exportSuccess'))
  } catch (error) {
    showError(extractErrorMessage(error, t('lensRuns.exportFailed')))
  }
}

function handleDetailMoreAction(action) {
  if (action === 'export') exportRun()
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
  activeExecutionView.value = 'trace'
  previewFile.value = null
  questionCanExpand.value = false
  questionExpanded.value = false
}

function closeDetail() {
  detailVisible.value = false
  selectedUuid.value = null
  detail.value = null
  activeExecutionView.value = 'trace'
  previewFile.value = null
  questionCanExpand.value = false
  questionExpanded.value = false
}

async function measureQuestionOverflow() {
  await nextTick()
  const element = questionTextRef.value
  if (!element) return

  element.classList.add('run-question-collapsed')
  const canExpand = element.scrollHeight > element.clientHeight + 1
  questionCanExpand.value = canExpand
  if (!canExpand || questionExpanded.value) {
    element.classList.remove('run-question-collapsed')
  }
}

function navigateFromEvidence(evidenceRef) {
  if (String(evidenceRef).startsWith('E-FILE-')) {
    activeDetailTab.value = 'results'
  } else if (evidenceRef === 'E-RUN') {
    activeDetailTab.value = 'overview'
  } else {
    activeDetailTab.value = 'execution'
    activeExecutionView.value = 'trace'
  }
}

async function fetchRuns() {
  loading.value = true
  loadError.value = ''
  try {
    const params = { page: page.value, page_size: pageSize.value }
    for (const [k, v] of Object.entries(filters.value)) {
      if (v) params[k] = v
    }
    const data = await getAdminRuns(params)
    runs.value = data?.results ?? []
    total.value = data?.total ?? 0
    statusSummary.value = resolveRunSummary(data)
    hasLoaded.value = true
  } catch (e) {
    loadError.value = extractErrorMessage(e, t('common.error'))
    showError(loadError.value)
  } finally {
    loading.value = false
  }
}

async function fetchDetail(background = false) {
  if (!selectedUuid.value) return
  if (!background) {
    detailLoading.value = true
    detail.value = null
  }
  try {
    detail.value = await getAdminRun(selectedUuid.value)
    questionExpanded.value = false
  } catch (e) {
    if (!background) {
      showError(extractErrorMessage(e, t('common.error')))
      detail.value = null
    }
  } finally {
    if (!background) {
      detailLoading.value = false
      await measureQuestionOverflow()
    }
  }
}

function applyTrajectoryRunUpdate(run) {
  if (!run || run.uuid !== selectedUuid.value || !detail.value) return
  detail.value = { ...detail.value, ...run }
}

function scheduleDetailRefresh() {
  clearTimeout(detailRefreshTimer)
  if (!detailVisible.value || !ACTIVE_RUN_STATUSES.has(detail.value?.status)) {
    return
  }
  detailRefreshTimer = setTimeout(async () => {
    await fetchDetail(true)
    scheduleDetailRefresh()
  }, 2000)
}

onMounted(() => {
  filters.value.user_id = String(route.query.user_id || '')
  filters.value.group_id = String(route.query.group_id || '')
  filters.value.username = String(route.query.username || '')
  filters.value.assistant = String(route.query.assistant || '')
  filters.value.lensnode = String(route.query.lensnode || '')
  filters.value.model = String(route.query.model || '')
  advancedFiltersOpen.value = advancedFilterCount.value > 0
  void Promise.all([
    listAssistants()
      .then((items) => {
        assistants.value = items
      })
      .catch(() => {
        assistants.value = []
      }),
    fetchRuns()
  ])
})

watch(detailVisible, (visible) => {
  if (visible && selectedUuid.value) fetchDetail()
})

watch(
  [detailVisible, () => detail.value?.status],
  () => scheduleDetailRefresh(),
  { immediate: true }
)

onBeforeUnmount(() => clearTimeout(detailRefreshTimer))
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
  @apply rounded-lg border border-gray-200 bg-gray-50/70 p-3;
}
.overview-dashboard {
  @apply space-y-3;
}
.overview-section-heading {
  @apply flex flex-wrap items-start justify-between gap-3;
}
.overview-section-description {
  @apply mt-1 text-xs leading-5 text-gray-500;
}
.overview-hero {
  @apply bg-white;
}
.overview-hero-header {
  @apply flex flex-wrap items-start justify-between gap-3 border-b
    border-gray-200 pb-3;
}
.overview-eyebrow {
  @apply text-[11px] font-semibold uppercase tracking-wide text-gray-400;
}
.overview-hero-title {
  @apply mt-1 text-base font-semibold text-gray-900;
}
.overview-hero-context {
  @apply mt-1 text-xs text-gray-500;
}
.overview-status-group {
  @apply flex shrink-0 flex-wrap items-center gap-x-4 gap-y-2;
}
.overview-status-item {
  @apply flex items-center gap-2;
}
.overview-status-item > span:last-child {
  @apply text-xs font-semibold;
}
.overview-kpi-grid {
  @apply mt-3 grid grid-cols-2 divide-x divide-y divide-gray-200
    overflow-hidden rounded-md border border-gray-200 bg-gray-50 sm:grid-cols-4
    sm:divide-y-0;
}
.overview-kpi {
  @apply min-w-0 bg-white px-3 py-2.5;
}
.overview-kpi-value {
  @apply mt-1 block truncate text-lg font-semibold leading-5 text-gray-900;
}
.overview-kpi-note {
  @apply mt-1 block truncate text-[11px] text-gray-400;
}
.overview-meta-row {
  @apply mt-3 grid gap-2 border-t border-gray-100 pt-3 sm:grid-cols-2
    lg:grid-cols-4;
}
.overview-meta-item {
  @apply flex min-w-0 items-center gap-2;
}
.overview-meta-item .overview-label {
  @apply shrink-0;
}
.overview-meta-value {
  @apply min-w-0 text-xs font-medium text-gray-700;
}
.overview-alert-row {
  @apply mt-3 grid gap-2 rounded-md border border-amber-200 bg-amber-50 px-3
    py-2 text-xs text-amber-800 sm:grid-cols-2;
}
.overview-alert-row > div:first-child {
  @apply flex min-w-0 flex-wrap items-center gap-2;
}
.overview-alert-value {
  @apply min-w-0 break-words font-medium;
}
.overview-usage-section {
  @apply border-indigo-100 bg-indigo-50/40;
}
.overview-token-breakdown {
  @apply mt-3 grid grid-cols-2 gap-px overflow-hidden rounded-md border
    border-indigo-100 bg-indigo-100 sm:grid-cols-4;
}
.overview-token-breakdown > div {
  @apply bg-white px-3 py-2;
}
.resource-ledger {
  @apply mt-3 overflow-hidden rounded-lg border border-gray-200 bg-white;
}
.resource-ledger-summary {
  @apply grid grid-cols-3 divide-x divide-gray-200 border-b border-gray-200
    bg-gray-50;
}
.resource-ledger-summary-item {
  @apply min-w-0 px-3 py-2.5;
}
.resource-ledger-summary-label {
  @apply block truncate text-[10px] font-semibold uppercase tracking-wide
    text-gray-500;
}
.resource-ledger-summary-value {
  @apply mt-1 block text-lg font-semibold text-gray-900 tabular-nums;
}
.resource-ledger-header {
  @apply grid grid-cols-[minmax(0,1fr)_auto_2.5rem] items-center gap-3 border-b
    border-gray-100 px-3 py-2 text-[10px] font-semibold uppercase tracking-wide
    text-gray-400;
}
.resource-ledger-list {
  @apply divide-y divide-gray-100;
}
.resource-ledger-row {
  @apply grid grid-cols-[minmax(0,1fr)_auto_2.5rem] items-center gap-3 px-3
    py-2.5 text-xs;
}
.resource-ledger-name {
  @apply flex min-w-0 items-center gap-2 font-medium text-gray-800;
}
.resource-ledger-type {
  @apply shrink-0 rounded border px-1.5 py-0.5 text-[10px] font-semibold
    uppercase tracking-wide;
}
.resource-ledger-type-skill {
  @apply border-blue-200 bg-blue-50 text-blue-700;
}
.resource-ledger-type-mcp {
  @apply border-emerald-200 bg-emerald-50 text-emerald-700;
}
.resource-ledger-type-plugin {
  @apply border-violet-200 bg-violet-50 text-violet-700;
}
.resource-ledger-type-tool {
  @apply border-gray-200 bg-gray-100 text-gray-600;
}
.resource-ledger-status {
  @apply whitespace-nowrap rounded-full px-2 py-0.5 text-[10px] font-medium;
}
.resource-ledger-status-used {
  @apply bg-green-50 text-green-700;
}
.resource-ledger-status-idle {
  @apply bg-gray-100 text-gray-500;
}
.resource-ledger-count {
  @apply text-right text-sm text-gray-900 tabular-nums;
}
.resource-ledger-empty {
  @apply px-3 py-4 text-center text-xs text-gray-400;
}
.overview-usage-callout {
  @apply rounded-full bg-white px-2.5 py-1 text-xs font-medium text-indigo-700
    ring-1 ring-inset ring-indigo-100;
}
.overview-usage-metrics {
  @apply mt-3 grid grid-cols-2 gap-2 sm:grid-cols-2;
}
.overview-usage-metric {
  @apply rounded-md border border-indigo-100 bg-white px-3 py-2;
}
.overview-usage-value {
  @apply mt-1 text-lg font-semibold text-gray-900 tabular-nums;
}
.overview-title {
  @apply text-sm font-semibold text-gray-800;
}
.overview-grid {
  @apply mt-2.5 grid grid-cols-2 gap-x-4 gap-y-2.5 text-sm;
}
.overview-time-row {
  @apply mt-3 grid gap-2 border-t border-gray-100 pt-3 text-xs sm:grid-cols-2;
}
.overview-time-row > div {
  @apply flex min-w-0 items-center gap-2;
}
.overview-time-value {
  @apply min-w-0 truncate font-medium text-gray-700;
}
.question-toggle {
  @apply shrink-0 text-xs font-medium text-primary-600 underline-offset-2
    hover:text-primary-700 hover:underline focus:outline-none focus:ring-2
    focus:ring-primary-500/20;
}
.run-question-content {
  @apply rounded-md border border-gray-200 bg-gray-50 p-3 text-sm leading-5
    text-gray-800 whitespace-pre-wrap break-words;
}
.run-question-collapsed {
  display: -webkit-box;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 10;
  overflow: hidden;
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
  @apply shrink-0 whitespace-nowrap py-3 text-sm font-medium border-b-2 border-transparent text-gray-500 transition-colors;
}
.detail-tab:hover {
  @apply text-gray-700;
}
.detail-tab-active {
  @apply border-primary-500 text-primary-600;
}

.execution-view-tabs {
  @apply inline-flex max-w-full gap-1 overflow-x-auto rounded-lg border
    border-gray-200 bg-gray-100 p-1;
}
.execution-view-tab {
  @apply min-h-11 shrink-0 whitespace-nowrap rounded-md px-3 text-xs
    font-medium text-gray-600 transition-colors hover:bg-white
    hover:text-gray-900 focus:outline-none focus:ring-2
    focus:ring-primary-500/20 md:min-h-0 md:py-1.5;
}
.execution-view-tab-active {
  @apply bg-white text-primary-700 shadow-sm ring-1 ring-gray-200;
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
