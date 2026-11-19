<template>
  <AdminLayout>
    <div class="admin-list-page">
      <section class="admin-list-surface">
        <div class="admin-page-heading">
          <div class="admin-heading-copy">
            <h1>
              {{ t('management.userManagement') }}
              <span class="admin-heading-count">{{
                statusCounts.all ?? totalCount
              }}</span>
            </h1>
            <p>{{ t('management.usersSubtitle') }}</p>
          </div>
          <BaseButton @click="openInvitation()">
            <Plus :size="14" aria-hidden="true" />{{ t('invitations.title') }}
          </BaseButton>
        </div>
        <div class="admin-list-panel">
          <form class="admin-list-toolbar" @submit.prevent="applyFilters">
            <div
              class="admin-status-tabs"
              :aria-label="t('management.memberStatus')"
            >
              <button
                v-for="tab in statusTabs"
                :key="tab.value"
                type="button"
                :aria-pressed="statusFilter === tab.value"
                @click="selectStatus(tab.value)"
              >
                {{ t(tab.label) }}
                <small v-if="tab.value === ''">{{
                  statusCounts.all ?? totalCount
                }}</small>
                <small
                  v-else-if="tab.value === 'invited' && statusCounts.invited"
                  >{{ statusCounts.invited }}</small
                >
              </button>
            </div>
            <label class="admin-toolbar-search">
              <Search :size="14" aria-hidden="true" />
              <input
                v-model="searchInput"
                type="search"
                :aria-label="t('common.search')"
                :placeholder="t('management.memberSearchPlaceholder')"
                @input="queueSearch"
              />
            </label>
            <BaseSelect
              v-model="roleFilter"
              class="admin-filter-chip"
              :full-width="false"
              :aria-label="t('management.roles')"
              @update:model-value="applyFilters"
            >
              <template #selected="{ option }"
                ><span class="admin-filter-label"
                  ><Plus :size="12" aria-hidden="true" />{{
                    option?.label
                  }}</span
                ></template
              >
              <option value="">{{ t('management.roles') }}</option>
              <option value="tenant_admin">
                {{ t('management.tenantAdminRole') }}
              </option>
              <option value="member">{{ t('management.memberRole') }}</option>
            </BaseSelect>
            <BaseSelect
              v-model="groupFilter"
              class="admin-filter-chip"
              :full-width="false"
              :aria-label="t('management.memberGroups')"
              @update:model-value="applyFilters"
            >
              <template #selected="{ option }"
                ><span class="admin-filter-label"
                  ><Plus :size="12" aria-hidden="true" />{{
                    option?.label
                  }}</span
                ></template
              >
              <option value="">{{ t('management.memberGroups') }}</option>
              <option
                v-for="group in groups"
                :key="group.id"
                :value="String(group.id)"
              >
                {{ group.name }}
              </option>
            </BaseSelect>
            <BaseButton
              v-if="hasFilters"
              variant="ghost"
              size="sm"
              @click="resetFilters"
            >
              {{ t('management.resetFilters') }}
            </BaseButton>
          </form>

          <div v-if="selectedMembers.length" class="admin-bulk-toolbar">
            <span class="mr-2 text-sm font-medium text-ink-700">{{
              t('common.selectedCount', { count: selectedMembers.length })
            }}</span>
            <BaseButton
              v-for="action in bulkActions"
              :key="action"
              variant="outline"
              :disabled="loading"
              @click="openBulk(action)"
            >
              {{ t(`management.memberBulk.actions.${action}`) }}
            </BaseButton>
            <BaseButton variant="ghost" @click="selectedMembers = []">{{
              t('common.clearSelection')
            }}</BaseButton>
            <BaseButton
              variant="outline"
              :disabled="loading"
              @click="openDelete(selectedMembers)"
            >
              {{ t('management.memberDelete.title') }}
            </BaseButton>
          </div>

          <BaseLoading v-if="loading && !members.length" />

          <div
            v-else-if="error"
            class="flex min-h-0 flex-1 items-center justify-center rounded-lg border border-line bg-surface-sunken py-16 text-center"
          >
            <p class="text-sm font-medium text-danger-700">{{ error }}</p>
          </div>

          <div
            v-else-if="!members.length"
            class="flex min-h-0 flex-1 items-center justify-center rounded-lg border border-line bg-surface-sunken py-16 text-center"
          >
            <div>
              <p class="text-sm font-medium text-ink-700">
                {{ t('management.noMembers') }}
              </p>
              <p class="mt-1 text-sm text-ink-500">
                {{ t('management.noMembersHint') }}
              </p>
            </div>
          </div>

          <div v-else class="admin-list-table relative min-h-0 min-w-0">
            <div class="member-mobile-list space-y-3 lg:hidden">
              <label
                class="flex min-h-11 cursor-pointer items-center gap-3 text-sm font-medium text-ink-700"
              >
                <input
                  type="checkbox"
                  :checked="allPageSelected"
                  :indeterminate="
                    selectedMembers.length > 0 && !allPageSelected
                  "
                  :disabled="loading"
                  class="h-4 w-4 rounded border-line text-brand-600"
                  @change="togglePageSelection"
                />
                {{ t('management.memberBulk.selectPage') }}
              </label>
              <MemberMobileCard
                v-for="member in members"
                :key="member.id"
                :member="member"
                :selected="selectedMembers.some((row) => row.id === member.id)"
                :disabled="loading"
                :role-label="roleLabel(member.role)"
                :status-label="statusLabel(member.status)"
                :last-login="formatLastLogin(member.last_login)"
                :actions="rowActions(member)"
                @select="toggleSelection(member)"
                @detail="openDetail(member)"
                @action="handleRowAction($event, member)"
              />
            </div>
            <table
              class="member-desktop-table hidden w-full min-w-[760px] divide-y divide-line lg:table"
            >
              <colgroup>
                <col class="w-11" />
                <col />
                <col class="w-[106px]" />
                <col class="w-[114px]" />
                <col class="w-[114px]" />
                <col class="w-[124px]" />
                <col class="w-[114px]" />
                <col class="w-[66px]" />
              </colgroup>
              <thead class="sticky top-0 z-10 bg-surface-sunken">
                <tr>
                  <th class="table-head">
                    <input
                      type="checkbox"
                      :checked="allPageSelected"
                      :indeterminate="
                        selectedMembers.length > 0 && !allPageSelected
                      "
                      :disabled="loading"
                      :aria-label="t('management.memberBulk.selectPage')"
                      @change="togglePageSelection"
                    />
                  </th>
                  <th class="table-head">
                    {{ t('management.memberUser') }}
                  </th>
                  <th class="table-head">{{ t('management.roles') }}</th>
                  <th class="table-head">{{ t('management.memberGroups') }}</th>
                  <th class="table-head">{{ t('management.memberStatus') }}</th>
                  <th class="table-head text-right">
                    {{ t('management.memberRuns') }}
                  </th>
                  <th class="table-head">
                    {{ t('management.memberLastLogin') }}
                  </th>
                  <th class="table-head text-right">
                    <span class="sr-only">{{ t('common.actions') }}</span>
                  </th>
                </tr>
              </thead>
              <tbody class="divide-y divide-line bg-surface">
                <tr
                  v-for="member in members"
                  :key="member.id"
                  class="cursor-pointer transition-colors hover:bg-line-soft"
                  @click="openDetail(member)"
                >
                  <td class="px-4 py-3" @click.stop>
                    <input
                      type="checkbox"
                      :checked="
                        selectedMembers.some((row) => row.id === member.id)
                      "
                      :disabled="loading"
                      :aria-label="
                        t('common.selectRow', {
                          name: member.display_name || member.username
                        })
                      "
                      @change="toggleSelection(member)"
                    />
                  </td>
                  <td class="px-4 py-3">
                    <div class="flex min-w-0 items-center gap-3">
                      <span
                        class="member-identity-avatar flex h-9 w-9 flex-shrink-0 items-center justify-center rounded-full"
                      >
                        {{ initial(member) }}
                      </span>
                      <div class="min-w-0">
                        <p class="truncate text-sm font-medium text-ink-900">
                          {{ member.display_name || member.username }}
                        </p>
                        <p class="member-email truncate text-xs text-ink-500">
                          {{ member.email }}
                        </p>
                      </div>
                    </div>
                  </td>
                  <td class="px-4 py-3">
                    <span
                      class="admin-member-role"
                      :class="{ 'is-admin': member.role === 'tenant_admin' }"
                    >
                      {{ roleLabel(member.role) }}
                    </span>
                  </td>
                  <td class="px-4 py-3 text-sm text-ink-600">
                    <span v-if="member.groups?.length">
                      {{ member.groups.map((group) => group.name).join('、') }}
                    </span>
                    <span v-else class="text-ink-400">—</span>
                  </td>
                  <td class="px-4 py-3">
                    <span class="admin-member-status">
                      <span
                        class="h-1.5 w-1.5 rounded-full"
                        :class="
                          member.status === 'active'
                            ? 'admin-status-active'
                            : member.status === 'locked'
                              ? 'admin-status-locked'
                              : member.status === 'disabled'
                                ? 'admin-status-disabled'
                                : 'admin-status-invited'
                        "
                      />
                      {{ statusLabel(member.status) }}
                    </span>
                  </td>
                  <td
                    class="px-4 py-3 text-right font-mono text-sm text-ink-700"
                  >
                    {{ member.runs_30d ?? 0 }}
                  </td>
                  <td class="px-4 py-3 text-sm text-ink-500">
                    {{ formatLastLogin(member.last_login) }}
                  </td>
                  <td class="px-4 py-3 text-right" @click.stop>
                    <RowActionMenu
                      :actions="rowActions(member)"
                      @select="handleRowAction($event, member)"
                    />
                  </td>
                </tr>
              </tbody>
            </table>
          </div>

          <PaginationBar
            prototype
            v-if="!loading"
            class="admin-list-pagination flex-shrink-0"
            v-model:page-size="pageSize"
            :current-page="currentPage"
            :total="totalCount"
            @page-size-change="handlePageSizeChange"
            @prev="goPrevPage"
            @next="goNextPage"
          />
        </div>
      </section>

      <BaseDrawer
        :show="!!detailMember"
        :title="detailMember?.display_name || detailMember?.username || ''"
        :subtitle="detailMember?.email || ''"
        width="md"
        @close="detailMember = null"
      >
        <div v-if="detailMember" class="space-y-6">
          <div
            v-if="detailMember.status === 'locked'"
            class="space-y-3 rounded-md border border-danger-100 bg-danger-50 p-3"
          >
            <p class="text-sm text-danger-700">
              {{
                t('management.memberLockReason', {
                  time: formatLockTime(detailMember.locked_until)
                })
              }}
            </p>
            <BaseButton
              variant="outline"
              :loading="unlockingMember === detailMember.id"
              :disabled="unlockingMember !== null"
              @click="unlockMember(detailMember)"
            >
              {{ t('management.unlockMember') }}
            </BaseButton>
          </div>
          <section class="space-y-3">
            <h3 class="text-sm font-semibold text-ink-900">
              {{ t('management.memberBasicInfo') }}
            </h3>
            <dl class="grid gap-3 sm:grid-cols-2">
              <div>
                <dt class="text-xs text-ink-500">
                  {{ t('management.memberStatus') }}
                </dt>
                <dd class="mt-1 text-sm text-ink-900">
                  {{ statusLabel(detailMember.status) }}
                </dd>
              </div>
              <div>
                <dt class="text-xs text-ink-500">
                  {{ t('management.memberLastLogin') }}
                </dt>
                <dd class="mt-1 text-sm text-ink-900">
                  {{ formatLastLogin(detailMember.last_login) }}
                </dd>
              </div>
              <div>
                <dt class="text-xs text-ink-500">
                  {{ t('management.dateJoined') }}
                </dt>
                <dd class="mt-1 text-sm text-ink-900">
                  {{ formatDate(detailMember.date_joined) }}
                </dd>
              </div>
              <div>
                <dt class="text-xs text-ink-500">
                  {{ t('management.memberRuns') }}
                </dt>
                <dd class="mt-1 text-sm text-ink-900">
                  {{ detailMember.runs_30d ?? 0 }}
                </dd>
              </div>
            </dl>
          </section>

          <section class="space-y-3">
            <h3 class="text-sm font-semibold text-ink-900">
              {{ t('management.memberRoleGroups') }}
            </h3>
            <label class="block">
              <span class="mb-1 block text-sm font-medium text-ink-700">
                {{ t('management.roles') }}
              </span>
              <BaseSelect v-model="detailForm.role" class="form-input">
                <option value="tenant_admin">
                  {{ t('management.tenantAdminRole') }}
                </option>
                <option value="member">
                  {{ t('management.memberRole') }}
                </option>
              </BaseSelect>
              <span class="mt-1 block text-xs text-ink-500">
                {{ t('management.memberRoleHint') }}
              </span>
            </label>
            <label class="block">
              <span class="mb-1 block text-sm font-medium text-ink-700">
                {{ t('management.memberStatus') }}
              </span>
              <BaseSelect
                v-model="detailForm.status"
                class="form-input"
                :disabled="
                  detailMember.is_self ||
                  detailMember.status === 'invited' ||
                  detailMember.requires_invitation
                "
              >
                <option
                  v-if="detailMember.status === 'invited'"
                  value="invited"
                >
                  {{ t('management.memberInvited') }}
                </option>
                <option value="active">
                  {{ t('management.memberActive') }}
                </option>
                <option value="disabled">
                  {{ t('management.memberDisabled') }}
                </option>
              </BaseSelect>
              <span
                v-if="
                  detailMember.status === 'invited' ||
                  detailMember.requires_invitation
                "
                class="mt-1 block text-xs text-ink-500"
              >
                {{ t('invitations.activationRequired') }}
              </span>
              <span
                v-if="detailMember.is_self"
                class="mt-1 block text-xs text-ink-500"
              >
                {{ t('management.cannotDisableSelf') }}
              </span>
            </label>
            <div>
              <span class="mb-1 block text-sm font-medium text-ink-700">
                {{ t('management.memberGroups') }}
              </span>
              <div
                v-if="groups.length"
                class="max-h-40 space-y-2 overflow-auto rounded-md border border-line bg-surface-sunken p-3"
              >
                <label
                  v-for="group in groups"
                  :key="group.id"
                  class="flex items-center gap-2 text-sm text-ink-700"
                >
                  <input
                    v-model="detailForm.groupIds"
                    type="checkbox"
                    :value="group.id"
                    class="h-4 w-4 rounded border-line text-brand-600 focus:ring-brand-500"
                  />
                  {{ group.name }}
                </label>
              </div>
              <p v-else class="text-sm text-ink-500">
                {{ t('management.noGroups') }}
              </p>
            </div>
          </section>

          <p v-if="detailError" class="text-sm text-danger-700">
            {{ detailError }}
          </p>
        </div>

        <template #footer>
          <div class="flex items-center justify-end gap-2">
            <BaseButton
              variant="outline"
              size="sm"
              :disabled="detailSaving"
              @click="detailMember = null"
            >
              {{ t('common.cancel') }}
            </BaseButton>
            <BaseButton
              variant="primary"
              size="sm"
              :loading="detailSaving"
              :disabled="detailSaving"
              @click="saveDetail"
            >
              {{ t('common.save') }}
            </BaseButton>
          </div>
        </template>
      </BaseDrawer>
    </div>
    <BaseModal
      :show="!!disableTarget"
      :title="t('management.disableMemberTitle')"
      @close="closeDisableDialog"
    >
      <p class="text-sm text-ink-700">
        {{ t('management.disableMemberKeysHint') }}
      </p>
      <p v-if="disableError" class="mt-3 text-sm text-danger-700">
        {{ disableError }}
      </p>
      <template #footer>
        <div class="flex flex-wrap justify-end gap-2">
          <BaseButton
            variant="outline"
            :disabled="disableSaving"
            @click="closeDisableDialog"
          >
            {{ t('common.cancel') }}
          </BaseButton>
          <BaseButton
            variant="outline"
            :disabled="disableSaving"
            @click="confirmDisable(false)"
          >
            {{ t('management.disableMemberOnly') }}
          </BaseButton>
          <BaseButton
            :loading="disableSaving"
            :disabled="disableSaving"
            @click="confirmDisable(true)"
          >
            {{ t('management.disableMemberWithKeys') }}
          </BaseButton>
        </div>
      </template>
    </BaseModal>
    <MemberBulkDialog
      :action="bulkAction"
      :targets="bulkTargets"
      :groups="groups"
      @close="closeBulk"
      @applied="bulkApplied"
    />
    <MemberDeleteDialog
      :targets="deleteTargets"
      @close="deleteTargets = []"
      @applied="membersDeleted"
    />
    <TenantInvitationDrawer
      :show="invitationOpen"
      :reinvite="reinvitationTarget"
      @close="closeInvitation"
      @saved="invitationSent"
    />
  </AdminLayout>
