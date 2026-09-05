<template>
  <BaseDrawer
    :show="show"
    :title="t('lensAdmin.datasourceDetail.title')"
    :subtitle="datasource?.name || ''"
    width="2xl"
    @close="$emit('close')"
  >
    <template v-if="datasource" #tabs>
      <div class="border-b border-line bg-surface">
        <div class="flex gap-5 px-6">
          <button
            type="button"
            class="detail-tab"
            :class="activeTab === 'basic' ? 'detail-tab-active' : ''"
            @click="activeTab = 'basic'"
          >
            {{ t('lensAdmin.datasourceDetail.tabs.basic') }}
          </button>
          <button
            type="button"
            class="detail-tab"
            :class="activeTab === 'details' ? 'detail-tab-active' : ''"
            @click="activeTab = 'details'"
          >
            {{ t('lensAdmin.datasourceDetail.tabs.details') }}
          </button>
        </div>
      </div>
    </template>

    <div v-if="datasource">
      <div v-show="activeTab === 'basic'" class="space-y-4">
        <section
          class="datasource-overview-block rounded-xl border border-line bg-surface p-4"
        >
          <div class="flex items-center justify-between gap-3">
            <h3 class="text-sm font-semibold text-ink-900">
              {{ t('lensAdmin.datasourceDetail.basicInfo') }}
            </h3>
            <StatusBadge :status="datasource.status" />
          </div>
          <dl
            class="mt-3 grid grid-cols-2 gap-px overflow-hidden rounded-lg border border-line bg-line"
          >
            <div
              v-for="item in datasourceOverviewDetails"
              :key="item.label"
              class="min-w-0 bg-surface-sunken px-3 py-2.5"
              :class="item.wide ? 'col-span-2' : ''"
            >
              <dt class="text-[11px] font-medium text-ink-500">
                {{ item.label }}
              </dt>
              <dd
                class="mt-1 truncate text-sm font-medium text-ink-900"
                :class="item.mono ? 'font-mono text-xs' : ''"
                :title="item.value"
              >
                {{ item.value }}
              </dd>
            </div>
          </dl>
        </section>

        <section
          class="datasource-resource-block rounded-xl border border-line bg-surface p-4"
        >
          <h3 class="text-sm font-semibold text-ink-900">
            {{ t('lensAdmin.datasourceDetail.resourceAndTarget') }}
          </h3>
          <dl
            class="mt-3 grid grid-cols-2 gap-px overflow-hidden rounded-lg border border-line bg-line"
          >
            <div
              v-for="item in datasourceResourceDetails"
              :key="item.label"
              class="min-w-0 bg-surface-sunken px-3 py-2.5"
              :class="item.wide ? 'col-span-2' : ''"
            >
              <dt class="text-[11px] font-medium text-ink-500">
                {{ item.label }}
              </dt>
              <dd
                class="mt-1 break-words text-sm font-medium text-ink-900"
                :class="item.mono ? 'font-mono text-xs' : ''"
              >
                <a
                  v-if="item.href"
                  class="line-clamp-2 break-all text-brand-600 hover:text-brand-700"
                  :href="item.href"
                  :title="item.value"
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  {{ item.value }}
                </a>
                <template v-else>{{ item.value }}</template>
              </dd>
            </div>
          </dl>
          <div v-if="organizationRepositories.length" class="mt-3 space-y-2">
            <div
              v-for="repo in organizationRepositories"
              :key="repo.repo_url || repo.name || repo.path"
              class="rounded-lg border border-line bg-surface-sunken px-3 py-2"
            >
              <div class="flex min-w-0 items-center gap-2">
                <span
                  class="min-w-0 flex-1 truncate text-xs font-medium text-ink-800"
                  :title="repo.name || repo.path || repo.repo_url"
                >
                  {{ repo.name || repo.path || repo.repo_url }}
                </span>
                <span
                  v-if="repo.branch"
                  class="max-w-40 shrink-0 truncate rounded border border-line bg-surface px-1.5 py-0.5 font-mono text-[11px] text-ink-500"
                  :title="repo.branch"
                >
                  {{ repo.branch }}
                </span>
              </div>
              <a
                v-if="repo.repo_url"
                class="mt-1 block truncate font-mono text-xs text-brand-600 hover:text-brand-700"
                :href="repo.repo_url"
                :title="repo.repo_url"
                target="_blank"
                rel="noopener noreferrer"
              >
                {{ repo.repo_url }}
              </a>
            </div>
          </div>
        </section>

        <section
          v-if="datasource.source_type !== 'managed_workspace'"
          class="datasource-sync-block rounded-xl border border-line bg-surface p-4"
        >
          <h3 class="text-sm font-semibold text-ink-900">
            {{ t('lensAdmin.datasourceDetail.sync') }}
          </h3>
          <dl class="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-3">
            <div
              v-for="item in datasourceSyncDetails"
              :key="item.label"
              class="min-w-0 rounded-lg bg-surface-sunken px-3 py-2.5"
              :class="item.wide ? 'col-span-2 sm:col-span-3' : ''"
            >
              <dt class="text-[11px] font-medium text-ink-500">
                {{ item.label }}
              </dt>
              <dd
                class="mt-1 truncate text-sm font-medium text-ink-900"
                :class="item.mono ? 'font-mono text-xs' : ''"
                :title="item.value"
              >
                {{ item.value }}
              </dd>
            </div>
          </dl>
          <div
            v-if="datasourceSyncError"
            class="mt-3 rounded-lg border border-danger-200 bg-danger-50 px-3 py-2.5"
          >
            <p class="text-[11px] font-medium text-danger-600">
              {{ t('lensAdmin.datasourceDetail.lastError') }}
            </p>
            <p class="mt-1 break-words font-mono text-xs text-danger-700">
              {{ datasourceSyncError }}
            </p>
          </div>
        </section>

        <section
          v-if="datasource.source_type !== 'managed_workspace'"
          class="datasource-retrieval-block rounded-xl border border-line bg-surface p-4"
        >
          <h3 class="text-sm font-semibold text-ink-900">
            {{ t('lensAdmin.datasourceDetail.retrieval') }}
          </h3>
          <div class="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2">
            <article
              v-for="group in datasourceRetrievalGroups"
              :key="group.key"
              class="rounded-lg bg-surface-sunken p-3"
              :class="group.wide ? 'sm:col-span-2' : ''"
            >
              <h4 class="text-xs font-semibold text-ink-800">
                {{ group.title }}
              </h4>
              <dl
                class="mt-3 grid grid-cols-2 gap-x-4 gap-y-3"
                :class="group.wide ? 'sm:grid-cols-4' : ''"
              >
                <div
                  v-for="item in group.items"
                  :key="item.label"
                  class="min-w-0"
                  :class="item.wide ? 'col-span-2' : ''"
                >
                  <dt class="text-[11px] leading-4 text-ink-500">
                    {{ item.label }}
                  </dt>
                  <dd
                    class="mt-1 break-words text-xs font-medium text-ink-800"
                    :class="[
                      item.mono ? 'font-mono' : '',
                      item.enabled === true ? 'text-success-700' : '',
                      item.enabled === false ? 'text-ink-500' : ''
                    ]"
                  >
                    {{ item.value }}
                  </dd>
                </div>
              </dl>
            </article>
          </div>
        </section>
      </div>
      <div v-show="activeTab === 'details'" class="space-y-6">
        <div
          class="relative overflow-hidden rounded-lg border border-line bg-surface shadow-sm"
        >
          <ul class="divide-y divide-line bg-surface">
            <li
              class="hidden grid-cols-[1fr_140px_100px_100px_80px_24px] items-center gap-3 bg-surface-sunken px-4 py-2 text-center text-xs font-semibold uppercase tracking-wider text-ink-600 sm:grid"
            >
              <span>{{
                t('lensAdmin.datasourceDetail.details.colTaskName')
              }}</span>
              <span>{{
                t('lensAdmin.datasourceDetail.details.colStartedAt')
              }}</span>
              <span>{{
                t('lensAdmin.datasourceDetail.details.colStatus')
              }}</span>
              <span>{{
                t('lensAdmin.datasourceDetail.details.colTrigger')
              }}</span>
              <span>{{
                t('lensAdmin.datasourceDetail.details.colDuration')
              }}</span>
              <span></span>
            </li>
            <li
              v-if="tasksLoading"
              class="px-4 py-6 text-center text-sm text-ink-500"
            >
              {{ t('common.loading') }}
            </li>
            <li
              v-else-if="!tasks.length"
              class="px-4 py-6 text-center text-sm text-ink-500"
            >
              {{ t('common.noData') }}
            </li>
            <template v-else>
              <li
                v-for="task in tasks"
                :key="task.id"
                class="transition-colors duration-150"
                :class="
                  expandedTaskId === task.id
                    ? 'border-primary-500 bg-primary-50'
                    : 'border-transparent hover:bg-surface-sunken'
                "
              >
                <div
                  class="px-3 py-3 sm:hidden"
                  :class="
                    tasksLoading
                      ? 'cursor-not-allowed opacity-60'
                      : 'cursor-pointer'
                  "
                  @click="toggleTaskExpand(task)"
                >
                  <div class="flex items-start justify-between gap-3">
                    <span
                      class="min-w-0 flex-1 truncate text-sm font-medium text-ink-900"
                      :title="task.task_name"
                    >
                      {{ task.task_name || '-' }}
                    </span>
                    <div class="flex shrink-0 items-center gap-2">
                      <StatusBadge :status="mapTaskStatus(task.status)" />
                      <span
                        class="text-ink-400 transition-transform duration-150"
                        :class="expandedTaskId === task.id ? 'rotate-90' : ''"
                        >▸</span
                      >
                    </div>
                  </div>
                  <dl class="mt-3 grid grid-cols-3 gap-2">
                    <div class="min-w-0">
                      <dt class="text-[11px] text-ink-500">
                        {{
                          t('lensAdmin.datasourceDetail.details.colStartedAt')
                        }}
                      </dt>
                      <dd class="mt-0.5 truncate text-xs text-ink-800">
                        {{ formatDateTime(task.started_at) }}
                      </dd>
                    </div>
                    <div class="min-w-0">
                      <dt class="text-[11px] text-ink-500">
                        {{ t('lensAdmin.datasourceDetail.details.colTrigger') }}
                      </dt>
                      <dd class="mt-0.5 truncate text-xs text-ink-800">
                        {{ formatTrigger(task) }}
                      </dd>
                    </div>
                    <div class="min-w-0">
                      <dt class="text-[11px] text-ink-500">
                        {{
                          t('lensAdmin.datasourceDetail.details.colDuration')
                        }}
                      </dt>
                      <dd class="mt-0.5 truncate text-xs text-ink-800">
                        {{ formatDuration(task.duration) }}
                      </dd>
                    </div>
                  </dl>
                </div>
                <div
                  class="hidden grid-cols-[1fr_140px_100px_100px_80px_24px] items-center gap-3 px-4 py-2 text-center text-sm sm:grid"
                  :class="
                    tasksLoading
                      ? 'cursor-not-allowed opacity-60'
                      : 'cursor-pointer'
                  "
                  @click="toggleTaskExpand(task)"
                >
                  <span
                    class="truncate text-left text-sm font-medium text-ink-900"
                    :title="task.task_name"
                  >
                    {{ task.task_name || '-' }}
                  </span>
                  <span
                    class="whitespace-nowrap text-sm font-medium text-ink-900"
                  >
                    {{ formatDate(task.started_at) }}
                  </span>
                  <div class="flex justify-center">
                    <StatusBadge :status="mapTaskStatus(task.status)" />
                  </div>
                  <span class="whitespace-nowrap text-sm text-ink-500">
                    {{ formatTrigger(task) }}
                  </span>
                  <span class="whitespace-nowrap text-sm text-ink-500">
                    {{ formatDuration(task.duration) }}
                  </span>
                  <span
                    class="text-center text-ink-400 transition-transform duration-150"
                    :class="expandedTaskId === task.id ? 'rotate-90' : ''"
                    >▸</span
                  >
                </div>
                <div
                  v-if="expandedTaskId === task.id"
                  class="border-t border-line bg-surface-sunken px-4 py-3"
                >
                  <div
                    v-if="expandedTaskDetailLoading"
                    class="py-2 text-center text-xs text-ink-500"
                  >
                    {{ t('common.loading') }}
                  </div>
                  <TaskSummaryCard v-else :task="expandedTask" />
                </div>
              </li>
            </template>
          </ul>
        </div>

        <div
          v-if="totalCount > 0"
          class="mt-4 flex flex-wrap items-center justify-between gap-2 border-t border-line pt-4"
        >
          <p class="text-sm text-ink-500">
            {{ t('common.pagination.showing', paginationShowing) }}
          </p>
          <div class="flex items-center gap-2">
            <BaseButton
              variant="outline"
              size="sm"
              :disabled="tasksLoading || currentPage <= 1"
              :title="t('common.pagination.previous')"
              @click="goPrevPage"
            >
              <svg
                class="h-4 w-4"
                fill="none"
                stroke="currentColor"
                viewBox="0 0 24 24"
              >
                <path
                  stroke-linecap="round"
                  stroke-linejoin="round"
                  stroke-width="2"
                  d="M15 19l-7-7 7-7"
                />
              </svg>
              <span class="sr-only">{{ t('common.pagination.previous') }}</span>
            </BaseButton>
            <BaseButton
              variant="outline"
              size="sm"
              :disabled="tasksLoading || currentPage >= totalPages"
              :title="t('common.pagination.next')"
              @click="goNextPage"
            >
              <svg
                class="h-4 w-4"
                fill="none"
                stroke="currentColor"
                viewBox="0 0 24 24"
              >
                <path
                  stroke-linecap="round"
                  stroke-linejoin="round"
                  stroke-width="2"
                  d="M9 5l7 7-7 7"
                />
              </svg>
              <span class="sr-only">{{ t('common.pagination.next') }}</span>
            </BaseButton>
          </div>
        </div>
      </div>
    </div>
    <div v-else class="py-12 text-center text-sm text-ink-500">
      {{ t('lensAdmin.datasourceDetail.selectHint') }}
    </div>
    <template v-if="datasource" #footer>
      <div class="flex flex-wrap items-center justify-between gap-2">
        <BaseButton
          variant="outline"
          @click="$emit('toggle-enabled', datasource)"
        >
          {{
            datasource.status === 'active'
              ? t('lensAdmin.actions.disableDatasource')
              : t('lensAdmin.actions.enableDatasource')
          }}
        </BaseButton>
        <div class="flex flex-wrap gap-2">
          <BaseButton
            v-if="datasource.source_type === 'managed_workspace'"
            variant="outline"
            @click="$emit('upload', datasource)"
            >{{ t('lensAdmin.actions.uploadFile') }}</BaseButton
          >
          <BaseButton
            v-else-if="isDataSourceSyncing(datasource)"
            variant="danger"
            @click="$emit('cancel-sync', datasource)"
            >{{ t('lensAdmin.actions.cancelSync') }}</BaseButton
          >
          <BaseButton
            v-else
            variant="outline"
            :disabled="datasource.status !== 'active'"
            @click="$emit('sync', datasource)"
            >{{ t('lensAdmin.actions.sync') }}</BaseButton
          >
          <BaseButton variant="primary" @click="$emit('edit', datasource)">{{
            t('common.edit')
          }}</BaseButton>
        </div>
      </div>
    </template>
  </BaseDrawer>
