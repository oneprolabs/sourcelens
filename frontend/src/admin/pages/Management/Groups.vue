<template>
  <AdminLayout>
    <div class="flex max-w-full flex-col gap-4 py-4">
      <section
        class="overflow-hidden rounded-lg border border-line bg-surface shadow-sm"
      >
        <div
          class="flex flex-col gap-4 border-b border-line px-5 py-4 lg:flex-row lg:items-start lg:justify-between"
        >
          <div class="min-w-0 space-y-2">
            <h1 class="text-xl font-semibold text-ink-900">
              {{ t('management.groupManagement') }}
            </h1>
            <p class="max-w-3xl text-sm leading-6 text-ink-500">
              {{ t('management.groupsSubtitle') }}
            </p>
            <div class="flex flex-wrap items-center gap-2 text-xs text-ink-500">
              <span
                class="rounded-md border border-line bg-surface-sunken px-2 py-1"
              >
                {{ t('management.totalGroups', { count: totalCount }) }}
              </span>
            </div>
          </div>
          <div class="flex flex-wrap items-center gap-2">
            <BaseButton
              variant="outline"
              size="sm"
              :loading="loading"
              @click="fetchGroups"
            >
              {{ t('common.refresh') }}
            </BaseButton>
            <BaseButton variant="primary" size="sm" @click="openCreateModal">
              {{ t('management.createGroup') }}
            </BaseButton>
          </div>
        </div>

        <div class="px-5 py-4">
          <TableBulkActions
            :actions="bulkActions"
            :loading-key="bulkLoadingKey"
            :selected-count="selectedRows.length"
            @action="runBulkDelete"
            @clear="clearSelection"
          />

          <BaseLoading v-if="loading && !groups.length" />

          <div
            v-else-if="error"
            class="rounded-lg border border-line bg-surface-sunken py-16 text-center"
          >
            <p class="text-sm font-medium text-danger-700">{{ error }}</p>
          </div>

          <div
            v-else-if="!groups.length"
            class="rounded-lg border border-line bg-surface-sunken py-16 text-center"
          >
            <p class="text-sm font-medium text-ink-500">
              {{ t('common.noData') }}
            </p>
          </div>

          <div
            v-else
            class="relative overflow-x-auto rounded-lg border border-line bg-surface"
          >
            <table class="min-w-full divide-y divide-line">
              <thead class="bg-surface-sunken">
                <tr>
                  <th class="table-head w-12">
                    <input
                      type="checkbox"
                      class="h-4 w-4 rounded border-line text-brand-600 focus:ring-brand-500"
                      :aria-label="t('common.selectAll')"
                      :checked="allSelected"
                      :indeterminate="someSelected"
                      @change="setAllSelected($event.target.checked)"
                    />
                  </th>
                  <th class="table-head">ID</th>
                  <th class="table-head">{{ t('management.groupName') }}</th>
                  <th class="table-head">
                    {{ t('management.groupUserCount') }}
                  </th>
                  <th class="table-head">
                    {{ t('management.permissionCount') }}
                  </th>
                  <th class="table-head">{{ t('common.actions') }}</th>
                </tr>
              </thead>
              <tbody class="divide-y divide-line bg-surface">
                <tr
                  v-for="group in groups"
                  :key="group.id"
                  class="cursor-pointer transition-colors hover:bg-line-soft focus-visible:bg-line-soft focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-brand-500"
                  data-testid="group-detail-row"
                  tabindex="0"
                  @click="openDetail(group)"
                  @keydown.enter.self="openDetail(group)"
                  @keydown.space.self.prevent="openDetail(group)"
                >
                  <td class="table-cell" @click.stop>
                    <input
                      type="checkbox"
                      class="h-4 w-4 rounded border-line text-brand-600 focus:ring-brand-500"
                      :aria-label="t('common.selectRow', { name: group.name })"
                      :checked="selectedIds.has(group.id)"
                      @change="setRowSelected(group, $event.target.checked)"
                    />
                  </td>
                  <td class="table-cell font-mono text-ink-500">
                    {{ group.id }}
                  </td>
                  <td class="table-cell">
                    <button
                      class="font-medium text-brand-700 hover:underline"
                      data-testid="group-detail-trigger"
                      @click.stop="openDetail(group)"
                    >
                      {{ group.name }}
                    </button>
                  </td>
                  <td class="table-cell text-ink-600">
                    {{ group.user_count ?? 0 }}
                  </td>
                  <td class="table-cell text-ink-600">
                    {{ group.permission_count ?? 0 }}
                  </td>
                  <td class="table-cell text-right" @click.stop>
                    <RowActionMenu
                      :actions="rowActions"
                      @select="handleRowAction($event, group)"
                    />
                  </td>
                </tr>
              </tbody>
            </table>
          </div>

          <PaginationBar
            v-if="!loading"
            v-model:page-size="pageSize"
            :current-page="currentPage"
            :total="totalCount"
            @page-size-change="handlePageSizeChange"
            @prev="goPrevPage"
            @next="goNextPage"
          />
        </div>
      </section>

      <GroupDetailDrawer
        :show="!!detailGroup"
        :group="detailGroup"
        @close="detailGroup = null"
        @edit="editFromDetail"
        @history="openGroupHistory"
      />

      <BaseDrawer
        :show="showModal"
        :title="modalTitle"
        :subtitle="form.name || ''"
        @close="closeModal"
      >
        <form
          id="group-form"
          class="space-y-4"
          novalidate
          @submit.prevent="submitGroup"
        >
          <p v-if="submitError" class="text-sm text-danger-700">
            {{ submitError }}
          </p>
          <BaseInput
            v-model="form.name"
            :label="t('management.groupName')"
            :error="formErrors.name"
            required
            @update:model-value="formErrors.name = ''"
          />
          <div>
            <label class="mb-1 block text-sm font-medium text-ink-700">{{
              t('management.members')
            }}</label>
            <div
              data-testid="group-member-selector"
              class="max-h-60 space-y-1 overflow-y-auto rounded-lg border border-line bg-surface-sunken p-2"
            >
              <label
                v-for="user in userOptions"
                :key="user.id"
                class="flex cursor-pointer items-start gap-2 rounded px-2 py-1.5 text-sm text-ink-700 hover:bg-surface"
              >
                <input
                  v-model="form.user_ids"
                  type="checkbox"
                  :value="user.id"
                  class="mt-0.5 h-4 w-4 shrink-0 rounded border-line text-brand-600 focus:ring-brand-500"
                />
                <span class="min-w-0 flex-1">
                  <span class="block break-words font-medium">{{
                    user.display_name || user.username
                  }}</span>
                  <span
                    v-if="user.email"
                    class="block break-all text-xs text-ink-400"
                    >{{ user.email }}</span
                  >
                </span>
              </label>
              <p
                v-if="!userOptions.length"
                class="px-2 py-1 text-xs text-ink-400"
              >
                {{ t('common.noData') }}
              </p>
            </div>
          </div>
        </form>
        <template #footer>
          <div class="flex flex-row-reverse gap-2">
            <BaseButton
              variant="primary"
              :loading="submitLoading"
              @click="submitGroup"
            >
              {{ t('common.confirm') }}
            </BaseButton>
            <BaseButton variant="outline" @click="closeModal">
              {{ t('common.cancel') }}
            </BaseButton>
          </div>
        </template>
      </BaseDrawer>

      <BaseModal
        :show="!!deleteTarget"
        :title="t('management.deleteGroup')"
        @close="deleteTarget = null"
      >
        <p class="text-sm leading-6 text-ink-700">
          {{
            t('management.deleteGroupConfirm', {
              name: deleteTarget?.name,
              members: deleteTarget?.user_count ?? 0,
              assistants: deleteTarget?.assistant_grant_count ?? 0
            })
          }}
        </p>
        <template #footer>
          <div class="flex flex-row-reverse gap-2">
            <BaseButton
              variant="danger"
              :loading="deletingId === deleteTarget?.id"
              @click="confirmDelete"
            >
              {{ t('common.delete') }}
            </BaseButton>
            <BaseButton variant="outline" @click="deleteTarget = null">
              {{ t('common.cancel') }}
            </BaseButton>
          </div>
        </template>
      </BaseModal>
    </div>
  </AdminLayout>
