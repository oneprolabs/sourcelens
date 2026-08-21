<template>
  <AdminLayout>
    <div class="flex h-full min-h-0 max-w-full flex-col gap-4 py-4">
      <section
        class="flex max-h-full min-h-0 w-full flex-col overflow-hidden rounded-lg border border-line bg-surface shadow-sm"
      >
        <div
          class="flex flex-shrink-0 flex-col gap-4 border-b border-line px-5 py-4 lg:flex-row lg:items-start lg:justify-between"
        >
          <div class="min-w-0">
            <div class="flex flex-wrap items-center gap-2">
              <h1 class="text-xl font-semibold text-ink-900">
                {{ t('lensAdmin.pages.datasources.title') }}
              </h1>
              <span
                class="rounded-md border border-line bg-surface-sunken px-2 py-1 text-xs text-ink-500"
              >
                {{
                  t('lensAdmin.total', {
                    label: t('lensAdmin.pages.datasources.label'),
                    count: totalDataSources
                  })
                }}
              </span>
            </div>
          </div>
          <div class="flex flex-wrap items-center gap-2">
            <BaseButton
              variant="outline"
              size="sm"
              :loading="loading"
              @click="load"
            >
              {{ t('common.refresh') }}
            </BaseButton>
            <BaseButton variant="primary" size="sm" @click="startCreate">
              {{ t('lensAdmin.pages.datasources.action') }}
            </BaseButton>
          </div>
        </div>

        <div class="flex min-h-0 flex-col px-5 py-4">
          <div
            class="mb-4 flex flex-shrink-0 flex-col gap-3 sm:flex-row sm:items-center sm:justify-between"
          >
            <div
              ref="searchBoxRef"
              class="relative min-w-0 flex-1 sm:max-w-2xl"
              @focusin="openSearchPicker"
              @focusout="handleSearchFocusOut"
            >
              <div
                class="flex max-h-20 min-w-0 flex-wrap items-center gap-2 overflow-y-auto rounded-md border border-line bg-surface px-3 py-1 shadow-sm transition focus-within:border-brand-500 focus-within:ring-2 focus-within:ring-brand-500/20"
              >
                <SearchIcon class="h-4 w-4 shrink-0 text-ink-400" />
                <span
                  v-for="(filter, index) in searchFilters"
                  :key="`${filter.key}:${filter.value}:${index}`"
                  class="inline-flex max-w-56 shrink-0 items-center gap-1 rounded-md border border-brand-200 bg-brand-50 px-2 py-1 text-xs font-medium text-brand-700"
                >
                  <span class="truncate">
                    {{ searchFilterLabel(filter) }}: {{ filter.value }}
                  </span>
                  <button
                    type="button"
                    class="text-brand-500 hover:text-brand-700"
                    :title="t('lensAdmin.datasourceSearch.removeFilter')"
                    @click.stop="removeSearchFilter(index)"
                  >
                    <XIcon class="h-3 w-3" />
                  </button>
                </span>
                <span
                  v-if="searchKey"
                  class="inline-flex max-w-md shrink-0 items-center gap-1 rounded-md border border-brand-200 bg-brand-50 px-2 py-1 text-xs font-medium text-brand-700"
                >
                  <span class="shrink-0"
                    >{{ selectedSearchOption.label }}:</span
                  >
                  <input
                    ref="searchValueInputRef"
                    v-model="searchQuery"
                    class="min-w-20 flex-1 border-0 bg-transparent p-0 text-xs font-medium text-brand-800 placeholder:text-brand-400 focus:outline-none focus:ring-0"
                    :placeholder="
                      t('lensAdmin.datasourceSearch.valuePlaceholder')
                    "
                    @keyup.enter.prevent="addSearchFilter"
                  />
                  <button
                    type="button"
                    class="shrink-0 text-brand-500 hover:text-brand-700"
                    :title="t('lensAdmin.datasourceSearch.clearField')"
                    @click.stop="clearSearchKey"
                  >
                    <XIcon class="h-3 w-3" />
                  </button>
                </span>
                <input
                  v-if="!searchKey"
                  ref="searchInputRef"
                  v-model="searchQuery"
                  class="min-w-0 flex-1 border-0 bg-transparent py-2 text-sm text-ink-900 placeholder:text-ink-400 focus:outline-none focus:ring-0"
                  :placeholder="searchInputPlaceholder"
                  type="search"
                  @keyup.enter.prevent="handleSearchEnter"
                />
                <button
                  v-if="searchQuery || searchKey"
                  type="button"
                  class="inline-flex h-7 w-7 shrink-0 items-center justify-center rounded text-ink-400 hover:bg-surface-sunken hover:text-ink-700"
                  :title="t('lensAdmin.datasourceSearch.clear')"
                  @click="clearSearch"
                >
                  <XIcon class="h-4 w-4" />
                </button>
              </div>
              <div
                v-if="searchPickerOpen"
                class="absolute left-0 right-0 top-full z-20 mt-2 rounded-lg border border-line bg-surface p-2 shadow-lg"
              >
                <div class="px-2 pb-2 text-xs font-medium text-ink-500">
                  {{ t('lensAdmin.datasourceSearch.fieldHint') }}
                </div>
                <div v-if="!searchKey" class="flex flex-wrap gap-2">
                  <button
                    v-for="option in filteredSearchOptions"
                    :key="option.value"
                    type="button"
                    class="rounded-md border px-2.5 py-1.5 text-xs font-medium transition"
                    :class="
                      searchKey === option.value
                        ? 'border-brand-200 bg-brand-50 text-brand-700'
                        : 'border-line bg-surface-sunken text-ink-600 hover:border-brand-200 hover:bg-brand-50 hover:text-brand-700'
                    "
                    @click="selectSearchKey(option.value)"
                  >
                    {{ option.label }}
                  </button>
                </div>
                <div v-else class="px-2 py-2 text-sm text-ink-500">
                  {{ t('lensAdmin.datasourceSearch.valueHint') }}
                </div>
              </div>
            </div>
            <div v-if="searchFilters.length" class="text-xs text-ink-500">
              {{
                t('lensAdmin.datasourceSearch.filterCount', {
                  count: searchFilters.length
                })
              }}
            </div>
          </div>

          <BaseLoading v-if="loading && dataSources.length === 0" />

          <div
            v-else-if="dataSources.length === 0"
            class="rounded-lg border border-line bg-surface-sunken py-16 text-center"
          >
            <p class="text-sm font-medium text-ink-500">
              {{ t('common.noData') }}
            </p>
          </div>

          <div
            v-else
            class="datasource-table-scroll relative min-h-0 overflow-auto rounded-lg border border-line bg-surface"
          >
            <table class="min-w-full divide-y divide-line">
              <thead class="sticky top-0 z-10 bg-surface-sunken">
                <tr>
                  <th class="table-head">
                    {{ t('lensAdmin.columns.datasource') }}
                  </th>
                  <th class="table-head">
                    {{ t('lensAdmin.columns.repository') }}
                  </th>
                  <th class="table-head">
                    {{ t('lensAdmin.columns.lensnode') }}
                  </th>
                  <th class="table-head">
                    {{ t('lensAdmin.columns.targetPath') }}
                  </th>
                  <th class="table-head">
                    {{ t('lensAdmin.columns.status') }}
                  </th>
                  <th class="table-head">
                    {{ t('lensAdmin.columns.policy') }}
                  </th>
                  <th class="table-head">
                    {{ t('lensAdmin.columns.actions') }}
                  </th>
                </tr>
              </thead>
              <tbody class="divide-y divide-line bg-surface">
                <tr
                  v-for="row in dataSources"
                  :key="row.uuid"
                  class="cursor-pointer transition-colors hover:bg-line-soft"
                  :class="
                    selectedDataSource?.uuid === row.uuid ? 'bg-brand-50' : ''
                  "
                  @click="selectDataSource(row)"
                >
                  <td class="table-cell">
                    <div
                      class="flex items-center gap-2 font-medium text-ink-900"
                    >
                      <span
                        :class="
                          isDataSourceEnabled(row)
                            ? 'bg-success-600'
                            : 'bg-danger-600'
                        "
                        class="h-2 w-2 shrink-0 rounded-full"
                      />
                      <span>{{ row.name }}</span>
                    </div>
                    <div class="mt-1 flex flex-wrap items-center gap-2">
                      <span class="font-mono text-xs text-ink-400">
                        {{ compactUuid(row.uuid) }}
                      </span>
                      <span
                        class="rounded border border-line bg-surface-sunken px-1.5 py-0.5 text-xs text-ink-500"
                      >
                        {{ formatSourceType(row.source_type) }}
                      </span>
                    </div>
                  </td>
                  <td class="table-cell max-w-xs text-ink-600">
                    <div class="truncate" :title="dataSourceRepository(row)">
                      {{ dataSourceRepository(row) }}
                    </div>
                    <div
                      v-if="isOrganizationDataSource(row)"
                      class="mt-2 flex max-w-xs flex-wrap gap-1"
                    >
                      <span
                        v-for="repo in visibleRepositoryTags(row)"
                        :key="repo.repo_url || repo.name || repo.path"
                        class="max-w-28 truncate rounded border border-primary-200 bg-primary-50 px-1.5 py-0.5 text-xs font-medium text-primary-700"
                        :title="repo.repo_url || repo.name || repo.path"
                      >
                        {{ repo.name || repo.path || repo.repo_url }}
                      </span>
                      <span
                        v-if="hiddenRepositoryTagCount(row) > 0"
                        class="rounded border border-line bg-surface-sunken px-1.5 py-0.5 text-xs text-ink-500"
                      >
                        +{{ hiddenRepositoryTagCount(row) }}
                      </span>
                    </div>
                    <div
                      v-if="
                        row.source_type === 'git' &&
                        !isOrganizationDataSource(row)
                      "
                      class="mt-1 font-mono text-xs text-ink-500"
                    >
                      {{ dataSourceBranch(row) }}
                    </div>
                  </td>
                  <td class="table-cell text-ink-600">
                    {{ row.lensnode_name || lensNodeName(row.lensnode) }}
                  </td>
                  <td
                    class="table-cell max-w-xs font-mono text-xs text-ink-500"
                  >
                    <div
                      class="truncate"
                      :title="row.target_path || emptyValue"
                    >
                      {{ row.target_path || emptyValue }}
                    </div>
                  </td>
                  <td class="table-cell text-ink-600">
                    <div class="flex max-w-sm flex-col items-start gap-2">
                      <div class="flex flex-wrap gap-1.5">
                        <span
                          v-for="tag in datasourceSyncTags(row)"
                          :key="tag.key"
                          :class="tag.class"
                          class="inline-flex items-center rounded-md border px-2 py-0.5 text-xs font-medium"
                        >
                          {{ tag.label }}
                        </span>
                      </div>
                      <div
                        v-if="isDataSourceSyncing(row)"
                        class="space-y-1 text-xs text-ink-500"
                      >
                        <div class="break-words">
                          {{
                            row.current_sync?.progress_message ||
                            row.current_sync?.progress_step ||
                            emptyValue
                          }}
                        </div>
                        <div class="font-mono">
                          {{ compactUuid(row.current_sync?.task_id) }}
                        </div>
                      </div>
                    </div>
                  </td>
                  <td class="table-cell">
                    <div
                      v-if="row.source_type === 'managed_workspace'"
                      class="space-y-1 text-xs text-ink-500"
                    >
                      <div class="font-medium text-ink-700">
                        {{ t('lensAdmin.availability.title') }}
                      </div>
                      <div>
                        {{ formatDateTime(row.availability_checked_at) }}
                      </div>
                    </div>
                    <div v-else class="space-y-1 text-xs text-ink-500">
                      <div class="font-mono text-ink-700">
                        {{ formatDataSourcePolicyLine(row.sync_policy) }}
                      </div>
                      <div>
                        {{ t('lensAdmin.table.lastSync') }}:
                        {{ formatDateTime(row.last_synced_at) }}
                      </div>
                      <div>
                        {{ t('lensAdmin.table.nextSync') }}:
                        {{ formatNextDatasourceSync(row) }}
                      </div>
                    </div>
                  </td>
                  <td class="table-cell" @click.stop>
                    <div class="flex flex-wrap gap-2">
                      <BaseButton
                        v-if="row.source_type !== 'managed_workspace'"
                        size="sm"
                        variant="outline"
                        :disabled="
                          !isDataSourceEnabled(row) || isDataSourceSyncing(row)
                        "
                        @click="sync(row)"
                      >
                        {{ t('lensAdmin.actions.sync') }}
                      </BaseButton>
                      <BaseButton
                        v-else
                        size="sm"
                        variant="outline"
                        @click="refreshAvailability(row)"
                      >
                        {{ t('lensAdmin.actions.refreshAvailability') }}
                      </BaseButton>
                      <BaseButton
                        size="sm"
                        :variant="
                          isDataSourceEnabled(row) ? 'outline' : 'primary'
                        "
                        @click="toggleDataSourceEnabled(row)"
                      >
                        {{
                          isDataSourceEnabled(row)
                            ? t('lensAdmin.actions.disableDatasource')
                            : t('lensAdmin.actions.enableDatasource')
                        }}
                      </BaseButton>
                      <BaseButton
                        v-if="isDataSourceSyncing(row)"
                        size="sm"
                        variant="danger"
                        @click="cancelSync(row)"
                      >
                        {{ t('lensAdmin.actions.cancelSync') }}
                      </BaseButton>
                      <RowActions
                        :row="row"
                        @edit="startEdit"
                        @delete="remove"
                      />
                    </div>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
          <PaginationBar
            v-if="!loading"
            v-model:page-size="pageSize"
            :current-page="currentPage"
            :total="totalDataSources"
            @page-size-change="handlePageSizeChange"
            @prev="goPrevPage"
            @next="goNextPage"
          />
        </div>
      </section>

      <DataSourceFormDrawer
        :show="showDrawer"
        :mode="mode"
        :form="form"
        :config="datasourceConfig"
        :lensnodes="lensnodes"
        :credentials="credentials"
        :llm-config-options="llmConfigOptions"
        v-model:sync-interval-seconds="syncIntervalSeconds"
        v-model:sync-policy-mode="syncPolicyMode"
        v-model:sync-cron="syncCron"
        v-model:sync-timezone="syncTimezone"
        :path-result="datasourcePathResult"
        :connection-result="datasourceConnectionResult"
        :checking-path="checkingDatasourcePath"
        :testing-connection="testingDatasourceConnection"
        :refreshing-credentials="refreshingCredentials"
        :refreshing-directories="refreshingDirectories"
        :saving="saving"
        :form-error="formError"
        @close="closeDrawer"
        @save="save"
        @type-change="handleDatasourceTypeChange"
        @check-path="checkDatasourcePath"
        @test-connection="testDatasourceConnection"
        @connection-change="resetDatasourceConnectionResult"
        @refresh-credentials="refreshCredentials"
        @refresh-dirs="refreshDirectories"
      />

      <DataSourceDetailDrawer
        :show="showDatasourceDetailDrawer"
        :datasource="selectedDataSource"
        :lensnodes="lensnodes"
        @close="closeDataSourceDetail"
      />
    </div>
  </AdminLayout>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { Search as SearchIcon, X as XIcon } from '@lucide/vue'