</template>

<script setup>
import BaseSelect from '@/components/ui/BaseSelect.vue'
import { Ban, Eye, KeyRound, Play, Plus, Search, Trash2 } from '@lucide/vue'
import { onBeforeUnmount, computed, onMounted, reactive, ref } from 'vue'
import { useI18n } from 'vue-i18n'

import AdminLayout from '@/admin/layout/AdminLayout.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseDrawer from '@/components/ui/BaseDrawer.vue'
import BaseLoading from '@/components/ui/BaseLoading.vue'
import BaseModal from '@/components/ui/BaseModal.vue'
import PaginationBar from '@/components/ui/PaginationBar.vue'
import RowActionMenu from '@/components/ui/RowActionMenu.vue'
import {
  getTenantGroups,
  getTenantMembers,
  resetTenantMemberPassword,
  resendTenantInvitation,
  unlockTenantMember,
  updateTenantMember
} from '@/api/tenant'
import TenantInvitationDrawer from './TenantInvitationDrawer.vue'
import MemberBulkDialog from './MemberBulkDialog.vue'
import MemberMobileCard from './MemberMobileCard.vue'
import MemberDeleteDialog from './MemberDeleteDialog.vue'
import { authApi } from '@/api/auth'
import { useUserStore } from '@/store/user'
import router from '@/router'
import { useToast } from '@/composables/useToast'

