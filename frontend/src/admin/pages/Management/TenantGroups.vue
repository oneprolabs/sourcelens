<template>
  <AdminLayout>
    <div class="admin-list-page">
      <section class="admin-list-surface">
        <div class="admin-page-heading">
          <div class="admin-heading-copy">
            <h1>
              {{ t('management.tenantGroupTitle') }}
              <span class="admin-heading-count">{{ groups.length }}</span>
            </h1>
            <p>{{ t('management.tenantGroupSubtitle') }}</p>
          </div>
          <BaseButton @click="openCreate"
            ><Plus :size="14" aria-hidden="true" />{{
              t('management.newTenantGroup')
            }}</BaseButton
          >
        </div>
        <div class="admin-list-panel">
          <form class="admin-list-toolbar" @submit.prevent="fetchGroups">
            <label class="admin-toolbar-search">
              <Search :size="14" aria-hidden="true" />
              <input
                v-model="searchInput"
                type="search"
                :aria-label="t('common.search')"
                :placeholder="t('management.groupSearchPlaceholder')"
                @input="queueSearch"
              />
            </label>
            <BaseButton
              v-if="searchInput"
              variant="ghost"
              size="sm"
              @click="resetSearch"
              >{{ t('management.resetFilters') }}</BaseButton
            >
          </form>
          <div v-if="selectedGroups.length" class="admin-bulk-toolbar">
            <span>{{
              t('common.selectedCount', { count: selectedGroups.length })
            }}</span>
            <BaseButton
              variant="danger-outline"
              size="sm"
              :disabled="loading"
              @click="openDelete(selectedGroups)"
              >{{ t('management.groupBulkDelete') }}</BaseButton
            >
            <BaseButton
              variant="ghost"
              size="sm"
              @click="selectedGroups = []"
              >{{ t('common.clearSelection') }}</BaseButton
            >
          </div>
          <BaseLoading v-if="loading && !groups.length" />

          <div
            v-else-if="error"
            class="flex min-h-0 flex-1 items-center justify-center rounded-lg border border-line bg-surface-sunken py-16 text-center"
          >
            <p class="text-sm font-medium text-danger-700">{{ error }}</p>
          </div>

          <div
            v-else-if="!groups.length"
            class="flex min-h-0 flex-1 items-center justify-center rounded-lg border border-line bg-surface-sunken py-16 text-center"
          >
            <div>
              <p class="text-sm font-medium text-ink-700">
                {{ t('management.groupNoGroups') }}
              </p>
              <p class="mt-1 text-sm text-ink-500">
                {{ t('management.groupNoGroupsHint') }}
              </p>
            </div>
          </div>

          <div v-else class="admin-list-table relative min-h-0">
            <table
              class="admin-group-desktop-table w-full divide-y divide-line"
            >
              <colgroup>
                <col class="w-11" />
                <col />
                <col class="w-[94px]" />
                <col class="w-[124px]" />
                <col class="w-[114px]" />
                <col class="w-[66px]" />
              </colgroup>
              <thead class="sticky top-0 z-10 bg-surface-sunken">
                <tr>
                  <th class="table-head">
                    <input
                      type="checkbox"
                      :checked="allGroupsSelected"
                      :disabled="loading || pagedGroups.length > 100"
                      :aria-label="t('management.groupSelectPage')"
                      @change="toggleAll"
                    />
                  </th>
                  <th class="table-head">{{ t('management.groupName') }}</th>
                  <th class="table-head">
                    {{ t('management.groupMembers') }}
                  </th>
                  <th class="table-head">
                    {{ t('management.groupAssistants') }}
                  </th>
                  <th class="table-head">
                    {{ t('management.groupCreatedAt') }}
                  </th>
                  <th class="table-head">
                    <span class="sr-only">{{ t('common.actions') }}</span>
                  </th>
                </tr>
              </thead>
              <tbody class="divide-y divide-line bg-surface">
                <tr
                  v-for="group in pagedGroups"
                  :key="group.uuid"
                  class="cursor-pointer transition-colors hover:bg-line-soft"
                  @click="openDetail(group)"
                >
                  <td class="px-4 py-3" @click.stop>
                    <input
                      type="checkbox"
                      :checked="
                        selectedGroups.some((row) => row.uuid === group.uuid)
                      "
                      :disabled="
                        loading ||
                        (selectedGroups.length >= 100 &&
                          !selectedGroups.some(
                            (row) => row.uuid === group.uuid
                          ))
                      "
                      :aria-label="t('common.selectRow', { name: group.name })"
                      @change="toggleGroup(group)"
                    />
                  </td>
                  <td class="px-4 py-3">
                    <div class="flex min-w-0 items-center gap-3">
                      <span class="admin-group-thumb"
                        ><Layers :size="16" aria-hidden="true"
                      /></span>
                      <div class="min-w-0">
                        <p class="truncate text-sm font-medium text-ink-900">
                          {{ group.name }}
                        </p>
                        <p
                          v-if="group.description"
                          class="truncate text-xs text-ink-500"
                        >
                          {{ group.description }}
                        </p>
                      </div>
                    </div>
                  </td>
                  <td class="px-4 py-3 font-mono text-sm text-ink-500">
                    {{
                      t('management.groupMemberTotal', {
                        count: group.member_count ?? 0
                      })
                    }}
                  </td>
                  <td class="px-4 py-3 font-mono text-sm text-ink-500">
                    {{
                      t('management.groupAssistantTotal', {
                        count: group.assistant_count ?? 0
                      })
                    }}
                  </td>
                  <td class="px-4 py-3 text-sm text-ink-500">
                    {{ formatDate(group.created_at) }}
                  </td>
                  <td class="px-4 py-3 text-right" @click.stop>
                    <RowActionMenu
                      :actions="groupActions"
                      @select="handleGroupAction($event, group)"
                    />
                  </td>
                </tr>
              </tbody>
            </table>
            <div class="admin-group-mobile-list">
              <article
                v-for="group in pagedGroups"
                :key="group.uuid"
                class="admin-group-card"
              >
                <input
                  type="checkbox"
                  class="admin-group-card-check"
                  :checked="
                    selectedGroups.some((row) => row.uuid === group.uuid)
                  "
                  :disabled="
                    loading ||
                    (selectedGroups.length >= 100 &&
                      !selectedGroups.some((row) => row.uuid === group.uuid))
                  "
                  :aria-label="t('common.selectRow', { name: group.name })"
                  @change="toggleGroup(group)"
                />
                <button
                  type="button"
                  class="admin-group-card-heading"
                  @click="openDetail(group)"
                >
                  <span class="admin-group-thumb"
                    ><Layers :size="16" aria-hidden="true" /></span
                  ><span class="min-w-0 break-words text-sm font-semibold">{{
                    group.name
                  }}</span>
                </button>
                <dl>
                  <div class="admin-group-card-meta">
                    <dt>{{ t('management.groupMembers') }}</dt>
                    <dd>
                      {{
                        t('management.groupMemberTotal', {
                          count: group.member_count ?? 0
                        })
                      }}
                    </dd>
                  </div>
                  <div class="admin-group-card-meta">
                    <dt>{{ t('management.groupCreatedAt') }}</dt>
                    <dd>{{ formatDate(group.created_at) }}</dd>
                  </div>
                </dl>
                <div class="admin-group-card-actions">
                  <RowActionMenu
                    :label="`${t('common.actions')} · ${group.name}`"
                    :actions="groupActions"
                    @select="handleGroupAction($event, group)"
                  />
                </div>
              </article>
            </div>
          </div>
          <PaginationBar
            prototype
            :current-page="currentPage"
            :page-size="pageSize"
            :total="groups.length"
            @prev="currentPage--"
            @next="currentPage++"
            @update:page-size="changeGroupPageSize"
          />
        </div>
      </section>

      <GroupDeleteDialog
        :targets="deleteTargets"
        @close="deleteTargets = []"
        @applied="groupsDeleted"
      />
      <BaseDrawer
        :show="showCreate"
        :title="t('management.newTenantGroup')"
        @close="closeCreate"
      >
        <form class="space-y-4" @submit.prevent="createGroup">
          <label class="block">
            <span class="mb-1 block text-sm font-medium text-ink-700">
              {{ t('management.groupName') }}
              <span class="text-danger-600">*</span>
            </span>
            <input
              v-model="createForm.name"
              type="text"
              class="form-input"
              maxlength="100"
            />
          </label>
          <label class="block">
            <span class="mb-1 block text-sm font-medium text-ink-700">
              {{ t('management.groupDescription') }}
            </span>
            <textarea
              v-model="createForm.description"
              rows="3"
              class="form-input"
              maxlength="500"
            />
          </label>
          <p v-if="createError" class="text-sm text-danger-700">
            {{ createError }}
          </p>
        </form>
        <template #footer>
          <div class="flex justify-end gap-2">
            <BaseButton variant="outline" size="sm" @click="closeCreate">
              {{ t('common.cancel') }}
            </BaseButton>
            <BaseButton
              variant="primary"
              size="sm"
              :loading="createSaving"
              :disabled="createSaving"
              @click="createGroup"
            >
              {{ t('management.groupCreate') }}
            </BaseButton>
          </div>
        </template>
      </BaseDrawer>

      <BaseDrawer
        :show="!!detailGroup"
        :title="
          detailMode === 'settings'
            ? t('management.groupRename')
            : t('management.groupManageMembers')
        "
        :subtitle="detailGroup?.name || ''"
        width="md"
        @close="closeDetail"
      >
        <BaseLoading v-if="detailLoading" />
        <div v-else-if="detailGroup" class="space-y-6">
          <p v-if="detailError" role="alert" class="text-sm text-danger-700">
            {{ detailError }}
          </p>
          <section v-if="detailMode === 'settings'" class="space-y-3">
            <h3 class="text-sm font-semibold text-ink-900">
              {{ t('management.groupSettings') }}
            </h3>
            <label class="block">
              <span class="mb-1 block text-sm font-medium text-ink-700">
                {{ t('management.groupName') }}
              </span>
              <input
                v-model="detailForm.name"
                type="text"
                class="form-input"
                maxlength="100"
              />
            </label>
            <label class="block">
              <span class="mb-1 block text-sm font-medium text-ink-700">
                {{ t('management.groupDescription') }}
              </span>
              <textarea
                v-model="detailForm.description"
                rows="2"
                class="form-input"
                maxlength="500"
              />
            </label>
          </section>

          <section v-if="detailMode === 'members'" class="space-y-3">
            <div class="flex items-center justify-between">
              <h3 class="text-sm font-semibold text-ink-900">
                {{ t('management.groupMembers') }}
              </h3>
              <span class="text-xs text-ink-500">
                {{
                  t('management.groupMemberCount', {
                    count: detailMembers.length
                  })
                }}
              </span>
            </div>
            <div class="space-y-2">
              <div
                v-for="member in detailMembers"
                :key="member.id"
                class="flex items-center gap-3 rounded-md border border-line px-3 py-2"
              >
                <span
                  class="flex h-8 w-8 items-center justify-center rounded-full bg-brand-50 text-xs font-semibold text-brand-700"
                >
                  {{ memberInitial(member) }}
                </span>
                <div class="min-w-0 flex-1">
                  <p class="truncate text-sm font-medium text-ink-900">
                    {{ member.display_name || member.username }}
                  </p>
                  <p class="truncate text-xs text-ink-500">
                    {{ member.email }}
                  </p>
                </div>
                <BaseButton
                  variant="outline"
                  size="sm"
                  :disabled="detailSaving"
                  @click="removeMember(member)"
                >
                  {{ t('management.groupMemberRemove') }}
                </BaseButton>
              </div>
              <p v-if="!detailMembers.length" class="text-sm text-ink-500">
                {{ t('management.groupNoMembers') }}
              </p>
            </div>

            <div class="rounded-md border border-line bg-surface-sunken p-3">
              <p class="mb-2 text-xs font-medium text-ink-600">
                {{ t('management.groupAddMember') }}
              </p>
              <div class="flex gap-2">
                <BaseSelect v-model="memberToAdd" class="form-input">
                  <option value="">
                    {{ t('management.groupSelectMember') }}
                  </option>
                  <option
                    v-for="member in availableMembers"
                    :key="member.id"
                    :value="String(member.id)"
                  >
                    {{ member.display_name || member.username }} ·
                    {{ member.email }}
                  </option>
                </BaseSelect>
                <BaseButton
                  variant="primary"
                  size="sm"
                  :disabled="!memberToAdd || detailSaving"
                  @click="addMember"
                >
                  {{ t('common.add') }}
                </BaseButton>
              </div>
              <p
                v-if="!availableMembers.length"
                class="mt-2 text-xs text-ink-500"
              >
                {{ t('management.groupNoAvailableMembers') }}
              </p>
            </div>
          </section>

          <section
            v-if="detailMode === 'settings'"
            class="rounded-md border border-danger-200 bg-danger-50 px-3 py-3"
          >
            <p class="text-sm font-medium text-danger-800">
              {{ t('management.groupDelete') }}
            </p>
            <p class="mt-1 text-xs text-danger-700">
              {{
                t('management.groupDeleteHint', {
                  members: detailGroup.member_count ?? 0,
                  assistants: detailGroup.assistant_count ?? 0
                })
              }}
            </p>
            <BaseButton
              class="mt-2"
              variant="danger-outline"
              size="sm"
              :disabled="detailSaving"
              @click="openDelete([detailGroup])"
            >
              {{ t('management.groupDelete') }}
            </BaseButton>
          </section>
        </div>

        <template #footer>
          <div class="flex justify-end gap-2">
            <BaseButton variant="outline" size="sm" @click="closeDetail">
              {{
                t(detailMode === 'members' ? 'common.close' : 'common.cancel')
              }}
            </BaseButton>
            <BaseButton
              v-if="detailMode === 'settings'"
              variant="primary"
              size="sm"
              :loading="detailSaving"
              :disabled="detailSaving || detailLoading"
              @click="saveDetail"
            >
              {{ t('common.save') }}
            </BaseButton>
          </div>
        </template>
      </BaseDrawer>
    </div>
  </AdminLayout>