import { useI18n } from 'vue-i18n'
import { onBeforeRouteLeave, useRoute } from 'vue-router'

import { extractErrorMessage } from '@/utils/api'
import { llmAdminApi } from '@/admin/api/llmAdmin'
import AdminLayout from '@/admin/layout/AdminLayout.vue'
import {
  cancelDataSourceSync,
  checkLensNodeDataSourcePath,
  createDataSource,
  deleteDataSource,
  listCredentials,
  listDataSources,
  listLensNodes,
  scanLensNodeDirs,
  refreshDataSourceAvailability,
  setDataSourceEnabled,
  syncDataSource,
  testLensNodeDataSourceConnection,
  updateDataSource
} from '@/api/lens'
import { useToast } from '@/composables/useToast'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseLoading from '@/components/ui/BaseLoading.vue'
import PaginationBar from '@/components/ui/PaginationBar.vue'

import DataSourceDetailDrawer from './DataSourceDetailDrawer.vue'
import DataSourceFormDrawer from './DataSourceFormDrawer.vue'
import RowActions from './components/RowActions.vue'
import {
  EMPTY_VALUE as emptyValue,
  compactUuid,
  normalizeList
} from './adminHelpers'
import {
  dataSourceBranch,
  dataSourceRepositories,
  dataSourceRepository,
  formatDataSourcePolicyLine,
  isDataSourceEnabled,
  isOrganizationDataSource,
  isDataSourceSyncing,
  syncTagClass
} from './datasourceHelpers'
import { useShortDateTime } from './useShortDateTime'