const { t } = useI18n()
const { showSuccess, showError } = useToast()

const bulkActions = ['role', 'groups', 'enable', 'disable', 'resend']
const selectedMembers = ref([])
const bulkAction = ref('')
const bulkTargets = ref([])
const deleteTargets = ref([])

function openDelete(targets) {
  if (loading.value) return
  deleteTargets.value = targets.map(
    ({ id, version, email, label, display_name }) => ({
      id,
      version,
      email,
      label: label || display_name || email || String(id)
    })
  )
}

async function membersDeleted(result) {
  if (result.deleted_ids.includes(detailMember.value?.id))
    detailMember.value = null
  await fetchMembers()
}
const members = ref([])
const allPageSelected = computed(
  () =>
    members.value.length > 0 &&
    members.value.every((member) =>
      selectedMembers.value.some((row) => row.id === member.id)
    )
)

function toggleSelection(member) {
  if (loading.value) return
  const index = selectedMembers.value.findIndex((row) => row.id === member.id)
  if (index >= 0) selectedMembers.value.splice(index, 1)
  else if (selectedMembers.value.length < 100)
    selectedMembers.value.push({
      id: member.id,
      version: member.version,
      email: member.email,
      label: member.display_name || member.username || member.email,
      is_self: member.is_self
    })
}

