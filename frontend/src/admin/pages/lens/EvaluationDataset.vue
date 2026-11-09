<template>
  <AdminLayout>
    <div class="flex max-w-full flex-col gap-4 py-4">
      <section class="admin-data-panel overflow-hidden">
        <div
          class="flex flex-col gap-4 border-b border-line px-5 py-4 lg:flex-row lg:items-start lg:justify-between"
        >
          <div class="min-w-0">
            <router-link
              to="/management/lens/evaluation"
              class="text-xs font-medium text-ink-500 hover:text-primary-600"
            >
              ← {{ t('evaluation.datasets.backToList') }}
            </router-link>
            <h1 class="admin-page-title mt-1">
              {{ dataset?.name || t('evaluation.datasets.detailTitle') }}
            </h1>
            <p class="admin-page-subtitle">
              <span v-if="dataset">
                {{ dataset.assistant_name }} · v{{ dataset.version }} ·
                {{ t(`evaluation.status.${dataset.status}`) }}
              </span>
            </p>
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
            <BaseButton variant="outline" size="sm" @click="openRevisions">
              {{ t('evaluation.revisions.title') }}
            </BaseButton>
            <BaseButton
              size="sm"
              :loading="launching"
              :disabled="!caseTotal"
              @click="launchRun"
            >
              {{ t('evaluation.runs.launch') }}
            </BaseButton>
          </div>
        </div>

        <div
          class="flex flex-wrap items-center justify-between gap-3 border-b border-line px-5"
        >
          <div class="flex min-w-0 items-center gap-6 overflow-x-auto">
            <button
              v-for="tab in tabs"
              :key="tab.key"
              type="button"
              class="qa-tab"
              :class="activeTab === tab.key ? 'qa-tab-active' : ''"
              @click="setTab(tab.key)"
            >
              {{ tab.label }}
            </button>
          </div>
          <div
            v-if="activeTab === 'cases'"
            class="flex items-center gap-2 py-2"
          >
            <BaseButton variant="outline" size="sm" @click="openImport">
              {{ t('evaluation.cases.import') }}
            </BaseButton>
            <BaseButton size="sm" @click="openCaseForm()">
              {{ t('evaluation.cases.add') }}
            </BaseButton>
          </div>
        </div>

        <div class="px-5 py-4">
          <BaseLoading v-if="loading && !rows.length" />

          <div
            v-else-if="!rows.length"
            class="rounded-lg border border-line bg-surface-sunken py-16 text-center"
          >
            <p class="text-sm font-medium text-ink-600">
              {{
                activeTab === 'cases'
                  ? t('evaluation.cases.empty')
                  : t('evaluation.runs.empty')
              }}
            </p>
            <p class="mt-1 text-xs text-ink-500">
              {{
                activeTab === 'cases'
                  ? t('evaluation.cases.emptyHint')
                  : t('evaluation.runs.emptyHint')
              }}
            </p>
          </div>

          <div
            v-else-if="activeTab === 'cases'"
            class="overflow-x-auto rounded-lg border border-line"
          >
            <table class="w-full table-fixed divide-y divide-line">
              <colgroup>
                <col style="width: 40%" />
                <col style="width: 22%" />
                <col style="width: 14%" />
                <col style="width: 12rem" />
              </colgroup>
              <thead class="bg-surface-sunken">
                <tr>
                  <th class="table-head">{{ t('evaluation.cases.input') }}</th>
                  <th class="table-head">
                    {{ t('evaluation.cases.expectedSources') }}
                  </th>
                  <th class="table-head">{{ t('evaluation.cases.tags') }}</th>
                  <th class="table-head">{{ t('common.actions') }}</th>
                </tr>
              </thead>
              <tbody class="divide-y divide-line bg-surface">
                <tr
                  v-for="row in rows"
                  :key="row.uuid"
                  class="hover:bg-surface-hover"
                >
                  <td class="table-cell">
                    <p class="line-clamp-2 text-ink-900">{{ row.input }}</p>
                    <p
                      v-if="row.expected_answer"
                      class="mt-1 line-clamp-1 text-xs text-ink-500"
                    >
                      {{ t('evaluation.cases.expectedAnswer') }}:
                      {{ row.expected_answer }}
                    </p>
                  </td>
                  <td class="table-cell text-xs">
                    <span v-if="row.expected_sources?.length">
                      {{ row.expected_sources.join(', ') }}
                    </span>
                    <span v-else class="text-ink-400">—</span>
                  </td>
                  <td class="table-cell text-xs">
                    <span v-if="row.tags?.length">{{
                      row.tags.join(', ')
                    }}</span>
                    <span v-else class="text-ink-400">—</span>
                  </td>
                  <td class="table-cell">
                    <div class="flex items-center gap-3">
                      <button
                        type="button"
                        class="text-sm font-medium text-primary-600 hover:text-primary-700"
                        @click="openCaseForm(row)"
                      >
                        {{ t('common.edit') }}
                      </button>
                      <button
                        type="button"
                        class="text-sm font-medium text-ink-500 hover:text-error-600"
                        @click="deleteCaseTarget = row"
                      >
                        {{ t('common.delete') }}
                      </button>
                    </div>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>

          <div v-else class="overflow-x-auto rounded-lg border border-line">
            <table class="w-full table-fixed divide-y divide-line">
              <colgroup>
                <col style="width: 11rem" />
                <col style="width: 10rem" />
                <col style="width: 14rem" />
                <col style="width: 8rem" />
                <col style="width: 11rem" />
                <col style="width: 9rem" />
              </colgroup>
              <thead class="bg-surface-sunken">
                <tr>
                  <th class="table-head">{{ t('evaluation.runs.status') }}</th>
                  <th class="table-head">
                    {{ t('evaluation.runs.progress') }}
                  </th>
                  <th class="table-head">
                    {{ t('evaluation.runs.verdicts') }}
                  </th>
                  <th class="table-head">{{ t('evaluation.runs.score') }}</th>
                  <th class="table-head">
                    {{ t('evaluation.runs.createdAt') }}
                  </th>
                  <th class="table-head">{{ t('common.actions') }}</th>
                </tr>
              </thead>
              <tbody class="divide-y divide-line bg-surface">
                <tr
                  v-for="row in rows"
                  :key="row.uuid"
                  class="hover:bg-surface-hover"
                >
                  <td class="table-cell">
                    <span
                      class="eval-status"
                      :class="runStatusClass(row.status)"
                    >
                      {{ t(`evaluation.runStatus.${row.status}`) }}
                    </span>
                  </td>
                  <td class="table-cell">
                    <div class="flex items-center gap-2">
                      <div
                        class="h-1.5 w-16 overflow-hidden rounded-full bg-surface-sunken"
                      >
                        <div
                          class="h-full rounded-full bg-primary-500"
                          :style="{ width: `${row.progress}%` }"
                        />
                      </div>
                      <span class="text-xs tabular-nums text-ink-500"
                        >{{ row.progress }}%</span
                      >
                    </div>
                  </td>
                  <td class="table-cell text-xs tabular-nums">
                    <span class="text-success-700">{{ row.passed_cases }}</span>
                    /
                    <span class="text-error-600">{{ row.failed_cases }}</span>
                    / {{ row.total_cases }}
                  </td>
                  <td class="table-cell tabular-nums">
                    {{ formatScore(row.aggregate_score) }}
                  </td>
                  <td class="table-cell whitespace-nowrap">
                    {{ formatDate(row.created_at) }}
                  </td>
                  <td class="table-cell">
                    <router-link
                      :to="`/management/lens/evaluation/runs/${row.uuid}`"
                      class="text-sm font-medium text-primary-600 hover:text-primary-700"
                    >
                      {{ t('evaluation.runs.open') }}
                    </router-link>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>

          <PaginationBar
            :current-page="page"
            :page-size="pageSize"
            :total="total"
            @prev="goPrev"
            @next="goNext"
            @update:page-size="changePageSize"
          />
        </div>
      </section>
    </div>

    <BaseModal :show="revisionsOpen" :title="t('evaluation.revisions.title')" max-width="xl" @close="revisionsOpen = false">
      <BaseLoading v-if="revisionsLoading" />
      <div v-else class="space-y-3">
        <p v-if="!revisions.length" class="text-sm text-ink-500">{{ t('evaluation.revisions.empty') }}</p>
        <div v-for="item in revisions" :key="item.revision" class="flex items-center justify-between gap-4 border-b border-line py-2">
          <span class="text-sm">#{{ item.revision }} · {{ formatDate(item.created_at) }} · {{ item.case_count }} {{ t('evaluation.cases.tab') }}</span>
          <BaseButton size="sm" variant="outline" @click="loadRevision(item.revision)">{{ t('common.viewDetails') }}</BaseButton>
        </div>
        <div v-if="selectedRevision" class="space-y-2">
          <h3 class="font-semibold">{{ t('evaluation.revisions.snapshot') }} #{{ selectedRevision.revision }}</h3>
          <div v-for="item in selectedRevision.cases" :key="item.uuid" class="rounded-lg bg-surface-sunken p-3 text-sm">
            <div class="font-medium whitespace-pre-wrap">{{ item.input }}</div>
            <div class="mt-1 whitespace-pre-wrap text-ink-600">{{ item.expected_answer || item.config?.baseline?.answer || '—' }}</div>
            <details v-if="item.config?.baseline?.metadata" class="mt-2 text-xs text-ink-600">
              <summary>{{ t('evaluation.revisions.metadata') }}</summary>
              <pre class="mt-2 max-h-48 overflow-auto whitespace-pre-wrap">{{ JSON.stringify(item.config.baseline.metadata, null, 2) }}</pre>
            </details>
          </div>
        </div>
      </div>
    </BaseModal>

    <BaseModal
      :show="caseFormOpen"
      :title="
        editingCase ? t('evaluation.cases.edit') : t('evaluation.cases.add')
      "
      max-width="xl"
      :close-on-backdrop="!saving"
      @close="closeCaseForm"
    >
      <div class="flex flex-col gap-4">
        <label class="block text-sm font-medium text-theme">
          {{ t('evaluation.cases.input') }} *
          <textarea
            v-model="caseForm.input"
            rows="3"
            class="mt-1 block w-full rounded-lg border border-line bg-surface px-3 py-2 text-sm text-theme shadow-sm focus:border-primary-500 focus:outline-none focus:ring-2 focus:ring-primary-500/20"
            :placeholder="t('evaluation.cases.inputPlaceholder')"
          />
        </label>
        <label class="block text-sm font-medium text-theme">
          {{ t('evaluation.cases.expectedAnswer') }}
          <textarea
            v-model="caseForm.expected_answer"
            rows="2"
            class="mt-1 block w-full rounded-lg border border-line bg-surface px-3 py-2 text-sm text-theme shadow-sm focus:border-primary-500 focus:outline-none focus:ring-2 focus:ring-primary-500/20"
          />
        </label>
        <div class="grid gap-4 md:grid-cols-2">
          <BaseInput
            v-model="caseForm.expected_sources"
            :label="t('evaluation.cases.expectedSources')"
            :help="t('evaluation.cases.listHint')"
          />
          <BaseInput
            v-model="caseForm.expected_facts"
            :label="t('evaluation.cases.expectedFacts')"
            :help="t('evaluation.cases.listHint')"
          />
          <BaseInput
            v-model="caseForm.tags"
            :label="t('evaluation.cases.tags')"
          />
          <BaseInput
            v-model="caseForm.config"
            :label="t('evaluation.cases.config')"
            :help="t('evaluation.cases.configHint')"
          />
        </div>
        <p v-if="formError" class="text-sm text-error-600" role="alert">
          {{ formError }}
        </p>
      </div>
      <template #footer>
        <div class="flex justify-end gap-2">
          <BaseButton
            variant="outline"
            size="sm"
            :disabled="saving"
            @click="closeCaseForm"
          >
            {{ t('common.cancel') }}
          </BaseButton>
          <BaseButton size="sm" :loading="saving" @click="submitCaseForm">
            {{ t('common.save') }}
          </BaseButton>
        </div>
      </template>
    </BaseModal>

    <BaseModal
      :show="importOpen"
      :title="t('evaluation.cases.import')"
      max-width="xl"
      :close-on-backdrop="!importing"
      @close="closeImport"
    >
      <div class="flex flex-col gap-4">
        <p class="text-sm text-ink-600">
          {{ t('evaluation.cases.importHint') }}
        </p>
        <input
          type="file"
          accept=".jsonl,.csv,text/plain"
          class="block w-full text-sm text-ink-600"
          @change="onImportFile"
        />
        <BaseSelect v-model="importFormat" aria-label="Import format">
          <option value="jsonl">JSONL</option>
          <option value="csv">CSV</option>
        </BaseSelect>
        <textarea
          v-model="importText"
          rows="10"
          class="block w-full rounded-lg border border-line bg-surface px-3 py-2 font-mono text-xs text-theme shadow-sm focus:border-primary-500 focus:outline-none focus:ring-2 focus:ring-primary-500/20"
          :placeholder="t('evaluation.cases.importPlaceholder')"
        />
        <div
          v-if="importErrors.length"
          class="rounded-lg bg-error-50 p-3"
          role="alert"
        >
          <p class="text-sm font-medium text-error-700">
            {{ t('evaluation.cases.importFailed') }}
          </p>
          <ul class="mt-2 space-y-1 text-xs text-error-700">
            <li
              v-for="item in importErrors.slice(0, 20)"
              :key="`${item.line}-${item.error}`"
            >
              {{ t('evaluation.cases.importLine', { line: item.line }) }}:
              {{ item.error }}
            </li>
          </ul>
        </div>
      </div>
      <template #footer>
        <div class="flex justify-end gap-2">
          <BaseButton
            variant="outline"
            size="sm"
            :disabled="importing"
            @click="closeImport"
          >
            {{ t('common.cancel') }}
          </BaseButton>
          <BaseButton size="sm" :loading="importing" @click="submitImport">
            {{ t('evaluation.cases.import') }}
          </BaseButton>
        </div>
      </template>
    </BaseModal>

    <ConfirmDeleteModal
      :show="Boolean(deleteCaseTarget)"
      :title="t('evaluation.cases.deleteTitle')"
      :name="deleteCaseTarget?.input"
      :message="t('evaluation.cases.deleteMessage')"
      :confirm-text="t('common.delete')"
      :loading="deletingCase"
      @confirm="confirmDeleteCase"
      @cancel="deleteCaseTarget = null"
    />
  </AdminLayout>
