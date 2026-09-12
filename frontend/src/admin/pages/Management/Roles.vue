<template>
  <AdminLayout>
    <div class="w-full max-w-full p-6">
      <div class="mb-4">
        <h1 class="text-lg font-semibold text-gray-900">
          {{ t('management.roleManagement') }}
        </h1>
        <p class="mt-1 text-sm text-gray-500">
          {{ t('management.rolesSubtitle') }}
        </p>
      </div>

      <div class="rounded-lg border border-gray-200 bg-white shadow-sm">
        <div class="p-6">
          <div class="mb-6 flex flex-wrap items-center justify-between gap-3">
            <span class="text-sm text-gray-600">{{
              t('management.totalRoles', { count: totalCount })
            }}</span>
            <div class="flex items-center gap-2">
              <BaseButton
                variant="outline"
                size="sm"
                :loading="loading"
                @click="fetchRoles"
              >
                {{ t('common.refresh') }}
              </BaseButton>
              <BaseButton variant="primary" size="sm" @click="openCreateModal">
                {{ t('management.createRole') }}
              </BaseButton>
            </div>
          </div>

          <TableBulkActions
            :actions="bulkActions"
            :loading-key="bulkLoadingKey"
            :selected-count="selectedRows.length"
            @action="runBulkAction"
            @clear="clearSelection"
          />

          <BaseLoading v-if="loading && !roles.length" />

          <div
            v-else-if="error"
            class="rounded-lg border border-gray-200 bg-gray-50 py-16 text-center"
          >
            <p class="text-sm font-medium text-red-600">{{ error }}</p>
          </div>

          <div
            v-else-if="!roles.length"
            class="rounded-lg border border-gray-200 bg-gray-50 py-16 text-center"
          >
            <p class="text-sm font-medium text-gray-600">
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
                      class="h-4 w-4 rounded border-gray-300 text-primary-600 focus:ring-primary-500"
                      :aria-label="t('common.selectAll')"
                      :checked="allSelected"
                      :indeterminate="someSelected"
                      @change="setAllSelected($event.target.checked)"
                    />
                  </th>
                  <th class="table-head">ID</th>
                  <th class="table-head">{{ t('management.roleName') }}</th>
                  <th class="table-head">
                    {{ t('management.visibleFeatures') }}
                  </th>
                  <th class="table-head">
                    {{ t('management.defaultPlatform') }}
                  </th>
                  <th class="table-head">
                    {{ t('management.groupUserCount') }}
                  </th>
                  <th class="table-head">{{ t('management.groupCount') }}</th>
                  <th class="table-head">{{ t('management.isActive') }}</th>
                  <th class="table-head">{{ t('common.actions') }}</th>
                </tr>
              </thead>
              <tbody class="divide-y divide-gray-100 bg-white">
                <tr
                  v-for="role in roles"
                  :key="role.id"
                  class="transition-colors duration-150 hover:bg-gray-50"
                >
                  <td class="table-cell">
                    <input
                      type="checkbox"
                      class="h-4 w-4 rounded border-gray-300 text-primary-600 focus:ring-primary-500"
                      :aria-label="t('common.selectRow', { name: role.name })"
                      :checked="selectedIds.has(role.id)"
                      @change="setRowSelected(role, $event.target.checked)"
                    />
                  </td>
                  <td class="table-cell text-gray-900">{{ role.id }}</td>
                  <td class="table-cell font-medium text-gray-900">
                    {{ role.name }}
                  </td>
                  <td class="table-cell text-gray-500">
                    {{ formatFeatures(role.visible_features) }}
                  </td>
                  <td class="table-cell text-gray-500">
                    {{ formatPlatform(role.preferred_platform) }}
                  </td>
                  <td class="table-cell text-gray-500">
                    {{ role.user_count ?? 0 }}
                  </td>
                  <td class="table-cell text-gray-500">
                    {{ role.group_count ?? 0 }}
                  </td>
                  <td class="table-cell">
                    <span
                      :class="
                        role.is_active ? 'text-green-600' : 'text-gray-400'
                      "
                    >
                      {{ role.is_active ? t('common.yes') : t('common.no') }}
                    </span>
                  </td>
                  <td class="table-cell text-right">
                    <RowActionMenu
                      :actions="rowActions"
                      @select="handleRowAction($event, role)"
                    />
                  </td>
                </tr>
              </tbody>
            </table>
          </div>

          <div
            v-if="!loading && totalCount > 0"
            class="mt-4 flex flex-wrap items-center justify-between gap-2 border-t border-gray-200 pt-4"
          >
            <p class="text-sm text-gray-600">
              {{ t('common.pagination.showing', paginationShowing) }}
            </p>
            <div class="flex items-center gap-2">
              <label class="text-sm text-gray-600"
                >{{ t('common.pagination.itemsPerPage') }}:</label
              >
              <BaseSelect
                v-model.number="pageSize"
                :full-width="false"
                size="sm"
                @change="handlePageSizeChange"
              >
                <option :value="10">10</option>
                <option :value="20">20</option>
                <option :value="50">50</option>
                <option :value="100">100</option>
              </BaseSelect>
              <BaseButton
                variant="outline"
                size="sm"
                :disabled="currentPage <= 1"
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
                <span class="sr-only">{{
                  t('common.pagination.previous')
                }}</span>
              </BaseButton>
              <BaseButton
                variant="outline"
                size="sm"
                :disabled="currentPage >= totalPages"
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

      <BaseModal :show="showModal" :title="modalTitle" @close="closeModal">
        <form @submit.prevent="submitRole" class="space-y-4">
          <p v-if="submitError" class="text-sm text-red-600">
            {{ submitError }}
          </p>
          <div>
            <label class="mb-1 block text-sm font-medium text-gray-700">{{
              t('management.roleName')
            }}</label>
            <input v-model="form.name" type="text" class="form-input" />
          </div>
          <div>
            <label class="mb-1 block text-sm font-medium text-gray-700">{{
              t('management.visibleFeatures')
            }}</label>
            <div
              class="space-y-2 rounded-md border border-gray-300 bg-white p-3"
            >
              <label
                v-for="platform in platformOptions"
                :key="platform.key"
                class="flex cursor-pointer items-start gap-3 rounded px-2 py-1 hover:bg-gray-50"
              >
                <input
                  v-model="form.visible_features"
                  type="checkbox"
                  :value="platform.key"
                  class="mt-0.5 h-4 w-4 rounded border-gray-300 text-primary-600 focus:ring-primary-500"
                />
                <span class="text-sm text-gray-700">{{ platform.label }}</span>
              </label>
            </div>
          </div>
          <div>
            <label class="mb-1 block text-sm font-medium text-gray-700">{{
              t('management.defaultPlatform')
            }}</label>
            <BaseSelect v-model="form.preferred_platform">
              <option value="">{{ t('management.noDefaultPlatform') }}</option>
              <option
                v-for="platform in selectedPlatformOptions"
                :key="platform.key"
                :value="platform.key"
              >
                {{ platform.label }}
              </option>
            </BaseSelect>
          </div>
          <div class="flex items-center gap-3">
            <input
              v-model="form.is_active"
              type="checkbox"
              id="role-is-active"
              class="h-4 w-4 rounded border-gray-300 text-primary-600 focus:ring-primary-500"
            />
            <label
              for="role-is-active"
              class="cursor-pointer text-sm font-medium text-gray-700"
            >
              {{ t('management.isActive') }}
            </label>
          </div>
        </form>
        <template #footer>
          <div class="flex flex-row-reverse gap-2">
            <BaseButton
              variant="primary"
              :loading="submitLoading"
              @click="submitRole"
            >
              {{ t('common.confirm') }}
            </BaseButton>
            <BaseButton variant="outline" @click="closeModal">
              {{ t('common.cancel') }}
            </BaseButton>
          </div>
        </template>
      </BaseModal>
    </div>
  </AdminLayout>