function togglePageSelection() {
  if (loading.value) return
  if (allPageSelected.value) selectedMembers.value = []
  else {
    selectedMembers.value = []
    members.value.forEach(toggleSelection)
  }
}

function openBulk(action) {
  if (!selectedMembers.value.length || loading.value) return
  bulkTargets.value = selectedMembers.value.map((row) => ({ ...row }))
  bulkAction.value = action
}

function closeBulk(refresh = false) {
  bulkAction.value = ''
  if (refresh) fetchMembers()
}

async function bulkApplied({ action, role, targets, result }) {
  selectedMembers.value = []
  const self = targets.find((target) => target.is_self)
  if (
    action === 'role' &&
    role === 'member' &&
    self &&
    result.items.some(
      (item) => item.id === self.id && item.status === 'changed'
    )
  ) {
    await leaveMemberManagement()
    return
  }
  await fetchMembers()
}

async function leaveMemberManagement() {
  const store = useUserStore()
  store.setUser({ ...store.user, tenant_role: 'member' })
  try {
    const response = await authApi.getProfile()
    store.setUser(response.data.data || response.data)
  } catch {
    showError(t('management.memberBulk.authorityChanged'))
  } finally {
    await router.replace('/lens/chat')
  }
}

const groups = ref([])
const loading = ref(false)
const error = ref('')
let memberRequest = 0
const totalCount = ref(0)
const currentPage = ref(1)
const pageSize = ref(20)
const invitationOpen = ref(false)
const reinvitationTarget = ref(null)