</template>

<script setup>
import BaseSelect from '@/components/ui/BaseSelect.vue'
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'

import { Layers, Plus, Search } from '@lucide/vue'
import RowActionMenu from '@/components/ui/RowActionMenu.vue'
import GroupDeleteDialog from './GroupDeleteDialog.vue'
import AdminLayout from '@/admin/layout/AdminLayout.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseDrawer from '@/components/ui/BaseDrawer.vue'
import BaseLoading from '@/components/ui/BaseLoading.vue'
import PaginationBar from '@/components/ui/PaginationBar.vue'
import {
  addTenantGroupMember,
  createTenantGroup,
  getTenantGroup,
  getTenantGroups,
  getTenantMembers,
  removeTenantGroupMember,
  updateTenantGroup
} from '@/api/tenant'
import { useToast } from '@/composables/useToast'

const { t } = useI18n()
const { showSuccess } = useToast()

const groups = ref([])
const currentPage = ref(1)
const pageSize = ref(20)
const pagedGroups = computed(() =>
  groups.value.slice(
    (currentPage.value - 1) * pageSize.value,
    currentPage.value * pageSize.value
  )
)
const loading = ref(false)
const error = ref('')
const searchInput = ref('')
const selectedGroups = ref([])
const deleteTargets = ref([])
let listRequest = 0
let detailRequest = 0
let searchTimer
const allGroupsSelected = computed(
  () =>
    pagedGroups.value.length > 0 &&
    pagedGroups.value.every((group) =>
      selectedGroups.value.some((row) => row.uuid === group.uuid)
    )
)
const groupActions = computed(() => [
  { key: 'members', label: t('management.groupManageMembers') },
  { key: 'rename', label: t('management.groupRename') },
  {
    key: 'delete',
    label: t('management.groupDelete'),
    variant: 'danger',
    divider: true
  }
])
function changeGroupPageSize(value) {
  pageSize.value = Number(value)
  currentPage.value = 1
}

