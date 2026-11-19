<template>
  <AdminLayout>
    <div class="flex h-full min-h-0 max-w-full flex-col gap-4 py-4">
      <section
        class="flex max-h-full min-h-0 w-full flex-col overflow-hidden rounded-lg border border-line bg-surface shadow-sm"
      >
        <div class="flex-shrink-0 border-b border-line px-5 py-4">
          <div class="flex items-start justify-between gap-3">
            <div class="min-w-0">
              <h1 class="text-xl font-semibold text-ink-900">
                {{ t('management.platformMembersTitle') }}
              </h1>
              <p class="mt-2 max-w-3xl text-sm leading-6 text-ink-500">
                {{ t('management.platformMembersSubtitle') }}
              </p>
            </div>
            <span class="inline-flex flex-shrink-0 rounded-md border border-line bg-surface-sunken px-2 py-1 text-xs text-ink-500">
              {{ t('management.platformMembersCount', { count: totalCount }) }}
            </span>
          </div>
        </div>

        <div class="flex min-h-0 flex-1 flex-col px-5 py-4">
          <form class="mb-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-[minmax(0,1fr)_200px_160px_auto_auto]" @submit.prevent="applyFilters">
            <label class="min-w-0">
              <span class="mb-1 block text-sm font-medium text-ink-700">{{ t('common.search') }}</span>
              <input
                v-model="searchInput"
                type="text"
                class="form-input"
                :placeholder="t('management.platformMembersSearch')"
              />
            </label>
            <label>
              <span class="mb-1 block text-sm font-medium text-ink-700">{{ t('management.memberTenant') }}</span>
              <BaseSelect v-model="tenantFilter" class="form-input">
                <option value="">{{ t('management.auditAllTenants') }}</option>
                <option v-for="tenant in tenants" :key="tenant.uuid" :value="tenant.uuid">
                  {{ tenant.name }}
                </option>
              </BaseSelect>
            </label>
            <label>
              <span class="mb-1 block text-sm font-medium text-ink-700">{{ t('management.memberRoleColumn') }}</span>
              <BaseSelect v-model="roleFilter" class="form-input">
                <option value="">{{ t('management.allRoles') }}</option>
                <option value="tenant_admin">{{ t('management.memberRoleTenantAdmin') }}</option>
                <option value="member">{{ t('management.memberRole') }}</option>
              </BaseSelect>
            </label>
            <BaseButton type="submit" :loading="loading">{{ t('common.search') }}</BaseButton>
            <BaseButton type="button" variant="outline" :disabled="!hasFilters" @click="resetFilters">
              {{ t('management.resetFilters') }}
            </BaseButton>
          </form>

          <div class="mb-3 flex flex-wrap gap-1 rounded-lg border border-line bg-surface-sunken p-1">
            <button
              v-for="segment in segments"
              :key="segment.value"
              type="button"
              class="rounded-md px-3 py-1.5 text-sm font-medium"
              :class="statusFilter === segment.value ? 'bg-surface text-ink-900 shadow-sm' : 'text-ink-500 hover:text-ink-700'"
              @click="selectStatus(segment.value)"
            >
              {{ segment.label }} · {{ segment.count }}
            </button>
          </div>

          <BaseLoading v-if="loading && !rows.length" />

          <div v-else-if="error" class="flex min-h-0 flex-1 items-center justify-center rounded-lg border border-line bg-surface-sunken py-16 text-center">
            <p class="text-sm font-medium text-danger-700">{{ error }}</p>
          </div>

          <div v-else-if="!rows.length" class="flex min-h-0 flex-1 items-center justify-center rounded-lg border border-line bg-surface-sunken py-16 text-center">
            <p class="text-sm font-medium text-ink-500">{{ t('management.platformMembersEmpty') }}</p>
          </div>

          <div v-else class="relative min-h-0 flex-1 overflow-auto rounded-lg border border-line bg-surface">
            <table class="w-full min-w-[900px] divide-y divide-line">
              <thead class="sticky top-0 z-10 bg-surface-sunken">
                <tr>
                  <th class="table-head">{{ t('management.platformMembersMember') }}</th>
                  <th class="table-head">{{ t('management.memberTenant') }}</th>
                  <th class="table-head">{{ t('management.memberRoleColumn') }}</th>
                  <th class="table-head">{{ t('management.memberStatus') }}</th>
                  <th class="table-head text-right">{{ t('management.memberApiKeys') }}</th>
                  <th class="table-head">{{ t('management.memberLastLogin') }}</th>
                  <th class="table-head text-right">{{ t('common.actions') }}</th>
                </tr>
              </thead>
              <tbody class="divide-y divide-line bg-surface">
                <tr v-for="row in rows" :key="`${row.id}-${row.tenant.uuid}`" class="hover:bg-line-soft">
                  <td class="px-4 py-3">
                    <div class="flex items-center gap-3">
                      <span class="flex h-9 w-9 items-center justify-center rounded-full bg-brand-50 text-sm font-semibold text-brand-700">
                        {{ initial(row) }}
                      </span>
                      <div class="min-w-0">
                        <p class="truncate text-sm font-medium text-ink-900">{{ row.name }}</p>
                        <p class="truncate text-xs text-ink-500">{{ row.email || row.username }}</p>
                      </div>
                    </div>
                  </td>
                  <td class="px-4 py-3 text-sm text-ink-700">{{ row.tenant.name }}</td>
                  <td class="px-4 py-3">
                    <span
                      class="inline-flex rounded-full px-2 py-0.5 text-xs font-medium"
                      :class="row.role === 'tenant_admin' ? 'bg-brand-50 text-brand-700' : 'bg-surface-sunken text-ink-600'"
                    >
                      {{ roleLabel(row.role) }}
                    </span>
                  </td>
                  <td class="px-4 py-3">
                    <span
                      class="inline-flex rounded-full px-2 py-0.5 text-xs font-medium"
                      :class="statusClass(row.status)"
                    >
                      {{ statusLabel(row.status) }}
                    </span>
                  </td>
                  <td class="px-4 py-3 text-right font-mono text-sm text-ink-700">
                    {{ row.role === 'tenant_admin' ? t('management.memberApiKeyCount', { count: row.api_key_count }) : '—' }}
                  </td>
                  <td class="px-4 py-3 text-sm text-ink-500">
                    {{ row.last_login ? formatDate(row.last_login) : t('management.memberNeverLogin') }}
                  </td>
                  <td class="px-4 py-3 text-right">
                    <div class="flex justify-end gap-2">
                      <BaseButton size="sm" variant="outline" @click="openOwnership(row)">
                        {{ t('management.memberViewOwnership') }}
                      </BaseButton>
                      <BaseButton
                        size="sm"
                        variant="outline"
                        :loading="startingTenantId === row.tenant.uuid"
                        :disabled="!!startingTenantId"
                        @click="enterTenant(row.tenant.uuid)"
                      >
                        {{ t('management.enterTenant') }}
                      </BaseButton>
                    </div>
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

      <BaseDrawer
        :show="!!ownership"
        :title="ownership?.name || ''"
        :subtitle="ownership?.email || ownership?.username || ''"
        width="md"
        @close="ownership = null"
      >
        <div v-if="ownership" class="space-y-4">
          <p class="rounded-md border border-line bg-surface-sunken px-4 py-3 text-sm leading-6 text-ink-600">
            {{ t('management.memberOwnershipHint') }}
          </p>
          <dl class="divide-y divide-line rounded-md border border-line">
            <div class="flex justify-between gap-3 px-4 py-2.5 text-sm">
              <dt class="text-ink-500">{{ t('management.memberTenant') }}</dt>
              <dd class="font-medium text-ink-900">{{ ownership.tenant.name }}</dd>
            </div>
            <div class="flex justify-between gap-3 px-4 py-2.5 text-sm">
              <dt class="text-ink-500">{{ t('management.memberRoleColumn') }}</dt>
              <dd class="text-ink-900">{{ roleLabel(ownership.role) }}</dd>
            </div>
            <div class="flex justify-between gap-3 px-4 py-2.5 text-sm">
              <dt class="text-ink-500">{{ t('management.memberStatus') }}</dt>
              <dd class="text-ink-900">{{ statusLabel(ownership.status) }}</dd>
            </div>
            <div class="flex justify-between gap-3 px-4 py-2.5 text-sm">
              <dt class="text-ink-500">{{ t('management.memberLastLogin') }}</dt>
              <dd class="text-ink-900">
                {{ ownership.last_login ? formatDate(ownership.last_login) : t('management.memberNeverLogin') }}
              </dd>
            </div>
            <div v-if="ownership.role === 'tenant_admin'" class="flex justify-between gap-3 px-4 py-2.5 text-sm">
              <dt class="text-ink-500">{{ t('management.memberApiKeys') }}</dt>
              <dd class="text-ink-900">{{ t('management.memberApiKeyCount', { count: ownership.api_key_count }) }}</dd>
            </div>
          </dl>
        </div>
        <template #footer>
          <div class="flex justify-end gap-2">
            <BaseButton variant="outline" size="sm" @click="ownership = null">
              {{ t('common.close') }}
            </BaseButton>
            <BaseButton
              size="sm"
              variant="primary"
              :loading="startingTenantId === ownership.tenant.uuid"
              :disabled="!!startingTenantId"
              @click="enterTenant(ownership.tenant.uuid)"
            >
              {{ t('management.enterTenant') }}
            </BaseButton>
          </div>
        </template>
      </BaseDrawer>
    </div>
  </AdminLayout>