</template>

<script setup>
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { format } from 'date-fns'

import api from '@/api'
import { llmAdminApi } from '@/admin/api/llmAdmin'
import { taskManagementApi } from '@/admin/api/taskManagement'
import { extractErrorMessage, extractResponseData } from '@/utils/api'
import { lensNodeErrorMessage } from '@/utils/lensNodeErrors'
import { formatDuration } from '@/utils/formatting'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseDrawer from '@/components/ui/BaseDrawer.vue'
import StatusBadge from '@/components/ui/StatusBadge.vue'
import TaskSummaryCard from '@/components/task-management/TaskSummaryCard.vue'

import {
  EMPTY_VALUE as emptyValue,
  compactUuid,
  formatLLMConfigLabel,
  normalizeList
} from './adminHelpers'
import {
  dataSourceBranch,
  dataSourceRepositories,
  dataSourceRepositoryUrl,
  isOrganizationDataSource,
  isDataSourceSyncing
} from './datasourceHelpers'
import { useShortDateTime } from './useShortDateTime'

const props = defineProps({
  show: { type: Boolean, default: false },
  datasource: { type: Object, default: null },
  lensnodes: { type: Array, default: () => [] }
})

defineEmits([
  'cancel-sync',
  'close',
  'edit',
  'sync',
  'toggle-enabled',
  'upload'
])