function queueSearch() {
  clearTimeout(searchTimer)
  searchTimer = setTimeout(fetchGroups, 250)
}
function toggleGroup(group) {
  if (loading.value) return
  if (selectedGroups.value.some((row) => row.uuid === group.uuid))
    selectedGroups.value = selectedGroups.value.filter(
      (row) => row.uuid !== group.uuid
    )
  else if (selectedGroups.value.length < 100)
    selectedGroups.value.push({ ...group })
}
function toggleAll() {
  if (!loading.value && pagedGroups.value.length <= 100)
    selectedGroups.value = allGroupsSelected.value
      ? []
      : pagedGroups.value.map((group) => ({ ...group }))
}
function handleGroupAction(action, group) {
  if (action === 'delete') openDelete([group])
  else openDetail(group, action === 'rename' ? 'settings' : 'members')
}
function openDelete(targets) {
  if (!loading.value && !detailSaving.value && !detailLoading.value)
    deleteTargets.value = targets.map((row) => ({ ...row }))
}
function groupsDeleted(result) {
  if (result.deleted_uuids.includes(detailGroup.value?.uuid)) closeDetail()
  fetchGroups()
}

watch([currentPage, pageSize], () => {
  selectedGroups.value = []
})

const showCreate = ref(false)
const createSaving = ref(false)
const createError = ref('')
const createForm = reactive({ name: '', description: '' })

