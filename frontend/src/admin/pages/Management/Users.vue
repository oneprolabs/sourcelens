<template>
  <AdminLayout>
    <div class="flex h-full min-h-0 max-w-full flex-col gap-4 py-4">
      <section
        class="flex max-h-full min-h-0 w-full flex-col overflow-hidden rounded-lg border border-line bg-surface shadow-sm"
      >
        <div
          class="flex flex-shrink-0 flex-col gap-4 border-b border-line px-5 py-4 lg:flex-row lg:items-start lg:justify-between"
        >
          <div class="min-w-0 space-y-2">
            <h1 class="text-xl font-semibold text-ink-900">
              {{ t('management.userManagement') }}
            </h1>
            <p class="max-w-3xl text-sm leading-6 text-ink-500">
              {{ t('management.usersSubtitle') }}
            </p>
            <div class="flex flex-wrap items-center gap-2 text-xs text-ink-500">
              <span
                class="rounded-md border border-line bg-surface-sunken px-2 py-1"
              >
                {{ t('management.totalUsers', { count: totalCount }) }}
              </span>
            </div>
          </div>
          <div class="flex flex-wrap items-center gap-2">
            <BaseButton
              variant="outline"
              size="sm"
              :loading="loading"
              @click="fetchUsers"
            >
              {{ t('common.refresh') }}
            </BaseButton>
            <BaseButton variant="primary" size="sm" @click="openCreateModal">
              {{ t('management.createUser') }}
            </BaseButton>
          </div>
        </div>

        <div class="flex min-h-0 flex-1 flex-col px-5 py-4">
          <form
            class="mb-4 grid flex-shrink-0 grid-cols-2 gap-3 sm:flex sm:flex-wrap sm:items-end"
            @submit.prevent="applyExactFilters"
          >
            <label class="min-w-0 sm:flex-1 sm:max-w-xs">
              <span class="mb-1 block text-sm font-medium text-ink-700">
                {{ t('management.usernameFilter') }}
              </span>
              <input
                v-model="usernameFilterInput"
                data-testid="username-filter-input"
                type="text"
                class="form-input user-filter-control"
                :placeholder="t('management.usernameFilterPlaceholder')"
              />
            </label>
            <label class="min-w-0 sm:flex-1 sm:max-w-xs">
              <span class="mb-1 block text-sm font-medium text-ink-700">
                {{ t('management.emailFilter') }}
              </span>
              <input
                v-model="emailFilterInput"
                data-testid="email-filter-input"
                type="email"
                class="form-input user-filter-control"
                :placeholder="t('management.emailFilterPlaceholder')"
              />
            </label>
            <BaseButton
              class="user-filter-action"
              data-testid="user-filter-submit"
              type="submit"
              :loading="loading"
            >
              {{ t('common.search') }}
            </BaseButton>
            <BaseButton
              class="user-filter-action"
              data-testid="user-filter-reset"
              variant="outline"
              :disabled="
                !usernameFilterInput &&
                !usernameFilter &&
                !emailFilterInput &&
                !emailFilter
              "
              @click="resetExactFilters"
            >
              {{ t('management.resetFilters') }}
            </BaseButton>
          </form>

          <TableBulkActions
            :actions="bulkActions"
            :loading-key="bulkLoadingKey"
            :selected-count="selectedRows.length"
            @action="runBulkAction"
            @clear="clearSelection"
          />

          <BaseLoading v-if="loading && !users.length" />

          <div
            v-else-if="error"
            class="flex min-h-0 flex-1 items-center justify-center rounded-lg border border-line bg-surface-sunken py-16 text-center"
          >
            <p class="text-sm font-medium text-danger-700">{{ error }}</p>
          </div>

          <div
            v-else-if="!users.length"
            class="flex min-h-0 flex-1 items-center justify-center rounded-lg border border-line bg-surface-sunken py-16 text-center"
          >
            <p class="text-sm font-medium text-ink-500">
              {{ t('common.noData') }}
            </p>
          </div>

          <div
            v-else
            class="relative min-h-0 flex-1 overflow-auto rounded-lg border border-line bg-surface"
            data-testid="user-table-scroll"
          >
            <table
              class="w-full min-w-[20.5rem] table-fixed divide-y divide-line md:min-w-[62.5rem]"
            >
              <colgroup>
                <col class="w-12" />
                <col class="w-16" />
                <col class="w-40" />
                <col class="hidden w-48 md:table-column" />
                <col class="hidden w-40 md:table-column" />
                <col class="hidden w-20 md:table-column" />
                <col class="hidden w-24 md:table-column" />
                <col class="hidden w-36 md:table-column" />
                <col class="w-14" />
              </colgroup>
              <thead class="sticky top-0 z-10 bg-surface-sunken">
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
                  <th class="table-head">{{ t('dashboard.username') }}</th>
                  <th class="table-head hidden md:table-cell">
                    {{ t('dashboard.email') }}
                  </th>
                  <th class="table-head hidden md:table-cell">
                    {{ t('management.groups') }}
                  </th>
                  <th class="table-head hidden md:table-cell">
                    {{ t('dashboard.isStaff') }}
                  </th>
                  <th class="table-head hidden md:table-cell">
                    {{ t('management.isActive') }}
                  </th>
                  <th class="table-head hidden md:table-cell">
                    {{ t('management.dateJoined') }}
                  </th>
                  <th
                    class="table-head text-right md:sticky md:right-0 md:z-20 md:bg-surface-sunken"
                  >
                    {{ t('common.actions') }}
                  </th>
                </tr>
              </thead>
              <tbody class="divide-y divide-line bg-surface">
                <tr
                  v-for="user in users"
                  :key="user.id"
                  class="group cursor-pointer transition-colors hover:bg-line-soft focus-visible:bg-line-soft focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-brand-500"
                  data-testid="user-detail-row"
                  tabindex="0"
                  @click="openDetail(user)"
                  @keydown.enter.self="openDetail(user)"
                  @keydown.space.self.prevent="openDetail(user)"
                >
                  <td class="table-cell" @click.stop>
                    <input
                      type="checkbox"
                      class="h-4 w-4 rounded border-line text-brand-600 focus:ring-brand-500"
                      :aria-label="
                        t('common.selectRow', { name: user.username })
                      "
                      :checked="selectedIds.has(user.id)"
                      @change="setRowSelected(user, $event.target.checked)"
                    />
                  </td>
                  <td class="table-cell font-mono text-ink-500">
                    {{ user.id }}
                  </td>
                  <td class="table-cell min-w-0" data-testid="user-identity">
                    <button
                      class="block w-full truncate text-left font-medium text-brand-700 hover:underline"
                      data-testid="user-detail-trigger"
                      :title="user.username"
                      @click.stop="openDetail(user)"
                    >
                      {{ user.username }}
                    </button>
                    <div
                      v-if="secondaryDisplayName(user)"
                      class="truncate text-xs text-ink-400"
                      data-testid="secondary-identity"
                      :title="secondaryDisplayName(user)"
                    >
                      {{ secondaryDisplayName(user) }}
                    </div>
                  </td>
                  <td
                    class="table-cell hidden min-w-0 text-ink-600 md:table-cell"
                  >
                    <div
                      class="truncate"
                      data-testid="user-email"
                      :title="secondaryIdentity(user.email, user.username)"
                    >
                      {{ secondaryIdentity(user.email, user.username) || '—' }}
                    </div>
                  </td>
                  <td
                    class="table-cell hidden min-w-0 text-ink-600 md:table-cell"
                  >
                    <div class="truncate" :title="joinNames(user.groups)">
                      {{ joinNames(user.groups) }}
                    </div>
                  </td>
                  <td class="table-cell hidden md:table-cell">
                    <span
                      v-if="user.is_staff"
                      class="inline-block rounded-md border border-primary-200 bg-primary-50 px-1.5 py-0.5 text-xs font-medium text-primary-700"
                    >
                      {{ t('common.yes') }}
                    </span>
                    <span v-else class="text-ink-400">—</span>
                  </td>
                  <td class="table-cell hidden md:table-cell">
                    <StatusBadge
                      :status="
                        user.is_active !== false ? 'enabled' : 'disabled'
                      "
                    />
                  </td>
                  <td class="table-cell hidden text-ink-500 md:table-cell">
                    {{ formatDate(user.date_joined) }}
                  </td>
                  <td
                    class="table-cell text-right md:sticky md:right-0 md:z-10 md:bg-surface md:group-hover:bg-line-soft md:group-focus-visible:bg-line-soft"
                    data-testid="user-actions"
                    @click.stop
                  >
                    <RowActionMenu
                      :actions="rowActions(user)"
                      @select="handleRowAction($event, user)"
                    />
                  </td>
                </tr>
              </tbody>
            </table>
          </div>

          <PaginationBar
            v-if="!loading"
            class="flex-shrink-0"
            v-model:page-size="pageSize"
            :current-page="currentPage"
            :total="totalCount"
            @page-size-change="handlePageSizeChange"
            @prev="goPrevPage"
            @next="goNextPage"
          />
        </div>
      </section>

      <UserDetailDrawer
        :show="!!detailUser"
        :user="detailUser"
        @close="detailUser = null"
        @edit="editFromDetail"
        @history="openUserHistory"
      />

      <BaseDrawer
        :show="showModal"
        :title="modalTitle"
        :subtitle="form.username || ''"
        @close="closeModal"
      >
        <form
          id="user-form"
          class="space-y-4"
          novalidate
          @submit.prevent="submitUser"
        >
          <p v-if="submitError" class="text-sm text-danger-700">
            {{ submitError }}
          </p>
          <BaseInput
            v-model="form.username"
            :label="t('dashboard.username')"
            :error="formErrors.username"
            required
            @update:model-value="formErrors.username = ''"
          />
          <BaseInput
            v-model="form.email"
            type="email"
            :label="t('dashboard.email')"
          />
          <BaseInput
            v-if="mode === 'create'"
            v-model="form.password"
            type="password"
            :label="t('password.reset.newPassword')"
            :error="formErrors.password"
            required
            @update:model-value="formErrors.password = ''"
          />
          <div>
            <label class="mb-1 block text-sm font-medium text-ink-700">{{
              t('management.selectGroups')
            }}</label>
            <div
              class="max-h-40 space-y-1 overflow-y-auto rounded-lg border border-line bg-surface-sunken p-2"
            >
              <label
                v-for="group in groupOptions"
                :key="group.id"
                class="flex cursor-pointer items-center gap-2 rounded px-2 py-1.5 text-sm text-ink-700 hover:bg-surface"
              >
                <input
                  v-model="form.group_ids"
                  type="checkbox"
                  :value="group.id"
                  class="h-4 w-4 rounded border-line text-brand-600 focus:ring-brand-500"
                />
                {{ group.name }}
              </label>
              <p
                v-if="!groupOptions.length"
                class="px-2 py-1 text-xs text-ink-400"
              >
                {{ t('common.noData') }}
              </p>
            </div>
          </div>
          <div class="grid gap-4 md:grid-cols-2">
            <label
              class="flex cursor-pointer items-center gap-3 text-sm font-medium text-ink-700"
            >
              <input
                v-model="form.is_staff"
                type="checkbox"
                class="h-4 w-4 rounded border-line text-brand-600 focus:ring-brand-500"
              />
              {{ t('dashboard.isStaff') }}
            </label>
            <label
              class="flex cursor-pointer items-center gap-3 text-sm font-medium text-ink-700"
            >
              <input
                v-model="form.is_active"
                type="checkbox"
                class="h-4 w-4 rounded border-line text-brand-600 focus:ring-brand-500"
              />
              {{ t('management.isActive') }}
            </label>
          </div>
        </form>
        <template #footer>
          <div class="flex flex-row-reverse gap-2">
            <BaseButton
              variant="primary"
              :loading="submitLoading"
              @click="submitUser"
            >
              {{ t('common.confirm') }}
            </BaseButton>
            <BaseButton variant="outline" @click="closeModal">
              {{ t('common.cancel') }}
            </BaseButton>
          </div>
        </template>
      </BaseDrawer>
    </div>
  </AdminLayout>