const { t } = useI18n()
const formatDateTime = useShortDateTime()
const activeTab = ref('basic')

const tasks = ref([])
const tasksLoading = ref(false)
const currentPage = ref(1)
const totalCount = ref(0)
const totalPages = ref(1)
const pageSize = 10
const processingRefreshTimer = ref(null)
const processingRefreshInFlight = ref(false)
const tasksLoadInFlight = ref(false)
const taskRequestSeq = ref(0)
const taskListContextKey = ref('')

const expandedTaskId = ref(null)
const expandedTask = ref(null)
const expandedTaskDetailLoading = ref(false)
const llmConfigOptions = ref([])
const llmConfigLoaded = ref(false)

const PROCESSING_STATUSES = new Set(['PENDING', 'STARTED', 'RETRY'])
const TASK_METADATA_FIELDS = [
  'datasource_uuid',
  'trigger',
  'progress_percent',
  'progress_step',
  'progress_message',
  'sync_summary',
  'source_type',
  'target_path',
  'error'
].join(',')

const paginationShowing = computed(() => ({
  from: (currentPage.value - 1) * pageSize + 1,
  to: Math.min(currentPage.value * pageSize, totalCount.value),
  total: totalCount.value
}))

function mapTaskStatus(status) {
  const m = {
    PENDING: 'pending',
    STARTED: 'processing',
    SUCCESS: 'success',
    FAILURE: 'failed',
    RETRY: 'processing',
    REVOKED: 'cancelled'
  }
  return m[status] || (status && status.toLowerCase()) || 'pending'
}