const detailGroup = ref(null)
const detailMode = ref('members')
const detailLoading = ref(false)
const detailMembers = ref([])
const detailSaving = ref(false)
const detailError = ref('')
const detailForm = reactive({ name: '', description: '' })
const tenantMembers = ref([])
let tenantMemberRequest = 0
const memberToAdd = ref('')

function memberInitial(member) {
  const value = member.display_name || member.username || ''
  return value.trim().charAt(0).toUpperCase() || 'U'
}

function formatDate(value) {
  if (!value) return '—'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleDateString()
}

async function fetchGroups() {
  clearTimeout(searchTimer)
  const request = ++listRequest
  selectedGroups.value = []
  loading.value = true
  error.value = ''
  try {
    const data = await getTenantGroups({
      search: searchInput.value.trim() || undefined
    })
    if (request !== listRequest) return
    groups.value = data?.results || []
    currentPage.value = 1
  } catch (err) {
    if (request !== listRequest) return
    error.value = err?.response?.data?.detail || t('management.groupLoadFailed')
  } finally {
    if (request === listRequest) loading.value = false
  }
}

function resetSearch() {
  searchInput.value = ''
  fetchGroups()
}

function openCreate() {
  if (createSaving.value) return
  createForm.name = ''
  createForm.description = ''
  createError.value = ''
  showCreate.value = true
}