</template>

<script setup>
import { Pencil, UserCheck, UserX } from '@lucide/vue'
import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRouter } from 'vue-router'

import { managementApi } from '@/admin/api'
import AdminLayout from '@/admin/layout/AdminLayout.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseDrawer from '@/components/ui/BaseDrawer.vue'
import BaseInput from '@/components/ui/BaseInput.vue'
import BaseLoading from '@/components/ui/BaseLoading.vue'
import PaginationBar from '@/components/ui/PaginationBar.vue'
import RowActionMenu from '@/components/ui/RowActionMenu.vue'
import StatusBadge from '@/components/ui/StatusBadge.vue'
import TableBulkActions from '@/components/ui/TableBulkActions.vue'
import { useTableSelection } from '@/composables/useTableSelection'
import { useToast } from '@/composables/useToast'
import { useUserStore } from '@/store/user'
import UserDetailDrawer from './UserDetailDrawer.vue'

const { t } = useI18n()
const { showSuccess, showError } = useToast()
const userStore = useUserStore()
const router = useRouter()

const users = ref([])
const loading = ref(false)
const error = ref(null)
const togglingId = ref(null)
const bulkLoadingKey = ref('')
const currentPage = ref(1)
const pageSize = ref(20)
const totalCount = ref(0)
const usernameFilterInput = ref('')
const usernameFilter = ref('')
const emailFilterInput = ref('')
const emailFilter = ref('')
const detailUser = ref(null)

