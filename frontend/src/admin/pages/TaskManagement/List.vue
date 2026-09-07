<template>
  <AdminLayout>
    <div class="flex h-full min-h-0 w-full max-w-full flex-col p-6">
      <div class="mb-4 flex-shrink-0">
        <h1 class="text-lg font-semibold text-gray-900">
          {{ t('taskManagement.list.title') }}
        </h1>
        <p class="mt-1 text-sm text-gray-500">
          {{ t('taskManagement.list.subtitle') }}
        </p>
      </div>

      <div
        class="flex min-h-0 flex-col overflow-hidden rounded border border-gray-200 bg-white shadow-sm"
      >
        <div class="flex min-h-0 flex-col p-6">
          <!-- Toolbar -->
          <div
            class="mb-6 flex flex-shrink-0 flex-col items-start justify-between gap-3 sm:flex-row sm:flex-nowrap sm:items-center"
          >
            <div class="flex min-w-0 flex-1 flex-nowrap items-center gap-2">
              <label
                class="text-sm font-medium text-gray-700 whitespace-nowrap"
              >
                {{ t('taskManagement.list.taskTypeFilter') }}
              </label>
              <BaseSelect
                v-model="filterModule"
                class="min-w-[6rem]"
                :full-width="false"
                @change="onFilterChange"
              >
                <option value="">
                  {{ t('taskManagement.list.taskTypeAll') }}
                </option>
                <option value="agentcore_notifier">
                  {{ t('taskManagement.list.taskTypeNotifier') }}
                </option>
                <option value="agentcore_task">
                  {{ t('taskManagement.list.taskTypeTask') }}
                </option>
                <option value="lens_datasource">
                  {{ t('taskManagement.list.taskTypeDatasource') }}
                </option>
              </BaseSelect>
              <label
                class="text-sm font-medium text-gray-700 whitespace-nowrap"
              >
                {{ t('taskManagement.list.userFilter') }}
              </label>
              <BaseSelect
                v-model="filterUserId"
                class="min-w-[6rem]"
                :full-width="false"
                @change="onFilterChange"
              >
                <option value="">
                  {{ t('taskManagement.list.userFilterAll') }}
                </option>
                <option v-for="u in userOptions" :key="u.id" :value="u.id">
                  {{ u.label }}
                </option>
              </BaseSelect>
              <div class="flex shrink-0 items-center gap-3">
                <span class="text-sm text-gray-600 whitespace-nowrap">{{
                  t('taskManagement.list.dateRange')
                }}</span>
                <BaseDateInput
                  v-model="filterStartDate"
                  compact
                  class="!w-28"
                  input-class="text-xs"
                  :max="filterEndDate || undefined"
                  :mobile-touch="false"
                  @change="onFilterChange"
                />
                <span class="text-gray-400">–</span>
                <BaseDateInput
                  v-model="filterEndDate"
                  compact
                  class="!w-28"
                  input-class="text-xs"
                  :min="filterStartDate || undefined"
                  :mobile-touch="false"
                  @change="onFilterChange"
                />
              </div>
            </div>

            <div
              class="flex min-w-0 shrink-0 items-center gap-3 w-full sm:w-auto"
            >
              <BaseInput
                v-model="searchQuery"
                :placeholder="t('taskManagement.list.searchPlaceholder')"
                class="min-w-0 flex-1 sm:w-[17rem]"
                @update:modelValue="debouncedLoad"
              >
                <template #icon>
                  <svg
                    class="w-4 h-4 text-gray-400"
                    fill="none"
                    stroke="currentColor"
                    viewBox="0 0 24 24"
                  >
                    <path
                      stroke-linecap="round"
                      stroke-linejoin="round"
                      stroke-width="2"
                      d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"
                    />
                  </svg>
                </template>
              </BaseInput>

              <BaseButton
                variant="outline"
                size="sm"
                :loading="loading"
                @click="loadTasks"
                :title="t('common.refresh')"
                class="flex items-center gap-1 shadow-sm hover:shadow-md transition-shadow"
              >
                <svg
                  v-if="!loading"
                  class="w-4 h-4"
                  fill="none"
                  stroke="currentColor"
                  viewBox="0 0 24 24"
                >
                  <path
                    stroke-linecap="round"
                    stroke-linejoin="round"
                    stroke-width="2"
                    d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"
                  />
                </svg>
                <span class="sr-only">{{ t('common.refresh') }}</span>
              </BaseButton>
            </div>
          </div>

          <BaseLoading v-if="loading && tasks.length === 0" />

          <div
            v-else-if="!loading && tasks.length === 0"
            class="rounded-lg border border-gray-200 bg-gray-50 py-16 text-center"
          >
            <svg
              class="mx-auto h-12 w-12 text-gray-400 mb-4"
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
            >
              <path
                stroke-linecap="round"
                stroke-linejoin="round"
                stroke-width="2"
                d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2"
              />
            </svg>
            <p class="text-sm font-medium text-gray-600">
              {{ t('taskManagement.list.noTasks') }}
            </p>
          </div>

          <!-- Desktop Table View -->
          <div
            v-else
            class="relative max-h-full overflow-auto rounded-lg border border-gray-200 bg-white shadow-sm"
          >
            <table class="min-w-full divide-y divide-gray-200">
              <thead
                class="sticky top-0 z-10 bg-gradient-to-r from-gray-50 to-gray-100"
              >
                <tr>
                  <th
                    class="px-4 py-3 text-left text-xs font-semibold text-gray-700 uppercase tracking-wider border-b border-gray-200"
                  >
                    {{ t('taskManagement.list.taskName') }}
                  </th>
                  <th
                    class="px-4 py-3 text-left text-xs font-semibold text-gray-700 uppercase tracking-wider border-b border-gray-200"
                  >
                    {{ t('taskManagement.list.module') }}
                  </th>
                  <th
                    class="px-4 py-3 text-left text-xs font-semibold text-gray-700 uppercase tracking-wider border-b border-gray-200"
                  >
                    {{ t('taskManagement.list.status') }}
                  </th>
                  <th
                    class="px-4 py-3 text-left text-xs font-semibold text-gray-700 uppercase tracking-wider border-b border-gray-200"
                  >
                    {{ t('taskManagement.list.startedAt') }}
                  </th>
                  <th
                    class="px-4 py-3 text-left text-xs font-semibold text-gray-700 uppercase tracking-wider border-b border-gray-200"
                  >
                    {{ t('taskManagement.list.duration') }}
                  </th>
                  <th
                    class="px-4 py-3 text-left text-xs font-semibold text-gray-700 uppercase tracking-wider border-b border-gray-200"
                  >
                    {{ t('taskManagement.list.createdBy') }}
                  </th>
                </tr>
              </thead>
              <tbody class="bg-white divide-y divide-gray-100">
                <tr
                  v-for="task in tasks"
                  :key="task.id"
                  @click="handlePreview(task)"
                  class="cursor-pointer transition-colors duration-150 hover:bg-gray-50"
                >
                  <td
                    class="px-4 py-3 whitespace-nowrap text-sm font-medium text-gray-900"
                  >
                    {{ task.task_name || '-' }}
                  </td>
                  <td class="px-4 py-3 whitespace-nowrap text-sm text-gray-500">
                    {{ task.module || '-' }}
                  </td>
                  <td class="px-4 py-3 whitespace-nowrap">
                    <StatusBadge :status="mapStatus(task.status)" />
                  </td>
                  <td class="px-4 py-3 whitespace-nowrap text-sm text-gray-500">
                    {{ formatDate(task.started_at) }}
                  </td>
                  <td class="px-4 py-3 whitespace-nowrap text-sm text-gray-500">
                    {{ formatDuration(task.duration) }}
                  </td>
                  <td class="px-4 py-3 whitespace-nowrap text-sm text-gray-500">
                    {{ task.created_by_username || '-' }}
                  </td>
                </tr>
              </tbody>
            </table>
          </div>

          <PaginationBar
            v-model:page-size="pageSize"
            :current-page="currentPage"
            :total="totalCount"
            @page-size-change="handlePageSizeChange"
            @prev="goPrevPage"
            @next="goNextPage"
          />
        </div>
      </div>

      <!-- Task Detail Panel -->
      <TaskExecutionDetailPanel
        :show="showPreviewModal"
        :task="selectedTask"
        :details-loading="selectedTaskDetailsLoading"
        @load-details="loadSelectedTaskDetails"
        @close="showPreviewModal = false"
      />
    </div>
  </AdminLayout>