function closeInvitation(refresh = false) {
  invitationOpen.value = false
  if (refresh) fetchMembers()
}

function openInvitation(member = null) {
  reinvitationTarget.value = member ? JSON.parse(JSON.stringify(member)) : null
  invitationOpen.value = true
}
const resendingMember = ref(null)
const unlockingMember = ref(null)
const resettingMember = ref(null)
const searchInput = ref('')
const search = ref('')
const roleFilter = ref('')
const statusFilter = ref('')
const statusCounts = ref({})
const statusTabs = [
  { value: '', label: 'management.statusAll' },
  { value: 'active', label: 'management.memberActive' },
  { value: 'invited', label: 'management.memberInvited' },
  { value: 'locked', label: 'management.statusLocked' },
  { value: 'disabled', label: 'management.memberDisabled' }
]
let searchTimer
function queueSearch() {
  clearTimeout(searchTimer)
  searchTimer = setTimeout(applyFilters, 250)
}
function selectStatus(value) {
  statusFilter.value = value
  applyFilters()
}
const groupFilter = ref('')

const disableTarget = ref(null)
const disableSaving = ref(false)
const disableError = ref('')
const detailMember = ref(null)
const detailSaving = ref(false)
const detailError = ref('')
const detailForm = reactive({
  role: 'member',
  status: 'active',
  groupIds: []
})