</template>

<script setup>
import { Pencil, Trash2 } from '@lucide/vue'
import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRouter } from 'vue-router'

import { managementApi } from '@/admin/api'
import AdminLayout from '@/admin/layout/AdminLayout.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseDrawer from '@/components/ui/BaseDrawer.vue'
import BaseInput from '@/components/ui/BaseInput.vue'
import BaseLoading from '@/components/ui/BaseLoading.vue'
import BaseModal from '@/components/ui/BaseModal.vue'
import PaginationBar from '@/components/ui/PaginationBar.vue'
import RowActionMenu from '@/components/ui/RowActionMenu.vue'
import TableBulkActions from '@/components/ui/TableBulkActions.vue'
import { useTableSelection } from '@/composables/useTableSelection'
import { useToast } from '@/composables/useToast'
import GroupDetailDrawer from './GroupDetailDrawer.vue'

const { t } = useI18n()
const { showSuccess, showError } = useToast()
const router = useRouter()

const groups = ref([])
const loading = ref(false)
const error = ref(null)
const currentPage = ref(1)
const pageSize = ref(20)
const totalCount = ref(0)
const userOptions = ref([])
const userOptionsLoaded = ref(false)
const deleteTarget = ref(null)
const deletingId = ref(null)
const bulkLoadingKey = ref('')
const detailGroup = ref(null)
const {
  allSelected,
  clearSelection,
  selectedIds,
  selectedRows,
  setAllSelected,
  setRowSelected,
  someSelected
} = useTableSelection(groups)

