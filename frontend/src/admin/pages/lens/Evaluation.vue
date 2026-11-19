<template>
  <AdminLayout>
    <div class="admin-list-page evaluation-list-page">
      <div class="admin-page-heading">
        <div class="admin-heading-copy">
          <h1>
            {{ t('evaluation.datasets.title')
            }}<span v-if="hasLoaded" class="admin-heading-count">{{
              total
            }}</span>
          </h1>
          <p>{{ t('evaluation.datasets.subtitle') }}</p>
        </div>
        <div class="flex flex-wrap items-center gap-2">
          <BaseButton variant="ghost" size="sm" :loading="loading" @click="load"
            ><RefreshCw :size="14" aria-hidden="true" />{{
              t('common.refresh')
            }}</BaseButton
          >
          <BaseButton v-if="canWriteEvaluations" size="sm" @click="openCreate"
            ><Plus :size="14" aria-hidden="true" />{{
              t('evaluation.datasets.create')
            }}</BaseButton
          >
        </div>
      </div>
      <section class="admin-list-panel">
        <form class="admin-list-toolbar" @submit.prevent="applyFilters">
          <div
            class="admin-status-tabs evaluation-status-tabs"
            :aria-label="t('evaluation.datasets.status')"
          >
            <button
              v-for="status in ['', 'active', 'draft', 'archived']"
              :key="status"
              type="button"
              :aria-pressed="filters.status === status"
              @click="setStatus(status)"
            >
              {{ status ? statusLabel(status) : t('common.all') }}
            </button>
          </div>
          <label class="admin-toolbar-search"
            ><Search :size="14" aria-hidden="true" /><input
              v-model="filters.q"
              type="search"
              :aria-label="t('common.search')"
              :placeholder="t('evaluation.datasets.search')"
              @input="queueSearch"
          /></label>
          <BaseSelect
            v-model="filters.assistant_slug"
            class="admin-filter-chip"
            :aria-label="t('evaluation.datasets.assistant')"
            @change="applyFilters"
          >
            <option value="">{{ t('lensRuns.assistantAll') }}</option>
            <option
              v-for="assistant in assistants"
              :key="assistant.uuid"
              :value="assistant.slug"
            >
              {{ assistant.name }}
            </option>
          </BaseSelect>
          <BaseButton
            v-if="filters.q || filters.status || filters.assistant_slug"
            size="sm"
            variant="ghost"
            @click="resetFilters"
            >{{ t('lensRuns.resetFilters') }}</BaseButton
          >
        </form>
        <BaseLoading v-if="loading && !hasLoaded" />
        <div v-if="loadError" class="evaluation-list-error" role="alert">
          <span>{{ loadError }}</span
          ><BaseButton variant="outline" size="sm" @click="load">{{
            t('lensRuns.retry')
          }}</BaseButton>
        </div>
        <div
          v-else-if="hasLoaded && !rows.length"
          class="evaluation-list-empty"
          role="status"
        >
          <FlaskConical :size="32" aria-hidden="true" />
          <p>{{ t('evaluation.datasets.empty') }}</p>
          <span>{{ t('evaluation.datasets.emptyHint') }}</span>
          <BaseButton
            v-if="canWriteEvaluations"
            size="sm"
            @click="openCreate"
            >{{ t('evaluation.datasets.create') }}</BaseButton
          >
        </div>
        <div
          v-if="rows.length"
          class="admin-list-table evaluation-table"
          :aria-busy="loading"
        >
          <table>
            <colgroup>
              <col />
              <col style="width: 90px" />
              <col style="width: 70px" />
              <col style="width: 150px" />
              <col style="width: 145px" />
              <col style="width: 44px" />
            </colgroup>
            <thead>
              <tr>
                <th>{{ t('evaluation.datasets.name') }}</th>
                <th>{{ t('evaluation.datasets.status') }}</th>
                <th>{{ t('evaluation.datasets.cases') }}</th>
                <th>{{ t('evaluation.datasets.latestScore') }}</th>
                <th>{{ t('evaluation.datasets.createdAt') }}</th>
                <th>
                  <span class="sr-only">{{ t('common.actions') }}</span>
                </th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="row in rows" :key="row.uuid">
                <td class="evaluation-identity">
                  <span class="evaluation-dataset-icon"
                    ><FlaskConical :size="17" aria-hidden="true"
                  /></span>
                  <div class="min-w-0">
                    <router-link
                      :to="`/management/lens/evaluation/datasets/${row.uuid}`"
                      class="evaluation-dataset-name"
                      >{{ row.name }}</router-link
                    ><span class="evaluation-dataset-meta"
                      >{{ row.assistant_name }} · v{{ row.version }}</span
                    >
                  </div>
                </td>
                <td class="evaluation-row-status">
                  <span class="eval-status" :class="statusClass(row.status)">{{
                    statusLabel(row.status)
                  }}</span>
                </td>
                <td class="evaluation-case-count">{{ row.case_count }}</td>
                <td class="evaluation-latest-score">
                  <router-link
                    v-if="scoreValue(row) !== null"
                    :to="`/management/lens/evaluation/runs/${row.latest_run.uuid}`"
                    class="evaluation-score"
                    :data-tone="
                      scoreValue(row) < 0.7
                        ? 'bad'
                        : scoreValue(row) < 0.85
                          ? 'warn'
                          : 'ok'
                    "
                    :aria-label="`${t('evaluation.datasets.latestScore')}: ${scoreValue(row).toFixed(2)}`"
                    ><span class="evaluation-score-track" aria-hidden="true"
                      ><span
                        :style="{
                          width: `${Math.max(0, Math.min(1, scoreValue(row))) * 100}%`
                        }" /></span
                    ><span>{{ scoreValue(row).toFixed(2) }}</span></router-link
                  ><span v-else class="text-ink-400">—</span>
                </td>
                <td class="evaluation-created-at">
                  {{ formatDate(row.created_at) }}
                </td>
                <td class="evaluation-row-menu">
                  <RowActionMenu
                    :actions="rowActions(row)"
                    :label="`${t('common.moreActions')}: ${row.name}`"
                    @select="handleRowAction($event, row)"
                  />
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
      </section>
    </div>

    <BaseDrawer
      :show="canWriteEvaluations && createOpen"
      :title="t('evaluation.datasets.create')"
      width="3xl"
      panel-class="evaluation-create-drawer"
      :close-on-backdrop="!saving"
      @close="closeCreate"
    >
      <div class="flex flex-col gap-4">
        <BaseInput
          v-model="form.name"
          :label="t('evaluation.datasets.name')"
          :placeholder="t('evaluation.datasets.namePlaceholder')"
        />
        <BaseSelect
          v-model="form.assistant"
          :label="t('evaluation.datasets.assistant')"
        >
          <option value="">
            {{ t('evaluation.datasets.assistantPlaceholder') }}
          </option>
          <option
            v-for="assistant in assistants"
            :key="assistant.uuid"
            :value="assistant.uuid"
          >
            {{ assistant.name }}
          </option>
        </BaseSelect>
        <BaseSelect
          v-model="form.status"
          :label="t('evaluation.datasets.status')"
        >
          <option value="draft">{{ t('evaluation.status.draft') }}</option>
          <option value="active">{{ t('evaluation.status.active') }}</option>
          <option value="archived">
            {{ t('evaluation.status.archived') }}
          </option>
        </BaseSelect>
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
            @click="closeCreate"
          >
            {{ t('common.cancel') }}
          </BaseButton>
          <BaseButton size="sm" :loading="saving" @click="submitCreate">
            {{ t('common.create') }}
          </BaseButton>
        </div>
      </template>
    </BaseDrawer>

    <ConfirmDeleteModal
      :show="canWriteEvaluations && Boolean(deleteTarget)"
      :title="t('evaluation.datasets.deleteTitle')"
      :name="deleteTarget?.name"
      :message="t('evaluation.datasets.deleteMessage')"
      :confirm-text="t('common.delete')"
      :loading="deleting"
      @confirm="confirmDelete"
      @cancel="deleteTarget = null"
    />
  </AdminLayout>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { Eye, FlaskConical, Plus, RefreshCw, Search, Trash2 } from '@lucide/vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'