</template>

<script setup>
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'

import AdminLayout from '@/admin/layout/AdminLayout.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseInput from '@/components/ui/BaseInput.vue'
import BaseLoading from '@/components/ui/BaseLoading.vue'
import BaseModal from '@/components/ui/BaseModal.vue'
import BaseSelect from '@/components/ui/BaseSelect.vue'
import ConfirmDeleteModal from '@/components/ui/ConfirmDeleteModal.vue'
import PaginationBar from '@/components/ui/PaginationBar.vue'
import {
  createEvaluationCase,
  deleteEvaluationCase,
  getEvaluationDataset,
  getEvaluationRevisions,
  importEvaluationCases,
  listDatasetRuns,
  listEvaluationCases,
  startEvaluationRun,
  updateEvaluationCase
} from '@/api/evaluation'
import { useToast } from '@/composables/useToast'
import { formatDate } from '@/utils/formatting'

const route = useRoute()
const router = useRouter()
const { t } = useI18n()
const { showSuccess, showError } = useToast()

const datasetUuid = computed(() => route.params.uuid)
const dataset = ref(null)
const revisionsOpen = ref(false)
const revisionsLoading = ref(false)
const revisions = ref([])
const selectedRevision = ref(null)

async function openRevisions() {
  revisionsOpen.value = true
  revisionsLoading.value = true
  selectedRevision.value = null
  try {
    revisions.value = await getEvaluationRevisions(datasetUuid.value)
  } catch (error) {
    showError(errorMessage(error))
  } finally {
    revisionsLoading.value = false
  }
}