const rowActions = computed(() => [
  {
    key: 'edit',
    label: t('common.edit'),
    icon: Pencil
  },
  {
    key: 'delete',
    label: t('common.delete'),
    icon: Trash2,
    variant: 'danger',
    divider: true
  }
])

const bulkActions = computed(() => [
  {
    key: 'delete',
    label: t('common.delete'),
    icon: Trash2,
    variant: 'danger',
    confirm: true
  }
])

const showModal = ref(false)
const mode = ref('create')
const editingGroupId = ref(null)
const submitLoading = ref(false)
const submitError = ref(null)
const formErrors = ref({ name: '' })
const form = ref({ name: '', user_ids: [] })

const totalPages = computed(() =>
  totalCount.value > 0 ? Math.ceil(totalCount.value / pageSize.value) : 1
)

const modalTitle = computed(() =>
  mode.value === 'create'
    ? t('management.createGroup')
    : t('management.editGroup')
)

function closeModal() {
  showModal.value = false
  editingGroupId.value = null
  submitError.value = null
  submitLoading.value = false
  formErrors.value = { name: '' }
  form.value = { name: '', user_ids: [] }
}

function openCreateModal() {
  mode.value = 'create'
  form.value = { name: '', user_ids: [] }
  formErrors.value = { name: '' }
  showModal.value = true
}

function memberIdsForGroup(groupId) {
  return userOptions.value
    .filter((user) => (user.groups || []).some((group) => group.id === groupId))
    .map((user) => user.id)
}

function openEditModal(group) {
  mode.value = 'edit'
  editingGroupId.value = group.id
  formErrors.value = { name: '' }
  form.value = {
    name: group.name || '',
    user_ids: memberIdsForGroup(group.id)
  }
  showModal.value = true
}

function openDetail(group) {
  detailGroup.value = group
}

