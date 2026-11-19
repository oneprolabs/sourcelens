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
                {{ t('management.platformApiKeysTitle') }}
              </h1>
              <p class="mt-2 max-w-3xl text-sm leading-6 text-ink-500">
                {{ t('management.platformApiKeysSubtitle') }}
              </p>
              <p
                class="mt-2 max-w-3xl rounded-md border border-line bg-surface-sunken px-3 py-2 text-xs leading-5 text-ink-500"
              >
                {{ t('management.platformApiKeysNotice') }}
              </p>
            </div>
            <span
              class="inline-flex flex-shrink-0 rounded-md border border-line bg-surface-sunken px-2 py-1 text-xs text-ink-500"
            >
              {{ t('management.platformApiKeysCount', { count: totalCount }) }}
            </span>
          </div>

          <div
            v-if="selectedKeys.length"
            class="mb-3 flex flex-wrap items-center gap-2 rounded-md border border-line bg-surface-sunken p-3"
          >
            <span class="mr-2 text-sm text-ink-700">{{
              t('management.apiKeyBulk.selected', {
                count: selectedKeys.length
              })
            }}</span>
            <BaseButton
              v-for="action in ['disable', 'revoke']"
              :key="action"
              size="sm"
              :variant="action === 'revoke' ? 'danger' : 'outline'"
              :disabled="loading || mutating || !!bulkAction"
              @click="openBulk(action)"
            >
              {{ t(`management.apiKeyBulk.${action}`) }}
            </BaseButton>
          </div>
        </div>

        <div class="flex min-h-0 flex-1 flex-col px-5 py-4">
          <ApiKeyRiskSummary v-if="!error" :summary="summary" class="mb-4" />
          <form
            class="mb-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-[minmax(0,1fr)_200px_auto_auto]"
            @submit.prevent="applyFilters"
          >
            <label class="min-w-0">
              <span class="mb-1 block text-sm font-medium text-ink-700">{{
                t('common.search')
              }}</span>
              <input
                v-model="searchInput"
                type="text"
                class="form-input"
                :placeholder="t('management.platformApiKeysSearch')"
              />
            </label>
            <label>
              <span class="mb-1 block text-sm font-medium text-ink-700">{{
                t('management.memberTenant')
              }}</span>
              <BaseSelect v-model="tenantFilter" class="form-input">
                <option value="">{{ t('management.auditAllTenants') }}</option>
                <option
                  v-for="tenant in tenants"
                  :key="tenant.uuid"
                  :value="tenant.uuid"
                >
                  {{ tenant.name }}
                </option>
              </BaseSelect>
            </label>
            <BaseButton type="submit" :loading="loading">{{
              t('common.search')
            }}</BaseButton>
            <BaseButton
              type="button"
              variant="outline"
              :disabled="!hasFilters"
              @click="resetFilters"
            >
              {{ t('management.resetFilters') }}
            </BaseButton>
          </form>

          <div
            class="mb-3 flex flex-wrap gap-1 rounded-lg border border-line bg-surface-sunken p-1"
          >
            <button
              v-for="segment in segments"
              :key="segment.value"
              type="button"
              class="rounded-md px-3 py-1.5 text-sm font-medium"
              :class="
                statusFilter === segment.value
                  ? 'bg-surface text-ink-900 shadow-sm'
                  : 'text-ink-500 hover:text-ink-700'
              "
              @click="selectStatus(segment.value)"
            >
              {{ segment.label }}
            </button>
          </div>

          <BaseLoading v-if="loading && !rows.length" />

          <div
            v-else-if="error"
            class="flex min-h-0 flex-1 items-center justify-center rounded-lg border border-line bg-surface-sunken py-16 text-center"
          >
            <p class="text-sm font-medium text-danger-700">{{ error }}</p>
          </div>

          <div
            v-else-if="!rows.length"
            class="flex min-h-0 flex-1 items-center justify-center rounded-lg border border-line bg-surface-sunken py-16 text-center"
          >
            <p class="text-sm font-medium text-ink-500">
              {{ t('management.platformApiKeysEmpty') }}
            </p>
          </div>

          <div
            v-else
            class="relative min-h-0 flex-1 overflow-auto rounded-lg border border-line bg-surface"
          >
            <table class="w-full min-w-[960px] divide-y divide-line">
              <thead class="sticky top-0 z-10 bg-surface-sunken">
                <tr>
                  <th class="table-head">
                    <input
                      type="checkbox"
                      :aria-label="t('management.apiKeyBulk.selectPage')"
                      :checked="allSelected"
                      :indeterminate="selectedKeys.length > 0 && !allSelected"
                      :disabled="loading || mutating || !!bulkAction"
                      class="h-4 w-4 rounded border-line text-brand-600"
                      @change="selectPage($event.target.checked)"
                    />
                  </th>
                  <th class="table-head">{{ t('management.apiKeyName') }}</th>
                  <th class="table-head">{{ t('management.memberTenant') }}</th>
                  <th class="table-head">
                    {{ t('management.apiKeyCreator') }}
                  </th>
                  <th class="table-head">{{ t('management.apiKeyExpiry') }}</th>
                  <th class="table-head">
                    {{ t('management.apiKeyLastUsed') }}
                  </th>
                  <th class="table-head">
                    {{ t('management.apiKeyInsights.calls7d') }}
                  </th>
                  <th class="table-head">{{ t('management.memberStatus') }}</th>
                  <th class="table-head text-right">
                    {{ t('common.actions') }}
                  </th>
                </tr>
              </thead>
              <tbody class="divide-y divide-line bg-surface">
                <tr
                  v-for="row in rows"
                  :key="row.uuid"
                  class="hover:bg-line-soft"
                >
                  <td class="px-4 py-3">
                    <input
                      v-model="selectedKeys"
                      type="checkbox"
                      :value="row.uuid"
                      :aria-label="
                        t('management.apiKeyBulk.selectKey', { name: row.name })
                      "
                      :disabled="loading || mutating || !!bulkAction"
                      class="h-4 w-4 rounded border-line text-brand-600"
                    />
                  </td>
                  <td class="px-4 py-3">
                    <div class="flex items-center gap-3">
                      <span
                        class="flex h-9 w-9 items-center justify-center rounded-lg bg-brand-50 text-brand-700"
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
                            d="M15 7a4 4 0 11-8 0 4 4 0 018 0zM9 7v10a2 2 0 002 2h2a2 2 0 002-2v-2m-6-4h4"
                          />
                        </svg>
                      </span>
                      <div class="min-w-0">
                        <p class="truncate text-sm font-medium text-ink-900">
                          {{ row.name }}
                        </p>
                        <p class="truncate font-mono text-xs text-ink-500">
                          {{ maskedKey(row) }}
                        </p>
                      </div>
                    </div>
                  </td>
                  <td class="px-4 py-3 text-sm text-ink-700">
                    {{ row.tenant.name }}
                  </td>
                  <td class="px-4 py-3 text-sm text-ink-600">
                    {{ row.created_by || '—' }}
                  </td>
                  <td class="px-4 py-3 text-sm text-ink-600">
                    <span
                      :class="
                        row.expiring_soon
                          ? 'rounded bg-warning-50 px-2 py-1 text-warning-700'
                          : ''
                      "
                      >{{ expiryLabel(row) }}</span
                    >
                  </td>
                  <td class="px-4 py-3 text-sm text-ink-600">
                    {{ lastUsedLabel(row.last_used_at) }}
                    <span
                      v-if="row.unused"
                      class="mt-1 block text-xs text-warning-700"
                      >{{
                        t(
                          `management.apiKeyInsights.${row.last_used_at ? 'longUnused' : 'neverUsed'}`
                        )
                      }}</span
                    >
                  </td>
                  <td class="px-4 py-3 text-sm text-ink-600">
                    {{ row.calls_7d ?? '—' }}
                  </td>
                  <td class="px-4 py-3">
                    <span
                      class="inline-flex rounded-full px-2 py-0.5 text-xs font-medium"
                      :class="statusClass(row.status)"
                    >
                      {{ statusLabel(row.status) }}
                    </span>
                  </td>
                  <td class="px-4 py-3">
                    <div class="flex justify-end gap-2">
                      <BaseButton
                        size="sm"
                        variant="outline"
                        @click="openDetail(row)"
                      >
                        {{ t('management.apiKeyDetail') }}
                      </BaseButton>
                      <BaseButton
                        v-if="row.raw_status === 'active'"
                        :disabled="loading || mutating || !!bulkAction"
                        size="sm"
                        variant="outline"
                        @click="forceAction(row, 'disable')"
                      >
                        {{ t('management.apiKeyForceDisable') }}
                      </BaseButton>
                      <BaseButton
                        v-if="row.status !== 'revoked'"
                        :disabled="loading || mutating || !!bulkAction"
                        size="sm"
                        variant="danger"
                        @click="forceAction(row, 'revoke')"
                      >
                        {{ t('management.apiKeyForceRevoke') }}
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

      <ApiKeyNotices
        :key="noticeRevision"
        :notices="notices"
        @updated="notices = $event"
      />
      <ApiKeyBulkModal
        v-if="bulkAction"
        :action="bulkAction"
        :keys="bulkSelection"
        platform
        @updated="handleBulkUpdated"
        @close="closeBulk"
      />

      <BaseDrawer
        :show="!!detail"
        :title="detail?.name || ''"
        :subtitle="detail ? maskedKey(detail) : ''"
        width="md"
        @close="detail = null"
      >
        <dl
          v-if="detail"
          class="divide-y divide-line rounded-md border border-line"
        >
          <div class="flex justify-between gap-3 px-4 py-2.5 text-sm">
            <dt class="text-ink-500">{{ t('management.memberTenant') }}</dt>
            <dd class="font-medium text-ink-900">{{ detail.tenant.name }}</dd>
          </div>
          <div class="flex justify-between gap-3 px-4 py-2.5 text-sm">
            <dt class="text-ink-500">{{ t('management.apiKeyCreator') }}</dt>
            <dd class="text-ink-900">{{ detail.created_by || '—' }}</dd>
          </div>
          <div class="flex justify-between gap-3 px-4 py-2.5 text-sm">
            <dt class="text-ink-500">{{ t('management.apiKeyScope') }}</dt>
            <dd class="text-ink-900">
              {{
                detail.scope === 'all'
                  ? t('management.apiKeyAllAssistants')
                  : t('management.apiKeySelectedAssistants', {
                      count: detail.assistant_uuids.length
                    })
              }}
            </dd>
          </div>
          <div class="flex justify-between gap-3 px-4 py-2.5 text-sm">
            <dt class="text-ink-500">{{ t('management.apiKeyExpiry') }}</dt>
            <dd class="text-ink-900">{{ expiryLabel(detail) }}</dd>
          </div>
          <div class="flex justify-between gap-3 px-4 py-2.5 text-sm">
            <dt class="text-ink-500">{{ t('management.apiKeyLastUsed') }}</dt>
            <dd class="text-ink-900">
              {{ lastUsedLabel(detail.last_used_at) }}
            </dd>
          </div>
          <div class="flex justify-between gap-3 px-4 py-2.5 text-sm">
            <dt class="text-ink-500">{{ t('management.memberStatus') }}</dt>
            <dd class="text-ink-900">{{ statusLabel(detail.status) }}</dd>
          </div>
          <div class="flex justify-between gap-3 px-4 py-2.5 text-sm">
            <dt class="text-ink-500">
              {{ t('management.apiKeyInsights.calls7d') }}
            </dt>
            <dd class="text-ink-900">{{ detail.calls_7d ?? '—' }}</dd>
          </div>
        </dl>
        <template #footer>
          <div class="flex justify-end gap-2">
            <BaseButton variant="outline" size="sm" @click="detail = null">
              {{ t('common.close') }}
            </BaseButton>
            <BaseButton
              v-if="detail && detail.status !== 'revoked'"
              :disabled="loading || mutating || !!bulkAction"
              variant="danger"
              size="sm"
              @click="forceAction(detail, 'revoke')"
            >
              {{ t('management.apiKeyForceRevoke') }}
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
import ApiKeyBulkModal from '../Management/ApiKeyBulkModal.vue'
import ApiKeyRiskSummary from '../Management/ApiKeyRiskSummary.vue'
import ApiKeyNotices from './ApiKeyNotices.vue'
import {
  getPlatformApiKeys,
  getPlatformTenants,
  platformApiKeyAction
} from '@/api/platform'
import { useToast } from '@/composables/useToast'