const currentUserId = computed(() => userStore.userInfo?.id)
const {
  allSelected,
  clearSelection,
  selectedIds,
  selectedRows,
  setAllSelected,
  setRowSelected,
  someSelected
} = useTableSelection(users)

const bulkActions = computed(() => [
  {
    key: 'enable',
    label: t('management.enable'),
    icon: UserCheck
  },
  {
    key: 'disable',
    label: t('management.disable'),
    icon: UserX,
    variant: 'danger',
    confirm: true
  }
])

const showModal = ref(false)
const mode = ref('create')
const editingUserId = ref(null)
const submitLoading = ref(false)
const submitError = ref(null)
const formErrors = ref({ username: '', password: '' })

const groupOptions = ref([])

const createEmptyForm = () => ({
  username: '',
  email: '',
  password: '',
  is_staff: false,
  is_active: true,
  group_ids: []
})

const form = ref(createEmptyForm())

const totalPages = computed(() =>
  totalCount.value > 0 ? Math.ceil(totalCount.value / pageSize.value) : 1
)

const modalTitle = computed(() =>
  mode.value === 'create'
    ? t('management.createUser')
    : t('management.editUser')
)

function joinNames(items) {
  return Array.isArray(items) && items.length
    ? items.map((item) => item.name).join(', ')
    : '—'
}