</template>

<script setup>
import { ref, onBeforeUnmount, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { format } from 'date-fns'
import { useDebounceFn } from '@vueuse/core'
import { useToast } from '@/composables/useToast'
import { extractResponseData, extractErrorMessage } from '@/utils/api'
import { formatDuration } from '@/utils/formatting'
import { taskManagementApi, managementApi } from '@/admin/api'
import AdminLayout from '@/admin/layout/AdminLayout.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseDateInput from '@/components/ui/BaseDateInput.vue'
import BaseInput from '@/components/ui/BaseInput.vue'
import BaseLoading from '@/components/ui/BaseLoading.vue'
import BaseSelect from '@/components/ui/BaseSelect.vue'
import PaginationBar from '@/components/ui/PaginationBar.vue'
import StatusBadge from '@/components/ui/StatusBadge.vue'
import TaskExecutionDetailPanel from '@/components/task-management/TaskExecutionDetailPanel.vue'

const { t } = useI18n()
const { showError } = useToast()
const route = useRoute()

function getDefaultDetailDateRange() {
  const now = new Date()
  const endStr = format(now, 'yyyy-MM-dd')
  const start = new Date(now)
  start.setDate(start.getDate() - 3)
  return { startDate: format(start, 'yyyy-MM-dd'), endDate: endStr }
}

const defaultDateRange = getDefaultDetailDateRange()
const loading = ref(false)
const tasks = ref([])
const searchQuery = ref('')
const filterModule = ref('')
const filterUserId = ref('')
const filterStartDate = ref(defaultDateRange.startDate)
const filterEndDate = ref(defaultDateRange.endDate)
const userOptions = ref([])
const showPreviewModal = ref(false)
const selectedTask = ref(null)
const selectedTaskDetailsLoading = ref(false)
const selectedTaskDetailsLoadedId = ref(null)
const currentPage = ref(1)
const totalCount = ref(0)
const totalPages = ref(1)
const pageSize = ref(20)
const processingRefreshTimer = ref(null)
const processingRefreshInFlight = ref(false)

const PROCESSING_STATUSES = new Set(['PENDING', 'STARTED', 'RETRY'])
const BASIC_METADATA_FIELDS = [
  'type',
  'trigger',
  'datasource_uuid',
  'datasource_name',
  'source_type',
  'repo_url',
  'branch',
  'auth_scheme',
  'document_url',
  'app_token',
  'doc_ids',
  'sync_mode',
  'folder_url',
  'folder_token',
  'recursive',
  'max_depth',
  'credential_configured',
  'lensnode_uuid',
  'lensnode_name',
  'target_path',
  'conversion',
  'conversion_enabled',
  'sync_policy',
  'sync_interval_seconds',
  'phase',
  'overall_progress_percent',
  'phase_progress',
  'progress_counts',
  'last_substantive_progress_at'
].join(',')

function mapStatus(status) {
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

async function loadTasks() {
  loading.value = true
  try {
    const params = {
      page: currentPage.value,
      page_size: pageSize.value,
      include_metadata: false,
      my_tasks: 'false'
    }
    if (filterModule.value) {
      params.module = filterModule.value
    }
    if (filterUserId.value) {
      params.created_by = filterUserId.value
    }
    if (filterStartDate.value) {
      params.start_date = filterStartDate.value
    }
    if (filterEndDate.value) {
      params.end_date = filterEndDate.value
    }
    if (searchQuery.value.trim()) {
      params.task_name = searchQuery.value.trim()
    }
    const res = await taskManagementApi.getExecutions(params)
    const data = extractResponseData(res)
    const list =
      data?.results ?? data?.list ?? (Array.isArray(data) ? data : [])
    const serverTotal = data?.count ?? data?.pagination?.total
    const hasServerPagination = Number.isFinite(Number(serverTotal))
    const total = hasServerPagination ? Number(serverTotal) : list.length
    tasks.value = hasServerPagination
      ? list
      : list.slice(
          (currentPage.value - 1) * pageSize.value,
          currentPage.value * pageSize.value
        )
    totalCount.value = total
    totalPages.value = total > 0 ? Math.ceil(total / pageSize.value) : 1
  } catch (e) {
    showError(extractErrorMessage(e, t('common.error')))
    tasks.value = []
  } finally {
    loading.value = false
  }
}

async function refreshProcessingTasks() {
  if (processingRefreshInFlight.value) {
    return
  }

  const processingTasks = tasks.value.filter((task) =>
    isProcessingStatus(task.status)
  )
  if (!processingTasks.length) {
    return
  }

  processingRefreshInFlight.value = true
  try {
    const results = await Promise.allSettled(
      processingTasks.map((task) =>
        taskManagementApi.getExecution(task.id, {
          include_metadata: false
        })
      )
    )
    const refreshedById = new Map()
    results.forEach((result) => {
      if (result.status !== 'fulfilled') {
        return
      }
      const row = extractResponseData(result.value)
      if (row?.id == null) {
        return
      }
      refreshedById.set(String(row.id), row)
    })
    if (!refreshedById.size) {
      return
    }
    tasks.value = tasks.value.map((task) =>
      refreshedById.has(String(task.id))
        ? { ...task, ...refreshedById.get(String(task.id)) }
        : task
    )
    if (
      selectedTask.value &&
      refreshedById.has(String(selectedTask.value.id))
    ) {
      const refreshed = refreshedById.get(String(selectedTask.value.id))
      selectedTask.value = {
        ...selectedTask.value,
        ...refreshed,
        metadata: selectedTask.value.metadata
      }
      if (selectedTaskDetailsLoadedId.value === String(selectedTask.value.id)) {
        await loadSelectedTaskDetails({ force: true, silent: true })
      }
    }
  } finally {
    processingRefreshInFlight.value = false
  }
}

function startProcessingRefresh() {
  stopProcessingRefresh()
  processingRefreshTimer.value = window.setInterval(
    refreshProcessingTasks,
    3000
  )
}

function stopProcessingRefresh() {
  if (!processingRefreshTimer.value) {
    return
  }
  window.clearInterval(processingRefreshTimer.value)
  processingRefreshTimer.value = null
}

function onFilterChange() {
  currentPage.value = 1
  loadTasks()
}

function handlePageSizeChange() {
  currentPage.value = 1
  loadTasks()
}

function goPrevPage() {
  if (currentPage.value <= 1) return
  currentPage.value -= 1
  loadTasks()
}

function goNextPage() {
  if (currentPage.value >= totalPages.value) return
  currentPage.value += 1
  loadTasks()
}

function toUserLabel(u) {
  if (u.display_name) return u.display_name
  if (u.username) return u.username
  if (u.id != null) return String(u.id)
  return ''
}

async function fetchUserOptions() {
  try {
    const list = await managementApi.getAllUsers({ compact: 'true' })
    userOptions.value = list.map((u) => ({ id: u.id, label: toUserLabel(u) }))
  } catch {
    userOptions.value = []
  }
}

const debouncedLoad = useDebounceFn(() => {
  currentPage.value = 1
  loadTasks()
}, 300)

async function handlePreview(task) {
  try {
    selectedTaskDetailsLoading.value = false
    selectedTaskDetailsLoadedId.value = null
    const res = await taskManagementApi.getExecution(task.id, {
      include_metadata: true,
      metadata_fields: BASIC_METADATA_FIELDS
    })
    selectedTask.value = extractResponseData(res)
    showPreviewModal.value = true
  } catch (e) {
    showError(extractErrorMessage(e, t('common.error')))
  }
}

async function loadSelectedTaskDetails(options = {}) {
  const taskId = selectedTask.value?.id
  if (
    !taskId ||
    selectedTaskDetailsLoading.value ||
    (!options.force && selectedTaskDetailsLoadedId.value === String(taskId))
  ) {
    return
  }
  if (!options.silent) {
    selectedTaskDetailsLoading.value = true
  }
  try {
    const res = await taskManagementApi.getExecution(taskId, {
      include_metadata: true
    })
    const detail = extractResponseData(res)
    if (String(selectedTask.value?.id) !== String(taskId)) {
      return
    }
    selectedTask.value = {
      ...selectedTask.value,
      ...detail
    }
    selectedTaskDetailsLoadedId.value = String(taskId)
  } catch (e) {
    if (!options.silent) {
      showError(extractErrorMessage(e, t('common.error')))
    }
  } finally {
    if (!options.silent && String(selectedTask.value?.id) === String(taskId)) {
      selectedTaskDetailsLoading.value = false
    }
  }
}

async function openInitialExecution() {
  const executionId = route.query.execution_id
  if (!executionId) return
  await handlePreview({ id: executionId })
}

onMounted(async () => {
  fetchUserOptions()
  await loadTasks()
  await openInitialExecution()
  startProcessingRefresh()
})

onBeforeUnmount(() => {
  stopProcessingRefresh()
})
</script>