const { t } = useI18n()
const { showSuccess, showError } = useToast()

const rows = ref([])
const tenants = ref([])
const loading = ref(false)
const error = ref('')
const totalCount = ref(0)
const summary = ref(null)
const currentPage = ref(1)
const pageSize = ref(20)
const searchInput = ref('')
const search = ref('')
const tenantFilter = ref('')
const statusFilter = ref('')
const detail = ref(null)
const notices = ref([])
const noticeRevision = ref(0)
const mutating = ref(false)
const selectedKeys = ref([])
const bulkAction = ref('')
const bulkSelection = ref([])
const allSelected = computed(
  () =>
    rows.value.length > 0 &&
    rows.value.every((row) => selectedKeys.value.includes(row.uuid))
)
let requestVersion = 0

function selectPage(checked) {
  selectedKeys.value = checked
    ? rows.value.slice(0, 100).map((row) => row.uuid)
    : []
}

function openBulk(action) {
  if (
    loading.value ||
    mutating.value ||
    bulkAction.value ||
    !['disable', 'revoke'].includes(action)
  )
    return
  bulkSelection.value = rows.value
    .filter((row) => selectedKeys.value.includes(row.uuid))
    .map((row) => ({ ...row }))
  if (bulkSelection.value.length) bulkAction.value = action
}