import AdminLayout from '@/admin/layout/AdminLayout.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseInput from '@/components/ui/BaseInput.vue'
import BaseLoading from '@/components/ui/BaseLoading.vue'
import BaseDrawer from '@/components/ui/BaseDrawer.vue'
import BaseSelect from '@/components/ui/BaseSelect.vue'
import ConfirmDeleteModal from '@/components/ui/ConfirmDeleteModal.vue'
import PaginationBar from '@/components/ui/PaginationBar.vue'
import RowActionMenu from '@/components/ui/RowActionMenu.vue'
import {
  createEvaluationDataset,
  deleteEvaluationDataset,
  listEvaluationDatasets
} from '@/api/evaluation'
import { listAssistants } from '@/api/lens'
import { useToast } from '@/composables/useToast'
import { useUserStore } from '@/store/user'
import { formatDate } from '@/utils/formatting'

const { t } = useI18n()
const { showSuccess, showError } = useToast()
const userStore = useUserStore()
const router = useRouter()
const lifecycleVersion = ref(0)
const authorityKey = computed(() => JSON.stringify([
  lifecycleVersion.value,
  userStore.userInfo?.id, userStore.userInfo?.tenant?.uuid, userStore.userInfo?.tenant?.status,
  userStore.userInfo?.tenant_role, userStore.userInfo?.platform_role,
  userStore.delegation?.delegation_id, userStore.delegation?.mode
]))
const canWriteEvaluations = computed(
  () => userStore.delegation?.mode !== 'readonly'
)