const hasFilters = computed(
  () =>
    !!search.value ||
    !!roleFilter.value ||
    !!statusFilter.value ||
    !!groupFilter.value
)

function initial(member) {
  const value = member.display_name || member.username || ''
  return value.trim().charAt(0).toUpperCase() || 'U'
}

function roleLabel(role) {
  return role === 'tenant_admin'
    ? t('management.tenantAdminRole')
    : t('management.memberRole')
}

function statusLabel(status) {
  if (status === 'locked') return t('management.memberLocked')
  if (status === 'disabled') return t('management.memberDisabled')
  if (status === 'invited') return t('management.memberInvited')
  return t('management.memberActive')
}

function formatLockTime(value) {
  if (!value) return '—'
  const date = new Date(value)
  return Number.isNaN(date.getTime())
    ? '—'
    : date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
}

async function unlockMember(member) {
  if (unlockingMember.value !== null || member.status !== 'locked') return
  const target = { id: member.id, version: member.version }
  unlockingMember.value = target.id
  try {
    const updated = await unlockTenantMember(target.id, target.version)
    if (
      detailMember.value?.id === target.id &&
      detailMember.value.version === target.version
    ) {
      detailMember.value = {
        ...detailMember.value,
        ...updated,
        runs_30d: detailMember.value.runs_30d
      }
    }
    showSuccess(t('management.memberUnlocked'))
    await fetchMembers()
  } catch (err) {
    showError(
      t(
        err?.response?.status === 409
          ? 'management.memberUnlockConflict'
          : 'management.memberUnlockFailed'
      )
    )
  } finally {
    unlockingMember.value = null
  }
}

function formatDate(value) {
  if (!value) return '—'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleDateString()
}

async function resetMemberPassword(member) {
  if (resettingMember.value !== null || !member.can_reset_password) return
  const target = { id: member.id, version: member.version, email: member.email }
  resettingMember.value = target.id
  try {
    await resetTenantMemberPassword(target.id, target.version)
    showSuccess(t('management.memberResetSent', { email: target.email }))
  } catch (err) {
    const key =
      {
        400: 'memberResetUnavailable',
        403: 'memberResetForbidden',
        404: 'memberResetConflict',
        409: 'memberResetConflict',
        429: 'memberResetLimited'
      }[err?.response?.status] || 'memberResetFailed'
    showError(t(`management.${key}`))
  } finally {
    resettingMember.value = null
  }
}