function isProcessingStatus(status) {
  return PROCESSING_STATUSES.has(String(status || '').toUpperCase())
}

function formatDate(val) {
  if (!val) return '-'
  try {
    return format(new Date(val), 'yyyy-MM-dd HH:mm')
  } catch {
    return val
  }
}

function resetTaskList() {
  tasks.value = []
  totalCount.value = 0
  totalPages.value = 1
  expandedTaskId.value = null
  expandedTask.value = null
}

function hasProcessingTasks() {
  return tasks.value.some((task) => isProcessingStatus(task.status))
}

async function loadTasks(options = {}) {
  if (tasksLoadInFlight.value) return false
  const requestSeq = taskRequestSeq.value + 1
  taskRequestSeq.value = requestSeq
  const uuid = props.datasource?.uuid
  if (!uuid) {
    resetTaskList()
    return false
  }
  tasksLoadInFlight.value = true
  if (!options.silent) {
    tasksLoading.value = true
  }
  try {
    const params = {
      page: currentPage.value,
      page_size: pageSize,
      metadata_fields: TASK_METADATA_FIELDS
    }
    const res = await api.get(`/lens/admin/datasources/${uuid}/sync-tasks/`, {
      params
    })
    const data = extractResponseData(res)
    const list =
      data?.results ?? data?.list ?? (Array.isArray(data) ? data : [])
    const serverTotal = data?.count ?? data?.pagination?.total
    const total = Number.isFinite(Number(serverTotal))
      ? Number(serverTotal)
      : list.length
    if (requestSeq !== taskRequestSeq.value) {
      return
    }
    tasks.value = list
    totalCount.value = total
    totalPages.value = total > 0 ? Math.ceil(total / pageSize) : 1
    return true
  } catch (e) {
    if (requestSeq !== taskRequestSeq.value) {
      return
    }
    if (!options.silent) {
      resetTaskList()
    }
    // eslint-disable-next-line no-console
    console.error(extractErrorMessage(e, t('common.error')))
    return false
  } finally {
    if (requestSeq === taskRequestSeq.value) {
      tasksLoadInFlight.value = false
      tasksLoading.value = false
    }
  }
}