const { t } = useI18n()
const { showSuccess, showError } = useToast()
const route = useRoute()

const loading = ref(false)
const saving = ref(false)
const mode = ref('create')
const form = ref({})
const formError = ref('')
const showDrawer = ref(false)
const showDatasourceDetailDrawer = ref(false)

const dataSources = ref([])
const totalDataSources = ref(0)
const currentPage = ref(1)
const pageSize = ref(20)
const searchKey = ref('')
const searchQuery = ref('')
const searchFilters = ref([])
const searchPickerOpen = ref(false)
const searchBoxRef = ref(null)
const searchInputRef = ref(null)
const searchValueInputRef = ref(null)
const lensnodes = ref([])
const credentials = ref([])
const llmConfigOptions = ref([])
const selectedDataSource = ref(null)

const datasourceConfig = ref({})
const datasourcePathResult = ref(null)
const datasourceConnectionResult = ref(null)
const suppressDatasourceConnectionReset = ref(false)
const datasourceConnectionBaseSignature = ref('')
const checkingDatasourcePath = ref(false)
const testingDatasourceConnection = ref(false)
const refreshingCredentials = ref(false)
const refreshingDirectories = ref(false)
const syncIntervalSeconds = ref(3600)
const syncPolicyMode = ref('interval')
const syncCron = ref('0 2 * * *')
const syncTimezone = ref('Asia/Shanghai')
const dynamicRefreshTimer = ref(null)
const dynamicRefreshInFlight = ref(false)

const totalPages = computed(() =>
  Math.max(1, Math.ceil(totalDataSources.value / pageSize.value))
)

const datasourceSearchOptions = computed(() => [
  {
    value: '_all',
    label: t('lensAdmin.datasourceSearch.all')
  },
  {
    value: 'name',
    label: t('lensAdmin.columns.datasource')
  },
  {
    value: 'source_type',
    label: t('lensAdmin.datasourceSearch.sourceType')
  },
  {
    value: 'repository',
    label: t('lensAdmin.columns.repository')
  },
  {
    value: 'lensnode',
    label: t('lensAdmin.columns.lensnode')
  },
  {
    value: 'target_path',
    label: t('lensAdmin.columns.targetPath')
  },
  {
    value: 'status',
    label: t('lensAdmin.columns.status')
  },
  {
    value: 'sync_policy',
    label: t('lensAdmin.columns.policy')
  }
])

const selectedSearchOption = computed(
  () =>
    datasourceSearchOptions.value.find(
      (item) => item.value === searchKey.value
    ) || datasourceSearchOptions.value[0]
)

const filteredSearchOptions = computed(() => {
  const keyword = searchQuery.value.trim()
  if (!keyword) {
    return datasourceSearchOptions.value
  }
  const normalized = keyword.toLowerCase()
  return datasourceSearchOptions.value.filter((option) =>
    option.label.toLowerCase().includes(normalized)
  )
})

const searchInputPlaceholder = computed(() => {
  if (searchKey.value) {
    return t('lensAdmin.datasourceSearch.valuePlaceholder')
  }
  return t('lensAdmin.datasourceSearch.placeholder')
})

function handlePageSizeChange() {
  currentPage.value = 1
  load()
}

function goPrevPage() {
  if (currentPage.value <= 1) return
  currentPage.value -= 1
  load()
}

function goNextPage() {
  if (currentPage.value >= totalPages.value) return
  currentPage.value += 1
  load()
}

function datasourceListParams() {
  const params = {
    page: currentPage.value,
    page_size: pageSize.value
  }
  if (searchFilters.value.length) {
    params.filters = JSON.stringify(searchFilters.value)
  }
  return params
}

function applySearch() {
  currentPage.value = 1
  load()
}

function openSearchPicker() {
  searchPickerOpen.value = true
}

function handleSearchFocusOut(event) {
  const nextTarget = event.relatedTarget
  if (nextTarget && searchBoxRef.value?.contains(nextTarget)) {
    return
  }
  searchPickerOpen.value = false
}

function selectSearchKey(value) {
  searchKey.value = value
  searchQuery.value = ''
  searchPickerOpen.value = false
  nextTick(() => {
    searchValueInputRef.value?.focus()
  })
}

function clearSearchKey() {
  searchKey.value = ''
  searchPickerOpen.value = true
}