</template>

<script setup>
import { Pencil, Power, PowerOff } from '@lucide/vue'
import { computed, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'

import { managementApi } from '@/admin/api'
import AdminLayout from '@/admin/layout/AdminLayout.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseLoading from '@/components/ui/BaseLoading.vue'
import BaseModal from '@/components/ui/BaseModal.vue'
import BaseSelect from '@/components/ui/BaseSelect.vue'
import RowActionMenu from '@/components/ui/RowActionMenu.vue'
import TableBulkActions from '@/components/ui/TableBulkActions.vue'
import { useTableSelection } from '@/composables/useTableSelection'
import { useToast } from '@/composables/useToast'
import { FEATURE_DEFINITIONS } from '@/utils/platformAccess'

const { t } = useI18n()
const { showError, showSuccess } = useToast()

const roles = ref([])
const loading = ref(false)
const error = ref(null)
const currentPage = ref(1)
const pageSize = ref(20)
const totalCount = ref(0)
const bulkLoadingKey = ref('')
const {
  allSelected,
  clearSelection,
  selectedIds,
  selectedRows,
  setAllSelected,
  setRowSelected,
  someSelected
} = useTableSelection(roles)

const rowActions = computed(() => [
  {
    key: 'edit',
    label: t('common.edit'),
    icon: Pencil
  }
])

const bulkActions = computed(() => [
  {
    key: 'enable',
    label: t('management.enable'),
    icon: Power
  },
  {
    key: 'disable',
    label: t('management.disable'),
    icon: PowerOff,
    variant: 'danger',
    confirm: true
  }
])

const showModal = ref(false)
const mode = ref('create')
const editingRoleId = ref(null)
const submitLoading = ref(false)
const submitError = ref(null)
const form = ref({
  name: '',
  visible_features: [],
  preferred_platform: '',
  is_active: true
})

const totalPages = computed(() =>
  totalCount.value > 0 ? Math.ceil(totalCount.value / pageSize.value) : 1
)

const paginationShowing = computed(() => ({
  from:
    totalCount.value === 0 ? 0 : (currentPage.value - 1) * pageSize.value + 1,
  to: Math.min(currentPage.value * pageSize.value, totalCount.value),
  total: totalCount.value
}))

const modalTitle = computed(() =>
  mode.value === 'create'
    ? t('management.createRole')
    : t('management.editRole')
)

const platformOptions = computed(() =>
  FEATURE_DEFINITIONS.map((item) => ({
    key: item.key,
    label: t(item.labelKey)
  }))
)

const selectedPlatformOptions = computed(() =>
  platformOptions.value.filter((item) =>
    form.value.visible_features.includes(item.key)
  )
)

watch(
  () => form.value.visible_features,
  (visibleFeatures) => {
    if (!visibleFeatures.includes(form.value.preferred_platform)) {
      form.value.preferred_platform = ''
    }
  }
)

function formatFeatures(items) {
  if (!Array.isArray(items) || !items.length) return '—'
  return items
    .map(
      (item) =>
        platformOptions.value.find((platform) => platform.key === item)
          ?.label || item
    )
    .join(', ')
}

function formatPlatform(value) {
  const match = platformOptions.value.find((item) => item.key === value)
  return match?.label || '—'
}

function closeModal() {
  showModal.value = false
  editingRoleId.value = null
  submitError.value = null
  submitLoading.value = false
  form.value = {
    name: '',
    visible_features: [],
    preferred_platform: '',
    is_active: true
  }
}

function openCreateModal() {
  mode.value = 'create'
  closeModal()
  mode.value = 'create'
  showModal.value = true
}

function openEditModal(role) {
  mode.value = 'edit'
  editingRoleId.value = role.id
  form.value = {
    name: role.name || '',
    visible_features: Array.isArray(role.visible_features)
      ? [...role.visible_features]
      : [],
    preferred_platform: role.preferred_platform || '',
    is_active: role.is_active !== false
  }
  showModal.value = true
}

function handleRowAction(action, role) {
  if (action === 'edit') openEditModal(role)
}

async function submitRole() {
  submitError.value = null
  const name = (form.value.name || '').trim()
  if (!name) {
    submitError.value = t('management.roleNameRequired')
    return
  }

  submitLoading.value = true
  try {
    const payload = {
      name,
      visible_features: Array.isArray(form.value.visible_features)
        ? form.value.visible_features
        : [],
      preferred_platform: form.value.preferred_platform || '',
      is_active: !!form.value.is_active
    }
    if (mode.value === 'create') {
      await managementApi.createRole(payload)
    } else {
      await managementApi.updateRole(editingRoleId.value, payload)
    }
    closeModal()
    await fetchRoles()
  } catch (e) {
    if (e?.response?.data?.code === 'name_taken') {
      submitError.value = t('management.roleNameTaken')
    } else {
      submitError.value =
        e?.response?.data?.detail || e?.message || t('common.error')
    }
  } finally {
    submitLoading.value = false
  }
}

async function runBulkAction(action) {
  if (bulkLoadingKey.value) return
  const isActive = action === 'enable'
  const targets = selectedRows.value.filter(
    (role) => role.is_active !== isActive
  )
  if (!targets.length) {
    showError(t('management.noEligibleRows'))
    return
  }

  bulkLoadingKey.value = action
  try {
    await managementApi.bulkUpdateRoles({
      role_ids: targets.map((role) => role.id),
      is_active: isActive
    })
    showSuccess(t('management.bulkUpdated', { count: targets.length }))
    await fetchRoles()
  } catch (e) {
    showError(e?.response?.data?.detail || e?.message || t('common.error'))
  } finally {
    bulkLoadingKey.value = ''
  }
}

async function fetchRoles() {
  loading.value = true
  error.value = null
  try {
    const data = await managementApi.getRoles({
      page: currentPage.value,
      page_size: pageSize.value
    })
    roles.value = Array.isArray(data) ? data : (data?.results ?? [])
    totalCount.value = Array.isArray(data)
      ? data.length
      : Number(data?.count ?? roles.value.length)
  } catch (e) {
    roles.value = []
    totalCount.value = 0
    error.value = e?.response?.data?.detail || e?.message || t('common.error')
  } finally {
    loading.value = false
  }
}

function handlePageSizeChange() {
  currentPage.value = 1
  fetchRoles()
}

function goPrevPage() {
  if (currentPage.value <= 1) return
  currentPage.value -= 1
  fetchRoles()
}

function goNextPage() {
  if (currentPage.value >= totalPages.value) return
  currentPage.value += 1
  fetchRoles()
}

onMounted(() => {
  fetchRoles()
})
</script>

<style scoped>
.table-head {
  @apply border-b border-line px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-ink-500;
}

.table-cell {
  @apply px-4 py-3 align-top text-sm text-ink-700;
}

.form-input {
  @apply w-full rounded-md border border-gray-300 px-3 py-2 text-sm focus:border-primary-500 focus:outline-none focus:ring-1 focus:ring-primary-500;
}
</style>