async function refreshProcessingTasks() {
  if (processingRefreshInFlight.value) return
  const processingTasks = tasks.value.filter((task) =>
    isProcessingStatus(task.status)
  )
  if (!processingTasks.length) {
    stopProcessingRefresh()
    return
  }
  processingRefreshInFlight.value = true
  try {
    const results = await Promise.allSettled(
      processingTasks.map((task) =>
        taskManagementApi.getExecution(task.id, {
          metadata_fields: TASK_METADATA_FIELDS
        })
      )
    )
    const refreshedById = new Map()
    results.forEach((result) => {
      if (result.status !== 'fulfilled') return
      const row = extractResponseData(result.value)
      if (row?.id == null) return
      refreshedById.set(String(row.id), row)
    })
    if (!refreshedById.size) return
    tasks.value = tasks.value.map((task) => {
      if (!refreshedById.has(String(task.id))) return task
      const updated = { ...task, ...refreshedById.get(String(task.id)) }
      if (expandedTaskId.value === task.id && expandedTask.value) {
        expandedTask.value = { ...expandedTask.value, ...updated }
      }
      return updated
    })
    if (!hasProcessingTasks()) {
      stopProcessingRefresh()
      await loadTasks({ silent: true })
    }
  } finally {
    processingRefreshInFlight.value = false
  }
}

function startProcessingRefresh() {
  if (processingRefreshTimer.value || !hasProcessingTasks()) return
  stopProcessingRefresh()
  processingRefreshTimer.value = window.setInterval(
    refreshProcessingTasks,
    3000
  )
}

function stopProcessingRefresh() {
  if (!processingRefreshTimer.value) return
  window.clearInterval(processingRefreshTimer.value)
  processingRefreshTimer.value = null
}

function goPrevPage() {
  if (tasksLoading.value || currentPage.value <= 1) return
  currentPage.value -= 1
  loadTasks()
}

function goNextPage() {
  if (tasksLoading.value || currentPage.value >= totalPages.value) return
  currentPage.value += 1
  loadTasks()
}