function secondaryDisplayName(user) {
  return secondaryIdentity(user?.display_name, user?.username)
}

function secondaryIdentity(value, username) {
  const identity = (value || '').trim()
  const primary = (username || '').trim()
  return identity && identity !== primary ? identity : ''
}

function formatDate(value) {
  if (!value) return '—'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString()
}

function closeModal() {
  showModal.value = false
  submitError.value = null
  submitLoading.value = false
  editingUserId.value = null
  formErrors.value = { username: '', password: '' }
  form.value = createEmptyForm()
}

function openCreateModal() {
  mode.value = 'create'
  form.value = createEmptyForm()
  formErrors.value = { username: '', password: '' }
  showModal.value = true
}

function openEditModal(user) {
  mode.value = 'edit'
  editingUserId.value = user.id
  formErrors.value = { username: '', password: '' }
  form.value = {
    username: user.username || '',
    email: user.email || '',
    password: '',
    is_staff: !!user.is_staff,
    is_active: user.is_active !== false,
    group_ids: Array.isArray(user.groups)
      ? user.groups.map((item) => item.id)
      : []
  }
  showModal.value = true
}

function openDetail(user) {
  detailUser.value = user
}

function editFromDetail(user) {
  detailUser.value = null
  openEditModal(user)
}

function openUserHistory(assistant) {
  const user = detailUser.value
  if (!user) return
  router.push({
    path: '/management/lens/runs',
    query: {
      user_id: String(user.id),
      username: user.username,
      ...(assistant?.slug ? { assistant: assistant.slug } : {})
    }
  })
}

async function loadOptions() {
  try {
    groupOptions.value = await managementApi.getAllGroups({
      compact: 'true'
    })
  } catch {
    groupOptions.value = []
  }
}

async function submitUser() {
  submitError.value = null
  formErrors.value = { username: '', password: '' }
  const payload = {
    username: (form.value.username || '').trim(),
    email: (form.value.email || '').trim(),
    is_staff: !!form.value.is_staff,
    is_active: !!form.value.is_active,
    group_ids: Array.isArray(form.value.group_ids) ? form.value.group_ids : []
  }

  if (!payload.username) {
    formErrors.value.username = t('management.usernameRequired')
  }

  if (mode.value === 'create') {
    payload.password = (form.value.password || '').trim()
    if (!payload.password) {
      formErrors.value.password = t('management.passwordRequired')
    }
  }

  if (formErrors.value.username || formErrors.value.password) return

  submitLoading.value = true
  try {
    if (mode.value === 'create') {
      await managementApi.createUser(payload)
    } else {
      await managementApi.updateUser(editingUserId.value, payload)
    }
    closeModal()
    await fetchUsers()
  } catch (e) {
    const detail = e?.response?.data?.detail
    if (e?.response?.data?.code === 'username_taken') {
      submitError.value = t('management.usernameTaken')
    } else if (e?.response?.data?.code === 'email_taken') {
      submitError.value = t('management.emailTaken')
    } else {
      submitError.value =
        typeof detail === 'string' ? detail : t('common.error')
    }
  } finally {
    submitLoading.value = false
  }
}