function formatLastLogin(value) {
  if (!value) return t('management.memberNever')
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  const diff = Date.now() - date.getTime()
  const minutes = Math.floor(diff / 60000)
  if (minutes < 1) return t('management.memberJustNow')
  if (minutes < 60) return t('time.minutesAgo', { count: minutes })
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return t('time.hoursAgo', { count: hours })
  const days = Math.floor(hours / 24)
  if (days < 30) return t('time.daysAgo', { count: days })
  return date.toLocaleDateString()
}

function rowActions(member) {
  const actions = [
    {
      key: 'detail',
      label: t('common.viewDetails'),
      icon: Eye
    }
  ]
  const disabled =
    member.is_self ||
    (member.status === 'disabled' && member.requires_invitation)
  if (member.status === 'disabled' && member.requires_invitation)
    actions.push({
      key: 'reinvite',
      label: t('invitations.bulk.reinvite'),
      icon: Play
    })
  if (member.status === 'invited')
    actions.push({
      key: 'resend',
      label: t('invitations.resend'),
      icon: Play,
      disabled: resendingMember.value !== null
    })
  if (member.status === 'locked')
    actions.push({
      key: 'unlock',
      label: t('management.unlockMember'),
      icon: Play,
      disabled: unlockingMember.value !== null
    })
  actions.push({
    key: 'reset-password',
    label: t('management.memberResetPassword'),
    icon: KeyRound,
    disabled: resettingMember.value !== null || !member.can_reset_password,
    disabledReason: !member.can_reset_password
      ? t('management.memberResetUnavailable')
      : ''
  })
  actions.push({
    key: 'toggle',
    label:
      member.status === 'invited'
        ? t('invitations.revoke')
        : member.status === 'disabled'
          ? t('management.enable')
          : t('management.disable'),
    icon: member.status === 'disabled' ? Play : Ban,
    variant: member.status === 'disabled' ? undefined : 'danger',
    divider: true,
    disabled
  })
  actions.push({
    key: 'delete',
    label: t('management.memberDelete.title'),
    icon: Trash2,
    variant: 'danger',
    disabled: member.is_self,
    disabledReason: member.is_self
      ? t('management.memberDelete.reasons.self')
      : ''
  })
  return actions
}

async function handleRowAction(action, member) {
  if (action === 'delete') {
    if (!member.is_self) openDelete([member])
    return
  }
  if (action === 'reset-password') {
    await resetMemberPassword(member)
    return
  }
  if (action === 'unlock') {
    await unlockMember(member)
    return
  }
  if (action === 'reinvite') {
    openInvitation(member)
    return
  }
  if (action === 'resend') {
    if (resendingMember.value !== null) return
    resendingMember.value = member.id
    try {
      await resendTenantInvitation(member.id)
      showSuccess(t('invitations.sent'))
      await fetchMembers()
    } catch {
      showError(t('invitations.deliveryFailed'))
    } finally {
      resendingMember.value = null
    }
    return
  }
  if (action === 'detail') {
    openDetail(member)
    return
  }
  if (action === 'toggle') {
    await toggleMember(member)
  }
}

async function fetchMembers() {
  const requestId = ++memberRequest
  selectedMembers.value = []
  loading.value = true
  error.value = ''
  try {
    const params = {
      page: currentPage.value,
      page_size: pageSize.value
    }
    if (search.value) params.search = search.value
    if (roleFilter.value) params.role = roleFilter.value
    if (statusFilter.value) params.status = statusFilter.value
    if (groupFilter.value) params.group = groupFilter.value
    const data = await getTenantMembers(params)
    if (requestId !== memberRequest) return
    statusCounts.value = data?.status_counts || {}
    members.value = data?.results || []
    totalCount.value = data?.count || 0
  } catch (err) {
    if (requestId !== memberRequest) return
    error.value =
      err?.response?.data?.detail || t('management.memberLoadFailed')
  } finally {
    if (requestId === memberRequest) loading.value = false
  }
}

async function fetchGroups() {
  try {
    const data = await getTenantGroups()
    groups.value = data.results || []
  } catch {
    groups.value = []
  }
}

async function invitationSent(options = {}) {
  if (!options.keepOpen) invitationOpen.value = false
  statusFilter.value = 'invited'
  currentPage.value = 1
  search.value = ''
  searchInput.value = ''
  roleFilter.value = ''
  groupFilter.value = ''
  await fetchMembers()
}