async function toggleTaskExpand(task) {
  if (tasksLoading.value) return
  if (expandedTaskId.value === task.id) {
    expandedTaskId.value = null
    expandedTask.value = null
    return
  }
  expandedTaskId.value = task.id
  expandedTask.value = task
  await loadExpandedTask(task.id)
}

async function loadExpandedTask(id) {
  expandedTaskDetailLoading.value = true
  try {
    const res = await taskManagementApi.getExecution(id, {
      metadata_fields: TASK_METADATA_FIELDS
    })
    const data = extractResponseData(res)
    if (expandedTaskId.value === id) {
      expandedTask.value = data
    }
  } catch (e) {
    // eslint-disable-next-line no-console
    console.error(extractErrorMessage(e, t('common.error')))
  } finally {
    expandedTaskDetailLoading.value = false
  }
}

function formatTrigger(task) {
  const trigger = task?.trigger || task?.metadata?.trigger
  if (trigger === 'manual') {
    return t('lensAdmin.datasourceDetail.details.triggerManual')
  }
  if (trigger === 'periodic' || trigger === 'scheduled') {
    return t('lensAdmin.datasourceDetail.details.triggerScheduled')
  }
  return trigger || t('lensAdmin.datasourceDetail.details.triggerSystem')
}

watch(
  () => [props.datasource?.uuid, props.show, activeTab.value],
  ([uuid, visible, tab]) => {
    if (!visible || !uuid || tab !== 'details') {
      stopProcessingRefresh()
      taskRequestSeq.value += 1
      taskListContextKey.value = ''
      tasksLoadInFlight.value = false
      tasksLoading.value = false
      resetTaskList()
      return
    }
    const contextKey = `${uuid}:${tab}`
    if (taskListContextKey.value === contextKey) {
      if (
        isDataSourceSyncing(props.datasource) &&
        !hasProcessingTasks() &&
        !tasksLoadInFlight.value
      ) {
        loadTasks({ silent: true }).then((loaded) => {
          if (loaded && hasProcessingTasks()) {
            startProcessingRefresh()
          }
        })
      }
      return
    }
    taskListContextKey.value = contextKey
    stopProcessingRefresh()
    taskRequestSeq.value += 1
    currentPage.value = 1
    resetTaskList()
    loadTasks().then((loaded) => {
      if (!loaded) return
      if (hasProcessingTasks()) {
        startProcessingRefresh()
      } else {
        stopProcessingRefresh()
      }
    })
  },
  { immediate: true }
)

watch(
  () => props.show,
  (visible) => {
    if (visible) {
      loadLLMConfigOptions()
    }
  },
  { immediate: true }
)

onBeforeUnmount(() => {
  stopProcessingRefresh()
})

function formatSourceType(sourceType) {
  if (sourceType === 'git') {
    return 'Git'
  }
  if (sourceType === 'feishu') {
    return t('lensAdmin.datasourceWizard.feishu')
  }
  if (sourceType === 'managed_workspace') {
    return t('lensAdmin.datasourceWizard.managedWorkspace')
  }
  return sourceType || emptyValue
}

async function loadLLMConfigOptions() {
  if (llmConfigLoaded.value) return
  llmConfigLoaded.value = true
  try {
    const rows = await llmAdminApi
      .getLLMConfigAll({ scope: 'global' })
      .catch(() => [])
    llmConfigOptions.value = normalizeList(rows)
  } catch {
    llmConfigOptions.value = []
  }
}

function authSchemeLabel(authScheme) {
  if (authScheme === 'token') {
    return t('lensAdmin.datasourceWizard.authToken')
  }
  return t('lensAdmin.datasourceWizard.authNone')
}

function feishuScopeLabel() {
  return t('lensAdmin.datasourceWizard.feishuScopeDriveFolder')
}

function formatSyncPolicy(syncPolicy) {
  if (syncPolicy?.mode === 'crontab') {
    const cron = syncPolicy.cron || emptyValue
    const timezone = syncPolicy.timezone || 'UTC'
    return `${cron} · ${timezone}`
  }
  const interval = syncPolicy?.interval_seconds
  return interval
    ? t('lensAdmin.table.intervalSeconds', { seconds: interval })
    : emptyValue
}

function lensNodeName(value) {
  const uuid = typeof value === 'object' ? value?.uuid : value
  const found = props.lensnodes.find((lensnode) => lensnode.uuid === uuid)
  return found?.name || uuid || emptyValue
}

function detailItem(label, value, mono = false, options = {}) {
  const normalized = Array.isArray(value) ? value.join(', ') : value
  return {
    label,
    value: normalized || emptyValue,
    mono,
    ...options
  }
}