async function toggleActive(user) {
  if (togglingId.value) return
  togglingId.value = user.id
  const nextActive = user.is_active === false
  try {
    await managementApi.updateUser(user.id, { is_active: nextActive })
    showSuccess(t('management.statusUpdated'))
    await fetchUsers()
  } catch (e) {
    showError(e?.response?.data?.detail || e?.message || t('common.error'))
  } finally {
    togglingId.value = null
  }
}

function rowActions(user) {
  const actions = [
    {
      key: 'edit',
      label: t('common.edit'),
      icon: Pencil
    }
  ]
  if (user.id !== currentUserId.value) {
    actions.push({
      key: 'toggle',
      label:
        user.is_active !== false
          ? t('management.disable')
          : t('management.enable'),
      icon: user.is_active !== false ? UserX : UserCheck,
      variant: user.is_active !== false ? 'danger' : undefined,
      divider: true,
      disabled: togglingId.value !== null
    })
  }
  return actions
}

function handleRowAction(action, user) {
  if (action === 'edit') {
    openEditModal(user)
    return
  }
  toggleActive(user)
}

async function runBulkAction(action) {
  if (bulkLoadingKey.value) return
  const isActive = action === 'enable'
  const targets = selectedRows.value.filter(
    (user) =>
      user.is_active !== isActive &&
      (isActive || user.id !== currentUserId.value)
  )
  if (!targets.length) {
    showError(t('management.noEligibleRows'))
    return
  }

  bulkLoadingKey.value = action
  try {
    await managementApi.bulkUpdateUsers({
      user_ids: targets.map((user) => user.id),
      is_active: isActive
    })
    showSuccess(t('management.bulkUpdated', { count: targets.length }))
    await fetchUsers()
  } catch (e) {
    showError(e?.response?.data?.detail || e?.message || t('common.error'))
  } finally {
    bulkLoadingKey.value = ''
  }
}

function applyExactFilters() {
  usernameFilter.value = usernameFilterInput.value.trim()
  emailFilter.value = emailFilterInput.value.trim()
  currentPage.value = 1
  fetchUsers()
}

function resetExactFilters() {
  usernameFilterInput.value = ''
  usernameFilter.value = ''
  emailFilterInput.value = ''
  emailFilter.value = ''
  currentPage.value = 1
  fetchUsers()
}

async function fetchUsers() {
  loading.value = true
  error.value = null
  try {
    const params = {
      page: currentPage.value,
      page_size: pageSize.value
    }
    if (usernameFilter.value) {
      params.username = usernameFilter.value
    }
    if (emailFilter.value) {
      params.email = emailFilter.value
    }
    const data = await managementApi.getUsers(params)
    users.value = Array.isArray(data) ? data : (data?.results ?? [])
    totalCount.value = Array.isArray(data)
      ? data.length
      : Number(data?.count ?? users.value.length)
  } catch (e) {
    users.value = []
    totalCount.value = 0
    error.value = e?.response?.data?.detail || e?.message || t('common.error')
  } finally {
    loading.value = false
  }
}

function handlePageSizeChange() {
  currentPage.value = 1
  fetchUsers()
}

function goPrevPage() {
  if (currentPage.value <= 1) return
  currentPage.value -= 1
  fetchUsers()
}

function goNextPage() {
  if (currentPage.value >= totalPages.value) return
  currentPage.value += 1
  fetchUsers()
}

onMounted(async () => {
  await Promise.all([fetchUsers(), loadOptions()])
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
  @apply w-full rounded-lg border border-line bg-surface px-3 py-2 text-sm text-ink-900 focus:border-brand-500 focus:outline-none focus:ring-2 focus:ring-brand-500/20;
}

@media (max-width: 767px), (hover: none), (pointer: coarse) {
  .user-filter-control,
  :deep(.user-filter-action) {
    min-height: 44px;
  }
}
</style>