function applyFilters() {
  clearTimeout(searchTimer)
  search.value = searchInput.value.trim()
  currentPage.value = 1
  fetchMembers()
}

function resetFilters() {
  clearTimeout(searchTimer)
  searchInput.value = ''
  search.value = ''
  roleFilter.value = ''
  statusFilter.value = ''
  groupFilter.value = ''
  currentPage.value = 1
  fetchMembers()
}

function goPrevPage() {
  if (currentPage.value <= 1) return
  currentPage.value -= 1
  fetchMembers()
}

function goNextPage() {
  if (currentPage.value * pageSize.value >= totalCount.value) return
  currentPage.value += 1
  fetchMembers()
}

function handlePageSizeChange(value) {
  pageSize.value = value
  currentPage.value = 1
  fetchMembers()
}

function openDetail(member) {
  detailMember.value = member
  detailError.value = ''
  detailForm.role = member.role
  detailForm.status = member.membership_status || member.status
  detailForm.groupIds = (member.groups || []).map((group) => group.id)
}

async function saveDetail() {
  if (!detailMember.value) return
  if (
    detailForm.status === 'disabled' &&
    detailMember.value.status !== 'disabled'
  ) {
    disableError.value = ''
    disableTarget.value = {
      member: detailMember.value,
      payload: {
        role: detailForm.role,
        status: 'disabled',
        group_ids: [...detailForm.groupIds]
      }
    }
    return
  }
  detailSaving.value = true
  detailError.value = ''
  try {
    const updated = await updateTenantMember(detailMember.value.id, {
      role: detailForm.role,
      status: detailForm.status,
      group_ids: detailForm.groupIds
    })
    showSuccess(t('management.memberSaved'))
    detailMember.value = null
    if (updated?.is_self && updated.role === 'member')
      await leaveMemberManagement()
    else await fetchMembers()
    return updated
  } catch (err) {
    const payload = err?.response?.data
    if (payload?.role === 'LAST_TENANT_ADMIN_REQUIRED') {
      detailError.value = t('management.lastAdminRequired')
    } else if (payload?.status === 'CANNOT_DISABLE_SELF') {
      detailError.value = t('management.cannotDisableSelf')
    } else {
      detailError.value = payload?.detail || t('management.memberSaveFailed')
    }
    showError(detailError.value)
  } finally {
    detailSaving.value = false
  }
}

function closeDisableDialog() {
  if (!disableSaving.value) disableTarget.value = null
}

async function confirmDisable(disableKeys) {
  if (!disableTarget.value || disableSaving.value) return
  const target = disableTarget.value
  disableSaving.value = true
  disableError.value = ''
  try {
    await updateTenantMember(target.member.id, {
      ...target.payload,
      disable_api_keys: disableKeys
    })
    disableTarget.value = null
    if (detailMember.value?.id === target.member.id) detailMember.value = null
    showSuccess(t('management.memberSaved'))
    await fetchMembers()
  } catch (err) {
    const payload = err?.response?.data
    disableError.value =
      payload?.role === 'LAST_TENANT_ADMIN_REQUIRED'
        ? t('management.lastAdminRequired')
        : payload?.status === 'CANNOT_DISABLE_SELF'
          ? t('management.cannotDisableSelf')
          : payload?.detail || t('management.memberSaveFailed')
    showError(disableError.value)
  } finally {
    disableSaving.value = false
  }
}

async function toggleMember(member) {
  const nextStatus = member.status === 'disabled' ? 'active' : 'disabled'
  if (nextStatus === 'disabled') {
    disableError.value = ''
    disableTarget.value = { member, payload: { status: nextStatus } }
    return
  }
  try {
    await updateTenantMember(member.id, { status: nextStatus })
    showSuccess(t('management.memberSaved'))
    await fetchMembers()
  } catch (err) {
    const payload = err?.response?.data
    showError(
      payload?.status === 'CANNOT_DISABLE_SELF'
        ? t('management.cannotDisableSelf')
        : t('management.memberSaveFailed')
    )
  }
}

onBeforeUnmount(() => clearTimeout(searchTimer))

onMounted(() => {
  fetchGroups()
  fetchMembers()
})
</script>