function settingItem(label, value, enabled = null, options = {}) {
  return detailItem(label, value, false, { enabled, ...options })
}

function booleanLabel(value) {
  return value ? t('common.status.enabled') : t('common.status.disabled')
}

function modelLabel(modelRef) {
  if (!modelRef) return emptyValue
  const found = llmConfigOptions.value.find(
    (config) => String(config.uuid || config.id) === String(modelRef)
  )
  if (found) return formatLLMConfigLabel(found)
  return t('lensAdmin.datasourceDetail.unknownModel', {
    id: compactUuid(modelRef)
  })
}

const datasourceOverviewDetails = computed(() => {
  const row = props.datasource
  if (!row) return []
  return [
    detailItem(t('lensAdmin.fields.name'), row.name),
    detailItem(t('lensAdmin.fields.type'), formatSourceType(row.source_type)),
    detailItem(
      t('lensAdmin.datasourceDetail.connection'),
      row.connection_name ||
        (row.connection
          ? compactUuid(
              typeof row.connection === 'object'
                ? row.connection.uuid
                : row.connection
            )
          : t('lensAdmin.datasourceDetail.legacyConnection'))
    ),
    detailItem('UUID', row.uuid, true)
  ]
})

const datasourceResourceDetails = computed(() => {
  const row = props.datasource
  if (!row) return []
  const config = row.config || {}
  if (row.source_type === 'managed_workspace') {
    return [
      detailItem(
        t('lensAdmin.fields.lensnode'),
        row.lensnode_name || lensNodeName(row.lensnode)
      ),
      detailItem(
        t('lensAdmin.availability.title'),
        t(`lensAdmin.availability.${row.availability_status || 'unknown'}`)
      ),
      detailItem(t('lensAdmin.fields.targetPath'), row.target_path, true, {
        wide: true
      }),
      detailItem(
        t('lensAdmin.availability.checkedAt'),
        formatDateTime(row.availability_checked_at)
      ),
      detailItem(
        t('lensAdmin.availability.message'),
        row.availability_message,
        false,
        { wide: true }
      )
    ]
  }
  if (row.source_type === 'git') {
    const repositoryUrl = dataSourceRepositoryUrl(row, row.connection_endpoint)
    const items = [
      detailItem(t('lensAdmin.fields.repoUrl'), repositoryUrl, true, {
        href: isHttpUrl(repositoryUrl) ? repositoryUrl : '',
        wide: true
      }),
      detailItem(
        t('lensAdmin.fields.lensnode'),
        row.lensnode_name || lensNodeName(row.lensnode)
      ),
      detailItem(t('lensAdmin.fields.targetPath'), row.target_path, true),
      detailItem(
        t('lensAdmin.fields.authScheme'),
        row.connection
          ? t('lensAdmin.datasourceDetail.connectionManaged')
          : authSchemeLabel(config.auth_scheme)
      )
    ]
    if (!isOrganizationDataSource(row)) {
      items.splice(
        1,
        0,
        detailItem(t('lensAdmin.fields.branch'), dataSourceBranch(row), true)
      )
    }
    return items
  }
  const resourceUrls = Array.isArray(row.datasource_config?.resource_urls)
    ? row.datasource_config.resource_urls
    : [config.folder_url, config.document_url].filter(Boolean)
  return [
    ...resourceUrls.map((url, index) =>
      detailItem(
        resourceUrls.length > 1
          ? `${t('lensAdmin.fields.url')} ${index + 1}`
          : t('lensAdmin.fields.url'),
        url,
        true,
        { href: isHttpUrl(url) ? url : '', wide: true }
      )
    ),
    detailItem(t('lensAdmin.fields.syncScope'), feishuScopeLabel()),
    detailItem(
      t('lensAdmin.fields.lensnode'),
      row.lensnode_name || lensNodeName(row.lensnode)
    ),
    detailItem(t('lensAdmin.fields.targetPath'), row.target_path, true, {
      wide: true
    })
  ].filter((item) => item.value !== emptyValue)
})

const organizationRepositories = computed(() =>
  dataSourceRepositories(props.datasource)
)

const datasourceSyncDetails = computed(() => {
  const row = props.datasource
  if (!row) return []
  return [
    detailItem(
      t('lensAdmin.fields.syncInterval'),
      formatSyncPolicy(row.sync_policy)
    ),
    detailItem(
      t('lensAdmin.datasourceDetail.lastSyncedAt'),
      formatDateTime(row.last_synced_at)
    ),
    detailItem(
      t('lensAdmin.table.nextSync'),
      formatDateTime(row.sync_state?.next_run_at)
    ),
    detailItem(
      t('lensAdmin.datasourceDetail.lastStatus'),
      syncStatusLabel(row.sync_state?.last_status)
    ),
    detailItem(
      t('lensAdmin.datasourceDetail.createdAt'),
      formatDateTime(row.created_at)
    ),
    detailItem(
      t('lensAdmin.datasourceDetail.updatedAt'),
      formatDateTime(row.updated_at)
    )
  ]
})