const rows = ref([])
const hasLoaded = ref(false)
const loadError = ref('')
const filters = reactive({ q: '', status: '', assistant_slug: '' })
let requestVersion = 0
let searchTimer
function scoreValue(row) {
  const score = row.latest_run?.aggregate_score
  if (score === null || score === undefined || score === '') return null
  const value = Number(score)
  return Number.isFinite(value) ? value : null
}
function rowActions() {
  return [
    { key: 'open', label: t('evaluation.datasets.open'), icon: Eye },
    ...(canWriteEvaluations.value
      ? [
          {
            key: 'delete',
            label: t('common.delete'),
            icon: Trash2,
            variant: 'danger'
          }
        ]
      : [])
  ]
}
function handleRowAction(action, row) {
  if (action.key === 'open')
    router.push(`/management/lens/evaluation/datasets/${row.uuid}`)
  if (action.key === 'delete') askDelete(row)
}
function applyFilters() {
  clearTimeout(searchTimer)
  page.value = 1
  load()
}
function queueSearch() {
  ++requestVersion
  clearTimeout(searchTimer)
  searchTimer = setTimeout(applyFilters, 300)
}
function setStatus(status) {
  filters.status = status
  applyFilters()
}
function resetFilters() {
  Object.assign(filters, { q: '', status: '', assistant_slug: '' })
  applyFilters()
}
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)
const loading = ref(true)
const createOpen = ref(false)
const saving = ref(false)
const formError = ref('')
const assistants = ref([])
const deleteTarget = ref(null)
const deleting = ref(false)

const form = reactive({
  name: '',
  assistant: '',
  status: 'active'
})

const STATUS_CLASSES = {
  draft: 'bg-surface-sunken text-ink-600',
  active: 'bg-success-50 text-success-700',
  archived: 'bg-surface-sunken text-ink-600'
}