</template>

<script setup>
import BaseSelect from '@/components/ui/BaseSelect.vue'
import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'

import AdminLayout from '@/admin/layout/AdminLayout.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseDrawer from '@/components/ui/BaseDrawer.vue'
import BaseLoading from '@/components/ui/BaseLoading.vue'
import PaginationBar from '@/components/ui/PaginationBar.vue'
import {
  getPlatformMembers,
  getPlatformTenants
} from '@/api/platform'
import { useEnterTenant } from './useEnterTenant'

const { t } = useI18n()
const { enterTenant, startingTenantId } = useEnterTenant()

const rows = ref([])
const tenants = ref([])
const loading = ref(false)
const error = ref('')
const totalCount = ref(0)
const counts = ref({ active: 0, invited: 0, disabled: 0 })
const currentPage = ref(1)
const pageSize = ref(20)
const searchInput = ref('')
const search = ref('')
const tenantFilter = ref('')
const roleFilter = ref('')
const statusFilter = ref('')

const ownership = ref(null)

const hasFilters = computed(
  () => !!search.value || !!tenantFilter.value || !!roleFilter.value || !!statusFilter.value
)

const segments = computed(() => [
  { value: '', label: t('management.allStatuses'), count: counts.value.total || 0 },
  { value: 'active', label: t('management.tenantActive'), count: counts.value.active || 0 },
  { value: 'invited', label: t('management.memberStatusInvited'), count: counts.value.invited || 0 },
  { value: 'disabled', label: t('management.tenantSuspended'), count: counts.value.disabled || 0 }
])

