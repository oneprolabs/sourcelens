<template>
  <AdminLayout>
    <div class="flex max-w-full flex-col gap-4 py-4">
      <section
        class="overflow-hidden rounded-lg border border-line bg-surface shadow-sm"
      >
        <div
          class="flex flex-col gap-4 border-b border-line px-5 py-4 lg:flex-row lg:items-start lg:justify-between"
        >
          <div class="min-w-0">
            <div class="flex flex-wrap items-center gap-2">
              <h1 class="text-xl font-semibold text-ink-900">
                {{ t('lensAdmin.pages.environmentVariables.title') }}
              </h1>
              <span
                class="rounded-md border border-line bg-surface-sunken px-2 py-1 text-xs text-ink-500"
              >
                {{
                  t('lensAdmin.total', {
                    label: t('lensAdmin.pages.environmentVariables.label'),
                    count: rows.length
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
              {{ t('lensAdmin.pages.environmentVariables.action') }}
            </BaseButton>
          </div>
        </div>

        <div v-if="rows.length" class="border-b border-line px-5 py-4">
          <label class="block max-w-md">
            <span class="mb-1 block text-xs font-medium text-ink-600">
              {{ t('lensAdmin.environmentVariables.searchLabel') }}
            </span>
            <input
              v-model="searchQuery"
              type="search"
              class="form-input"
              :placeholder="
                t('lensAdmin.environmentVariables.searchPlaceholder')
              "
            />
          </label>
          <p class="mt-2 text-xs text-ink-500" role="status">
            {{
              t('lensAdmin.environmentVariables.filteredCount', {
                filtered: filteredRows.length,
                total: rows.length
              })
            }}
          </p>
        </div>

        <div class="px-5 py-4">
          <BaseLoading v-if="loading && rows.length === 0" />

          <div
            v-else-if="rows.length === 0"
            class="rounded-lg border border-line bg-surface-sunken py-16 text-center"
          >
            <p class="text-sm font-medium text-ink-500">
              {{ t('lensAdmin.environmentVariables.empty') }}
            </p>
          </div>

          <div
            v-else-if="filteredRows.length === 0"
            class="rounded-lg border border-line bg-surface-sunken py-12 text-center"
          >
            <p class="text-sm font-medium text-ink-500">
              {{ t('lensAdmin.environmentVariables.noResults') }}
            </p>
          </div>

          <div
            v-else
            class="env-list relative overflow-x-auto rounded-lg border border-line bg-surface"
            data-testid="environment-variables-list"
          >
            <table
              class="env-table w-full min-w-[62rem] table-fixed divide-y divide-line"
            >
              <colgroup>
                <col class="w-52" />
                <col class="w-56" />
                <col class="w-56" />
                <col class="w-24" />
                <col class="w-24" />
              </colgroup>
              <thead class="bg-surface-sunken">
                <tr>
                  <th
                    v-for="column in activeColumns"
                    :key="column"
                    class="table-head"
                  >
                    {{ column }}
                  </th>
                </tr>
              </thead>
              <tbody class="divide-y divide-line bg-surface">
                <tr
                  v-for="row in pagedRows"
                  :key="row.uuid"
                  class="transition-colors hover:bg-line-soft"
                >
                  <td class="table-cell" :data-label="activeColumns[0]">
                    <div class="font-medium text-ink-900">{{ row.name }}</div>
                    <div
                      v-if="row.description"
                      class="mt-1 text-xs text-ink-500"
                    >
                      {{ row.description }}
                    </div>
                  </td>
                  <td class="table-cell" :data-label="activeColumns[1]">
                    <div v-if="row.keys?.length" class="flex flex-wrap gap-1.5">
                      <span
                        v-for="key in row.keys"
                        :key="key"
                        class="inline-flex max-w-48 items-center gap-1 rounded-md border border-line bg-surface-sunken px-2 py-1 font-mono text-xs text-ink-600"
                      >
                        <span class="truncate">{{ key }}</span>
                      </span>
                    </div>
                    <span v-else class="text-xs text-ink-400">{{
                      emptyValue
                    }}</span>
                  </td>
                  <td class="table-cell" :data-label="activeColumns[2]">
                    <div
                      v-if="row.usages?.length"
                      class="flex flex-wrap gap-1.5"
                    >
                      <span
                        v-for="usage in visibleUsages(row)"
                        :key="usageKey(usage)"
                        class="inline-flex max-w-64 items-center gap-1 rounded-md border border-line bg-surface-sunken px-2 py-1 text-xs text-ink-600"
                        :title="usageTitle(usage)"
                      >
                        <span class="font-medium text-ink-700">
                          {{ usageTypeLabel(usage.type) }}
                        </span>
                        <span aria-hidden="true">·</span>
                        <span class="truncate">{{ usage.resource_name }}</span>
                        <span v-if="usage.assistant_name" aria-hidden="true"
                          >·</span
                        >
                        <span
                          v-if="usage.assistant_name"
                          class="truncate text-ink-400"
                        >
                          {{ usage.assistant_name }}
                        </span>
                      </span>
                      <span
                        v-if="row.usages.length > VISIBLE_USAGE_LIMIT"
                        class="inline-flex items-center rounded-md border border-line bg-surface-sunken px-2 py-1 text-xs text-ink-500"
                        :title="row.usages.map(usageTitle).join('\n')"
                      >
                        {{
                          t('lensAdmin.environmentVariables.moreUsages', {
                            count: row.usages.length - VISIBLE_USAGE_LIMIT
                          })
                        }}
                      </span>
                    </div>
                    <span v-else class="text-xs text-ink-400">{{
                      emptyValue
                    }}</span>
                  </td>
                  <td class="table-cell" :data-label="activeColumns[3]">
                    <StatusBadge
                      :status="row.enabled ? 'enabled' : 'disabled'"
                    />
                  </td>
                  <td class="table-cell !px-2" :data-label="activeColumns[4]">
                    <div
                      class="flex flex-nowrap items-center justify-end gap-1 whitespace-nowrap"
                    >
                      <button
                        type="button"
                        class="inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-md border border-transparent text-ink-500 transition-colors hover:border-line hover:bg-line-soft hover:text-ink-900 focus:outline-none focus:ring-2 focus:ring-brand-500/30 md:h-8 md:w-8"
                        :aria-label="
                          t('lensAdmin.environmentVariables.viewAction', {
                            name: row.name
                          })
                        "
                        :title="
                          t('lensAdmin.environmentVariables.viewAction', {
                            name: row.name
                          })
                        "
                        @click="openView(row)"
                      >
                        <EyeIcon class="h-4 w-4" />
                      </button>
                      <RowActions
                        :row="row"
                        :confirm-inline="false"
                        @edit="startEdit"
                        @delete="requestDelete"
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
            :total="filteredRows.length"
            @page-size-change="handlePageSizeChange"
            @prev="goPrevPage"
            @next="goNextPage"
          />
        </div>
      </section>

      <BaseDrawer
        :show="showDrawer"
        :title="drawerTitle"
        :subtitle="form.name || ''"
        @close="closeDrawer"
      >
        <form
          id="environment-set-form"
          class="space-y-4"
          novalidate
          @input="formError = ''"
          @submit.prevent="save"
        >
          <FormRow :label="t('lensAdmin.fields.name')" required>
            <input v-model="form.name" class="form-input" required />
          </FormRow>
          <FormRow :label="t('lensAdmin.fields.description')">
            <input v-model="form.description" class="form-input" />
          </FormRow>
          <FormRow :label="t('lensAdmin.environmentVariables.values')">
            <div class="overflow-hidden rounded-lg border border-line">
              <div
                class="flex justify-end border-b border-line bg-surface-sunken p-2"
              >
                <BaseButton size="sm" variant="outline" @click="addValue">
                  {{ t('lensAdmin.environmentVariables.addValue') }}
                </BaseButton>
              </div>
              <div v-if="!form.values.length" class="p-4 text-sm text-ink-400">
                {{ t('lensAdmin.environmentVariables.noValues') }}
              </div>
              <div
                v-for="(item, index) in form.values"
                :key="index"
                class="grid gap-2 border-b border-line p-3 last:border-b-0 sm:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_auto]"
              >
                <input
                  v-model.trim="item.key"
                  class="form-input font-mono"
                  :pattern="SHELL_ENVIRONMENT_NAME_PATTERN"
                  :placeholder="t('lensAdmin.skills.environmentKey')"
                  :aria-label="t('lensAdmin.skills.environmentKey')"
                  required
                />
                <input
                  v-model="item.value"
                  class="form-input font-mono"
                  type="password"
                  :autocomplete="'new-password'"
                  :placeholder="t('lensAdmin.environmentVariables.value')"
                  :aria-label="t('lensAdmin.environmentVariables.value')"
                />
                <BaseButton
                  size="sm"
                  variant="outline"
                  :aria-label="t('common.delete')"
                  @click="removeValue(index)"
                >
                  {{ t('common.delete') }}
                </BaseButton>
              </div>
            </div>
          </FormRow>
          <BooleanRow v-model="form.enabled" />
          <p v-if="formError" class="text-sm text-danger-700" role="alert">
            {{ formError }}
          </p>
        </form>
        <template #footer>
          <div class="flex flex-row-reverse gap-2">
            <BaseButton
              form="environment-set-form"
              type="submit"
              :loading="saving"
            >
              {{ t('common.save') }}
            </BaseButton>
            <BaseButton variant="outline" @click="closeDrawer">
              {{ t('common.cancel') }}
            </BaseButton>
          </div>
        </template>
      </BaseDrawer>

      <BaseModal
        :show="!!deleteTarget"
        :title="t('lensAdmin.environmentVariables.deleteTitle')"
        @close="deleteTarget = null"
      >
        <p class="text-sm text-ink-700">
          {{
            t('lensAdmin.environmentVariables.deleteMessage', {
              name: deleteTarget?.name
            })
          }}
        </p>
        <div
          v-if="deleteTarget?.usages?.length"
          class="mt-4 rounded-lg border border-warning-200 bg-warning-50 p-3"
          role="alert"
        >
          <p class="text-sm font-medium text-warning-800">
            {{
              t('lensAdmin.environmentVariables.deleteBlocked', {
                count: deleteTarget.usages.length
              })
            }}
          </p>
          <ul
            class="mt-2 max-h-40 list-disc space-y-1 overflow-y-auto pl-5 text-sm text-warning-800"
          >
            <li v-for="usage in deleteTarget.usages" :key="usageKey(usage)">
              {{ usageTitle(usage) }}
            </li>
          </ul>
        </div>
        <template #footer>
          <div class="flex flex-row-reverse gap-2">
            <BaseButton
              variant="danger"
              :loading="deleting"
              :disabled="!!deleteTarget?.usages?.length"
              @click="confirmDelete"
            >
              {{ t('lensAdmin.environmentVariables.deleteAction') }}
            </BaseButton>
            <BaseButton variant="outline" @click="deleteTarget = null">
              {{ t('common.cancel') }}
            </BaseButton>
          </div>
        </template>
      </BaseModal>

      <BaseDrawer
        :show="!!viewTarget"
        :title="t('lensAdmin.environmentVariables.viewTitle')"
        :subtitle="viewTarget?.name || ''"
        @close="closeView"
      >
        <template v-if="viewTarget">
          <div class="space-y-6">
            <section>
              <dl class="grid grid-cols-1 gap-4">
                <div v-if="viewTarget.description">
                  <dt
                    class="mb-2 text-xs font-semibold uppercase tracking-wider text-ink-600"
                  >
                    {{ t('lensAdmin.fields.description') }}
                  </dt>
                  <dd class="break-words text-sm font-medium text-ink-900">
                    {{ viewTarget.description }}
                  </dd>
                </div>
                <div>
                  <dt
                    class="mb-2 text-xs font-semibold uppercase tracking-wider text-ink-600"
                  >
                    {{ t('lensAdmin.fields.status') }}
                  </dt>
                  <dd>
                    <StatusBadge
                      :status="viewTarget.enabled ? 'enabled' : 'disabled'"
                    />
                  </dd>
                </div>
              </dl>
            </section>

            <section class="border-t border-line pt-6">
              <div class="mb-3 flex items-center justify-between gap-3">
                <h3 class="text-sm font-semibold text-ink-900">
                  {{ t('lensAdmin.environmentVariables.values') }}
                </h3>
                <BaseButton
                  size="sm"
                  variant="outline"
                  @click="toggleViewReveal"
                >
                  {{
                    viewRevealed
                      ? t('lensAdmin.environmentVariables.hideValues')
                      : t('lensAdmin.environmentVariables.revealValues')
                  }}
                </BaseButton>
              </div>
              <BaseLoading v-if="viewLoading" />
              <div
                v-else-if="!viewValues.length"
                class="rounded-md border border-line bg-surface-sunken p-4 text-sm text-ink-400"
              >
                {{ t('lensAdmin.environmentVariables.noValues') }}
              </div>
              <div v-else class="overflow-hidden rounded-lg border border-line">
                <div
                  v-for="item in viewValues"
                  :key="item.key"
                  class="grid grid-cols-[minmax(0,1fr)_minmax(0,1.4fr)] gap-3 border-b border-line px-4 py-3 text-sm last:border-b-0"
                >
                  <span
                    class="min-w-0 truncate font-mono text-xs font-medium text-ink-700"
                  >
                    {{ item.key }}
                  </span>
                  <span
                    class="min-w-0 truncate font-mono text-xs text-ink-500"
                    :class="viewRevealed ? '' : 'select-none tracking-widest'"
                  >
                    {{ viewRevealed ? item.value : '••••••••' }}
                  </span>
                </div>
              </div>
            </section>

            <section class="border-t border-line pt-6">
              <h3 class="mb-3 text-sm font-semibold text-ink-900">
                {{ t('lensAdmin.environmentVariables.usages') }}
              </h3>
              <div
                v-if="viewTarget.usages?.length"
                class="flex flex-wrap gap-1.5"
              >
                <span
                  v-for="usage in viewTarget.usages"
                  :key="usageKey(usage)"
                  class="inline-flex max-w-64 items-center gap-1 rounded-md border border-line bg-surface-sunken px-2 py-1 text-xs text-ink-600"
                  :title="usageTitle(usage)"
                >
                  <span class="font-medium text-ink-700">
                    {{ usageTypeLabel(usage.type) }}
                  </span>
                  <span aria-hidden="true">·</span>
                  <span class="truncate">{{ usage.resource_name }}</span>
                  <span v-if="usage.assistant_name" aria-hidden="true">·</span>
                  <span
                    v-if="usage.assistant_name"
                    class="truncate text-ink-400"
                  >
                    {{ usage.assistant_name }}
                  </span>
                </span>
              </div>
              <p v-else class="text-sm text-ink-400">{{ emptyValue }}</p>
            </section>
          </div>
        </template>
      </BaseDrawer>
    </div>
  </AdminLayout>
</template>

<script setup>
import { Eye as EyeIcon } from '@lucide/vue'
import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'

import AdminLayout from '@/admin/layout/AdminLayout.vue'
import {
  createEnvironmentVariableSet,
  deleteEnvironmentVariableSet,
  listEnvironmentVariableSets,
  revealEnvironmentVariableSet,
  updateEnvironmentVariableSet
} from '@/api/lens'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseDrawer from '@/components/ui/BaseDrawer.vue'
import BaseLoading from '@/components/ui/BaseLoading.vue'
import BaseModal from '@/components/ui/BaseModal.vue'
import PaginationBar from '@/components/ui/PaginationBar.vue'
import StatusBadge from '@/components/ui/StatusBadge.vue'
import { useToast } from '@/composables/useToast'
import { extractErrorMessage } from '@/utils/api'

import BooleanRow from './components/BooleanRow.vue'
import FormRow from './components/FormRow.vue'
import RowActions from './components/RowActions.vue'
import { EMPTY_VALUE as emptyValue, normalizeList } from './adminHelpers'
import { SHELL_ENVIRONMENT_NAME_PATTERN } from './skillEnvironment'

const VISIBLE_USAGE_LIMIT = 3

const { t } = useI18n()
const { showSuccess, showError } = useToast()
const rows = ref([])
const loading = ref(false)
const saving = ref(false)
const deleting = ref(false)
const showDrawer = ref(false)
const mode = ref('create')
const form = ref(defaultForm())
const formError = ref('')
const deleteTarget = ref(null)
const viewTarget = ref(null)
const viewValues = ref([])
const viewLoading = ref(false)
const viewRevealed = ref(false)
const searchQuery = ref('')
const currentPage = ref(1)
const pageSize = ref(20)

const drawerTitle = computed(() =>
  mode.value === 'create'
    ? t('lensAdmin.environmentVariables.createTitle')
    : t('lensAdmin.environmentVariables.editTitle')
)

const activeColumns = computed(() => [
  t('lensAdmin.fields.name'),
  t('lensAdmin.environmentVariables.keys'),
  t('lensAdmin.environmentVariables.usages'),
  t('lensAdmin.columns.status'),
  t('lensAdmin.columns.actions')
])

const filteredRows = computed(() => {
  const query = searchQuery.value.trim().toLowerCase()
  if (!query) {
    return rows.value
  }
  return rows.value.filter((row) => {
    const haystack = [
      row.name,
      row.description,
      (row.keys || []).join(' '),
      ...(row.usages || []).flatMap((usage) => [
        usage.resource_name,
        usage.assistant_name
      ])
    ]
      .filter(Boolean)
      .join(' ')
      .toLowerCase()
    return haystack.includes(query)
  })
})

const totalPages = computed(() =>
  Math.max(1, Math.ceil(filteredRows.value.length / pageSize.value))
)

const pagedRows = computed(() => {
  const start = (currentPage.value - 1) * pageSize.value
  return filteredRows.value.slice(start, start + pageSize.value)
})

function defaultForm() {
  return {
    uuid: '',
    name: '',
    description: '',
    values: [],
    enabled: true
  }
}

function handlePageSizeChange() {
  currentPage.value = 1
}

function goPrevPage() {
  if (currentPage.value <= 1) return
  currentPage.value -= 1
}

function goNextPage() {
  if (currentPage.value >= totalPages.value) return
  currentPage.value += 1
}

function visibleUsages(row) {
  return (row.usages || []).slice(0, VISIBLE_USAGE_LIMIT)
}

async function load() {
  loading.value = true
  try {
    rows.value = normalizeList(await listEnvironmentVariableSets())
  } catch (error) {
    showError(extractErrorMessage(error, t('lensAdmin.messages.loadFailed')))
  } finally {
    loading.value = false
  }
}

function startCreate() {
  mode.value = 'create'
  form.value = defaultForm()
  formError.value = ''
  showDrawer.value = true
}

async function startEdit(row) {
  mode.value = 'edit'
  formError.value = ''
  try {
    const revealed = await revealEnvironmentVariableSet(row.uuid)
    form.value = {
      uuid: row.uuid,
      name: row.name || '',
      description: row.description || '',
      values: (revealed.values || []).map((item) => ({ ...item })),
      enabled: row.enabled !== false
    }
    showDrawer.value = true
  } catch (error) {
    showError(extractErrorMessage(error, t('lensAdmin.messages.loadFailed')))
  }
}

function closeDrawer() {
  showDrawer.value = false
  form.value = defaultForm()
  formError.value = ''
}

function addValue() {
  form.value.values.push({ key: '', value: '' })
}

function removeValue(index) {
  form.value.values.splice(index, 1)
}

async function openView(row) {
  viewTarget.value = row
  viewRevealed.value = false
  viewValues.value = []
  viewLoading.value = true
  try {
    const revealed = await revealEnvironmentVariableSet(row.uuid)
    viewValues.value = (revealed.values || []).map((item) => ({ ...item }))
  } catch (error) {
    showError(extractErrorMessage(error, t('lensAdmin.messages.loadFailed')))
  } finally {
    viewLoading.value = false
  }
}

function closeView() {
  viewTarget.value = null
  viewValues.value = []
  viewRevealed.value = false
}

function toggleViewReveal() {
  viewRevealed.value = !viewRevealed.value
}

function usageTypeLabel(type) {
  return type === 'mcp'
    ? t('lensAdmin.environmentVariables.mcpUsage')
    : t('lensAdmin.environmentVariables.skillUsage')
}

function usageKey(usage) {
  return `${usage.type}:${usage.resource_uuid}:${usage.assistant_uuid}`
}

function usageTitle(usage) {
  const assistant = usage.assistant_name
    ? ` · ${t('lensAdmin.environmentVariables.assistantUsage', {
        name: usage.assistant_name
      })}`
    : ''
  return `${usageTypeLabel(usage.type)} · ${usage.resource_name}${assistant}`
}

function payload() {
  return {
    name: form.value.name.trim(),
    description: form.value.description.trim(),
    values: form.value.values.map((item) => ({
      key: item.key.trim(),
      value: item.value
    })),
    enabled: !!form.value.enabled
  }
}

async function save() {
  saving.value = true
  formError.value = ''
  try {
    if (mode.value === 'create') {
      await createEnvironmentVariableSet(payload())
    } else {
      await updateEnvironmentVariableSet(form.value.uuid, payload())
    }
    showSuccess(t('lensAdmin.messages.saveSuccess'))
    closeDrawer()
    await load()
  } catch (error) {
    formError.value = extractErrorMessage(
      error,
      t('lensAdmin.messages.saveFailed')
    )
  } finally {
    saving.value = false
  }
}

function requestDelete(row) {
  deleteTarget.value = row
}

async function confirmDelete() {
  const row = deleteTarget.value
  if (!row?.uuid || row.usages?.length || deleting.value) {
    return
  }
  deleting.value = true
  try {
    await deleteEnvironmentVariableSet(row.uuid)
    showSuccess(t('lensAdmin.messages.deleteSuccess'))
    deleteTarget.value = null
    await load()
  } catch (error) {
    showError(extractErrorMessage(error, t('lensAdmin.messages.deleteFailed')))
  } finally {
    deleting.value = false
  }
}

onMounted(load)
</script>

<style scoped>
.form-input {
  @apply w-full rounded-lg border border-line bg-surface px-3 py-2 text-sm text-ink-900 focus:border-brand-500 focus:outline-none focus:ring-2 focus:ring-brand-500/20;
}

.table-head {
  @apply border-b border-line px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-ink-500;
}

.table-cell {
  @apply px-4 py-3 text-sm text-ink-700;
}

@media (max-width: 767px) {
  .env-list {
    @apply overflow-x-hidden border-0 bg-transparent;
  }

  .env-table,
  .env-table tbody {
    display: block;
    width: 100%;
  }

  .env-table {
    min-width: 100% !important;
    table-layout: auto;
  }

  .env-table colgroup {
    display: none;
  }

  .env-table thead {
    position: absolute;
    width: 1px;
    height: 1px;
    padding: 0;
    margin: -1px;
    overflow: hidden;
    clip: rect(0, 0, 0, 0);
    white-space: nowrap;
    border: 0;
  }

  .env-table tbody {
    @apply space-y-3;
  }

  .env-table tbody tr {
    @apply block overflow-hidden rounded-lg border border-line bg-surface;
  }

  .env-table .table-cell {
    display: grid;
    grid-template-columns: minmax(6rem, 36%) minmax(0, 1fr);
    @apply gap-3 border-b border-line px-3 py-3;
  }

  .env-table .table-cell::before {
    content: attr(data-label);
    @apply text-xs font-semibold uppercase tracking-wide text-ink-500;
  }

  .env-table .table-cell:last-child {
    @apply border-b-0;
  }
}
</style>