function clearSearch() {
  searchQuery.value = ''
  searchKey.value = ''
  searchPickerOpen.value = true
}

function handleSearchEnter() {
  if (!searchKey.value) {
    if (filteredSearchOptions.value.length === 1) {
      selectSearchKey(filteredSearchOptions.value[0].value)
    }
    return
  }
  addSearchFilter()
}

function addSearchFilter() {
  const value = searchQuery.value.trim()
  if (!searchKey.value || !value) {
    return
  }
  searchFilters.value = [
    ...searchFilters.value,
    {
      key: searchKey.value,
      value
    }
  ]
  searchKey.value = ''
  searchQuery.value = ''
  searchPickerOpen.value = false
  applySearch()
}

function removeSearchFilter(index) {
  searchFilters.value = searchFilters.value.filter(
    (_, itemIndex) => itemIndex !== index
  )
  applySearch()
}

function searchFilterLabel(filter) {
  const option = datasourceSearchOptions.value.find(
    (item) => item.value === filter.key
  )
  return option?.label || t('lensAdmin.datasourceSearch.all')
}

const DYNAMIC_REFRESH_INTERVAL_MS = 3000

const formatDateTime = useShortDateTime()

const selectedDatasourceLensNode = computed(() =>
  lensnodes.value.find((node) => node.uuid === form.value.lensnode_uuid)
)

function datasourceSyncTags(row) {
  const tags = []
  if (!isDataSourceEnabled(row)) {
    tags.push({
      key: 'disabled',
      label: t('common.status.disabled'),
      class: syncTagClass('disabled')
    })
  }
  if (row.source_type === 'managed_workspace') {
    const availability = row.availability_status || 'unknown'
    tags.push({
      key: 'availability',
      label: t(`lensAdmin.availability.${availability}`),
      class: syncTagClass(availability)
    })
    return tags
  }
  if (!isDataSourceEnabled(row)) {
    return tags
  }
  if (isDataSourceSyncing(row)) {
    tags.push({
      key: 'running',
      label: t('lensAdmin.table.syncRunning'),
      class: syncTagClass('running')
    })
  } else {
    const status = row.sync_state?.last_status || ''
    tags.push({
      key: 'last-status',
      label: formatDatasourceLastSyncStatus(status),
      class: syncTagClass(status || 'not_synced')
    })
  }
  return tags
}

function formatDatasourceLastSyncStatus(status) {
  if (status === 'success') {
    return t('common.status.success')
  }
  if (status === 'failed') {
    return t('common.status.failed')
  }
  return t('lensAdmin.table.notSynced')
}

function formatNextDatasourceSync(row) {
  if (!isDataSourceEnabled(row)) {
    return t('common.status.disabled')
  }
  if (row.sync_state?.next_run_at) {
    return formatDateTime(row.sync_state.next_run_at)
  }
  if (row.sync_policy?.mode === 'crontab') {
    return t('lensAdmin.table.followCrontab')
  }
  return t('lensAdmin.table.notRecorded')
}

function selectDataSource(row) {
  selectedDataSource.value = row
  showDatasourceDetailDrawer.value = true
}

function closeDataSourceDetail() {
  showDatasourceDetailDrawer.value = false
}