const datasourceSyncError = computed(() => {
  const row = props.datasource
  return lensNodeErrorMessage(row?.last_error, t) || row?.last_error || ''
})

function syncStatusLabel(status) {
  const normalized = String(status || '').toLowerCase()
  const statusKey = {
    running: 'processing',
    success: 'success',
    failed: 'failed',
    processing: 'processing',
    pending: 'pending',
    cancelled: 'cancelled'
  }[normalized]
  if (statusKey) {
    return t(`common.status.${statusKey}`)
  }
  return status || emptyValue
}

const datasourceRetrievalGroups = computed(() => {
  const conversion = props.datasource?.sync_policy?.conversion || {}
  return [
    {
      key: 'document',
      title: t('lensAdmin.datasourceDetail.processing.document'),
      items: [
        settingItem(
          t('lensAdmin.datasourceWizard.convertDocuments'),
          booleanLabel(conversion.document),
          Boolean(conversion.document),
          { wide: true }
        ),
        settingItem(
          t('lensAdmin.fields.documentModel'),
          modelLabel(conversion.document_model_ref),
          null,
          { wide: true }
        ),
        settingItem(
          t('lensAdmin.fields.maxFileSizeMb'),
          conversion.max_file_size_mb || 100
        ),
        settingItem(t('lensAdmin.fields.maxPages'), conversion.max_pages || 500)
      ]
    },
    {
      key: 'image',
      title: t('lensAdmin.datasourceDetail.processing.image'),
      items: [
        settingItem(
          t('lensAdmin.datasourceWizard.convertImages'),
          booleanLabel(conversion.image),
          Boolean(conversion.image)
        ),
        settingItem(
          t('lensAdmin.datasourceWizard.convertEmbeddedImages'),
          booleanLabel(conversion.embedded_image),
          Boolean(conversion.embedded_image)
        ),
        settingItem(
          t('lensAdmin.fields.visionModel'),
          modelLabel(conversion.vision_model_ref),
          null,
          { wide: true }
        ),
        settingItem(
          t('lensAdmin.fields.maxImages'),
          conversion.max_images || 100
        )
      ]
    },
    {
      key: 'pdf',
      title: t('lensAdmin.datasourceDetail.processing.pdf'),
      wide: true,
      items: [
        settingItem(
          t('lensAdmin.datasourceWizard.pdfExtractImages'),
          booleanLabel(conversion.pdf_extract_images !== false),
          conversion.pdf_extract_images !== false
        ),
        settingItem(
          t('lensAdmin.datasourceWizard.pdfExtractImagesOnTextPages'),
          booleanLabel(conversion.pdf_extract_images_on_text_pages),
          Boolean(conversion.pdf_extract_images_on_text_pages)
        ),
        settingItem(
          t('lensAdmin.datasourceWizard.pdfRenderScannedPages'),
          booleanLabel(conversion.pdf_render_scanned_pages),
          Boolean(conversion.pdf_render_scanned_pages),
          { wide: true }
        ),
        settingItem(
          t('lensAdmin.fields.pdfMaxPages'),
          conversion.pdf_max_pages || 30
        ),
        settingItem(
          t('lensAdmin.fields.pdfMaxImagesPerPage'),
          conversion.pdf_max_images_per_page || 3
        ),
        settingItem(
          t('lensAdmin.fields.pdfRenderDpi'),
          conversion.pdf_render_dpi || 144
        ),
        settingItem(
          t('lensAdmin.fields.pdfMinTextChars'),
          conversion.pdf_min_text_chars || 30
        ),
        settingItem(
          t('lensAdmin.fields.pdfMinImageAreaRatio'),
          conversion.pdf_min_image_area_ratio || 0.08,
          null,
          { wide: true }
        )
      ]
    }
  ]
})

function isHttpUrl(value) {
  return /^https?:\/\//i.test(String(value || ''))
}
</script>

<style scoped>
.detail-tab {
  @apply border-b-2 border-transparent py-3 text-sm font-medium text-ink-500 transition-colors;
}

.detail-tab:hover {
  @apply text-ink-700;
}

.detail-tab-active {
  @apply border-primary-500 text-primary-600;
}
</style>
