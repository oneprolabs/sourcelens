<template>
  <AdminLayout>
    <div class="flex max-w-full flex-col gap-4 py-4">
      <section class="admin-data-panel overflow-hidden">
        <div
          class="flex flex-col gap-4 border-b border-line px-5 py-4 lg:flex-row lg:items-start lg:justify-between"
        >
          <div class="min-w-0">
            <h1 class="admin-page-title">
              {{ t('evaluation.datasets.title') }}
            </h1>
            <p class="admin-page-subtitle">
              {{ t('evaluation.datasets.subtitle') }}
            </p>
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
            <BaseButton size="sm" @click="openCreate">
              {{ t('evaluation.datasets.create') }}
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
              {{ t('evaluation.datasets.empty') }}
            </p>
            <p class="mt-1 text-xs text-ink-500">
              {{ t('evaluation.datasets.emptyHint') }}
            </p>
          </div>

          <div v-else class="overflow-x-auto rounded-lg border border-line">
            <table class="w-full table-fixed divide-y divide-line">
              <colgroup>
                <col style="width: 26%" />
                <col style="width: 22%" />
                <col style="width: 8rem" />
                <col style="width: 8rem" />
                <col style="width: 6rem" />
                <col style="width: 11rem" />
                <col style="width: 12rem" />
              </colgroup>
              <thead class="bg-surface-sunken">
                <tr>
                  <th class="table-head">
                    {{ t('evaluation.datasets.name') }}
                  </th>
                  <th class="table-head">
                    {{ t('evaluation.datasets.assistant') }}
                  </th>
                  <th class="table-head">
                    {{ t('evaluation.datasets.version') }}
                  </th>
                  <th class="table-head">
                    {{ t('evaluation.datasets.status') }}
                  </th>
                  <th class="table-head">
                    {{ t('evaluation.datasets.cases') }}
                  </th>
                  <th class="table-head">
                    {{ t('evaluation.datasets.createdAt') }}
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
                    <router-link
                      :to="`/management/lens/evaluation/datasets/${row.uuid}`"
                      class="font-medium text-primary-600 hover:text-primary-700"
                    >
                      {{ row.name }}
                    </router-link>
                  </td>
                  <td class="table-cell truncate" :title="row.assistant_name">
                    {{ row.assistant_name }}
                  </td>
                  <td class="table-cell tabular-nums">v{{ row.version }}</td>
                  <td class="table-cell">
                    <span class="eval-status" :class="statusClass(row.status)">
                      {{ statusLabel(row.status) }}
                    </span>
                  </td>
                  <td class="table-cell tabular-nums">{{ row.case_count }}</td>
                  <td class="table-cell whitespace-nowrap">
                    {{ formatDate(row.created_at) }}
                  </td>
                  <td class="table-cell">
                    <div class="flex items-center gap-3">
                      <router-link
                        :to="`/management/lens/evaluation/datasets/${row.uuid}`"
                        class="text-sm font-medium text-primary-600 hover:text-primary-700"
                      >
                        {{ t('evaluation.datasets.open') }}
                      </router-link>
                      <button
                        type="button"
                        class="text-sm font-medium text-ink-500 hover:text-error-600"
                        @click="askDelete(row)"
                      >
                        {{ t('common.delete') }}
                      </button>
                    </div>
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

    <BaseModal
      :show="createOpen"
      :title="t('evaluation.datasets.create')"
      max-width="lg"
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
    </BaseModal>

    <ConfirmDeleteModal
      :show="Boolean(deleteTarget)"
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
import { onMounted, reactive, ref } from 'vue'
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
  createEvaluationDataset,
  deleteEvaluationDataset,
  listEvaluationDatasets
} from '@/api/evaluation'
import { listAssistants } from '@/api/lens'
import { useToast } from '@/composables/useToast'
import { formatDate } from '@/utils/formatting'

const { t } = useI18n()
const { showSuccess, showError } = useToast()

const rows = ref([])
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
  archived: 'bg-warning-50 text-warning-700'
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
  loading.value = true
  try {
    const data = await listEvaluationDatasets({
      page: page.value,
      page_size: pageSize.value
    })
    rows.value = Array.isArray(data) ? data : data?.results || []
    total.value = Array.isArray(data)
      ? data.length
      : Number(data?.count ?? rows.value.length)
  } catch (error) {
    rows.value = []
    total.value = 0
    showError(error)
  } finally {
    loading.value = false
  }
}

async function loadAssistants() {
  try {
    assistants.value = await listAssistants()
  } catch {
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
    createOpen.value = false
    showSuccess(t('evaluation.datasets.created'))
    page.value = 1
    await load()
  } catch (error) {
    formError.value = error?.response?.data?.message || String(error)
  } finally {
    saving.value = false
  }
}

function askDelete(row) {
  deleteTarget.value = row
}

async function confirmDelete() {
  if (!deleteTarget.value) return
  deleting.value = true
  try {
    await deleteEvaluationDataset(deleteTarget.value.uuid)
    showSuccess(t('evaluation.datasets.deleted'))
    deleteTarget.value = null
    await load()
  } catch (error) {
    showError(errorMessage(error))
  } finally {
    deleting.value = false
  }
}

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