function closeBulk(refresh = false) {
  bulkAction.value = ''
  bulkSelection.value = []
  if (refresh) fetchKeys()
}

function storeNotices(items) {
  const ids = new Set(items.map((item) => item.uuid))
  notices.value = [
    ...items,
    ...notices.value.filter((item) => !ids.has(item.uuid))
  ].slice(0, 20)
  noticeRevision.value++
}

function handleBulkUpdated(data) {
  storeNotices(data.notices || [])
  fetchKeys()
}

const hasFilters = computed(
  () => !!search.value || !!tenantFilter.value || !!statusFilter.value
)

const segments = computed(() => [
  { value: '', label: t('management.allStatuses') },
  { value: 'active', label: t('management.apiKeyActive') },
  { value: 'disabled', label: t('management.apiKeyDisabled') },
  { value: 'expired', label: t('management.apiKeyExpired') },
  { value: 'revoked', label: t('management.apiKeyRevoked') }
])

function maskedKey(row) {
  return row.key_display || `${row.key_prefix || 'slk_'}••••`
}

function statusLabel(status) {
  if (status === 'disabled') return t('management.apiKeyDisabled')
  if (status === 'revoked') return t('management.apiKeyRevoked')
  if (status === 'expired') return t('management.apiKeyExpired')
  return t('management.apiKeyActive')
}