function initial(row) {
  return (row.name || row.username || 'M').trim().charAt(0).toUpperCase()
}

function roleLabel(role) {
  return role === 'tenant_admin'
    ? t('management.memberRoleTenantAdmin')
    : t('management.memberRole')
}

function statusLabel(status) {
  if (status === 'disabled') return t('management.tenantSuspended')
  if (status === 'invited') return t('management.memberStatusInvited')
  return t('management.tenantActive')
}

function statusClass(status) {
  if (status === 'disabled') return 'bg-surface-sunken text-ink-600'
  if (status === 'invited') return 'bg-warning-50 text-warning-700'
  return 'bg-success-50 text-success-700'
}

function formatDate(value) {
  if (!value) return '—'
  return new Date(value).toLocaleString()
}

async function fetchMembers() {
  loading.value = true
  error.value = ''
  try {
    const params = {
      page: currentPage.value,
      page_size: pageSize.value
    }
    if (search.value) params.search = search.value
    if (tenantFilter.value) params.tenant = tenantFilter.value
    if (roleFilter.value) params.role = roleFilter.value
    if (statusFilter.value) params.status = statusFilter.value
    const data = await getPlatformMembers(params)
    rows.value = data?.results || []
    totalCount.value = data?.count || 0
    const segmentCounts = data?.counts || {}
    counts.value = {
      active: segmentCounts.active || 0,
      invited: segmentCounts.invited || 0,
      disabled: segmentCounts.disabled || 0,
      total: (segmentCounts.active || 0) + (segmentCounts.invited || 0) + (segmentCounts.disabled || 0)
    }
  } catch (err) {
    error.value =
      err?.response?.data?.detail || t('management.platformMembersLoadFailed')
  } finally {
    loading.value = false
  }
}

async function fetchTenants() {
  try {
    const data = await getPlatformTenants()
    tenants.value = data?.results || []
  } catch {
    tenants.value = []
  }
}

function applyFilters() {
  search.value = searchInput.value.trim()
  currentPage.value = 1
  fetchMembers()
}

function selectStatus(value) {
  statusFilter.value = value
  currentPage.value = 1
  fetchMembers()
}

function resetFilters() {
  searchInput.value = ''
  search.value = ''
  tenantFilter.value = ''
  roleFilter.value = ''
  statusFilter.value = ''
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

function openOwnership(row) {
  ownership.value = row
}

onMounted(() => {
  fetchTenants()
  fetchMembers()
})
</script>