function closeCreate() {
  if (createSaving.value) return
  showCreate.value = false
}

async function createGroup() {
  if (createSaving.value) return
  if (!createForm.name.trim()) {
    createError.value = t('management.groupNameRequired')
    return
  }
  createSaving.value = true
  createError.value = ''
  try {
    await createTenantGroup({
      name: createForm.name.trim(),
      description: createForm.description
    })
    showSuccess(t('management.groupCreated'))
    showCreate.value = false
    await fetchGroups()
  } catch (err) {
    const code = err?.response?.data?.name
    createError.value =
      code === 'GROUP_NAME_TAKEN'
        ? t('management.groupNameTaken')
        : t('management.groupSaveFailed')
  } finally {
    createSaving.value = false
  }
}

async function fetchTenantMembers() {
  const request = ++tenantMemberRequest
  try {
    const data = await getTenantMembers({ page_size: 100 })
    if (request === tenantMemberRequest)
      tenantMembers.value = data?.results || []
  } catch {
    if (request === tenantMemberRequest) tenantMembers.value = []
  }
}

async function openDetail(group, mode = 'members') {
  if (detailSaving.value) return
  detailMode.value = mode
  detailRequest++
  detailMembers.value = []
  detailError.value = ''
  detailGroup.value = group
  detailForm.name = group.name
  detailForm.description = group.description || ''
  memberToAdd.value = ''
  await Promise.all([loadDetail(group.uuid), fetchTenantMembers()])
}