async function loadRevision(revision) {
  try {
    selectedRevision.value = await getEvaluationRevisions(datasetUuid.value, revision)
  } catch (error) {
    showError(errorMessage(error))
  }
}
const activeTab = ref('cases')
const rows = ref([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)
const loading = ref(true)
const launching = ref(false)
const caseTotal = ref(0)

const caseFormOpen = ref(false)
const editingCase = ref(null)
const saving = ref(false)
const formError = ref('')
const deleteCaseTarget = ref(null)
const deletingCase = ref(false)

const importOpen = ref(false)
const importText = ref('')
const importFormat = ref('jsonl')
const importFile = ref(null)
const importErrors = ref([])
const importing = ref(false)

const caseForm = reactive({
  input: '',
  expected_answer: '',
  expected_sources: '',
  expected_facts: '',
  tags: '',
  config: ''
})

const tabs = computed(() => [
  { key: 'cases', label: t('evaluation.cases.tab') },
  { key: 'runs', label: t('evaluation.runs.tab') }
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

function errorMessage(error) {
  return (
    error?.response?.data?.data?.detail ||
    error?.response?.data?.message ||
    error?.message ||
    String(error)
  )
}

function formatScore(value) {
  if (value === null || value === undefined || value === '') return '—'
  return Number(value).toFixed(3)
}

function parseList(value) {
  return String(value || '')
    .split('|')
    .map((item) => item.trim())
    .filter(Boolean)
}

function joinList(value) {
  return Array.isArray(value) ? value.join('|') : ''
}

async function loadDataset() {
  try {
    dataset.value = await getEvaluationDataset(datasetUuid.value)
    caseTotal.value = dataset.value?.case_count ?? 0
  } catch (error) {
    showError(errorMessage(error))
  }
}

async function loadRows() {
  loading.value = true
  try {
    const fetcher =
      activeTab.value === 'cases'
        ? listEvaluationCases(datasetUuid.value, {
            page: page.value,
            page_size: pageSize.value
          })
        : listDatasetRuns(datasetUuid.value, {
            page: page.value,
            page_size: pageSize.value
          })
    const data = await fetcher
    rows.value = Array.isArray(data) ? data : data?.results || []
    total.value = Array.isArray(data)
      ? data.length
      : Number(data?.count ?? rows.value.length)
  } catch (error) {
    rows.value = []
    total.value = 0
    showError(errorMessage(error))
  } finally {
    loading.value = false
  }
}

async function reload() {
  await Promise.all([loadDataset(), loadRows()])
}

function setTab(key) {
  activeTab.value = key
  page.value = 1
  rows.value = []
  loadRows()
}

function goPrev() {
  if (page.value <= 1) return
  page.value -= 1
  loadRows()
}

function goNext() {
  if (page.value * pageSize.value >= total.value) return
  page.value += 1
  loadRows()
}

function changePageSize(value) {
  pageSize.value = Number(value) || 20
  page.value = 1
  loadRows()
}

function openCaseForm(row = null) {
  editingCase.value = row
  formError.value = ''
  caseForm.input = row?.input || ''
  caseForm.expected_answer = row?.expected_answer || ''
  caseForm.expected_sources = joinList(row?.expected_sources)
  caseForm.expected_facts = joinList(row?.expected_facts)
  caseForm.tags = joinList(row?.tags)
  caseForm.config =
    row?.config && Object.keys(row.config).length
      ? JSON.stringify(row.config)
      : ''
  caseFormOpen.value = true
}

function closeCaseForm() {
  if (saving.value) return
  caseFormOpen.value = false
}

async function submitCaseForm() {
  formError.value = ''
  if (!caseForm.input.trim()) {
    formError.value = t('evaluation.cases.inputRequired')
    return
  }
  let config = {}
  if (caseForm.config.trim()) {
    try {
      config = JSON.parse(caseForm.config)
    } catch {
      formError.value = t('evaluation.cases.configInvalid')
      return
    }
  }
  const payload = {
    input: caseForm.input.trim(),
    expected_answer: caseForm.expected_answer,
    expected_sources: parseList(caseForm.expected_sources),
    expected_facts: parseList(caseForm.expected_facts),
    tags: parseList(caseForm.tags),
    config
  }
  saving.value = true
  try {
    if (editingCase.value) {
      await updateEvaluationCase(editingCase.value.uuid, payload)
      showSuccess(t('evaluation.cases.updated'))
    } else {
      await createEvaluationCase(datasetUuid.value, payload)
      showSuccess(t('evaluation.cases.created'))
    }
    caseFormOpen.value = false
    await reload()
  } catch (error) {
    formError.value = errorMessage(error)
  } finally {
    saving.value = false
  }
}

async function confirmDeleteCase() {
  if (!deleteCaseTarget.value) return
  deletingCase.value = true
  try {
    await deleteEvaluationCase(deleteCaseTarget.value.uuid)
    showSuccess(t('evaluation.cases.deleted'))
    deleteCaseTarget.value = null
    await reload()
  } catch (error) {
    showError(errorMessage(error))
  } finally {
    deletingCase.value = false
  }
}

function openImport() {
  importText.value = ''
  importFormat.value = 'jsonl'
  importFile.value = null
  importErrors.value = []
  importOpen.value = true
}

function closeImport() {
  if (importing.value) return
  importOpen.value = false
}

function onImportFile(event) {
  const file = event.target.files?.[0]
  importFile.value = file || null
  if (!file) return
  if (/\.csv$/i.test(file.name)) importFormat.value = 'csv'
  if (/\.jsonl$/i.test(file.name)) importFormat.value = 'jsonl'
  const reader = new FileReader()
  reader.onload = () => {
    importText.value = String(reader.result || '')
  }
  reader.readAsText(file)
}

async function submitImport() {
  importErrors.value = []
  if (!importText.value.trim()) {
    importErrors.value = [{ line: 0, error: t('evaluation.cases.importEmpty') }]
    return
  }
  importing.value = true
  try {
    const result = await importEvaluationCases(datasetUuid.value, {
      text: importText.value,
      filename: `import.${importFormat.value}`
    })
    showSuccess(
      t('evaluation.cases.imported', {
        created: result?.created ?? 0,
        duplicates: result?.duplicates?.length ?? 0
      })
    )
    importOpen.value = false
    await reload()
  } catch (error) {
    importErrors.value = error?.response?.data?.data?.errors || [
      { line: 0, error: errorMessage(error) }
    ]
  } finally {
    importing.value = false
  }
}

async function launchRun() {
  launching.value = true
  try {
    const run = await startEvaluationRun(datasetUuid.value, {})
    showSuccess(t('evaluation.runs.started'))
    router.push(`/management/lens/evaluation/runs/${run.uuid}`)
  } catch (error) {
    showError(errorMessage(error))
  } finally {
    launching.value = false
  }
}

watch(datasetUuid, () => {
  page.value = 1
  activeTab.value = 'cases'
  reload()
})

onMounted(reload)
</script>

<style scoped>
.table-head {
  @apply border-b border-line px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-ink-500;
}

.table-cell {
  @apply px-4 py-3 align-top text-sm text-ink-700;
}

.qa-tab {
  @apply shrink-0 whitespace-nowrap border-b-2 border-transparent py-3 text-sm font-medium text-ink-500 transition-colors;
}

.qa-tab:hover {
  @apply text-ink-700;
}

.qa-tab-active {
  @apply border-primary-500 text-primary-600;
}

.eval-status {
  @apply inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium;
}
</style>