function errorMessage(error) {
  return (
    error?.response?.data?.data?.detail ||
    error?.response?.data?.message ||
    error?.message ||
    String(error)
  )
}

function statusClass(status) {
  return STATUS_CLASSES[status] || STATUS_CLASSES.draft
}

function statusLabel(status) {
  return t(`evaluation.status.${status}`)
}

async function load() {
  const version = ++requestVersion
  loading.value = true
  loadError.value = ''
  try {
    const data = await listEvaluationDatasets({
      page: page.value,
      page_size: pageSize.value,
      q: filters.q.trim() || undefined,
      status: filters.status || undefined,
      assistant_slug: filters.assistant_slug || undefined
    })
    if (version !== requestVersion) return
    hasLoaded.value = true
    rows.value = Array.isArray(data) ? data : data?.results || []
    total.value = Array.isArray(data)
      ? data.length
      : Number(data?.count ?? rows.value.length)
  } catch (error) {
    if (version !== requestVersion) return
    loadError.value = errorMessage(error)
  } finally {
    if (version === requestVersion) loading.value = false
  }
}

async function loadAssistants() {
  const scope = authorityKey.value
  try {
    const items = await listAssistants({ managed: true })
    if (scope !== authorityKey.value) return
    assistants.value = items.filter((assistant) => !assistant.is_system)
  } catch {
    if (scope !== authorityKey.value) return
    assistants.value = []
  }
}

function goPrev() {
  if (page.value <= 1) return
  page.value -= 1
  load()
}

function goNext() {
  if (page.value * pageSize.value >= total.value) return
  page.value += 1
  load()
}

function changePageSize(value) {
  pageSize.value = Number(value) || 20
  page.value = 1
  load()
}

function openCreate() {
  if (!canWriteEvaluations.value) return
  form.name = ''
  form.assistant = ''
  form.status = 'active'
  formError.value = ''
  createOpen.value = true
}

function closeCreate() {
  if (saving.value) return
  createOpen.value = false
}

async function submitCreate() {
  if (!canWriteEvaluations.value || saving.value) return
  const scope = authorityKey.value
  formError.value = ''
  if (!form.name.trim() || !form.assistant) {
    formError.value = t('evaluation.datasets.requiredFields')
    return
  }
  saving.value = true
  try {
    await createEvaluationDataset({
      name: form.name.trim(),
      assistant: form.assistant,
      status: form.status
    })
    if (scope !== authorityKey.value) return
    createOpen.value = false
    showSuccess(t('evaluation.datasets.created'))
    page.value = 1
    await load()
  } catch (error) {
    if (scope !== authorityKey.value) return
    formError.value = errorMessage(error)
  } finally {
    saving.value = false
  }
}

function askDelete(row) {
  if (!canWriteEvaluations.value) return
  deleteTarget.value = row
}

async function confirmDelete() {
  if (!canWriteEvaluations.value) return
  if (!deleteTarget.value || deleting.value) return
  const scope = authorityKey.value
  const target = deleteTarget.value.uuid
  deleting.value = true
  try {
    await deleteEvaluationDataset(target)
    if (scope !== authorityKey.value) return
    showSuccess(t('evaluation.datasets.deleted'))
    deleteTarget.value = null
    if (rows.value.length === 1 && page.value > 1) page.value -= 1
    await load()
  } catch (error) {
    if (scope !== authorityKey.value) return
    showError(errorMessage(error))
  } finally {
    deleting.value = false
  }
}

watch(
  authorityKey,
  () => {
    ++requestVersion
    clearTimeout(searchTimer)
    rows.value = []
    total.value = 0
    hasLoaded.value = false
    createOpen.value = false
    deleteTarget.value = null
    assistants.value = []
    load()
    loadAssistants()
  }
)
onBeforeUnmount(() => {
  ++requestVersion
  clearTimeout(searchTimer)
  ++lifecycleVersion.value
})
onMounted(() => {
  load()
  loadAssistants()
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