function formatSourceType(sourceType) {
  if (isGitSourceType(sourceType)) {
    if (sourceType === 'github') return 'GitHub'
    if (sourceType === 'gitlab') return 'GitLab'
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

function isGitSourceType(sourceType) {
  return ['git', 'github', 'gitlab'].includes(sourceType)
}

function normalizedSourceType(sourceType) {
  return isGitSourceType(sourceType) ? 'git' : sourceType
}

function uiSourceTypeFromRow(row) {
  if (row?.source_type !== 'git') {
    return row?.source_type || 'feishu'
  }
  const credentialUuid = row?.credential || ''
  const credential = credentials.value.find(
    (item) => item.uuid === credentialUuid
  )
  const provider =
    credential?.provider ||
    row?.credential_provider ||
    row?.credential_detail?.provider
  if (provider === 'gitlab' || provider === 'github') {
    return provider
  }
  const repoUrl = String(
    row?.config?.organization_url || row?.config?.repo_url || ''
  )
  if (repoUrl.includes('github.com')) {
    return 'github'
  }
  if (repoUrl.includes('gitlab')) {
    return 'gitlab'
  }
  return 'github'
}

function lensNodeName(value) {
  const uuid = typeof value === 'object' ? value?.uuid : value
  const found = lensnodes.value.find((lensnode) => lensnode.uuid === uuid)
  return found?.name || uuid || emptyValue
}

function visibleRepositoryTags(row) {
  return dataSourceRepositories(row).slice(0, 4)
}

function hiddenRepositoryTagCount(row) {
  return Math.max(0, dataSourceRepositories(row).length - 4)
}

async function load() {
  loading.value = true
  formError.value = ''
  try {
    const [dataSourceRows, lensnodeRows, credentialRows, llmConfigRows] =
      await Promise.all([
        listDataSources(datasourceListParams()),
        listLensNodes(),
        listCredentials(),
        llmAdminApi.getLLMConfigAll({ scope: 'global' }).catch(() => [])
      ])
    applyDataSourceRows(dataSourceRows, { selectFallback: true })
    lensnodes.value = normalizeList(lensnodeRows)
    credentials.value = normalizeList(credentialRows)
    llmConfigOptions.value = normalizeList(llmConfigRows)
    updateDynamicRefresh()
  } catch (error) {
    showError(extractErrorMessage(error, t('lensAdmin.messages.loadFailed')))
  } finally {
    loading.value = false
  }
}

function applyDataSourceRows(rows, options = {}) {
  const payload = rows && typeof rows === 'object' ? rows : {}
  dataSources.value = Array.isArray(payload.results)
    ? payload.results
    : normalizeList(rows)
  totalDataSources.value =
    typeof payload.count === 'number' ? payload.count : dataSources.value.length
  const selectedUuid = selectedDataSource.value?.uuid
  const existing = dataSources.value.find((row) => row.uuid === selectedUuid)
  if (existing) {
    selectedDataSource.value = existing
    return
  }
  if (options.selectFallback) {
    selectedDataSource.value = dataSources.value[0] || null
  }
}

async function refreshDataSourceRows() {
  if (!shouldRefreshDataSources() || dynamicRefreshInFlight.value) {
    if (!shouldRefreshDataSources()) {
      stopDynamicRefresh()
    }
    return
  }
  dynamicRefreshInFlight.value = true
  try {
    applyDataSourceRows(await listDataSources(datasourceListParams()))
    updateDynamicRefresh()
  } catch {
    // Silent refresh should not interrupt the datasource management workflow.
  } finally {
    dynamicRefreshInFlight.value = false
  }
}

function startDynamicRefresh() {
  if (!shouldRefreshDataSources()) {
    stopDynamicRefresh()
    return
  }
  if (dynamicRefreshTimer.value) {
    return
  }
  stopDynamicRefresh()
  dynamicRefreshTimer.value = window.setInterval(
    refreshDataSourceRows,
    DYNAMIC_REFRESH_INTERVAL_MS
  )
}

function stopDynamicRefresh() {
  if (!dynamicRefreshTimer.value) {
    return
  }
  window.clearInterval(dynamicRefreshTimer.value)
  dynamicRefreshTimer.value = null
}

function hasRunningDataSourceSync() {
  return dataSources.value.some((row) => isDataSourceSyncing(row))
}

function isCurrentDataSourcePage() {
  return route.name === 'LensDataSources'
}

function shouldRefreshDataSources() {
  return isCurrentDataSourcePage() && hasRunningDataSourceSync()
}

function updateDynamicRefresh() {
  if (shouldRefreshDataSources()) {
    startDynamicRefresh()
  } else {
    stopDynamicRefresh()
  }
}

function startCreate() {
  showDatasourceDetailDrawer.value = false
  mode.value = 'create'
  formError.value = ''
  datasourceConfig.value = {}
  datasourcePathResult.value = null
  datasourceConnectionResult.value = null
  syncIntervalSeconds.value = 3600
  form.value = defaultForm()
  showDrawer.value = true
}

function startEdit(row) {
  showDatasourceDetailDrawer.value = false
  mode.value = 'edit'
  formError.value = ''
  datasourceConfig.value = { ...(row.config || {}) }
  datasourcePathResult.value = null
  syncIntervalSeconds.value = row.sync_policy?.interval_seconds || 3600
  form.value = formFromRow(row)
  datasourceConnectionResult.value = cachedDatasourceConnectionResult(row)
  showDrawer.value = true
}

function closeDrawer() {
  showDrawer.value = false
  form.value = {}
  formError.value = ''
  datasourcePathResult.value = null
  datasourceConnectionResult.value = null
  resetDatasourceSyncPolicy()
}

function defaultForm() {
  const seed = {
    name: '',
    source_type: 'github',
    lensnode_uuid: '',
    workspace_relative_path: '',
    target_path: '',
    credential_uuid: '',
    credential_configured: false,
    conversion_document: true,
    conversion_document_model_ref: '',
    conversion_image: false,
    conversion_embedded_image: false,
    conversion_vision_model_ref: '',
    conversion_max_images: 100,
    conversion_max_file_size_mb: 100,
    conversion_max_pages: 500,
    conversion_pdf_extract_images: true,
    conversion_pdf_extract_images_on_text_pages: false,
    conversion_pdf_render_scanned_pages: false,
    conversion_pdf_max_pages: 30,
    conversion_pdf_max_images_per_page: 3,
    conversion_pdf_render_dpi: 144,
    conversion_pdf_min_text_chars: 30,
    conversion_pdf_min_image_area_ratio: 0.08,
    status: 'active'
  }
  handleDatasourceTypeChange(seed)
  return seed
}

function formFromRow(row) {
  const lensnodeUuid = row.lensnode?.uuid || row.lensnode || ''
  datasourceConfig.value = datasourceConfigFromRow(row)
  hydrateDatasourceSyncPolicy(row.sync_policy || {})
  return {
    uuid: row.uuid,
    name: row.name || '',
    source_type: uiSourceTypeFromRow(row),
    lensnode_uuid: lensnodeUuid,
    workspace_relative_path: workspaceRelativePath(
      row.target_path || '',
      lensnodeUuid
    ),
    target_path: row.target_path || '',
    credential_uuid: row.credential || '',
    credential_configured: !!row.credential_configured,
    conversion_document: row.sync_policy?.conversion?.document === true,
    conversion_document_model_ref:
      row.sync_policy?.conversion?.document_model_ref || '',
    conversion_image: row.sync_policy?.conversion?.image === true,
    conversion_embedded_image:
      row.sync_policy?.conversion?.embedded_image === true,
    conversion_vision_model_ref:
      row.sync_policy?.conversion?.vision_model_ref || '',
    conversion_max_images:
      Number(row.sync_policy?.conversion?.max_images) || 100,
    conversion_max_file_size_mb:
      Number(row.sync_policy?.conversion?.max_file_size_mb) || 100,
    conversion_max_pages: Number(row.sync_policy?.conversion?.max_pages) || 500,
    conversion_pdf_extract_images:
      row.sync_policy?.conversion?.pdf_extract_images !== false,
    conversion_pdf_extract_images_on_text_pages:
      row.sync_policy?.conversion?.pdf_extract_images_on_text_pages === true,
    conversion_pdf_render_scanned_pages:
      row.sync_policy?.conversion?.pdf_render_scanned_pages === true,
    conversion_pdf_max_pages:
      Number(row.sync_policy?.conversion?.pdf_max_pages) || 30,
    conversion_pdf_max_images_per_page:
      Number(row.sync_policy?.conversion?.pdf_max_images_per_page) || 3,
    conversion_pdf_render_dpi:
      Number(row.sync_policy?.conversion?.pdf_render_dpi) || 144,
    conversion_pdf_min_text_chars:
      Number(row.sync_policy?.conversion?.pdf_min_text_chars) || 30,
    conversion_pdf_min_image_area_ratio:
      Number(row.sync_policy?.conversion?.pdf_min_image_area_ratio) || 0.08,
    status: row.status || 'active'
  }
}

function datasourceConfigFromRow(row) {
  if (row.source_type === 'feishu') {
    return {
      ...(row.config || {}),
      sync_mode: row.config?.sync_mode || 'drive_folder',
      doc_ids_text: (row.config?.doc_ids || []).join(','),
      recursive: row.config?.recursive !== false,
      max_depth: row.config?.max_depth || 10,
      feishu_incremental: row.config?.feishu_incremental !== false,
      feishu_delete_missing: row.config?.feishu_delete_missing === true
    }
  }
  const config = { ...(row.config || {}) }
  delete config.access_token
  if (Array.isArray(config.repositories)) {
    config.git_repositories = config.repositories.map((repo) => ({
      ...repo,
      branches: repo.branch ? [repo.branch] : [],
      branch: repo.branch || '',
      selected: true
    }))
  }
  return config
}

function cachedDatasourceConnectionResult(row) {
  const config = datasourceConfig.value || {}
  if (row.source_type === 'git' && Array.isArray(config.git_repositories)) {
    return {
      status: 'success',
      message_code: 'git_organization_available',
      message: 'Git organization configuration is cached.',
      details: {
        scope: 'organization',
        organization_url: config.organization_url || config.repo_url || '',
        repositories: config.git_repositories
      }
    }
  }
  if (row.source_type === 'git' && config.branch) {
    return {
      status: 'success',
      message_code: 'git_branch_available',
      message: 'Git repository configuration is cached.',
      details: {
        branch: config.branch,
        branches: [config.branch]
      }
    }
  }
  if (row.source_type === 'feishu') {
    return {
      status: 'success',
      message_code: 'feishu_folder_available',
      message: 'Feishu configuration is cached.',
      details: {}
    }
  }
  return null
}

function handleDatasourceTypeChange(seed = null) {
  datasourcePathResult.value = null
  datasourceConnectionResult.value = null
  resetDatasourceSyncPolicy()
  if (!seed) {
    form.value.credential_uuid = ''
  }
  const sourceType = seed?.source_type || form.value.source_type
  if (isGitSourceType(sourceType)) {
    datasourceConfig.value = {
      repo_url: '',
      branch: '',
      auth_scheme: 'token'
    }
  } else if (sourceType === 'feishu') {
    datasourceConfig.value = {
      sync_mode: 'drive_folder',
      document_url: '',
      doc_ids_text: '',
      folder_url: '',
      folder_token: '',
      recursive: true,
      max_depth: 10,
      feishu_incremental: true,
      feishu_delete_missing: false
    }
  } else {
    datasourceConfig.value = {}
  }
}

function resetDatasourceSyncPolicy() {
  syncPolicyMode.value = 'interval'
  syncIntervalSeconds.value = 3600
  syncCron.value = '0 2 * * *'
  syncTimezone.value = 'Asia/Shanghai'
}

function hydrateDatasourceSyncPolicy(syncPolicy) {
  if ((syncPolicy.mode || 'interval') === 'crontab') {
    syncPolicyMode.value = 'crontab'
    syncCron.value = syncPolicy.cron || '0 2 * * *'
    syncTimezone.value = syncPolicy.timezone || 'Asia/Shanghai'
    return
  }
  syncPolicyMode.value = 'interval'
  syncIntervalSeconds.value = Number(syncPolicy.interval_seconds) || 3600
  syncCron.value = '0 2 * * *'
  syncTimezone.value = 'Asia/Shanghai'
}

async function save() {
  saving.value = true
  formError.value = ''
  try {
    if (
      form.value.source_type !== 'managed_workspace' &&
      !canSaveDatasource()
    ) {
      throw new Error(t('lensAdmin.datasourceWizard.connectionRequired'))
    }
    const uuid = form.value.uuid
    if (mode.value === 'create') {
      const payloads = await buildCreatePayloads()
      for (const payload of payloads) {
        await createDataSource(payload)
      }
    } else {
      const payload = buildPayload()
      await updateDataSource(uuid, payload)
    }
    showSuccess(t('lensAdmin.messages.saveSuccess'))
    closeDrawer()
    await load()
  } catch (error) {
    formError.value = extractErrorMessage(
      error,
      t('lensAdmin.messages.saveFailed')
    )
    showError(formError.value)
  } finally {
    saving.value = false
  }
}

function buildPayload() {
  const managedWorkspace = form.value.source_type === 'managed_workspace'
  return {
    name: form.value.name,
    source_type: normalizedSourceType(form.value.source_type),
    lensnode_uuid: form.value.lensnode_uuid,
    target_path: datasourceTargetPath(),
    config: managedWorkspace ? {} : buildDatasourceConfig(),
    sync_policy: managedWorkspace ? {} : buildDatasourceSyncPolicy(),
    status: form.value.status || 'active',
    credential_uuid: shouldUseDatasourceCredential()
      ? form.value.credential_uuid
      : null
  }
}

async function buildCreatePayloads() {
  if (!isGitOrganizationCreateMode()) {
    return [buildPayload()]
  }
  const repositories = buildSelectedGitRepositoryPayloads()
  if (!repositories.length) {
    throw new Error(t('lensAdmin.datasourceWizard.gitRepositoryRequired'))
  }
  return [
    {
      ...buildPayload(),
      config: {
        ...buildDatasourceConfig(),
        scope_type: 'organization',
        organization_url:
          datasourceConfig.value.organization_url ||
          datasourceConfig.value.repo_url,
        repositories
      }
    }
  ]
}

function buildDatasourceConfig() {
  const config = { ...datasourceConfig.value }
  if (isGitSourceType(form.value.source_type)) {
    if (Array.isArray(config.git_repositories)) {
      config.scope_type = 'organization'
      config.organization_url = config.organization_url || config.repo_url
      config.repositories = buildSelectedGitRepositoryPayloads()
      delete config.git_repositories
      delete config.branch
    } else if (!Array.isArray(config.repositories)) {
      delete config.git_repositories
      delete config.organization_url
    }
  }
  if (form.value.source_type === 'feishu') {
    config.doc_ids = String(config.doc_ids_text || '')
      .split(',')
      .map((item) => item.trim())
      .filter(Boolean)
    delete config.doc_ids_text
    if (config.sync_mode !== 'drive_folder') {
      delete config.folder_url
      delete config.folder_token
      delete config.recursive
      delete config.max_depth
      delete config.feishu_incremental
      delete config.feishu_delete_missing
    } else {
      delete config.document_url
      delete config.doc_ids
      config.feishu_incremental = config.feishu_incremental !== false
      config.feishu_delete_missing = config.feishu_delete_missing === true
    }
    delete config.app_token
    delete config.app_id
    delete config.app_secret
  }
  return config
}

function buildDatasourceSyncPolicy() {
  const conversion = {
    document: form.value.conversion_document === true,
    image: form.value.conversion_image === true,
    embedded_image:
      form.value.conversion_document === true &&
      form.value.conversion_embedded_image === true,
    max_images: Math.max(1, Number(form.value.conversion_max_images) || 100),
    max_file_size_mb: Math.max(
      1,
      Number(form.value.conversion_max_file_size_mb) || 100
    ),
    max_pages: Math.max(1, Number(form.value.conversion_max_pages) || 500),
    pdf_extract_images: form.value.conversion_pdf_extract_images !== false,
    pdf_extract_images_on_text_pages:
      form.value.conversion_pdf_extract_images_on_text_pages === true,
    pdf_render_scanned_pages:
      form.value.conversion_pdf_render_scanned_pages === true,
    pdf_max_pages: Math.max(
      1,
      Number(form.value.conversion_pdf_max_pages) || 30
    ),
    pdf_max_images_per_page: Math.max(
      1,
      Number(form.value.conversion_pdf_max_images_per_page) || 3
    ),
    pdf_render_dpi: Math.max(
      1,
      Number(form.value.conversion_pdf_render_dpi) || 144
    ),
    pdf_min_text_chars: Math.max(
      1,
      Number(form.value.conversion_pdf_min_text_chars) || 30
    ),
    pdf_min_image_area_ratio: Math.max(
      0.01,
      Math.min(
        1,
        Number(form.value.conversion_pdf_min_image_area_ratio) || 0.08
      )
    )
  }
  if (conversion.document && form.value.conversion_document_model_ref) {
    conversion.document_model_ref = form.value.conversion_document_model_ref
  }
  if (conversion.image && form.value.conversion_vision_model_ref) {
    conversion.vision_model_ref = form.value.conversion_vision_model_ref
  }
  if (syncPolicyMode.value === 'crontab') {
    return {
      mode: 'crontab',
      cron: String(syncCron.value || '').trim(),
      timezone: String(syncTimezone.value || '').trim() || 'UTC',
      conversion
    }
  }
  return {
    mode: 'interval',
    interval_seconds: Math.max(1, Number(syncIntervalSeconds.value) || 3600),
    conversion
  }
}

function datasourceTargetPath() {
  const relative = String(form.value.workspace_relative_path || '').trim()
  const workspace = datasourceWorkspaceRoot()
  return relative ? `${workspace}/${relative}` : ''
}

function workspaceRelativePath(targetPath, lensnodeUuid = null) {
  const value = String(targetPath || '').trim()
  const workspace = datasourceWorkspaceRoot(lensnodeUuid)
  if (value.startsWith(`${workspace}/`)) {
    return value.slice(workspace.length + 1)
  }
  return value
}

function datasourceWorkspaceRoot(lensnodeUuid = null) {
  const lensnode = lensnodeUuid
    ? lensnodes.value.find((node) => node.uuid === lensnodeUuid)
    : selectedDatasourceLensNode.value
  return String(lensnode?.workspace_path || '/workspace').replace(/\/+$/, '')
}

async function checkDatasourcePath() {
  if (!form.value.lensnode_uuid || !form.value.workspace_relative_path) return
  checkingDatasourcePath.value = true
  datasourcePathResult.value = null
  try {
    datasourcePathResult.value = await checkLensNodeDataSourcePath(
      form.value.lensnode_uuid,
      {
        datasource_uuid: form.value.uuid || null,
        target_path: datasourceTargetPath(),
        source_type: normalizedSourceType(form.value.source_type),
        config: buildDatasourcePathCheckConfig()
      }
    )
  } catch (error) {
    datasourcePathResult.value = {
      status: 'blocked',
      message: extractErrorMessage(error, t('lensAdmin.messages.loadFailed'))
    }
  } finally {
    checkingDatasourcePath.value = false
  }
}

function buildDatasourcePathCheckConfig() {
  const config = buildDatasourceConfig()
  if (isGitOrganizationSelectionMode()) {
    config.git_organization_parent = true
  }
  return config
}

function canSaveDatasource() {
  if (datasourceConnectionResult.value?.status !== 'success') {
    return false
  }
  if (isGitOrganizationSelectionMode()) {
    return selectedGitOrganizationRepositories().length > 0
  }
  return true
}

function resetDatasourceConnectionResult() {
  if (suppressDatasourceConnectionReset.value) {
    suppressDatasourceConnectionReset.value = false
    return
  }
  if (shouldKeepGitBranchConnectionResult()) {
    datasourceConnectionResult.value = {
      ...datasourceConnectionResult.value,
      details: {
        ...(datasourceConnectionResult.value?.details || {}),
        branch: datasourceConfig.value.branch
      }
    }
    return
  }
  datasourceConnectionResult.value = null
  datasourceConnectionBaseSignature.value = ''
}

async function testDatasourceConnection() {
  if (!form.value.lensnode_uuid) return
  testingDatasourceConnection.value = true
  datasourceConnectionResult.value = null
  try {
    const result = await testLensNodeDataSourceConnection(
      form.value.lensnode_uuid,
      {
        datasource_uuid: form.value.uuid || null,
        credential_uuid: shouldUseDatasourceCredential()
          ? form.value.credential_uuid
          : null,
        source_type: normalizedSourceType(form.value.source_type),
        config: buildDatasourceConfig()
      }
    )
    applyDatasourceConnectionResult(result)
    datasourceConnectionResult.value = result
    datasourceConnectionBaseSignature.value =
      datasourceConnectionSignature(true)
  } catch (error) {
    datasourceConnectionResult.value = {
      status: 'failed',
      message: extractErrorMessage(error, t('lensAdmin.messages.loadFailed'))
    }
  } finally {
    testingDatasourceConnection.value = false
  }
}

function shouldUseDatasourceCredential() {
  if (!form.value.credential_uuid) {
    return false
  }
  if (isGitSourceType(form.value.source_type)) {
    return true
  }
  return form.value.source_type === 'feishu'
}

async function refreshCredentials() {
  refreshingCredentials.value = true
  try {
    credentials.value = normalizeList(await listCredentials())
  } catch (error) {
    showError(extractErrorMessage(error, t('lensAdmin.messages.loadFailed')))
  } finally {
    refreshingCredentials.value = false
  }
}

async function refreshDirectories() {
  const lensnodeUuid = form.value.lensnode_uuid
  if (!lensnodeUuid) return

  const lensnode = lensnodes.value.find((item) => item.uuid === lensnodeUuid)
  if (!lensnode) return

  refreshingDirectories.value = true
  try {
    const workspacePath = lensnode.workspace_path || '/workspace'
    const result = await scanLensNodeDirs(lensnodeUuid, [workspacePath])
    const directories = refreshedDirectories(result, workspacePath)
    lensnodes.value = lensnodes.value.map((item) =>
      item.uuid === lensnodeUuid
        ? { ...item, available_dirs: directories }
        : item
    )
  } catch (error) {
    showError(extractErrorMessage(error, t('lensAdmin.messages.loadFailed')))
  } finally {
    refreshingDirectories.value = false
  }
}

function refreshedDirectories(result, workspacePath) {
  const dirs = result?.dirs ?? result
  if (Array.isArray(dirs)) return dirs
  if (!dirs || typeof dirs !== 'object') return []

  const workspaceDirs = dirs[workspacePath]
  if (Array.isArray(workspaceDirs)) return workspaceDirs

  return Object.values(dirs).flatMap((value) =>
    Array.isArray(value) ? value : []
  )
}

function applyDatasourceConnectionResult(result) {
  if (
    !isGitSourceType(form.value.source_type) ||
    result?.status !== 'success'
  ) {
    return
  }
  const repositories = result?.details?.repositories
  if (Array.isArray(repositories) && repositories.length) {
    suppressDatasourceConnectionReset.value = true
    datasourceConfig.value.git_repositories =
      mergeDiscoveredGitRepositories(repositories)
    datasourceConfig.value.organization_url =
      result?.details?.organization_url || datasourceConfig.value.repo_url
    datasourceConfig.value.branch = ''
    return
  }
  suppressDatasourceConnectionReset.value = true
  delete datasourceConfig.value.git_repositories
  delete datasourceConfig.value.organization_url
  delete datasourceConfig.value.repositories
  delete datasourceConfig.value.scope_type
  const branches = result?.details?.branches
  if (!Array.isArray(branches) || branches.length !== 1) {
    if (result?.details?.branch && !datasourceConfig.value.branch) {
      datasourceConfig.value.branch = result.details.branch
    }
    return
  }
  const branch = branches[0]
  if (datasourceConfig.value.branch !== branch) {
    suppressDatasourceConnectionReset.value = true
    datasourceConfig.value.branch = branch
  }
}

function shouldKeepGitBranchConnectionResult() {
  if (
    !isGitSourceType(form.value.source_type) ||
    datasourceConnectionResult.value?.status !== 'success'
  ) {
    return false
  }
  if (
    datasourceConnectionSignature(true) !==
    datasourceConnectionBaseSignature.value
  ) {
    return false
  }
  const branches = datasourceConnectionResult.value?.details?.branches
  return (
    Array.isArray(branches) && branches.includes(datasourceConfig.value.branch)
  )
}

function datasourceConnectionSignature(ignoreBranch = false) {
  const config = buildDatasourceConfig()
  if (ignoreBranch) {
    delete config.branch
  }
  return JSON.stringify({
    lensnode_uuid: form.value.lensnode_uuid || '',
    source_type: normalizedSourceType(form.value.source_type) || '',
    config
  })
}

function isGitOrganizationCreateMode() {
  return (
    mode.value === 'create' &&
    isGitSourceType(form.value.source_type) &&
    Array.isArray(datasourceConnectionResult.value?.details?.repositories) &&
    datasourceConnectionResult.value.details.repositories.length > 0
  )
}

function selectedGitOrganizationRepositories() {
  if (!isGitOrganizationSelectionMode()) {
    return []
  }
  return (datasourceConfig.value.git_repositories || []).filter(
    (repo) => repo.selected && repo.branch && repo.repo_url
  )
}

function isGitOrganizationSelectionMode() {
  return (
    isGitSourceType(form.value.source_type) &&
    Array.isArray(datasourceConfig.value.git_repositories)
  )
}

function buildSelectedGitRepositoryPayloads() {
  const usedSegments = new Set()
  return selectedGitOrganizationRepositories().map((repo) => {
    const repoName = repoDatasourceName(repo)
    const segment = uniquePathSegment(
      safePathSegment(repo.target_subdir || repoName),
      usedSegments
    )
    return {
      name: repoName,
      path: repo.path || repoName,
      repo_url: repo.repo_url,
      branch: repo.branch,
      target_subdir: segment,
      enabled: repo.enabled !== false
    }
  })
}

function mergeDiscoveredGitRepositories(discoveredRepositories) {
  const existingRepositories = Array.isArray(
    datasourceConfig.value.git_repositories
  )
    ? datasourceConfig.value.git_repositories
    : Array.isArray(datasourceConfig.value.repositories)
      ? datasourceConfig.value.repositories.map((repo) => ({
          ...repo,
          selected: true
        }))
      : []
  const existingByKey = new Map()
  existingRepositories.forEach((repo) => {
    repositoryMatchKeys(repo).forEach((key) => {
      existingByKey.set(key, repo)
    })
  })
  const merged = discoveredRepositories.map((repo) => {
    const existing = repositoryMatchKeys(repo)
      .map((key) => existingByKey.get(key))
      .find(Boolean)
    const branches = normalizeRepositoryBranches(repo, existing)
    return {
      ...existing,
      name:
        repo.name || existing?.name || repo.path || repoDatasourceName(repo),
      path: repo.path || existing?.path || repo.name || '',
      repo_url: repo.repo_url || existing?.repo_url || '',
      branches,
      branch: existing?.branch || repo.default_branch || branches[0] || '',
      selected: existing ? existing.selected !== false : mode.value === 'create'
    }
  })
  const discoveredKeys = new Set(
    discoveredRepositories.flatMap((repo) => repositoryMatchKeys(repo))
  )
  existingRepositories.forEach((repo) => {
    const stillVisible = repositoryMatchKeys(repo).some((key) =>
      discoveredKeys.has(key)
    )
    if (!stillVisible) {
      merged.push({
        ...repo,
        branches: normalizeRepositoryBranches(null, repo),
        selected: repo.selected !== false
      })
    }
  })
  return merged
}

function normalizeRepositoryBranches(repo, existing) {
  const branches = Array.isArray(repo?.branches) ? [...repo.branches] : []
  const branch = existing?.branch || repo?.default_branch || ''
  if (branch && !branches.includes(branch)) {
    branches.unshift(branch)
  }
  return branches
}

function repositoryMatchKeys(repo) {
  return [repo?.repo_url, repo?.path, repo?.name]
    .filter(Boolean)
    .map((value) => String(value).toLowerCase())
}

function repoDatasourceName(repo) {
  const raw = repo.name || repo.path || repo.repo_url || ''
  const cleaned = String(raw)
    .replace(/\.git$/, '')
    .split('/')
    .filter(Boolean)
  return cleaned[cleaned.length - 1] || 'repository'
}

function safePathSegment(value) {
  return String(value || 'repository')
    .trim()
    .replace(/[\\/:*?"<>|]+/g, '-')
    .replace(/^\.+$/, 'repository')
}

function uniquePathSegment(value, usedSegments) {
  const base = value || 'repository'
  let candidate = base
  let index = 2
  while (usedSegments.has(candidate)) {
    candidate = `${base}-${index}`
    index += 1
  }
  usedSegments.add(candidate)
  return candidate
}

async function remove(row) {
  try {
    await deleteDataSource(row.uuid)
    if (selectedDataSource.value?.uuid === row.uuid) {
      selectedDataSource.value = null
      showDatasourceDetailDrawer.value = false
    }
    showSuccess(t('lensAdmin.messages.deleteSuccess'))
    if (dataSources.value.length === 1 && currentPage.value > 1) {
      currentPage.value -= 1
    }
    await load()
  } catch (error) {
    showError(extractErrorMessage(error, t('lensAdmin.messages.deleteFailed')))
  }
}

async function sync(row) {
  if (!isDataSourceEnabled(row)) {
    showError(t('lensAdmin.messages.datasourceDisabled'))
    return
  }
  try {
    const result = await syncDataSource(row.uuid)
    const taskId = result?.task_id || ''
    showSuccess(
      taskId
        ? `${t('lensAdmin.messages.syncStarted')} (${taskId})`
        : t('lensAdmin.messages.syncStarted')
    )
    await load()
  } catch (error) {
    showError(extractErrorMessage(error, t('lensAdmin.messages.syncFailed')))
  }
}

async function refreshAvailability(row) {
  try {
    await refreshDataSourceAvailability(row.uuid)
    showSuccess(t('lensAdmin.messages.availabilityRefreshed'))
    await load()
  } catch (error) {
    showError(extractErrorMessage(error, t('lensAdmin.messages.saveFailed')))
  }
}

async function toggleDataSourceEnabled(row) {
  try {
    await setDataSourceEnabled(row.uuid, !isDataSourceEnabled(row))
    showSuccess(t('lensAdmin.messages.saveSuccess'))
    await load()
  } catch (error) {
    showError(extractErrorMessage(error, t('lensAdmin.messages.saveFailed')))
  }
}

async function cancelSync(row) {
  try {
    const result = await cancelDataSourceSync(row.uuid)
    const taskId = result?.task_id || row.current_sync?.task_id || ''
    showSuccess(
      taskId
        ? `${t('lensAdmin.messages.syncCancelled')} (${taskId})`
        : t('lensAdmin.messages.syncCancelled')
    )
    await load()
  } catch (error) {
    showError(
      extractErrorMessage(error, t('lensAdmin.messages.syncCancelFailed'))
    )
  }
}

onMounted(async () => {
  await load()
})

onBeforeRouteLeave(() => {
  stopDynamicRefresh()
})

onBeforeUnmount(() => {
  stopDynamicRefresh()
})
</script>

<style scoped>
.datasource-table-scroll {
  max-height: calc(100vh - 20rem);
}

.table-head {
  @apply border-b border-line px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-ink-500;
}

.table-cell {
  @apply px-4 py-4 text-sm text-ink-700;
}
</style>