async function loadDetail(groupUuid) {
  const request = detailRequest
  detailLoading.value = true
  try {
    const data = await getTenantGroup(groupUuid)
    if (request !== detailRequest || detailGroup.value?.uuid !== groupUuid)
      return
    detailGroup.value = data
    detailMembers.value = data?.members || []
  } catch (err) {
    if (request !== detailRequest || detailGroup.value?.uuid !== groupUuid)
      return
    detailError.value =
      err?.response?.data?.detail || t('management.groupLoadFailed')
  } finally {
    if (request === detailRequest) detailLoading.value = false
  }
}

function closeDetail() {
  if (detailSaving.value) return
  detailRequest++
  detailGroup.value = null
  detailMembers.value = []
  detailLoading.value = false
}

async function saveDetail() {
  if (!detailGroup.value || detailSaving.value || detailLoading.value) return
  if (!detailForm.name.trim()) {
    detailError.value = t('management.groupNameRequired')
    return
  }
  detailSaving.value = true
  detailError.value = ''
  try {
    const updated = await updateTenantGroup(detailGroup.value.uuid, {
      name: detailForm.name.trim(),
      description: detailForm.description
    })
    detailGroup.value = { ...detailGroup.value, ...updated }
    showSuccess(t('management.groupSaved'))
    await fetchGroups()
  } catch (err) {
    const code = err?.response?.data?.name
    detailError.value =
      code === 'GROUP_NAME_TAKEN'
        ? t('management.groupNameTaken')
        : t('management.groupSaveFailed')
  } finally {
    detailSaving.value = false
  }
}

async function addMember() {
  if (
    !detailGroup.value ||
    !memberToAdd.value ||
    detailSaving.value ||
    detailLoading.value
  )
    return
  const uuid = detailGroup.value.uuid
  const userId = Number(memberToAdd.value)
  detailSaving.value = true
  detailError.value = ''
  try {
    await addTenantGroupMember(uuid, userId)
    memberToAdd.value = ''
    await loadDetail(uuid)
    await fetchGroups()
  } catch {
    detailError.value = t('management.groupSaveFailed')
  } finally {
    detailSaving.value = false
  }
}

async function removeMember(member) {
  if (!detailGroup.value || detailSaving.value || detailLoading.value) return
  const uuid = detailGroup.value.uuid
  detailSaving.value = true
  detailError.value = ''
  try {
    await removeTenantGroupMember(uuid, member.id)
    await loadDetail(uuid)
    await fetchGroups()
  } catch {
    detailError.value = t('management.groupSaveFailed')
  } finally {
    detailSaving.value = false
  }
}

const availableMembers = ref([])

onBeforeUnmount(() => {
  clearTimeout(searchTimer)
  listRequest++
  detailRequest++
})

onMounted(async () => {
  await fetchGroups()
  await fetchTenantMembers()
})

watch(
  [tenantMembers, detailMembers],
  () => {
    const currentIds = new Set(detailMembers.value.map((item) => item.id))
    availableMembers.value = tenantMembers.value.filter(
      (item) => item.status === 'active' && !currentIds.has(item.id)
    )
  },
  { immediate: true }
)
</script>