function statusClass(status) {
  if (status === 'revoked' || status === 'disabled') {
    return 'bg-surface-sunken text-ink-600'
  }
  if (status === 'expired') return 'bg-warning-50 text-warning-700'
  return 'bg-success-50 text-success-700'
}

function expiryLabel(row) {
  if (!row.expires_at) return t('management.apiKeyNever')
  return new Date(row.expires_at).toLocaleDateString()
}

function lastUsedLabel(value) {
  if (!value) return t('management.memberNever')
  return new Date(value).toLocaleString()
}

async function fetchKeys() {
  const version = ++requestVersion
  summary.value = null
  selectedKeys.value = []
  loading.value = true
  error.value = ''
  try {
    const params = {
      page: currentPage.value,
      page_size: pageSize.value
    }
    if (search.value) params.search = search.value
    if (tenantFilter.value) params.tenant = tenantFilter.value
    if (statusFilter.value) params.status = statusFilter.value
    const data = await getPlatformApiKeys(params)
    if (version !== requestVersion) return
    rows.value = data?.results || []
    summary.value = data?.summary || null
    totalCount.value = data?.count || 0
  } catch (err) {
    if (version !== requestVersion) return
    error.value =
      err?.response?.data?.detail || t('management.apiKeyLoadFailed')
  } finally {
    if (version === requestVersion) loading.value = false
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
  fetchKeys()
}

function selectStatus(value) {
  statusFilter.value = value
  currentPage.value = 1
  fetchKeys()
}

function resetFilters() {
  searchInput.value = ''
  search.value = ''
  tenantFilter.value = ''
  statusFilter.value = ''
  currentPage.value = 1
  fetchKeys()
}

function goPrevPage() {
  if (currentPage.value <= 1) return
  currentPage.value -= 1
  fetchKeys()
}

function goNextPage() {
  if (currentPage.value * pageSize.value >= totalCount.value) return
  currentPage.value += 1
  fetchKeys()
}

function handlePageSizeChange(value) {
  pageSize.value = value
  currentPage.value = 1
  fetchKeys()
}

function openDetail(row) {
  detail.value = row
}

async function forceAction(row, action) {
  if (
    mutating.value ||
    loading.value ||
    bulkAction.value ||
    !['disable', 'revoke'].includes(action)
  )
    return
  const labels = {
    disable: t('management.apiKeyForceDisableConfirm', { name: row.name }),
    revoke: t('management.apiKeyForceRevokeConfirm', { name: row.name })
  }
  if (!window.confirm(labels[action])) return
  mutating.value = true
  try {
    const data = await platformApiKeyAction(row.uuid, action)
    if (data?.notification) storeNotices([data.notification])
    detail.value = null
    showSuccess(t('management.apiKeyForceDone'))
    await fetchKeys()
  } catch (err) {
    showError(err?.response?.data?.detail || t('management.apiKeyForceFailed'))
  } finally {
    mutating.value = false
  }
}

onMounted(() => {
  fetchTenants()
  fetchKeys()
})
</script>