function editFromDetail(group) {
  detailGroup.value = null
  openEditModal(group)
}

function openGroupHistory(assistant) {
  const group = detailGroup.value
  if (!group) return
  router.push({
    path: '/management/lens/runs',
    query: {
      group_id: String(group.id),
      ...(assistant?.slug ? { assistant: assistant.slug } : {})
    }
  })
}

async function loadUsers() {
  userOptionsLoaded.value = false
  try {
    userOptions.value = await managementApi.getAllUsers()
    userOptionsLoaded.value = true
  } catch {
    userOptions.value = []
  }
}

function askDelete(group) {
  deleteTarget.value = group
}

function handleRowAction(action, group) {
  if (action === 'edit') {
    openEditModal(group)
    return
  }
  askDelete(group)
}

async function confirmDelete() {
  const group = deleteTarget.value
  if (!group) return
  deletingId.value = group.id
  try {
    await managementApi.deleteGroup(group.id)
    deleteTarget.value = null
    showSuccess(t('management.groupDeleted'))
    await Promise.all([fetchGroups(), loadUsers()])
  } catch (e) {
    showError(e?.response?.data?.detail || e?.message || t('common.error'))
  } finally {
    deletingId.value = null
  }
}

async function runBulkDelete() {
  if (bulkLoadingKey.value) return
  const targets = [...selectedRows.value]
  bulkLoadingKey.value = 'delete'
  try {
    await managementApi.bulkDeleteGroups(targets.map((group) => group.id))
    showSuccess(t('management.bulkDeleted', { count: targets.length }))
    await Promise.all([fetchGroups(), loadUsers()])
  } catch (e) {
    showError(e?.response?.data?.detail || e?.message || t('common.error'))
  } finally {
    bulkLoadingKey.value = ''
  }
}

async function submitGroup() {
  submitError.value = null
  formErrors.value = { name: '' }
  const name = (form.value.name || '').trim()
  if (!name) {
    formErrors.value.name = t('management.groupNameRequired')
    return
  }

  submitLoading.value = true
  try {
    const payload = { name }
    if (userOptionsLoaded.value) {
      payload.user_ids = Array.isArray(form.value.user_ids)
        ? form.value.user_ids
        : []
    }
    if (mode.value === 'create') {
      await managementApi.createGroup(payload)
    } else {
      await managementApi.updateGroup(editingGroupId.value, payload)
    }
    closeModal()
    await Promise.all([fetchGroups(), loadUsers()])
  } catch (e) {
    if (e?.response?.data?.code === 'name_taken') {
      submitError.value = t('management.groupNameTaken')
    } else {
      submitError.value =
        e?.response?.data?.detail || e?.message || t('common.error')
    }
  } finally {
    submitLoading.value = false
  }
}

async function fetchGroups() {
  loading.value = true
  error.value = null
  try {
    const data = await managementApi.getGroups({
      page: currentPage.value,
      page_size: pageSize.value
    })
    groups.value = Array.isArray(data) ? data : (data?.results ?? [])
    totalCount.value = Array.isArray(data)
      ? data.length
      : Number(data?.count ?? groups.value.length)
  } catch (e) {
    groups.value = []
    totalCount.value = 0
    error.value = e?.response?.data?.detail || e?.message || t('common.error')
  } finally {
    loading.value = false
  }
}

function handlePageSizeChange() {
  currentPage.value = 1
  fetchGroups()
}

function goPrevPage() {
  if (currentPage.value <= 1) return
  currentPage.value -= 1
  fetchGroups()
}

function goNextPage() {
  if (currentPage.value >= totalPages.value) return
  currentPage.value += 1
  fetchGroups()
}

onMounted(async () => {
  await Promise.all([fetchGroups(), loadUsers()])
})
</script>

<style scoped>
.table-head {
  @apply border-b border-line px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-ink-500;
}

.table-cell {
  @apply px-4 py-3 align-top text-sm text-ink-700;
}
</style>
