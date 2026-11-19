<template>
  <AdminLayout>
    <div class="admin-list-page">
      <section class="admin-list-surface">
        <div class="admin-page-heading">
          <div class="admin-heading-copy">
            <h1>
              {{ t('management.tenantApiKeyTitle') }}
              <span class="admin-heading-count">{{ totalCount }}</span>
            </h1>
            <p>{{ t('management.tenantApiKeySubtitle') }}</p>
          </div>
          <BaseButton :disabled="mutating || createSaving" @click="openCreate"
            ><Plus :size="14" aria-hidden="true" />{{
              t('management.tenantApiKeyCreate')
            }}</BaseButton
          >
        </div>
        <ApiKeyRiskSummary v-if="!error" :summary="summary" />
        <div class="admin-list-panel">
          <form class="admin-list-toolbar" @submit.prevent="applyFilters">
            <div
              class="admin-status-tabs"
              :aria-label="t('management.memberStatus')"
            >
              <button
                v-for="tab in keyStatusTabs"
                :key="tab.value"
                type="button"
                :aria-pressed="statusFilter === tab.value"
                @click="selectKeyStatus(tab.value)"
              >
                {{ t(tab.label) }}
              </button>
            </div>
            <label class="admin-toolbar-search">
              <Search :size="14" aria-hidden="true" />
              <input
                v-model="searchInput"
                type="search"
                :aria-label="t('common.search')"
                :placeholder="t('management.tenantApiKeySearch')"
                @input="queueSearch"
              />
            </label>
            <BaseButton
              v-if="searchInput || statusFilter"
              variant="ghost"
              size="sm"
              @click="resetFilters"
              >{{ t('management.resetFilters') }}</BaseButton
            >
          </form>
          <div v-if="selectedKeys.length" class="admin-bulk-toolbar">
            <span class="mr-2 text-sm text-ink-700">{{
              t('management.apiKeyBulk.selected', {
                count: selectedKeys.length
              })
            }}</span>
            <BaseButton
              v-for="action in ['enable', 'disable', 'extend', 'revoke']"
              :key="action"
              size="sm"
              :variant="action === 'revoke' ? 'danger' : 'outline'"
              :disabled="loading || mutating || !!bulkAction"
              @click="openBulk(action)"
            >
              {{ t(`management.apiKeyBulk.${action}`) }}
            </BaseButton>
          </div>

          <BaseLoading v-if="loading && !keys.length" />

          <div
            v-else-if="error"
            class="flex min-h-0 flex-1 items-center justify-center rounded-lg border border-line bg-surface-sunken py-16 text-center"
          >
            <p class="text-sm font-medium text-danger-700">{{ error }}</p>
          </div>

          <div
            v-else-if="!keys.length"
            class="flex min-h-0 flex-1 items-center justify-center rounded-lg border border-line bg-surface-sunken py-16 text-center"
          >
            <div>
              <p class="text-sm font-medium text-ink-700">
                {{ t('management.apiKeyNoKeys') }}
              </p>
              <p class="mt-1 text-sm text-ink-500">
                {{ t('management.apiKeyNoKeysHint') }}
              </p>
            </div>
          </div>

          <div v-else class="admin-list-table relative min-h-0">
            <table
              class="admin-key-desktop-table w-full min-w-[940px] divide-y divide-line"
            >
              <colgroup>
                <col class="w-12" />
                <col class="w-[22%]" />
                <col class="w-[12%]" />
                <col class="w-[18%]" />
                <col class="w-[14%]" />
                <col class="w-[14%]" />
                <col class="w-[10%]" />
                <col class="w-[10%]" />
                <col class="w-14" />
              </colgroup>
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
                  <th class="table-head">
                    {{ t('management.apiKeyCreator') }}
                  </th>
                  <th class="table-head">{{ t('management.apiKeyScope') }}</th>
                  <th class="table-head">{{ t('management.apiKeyExpiry') }}</th>
                  <th class="table-head">
                    {{ t('management.apiKeyLastUsed') }}
                  </th>
                  <th class="table-head">{{ t('management.memberStatus') }}</th>
                  <th class="table-head text-right">
                    {{ t('management.apiKeyInsights.calls7d') }}
                  </th>
                  <th class="table-head text-right">
                    <span class="sr-only">{{ t('common.actions') }}</span>
                  </th>
                </tr>
              </thead>
              <tbody class="divide-y divide-line bg-surface">
                <tr
                  v-for="key in keys"
                  :key="key.uuid"
                  class="transition-colors hover:bg-line-soft"
                >
                  <td class="px-4 py-3">
                    <input
                      v-model="selectedKeys"
                      type="checkbox"
                      :value="key.uuid"
                      :aria-label="
                        t('management.apiKeyBulk.selectKey', { name: key.name })
                      "
                      :disabled="loading || mutating || !!bulkAction"
                      class="h-4 w-4 rounded border-line text-brand-600"
                    />
                  </td>
                  <td class="px-4 py-3">
                    <div class="flex min-w-0 items-center gap-3">
                      <span class="admin-group-thumb"
                        ><KeyRound :size="16" aria-hidden="true"
                      /></span>
                      <div class="min-w-0">
                        <button
                          type="button"
                          class="max-w-full truncate text-left text-sm font-medium text-ink-900 hover:text-brand-700"
                          @click="detailKey = { ...key }"
                        >
                          {{ key.name }}
                        </button>
                        <p class="member-email truncate">
                          {{ key.key_display || `${key.key_prefix}••••` }}
                        </p>
                      </div>
                    </div>
                  </td>
                  <td class="px-4 py-3 text-sm text-ink-600">
                    {{ key.created_by || '—' }}
                  </td>
                  <td class="px-4 py-3 text-sm text-ink-600">
                    {{
                      key.scope === 'all'
                        ? t('management.apiKeyAllAssistants')
                        : t('management.apiKeySelectedAssistants', {
                            count: key.assistant_count
                          })
                    }}
                  </td>
                  <td class="px-4 py-3 text-sm text-ink-500">
                    <span
                      :class="
                        key.expiring_soon
                          ? 'rounded bg-warning-50 px-2 py-1 text-warning-700'
                          : ''
                      "
                      >{{ expiryLabel(key) }}</span
                    >
                  </td>
                  <td class="px-4 py-3 text-sm text-ink-500">
                    {{ lastUsedLabel(key.last_used_at) }}
                    <span
                      v-if="key.unused"
                      class="mt-1 block text-xs text-warning-700"
                      >{{
                        t(
                          `management.apiKeyInsights.${key.last_used_at ? 'longUnused' : 'neverUsed'}`
                        )
                      }}</span
                    >
                  </td>
                  <td class="px-4 py-3">
                    <span class="admin-member-status">
                      <span
                        class="h-1.5 w-1.5 rounded-full"
                        :class="
                          key.status === 'active'
                            ? 'admin-status-active'
                            : key.status === 'expired'
                              ? 'admin-status-invited'
                              : key.status === 'revoked'
                                ? 'admin-status-locked'
                                : 'admin-status-disabled'
                        "
                      />{{ statusLabel(key.status) }}
                    </span>
                  </td>
                  <td class="px-4 py-3 text-right">
                    <span class="text-sm tabular-nums text-ink-700">{{
                      key.calls_7d ?? '—'
                    }}</span>
                  </td>
                  <td class="px-4 py-3 text-right">
                    <RowActionMenu
                      :actions="rowActions(key)"
                      @select="handleRowAction($event, key)"
                    />
                  </td>
                </tr>
              </tbody>
            </table>
            <div class="admin-key-mobile-list">
              <article
                v-for="key in keys"
                :key="key.uuid"
                class="admin-group-card"
              >
                <input
                  v-model="selectedKeys"
                  type="checkbox"
                  :value="key.uuid"
                  class="admin-group-card-check"
                  :disabled="loading || mutating || !!bulkAction"
                  :aria-label="
                    t('management.apiKeyBulk.selectKey', { name: key.name })
                  "
                />
                <button
                  type="button"
                  class="admin-group-card-heading"
                  @click="detailKey = { ...key }"
                >
                  <span class="admin-group-thumb"
                    ><KeyRound :size="16" aria-hidden="true" /></span
                  ><span class="min-w-0"
                    ><span class="block break-words text-sm font-semibold">{{
                      key.name
                    }}</span
                    ><span class="member-email block break-all">{{
                      key.key_display || `${key.key_prefix}••••`
                    }}</span></span
                  >
                </button>
                <dl>
                  <div class="admin-group-card-meta">
                    <dt>{{ t('management.apiKeyCreator') }}</dt>
                    <dd>{{ key.created_by || '—' }}</dd>
                  </div>
                  <div class="admin-group-card-meta">
                    <dt>{{ t('management.apiKeyExpiry') }}</dt>
                    <dd>{{ expiryLabel(key) }}</dd>
                  </div>
                  <div class="admin-group-card-meta">
                    <dt>{{ t('management.apiKeyLastUsed') }}</dt>
                    <dd>{{ lastUsedLabel(key.last_used_at) }}</dd>
                  </div>
                  <div class="admin-group-card-meta">
                    <dt>{{ t('management.memberStatus') }}</dt>
                    <dd>{{ statusLabel(key.status) }}</dd>
                  </div>
                </dl>
                <div class="admin-group-card-actions">
                  <RowActionMenu
                    :label="`${t('common.actions')} · ${key.name}`"
                    :actions="rowActions(key)"
                    @select="handleRowAction($event, key)"
                  />
                </div>
              </article>
            </div>
          </div>
          <PaginationBar
            prototype
            v-if="!loading && !error"
            :page-size="pageSize"
            :current-page="currentPage"
            :total="totalCount"
            @page-size-change="changePageSize"
            @prev="changePage(-1)"
            @next="changePage(1)"
          />
        </div>
      </section>

      <ApiKeyBulkModal
        v-if="bulkAction"
        :action="bulkAction"
        :keys="bulkSelection"
        @updated="fetchKeys"
        @close="closeBulk"
      />

      <ApiKeyDetailDrawer
        v-if="detailKey"
        :selected="detailKey"
        :revision="detailRevision"
        :busy="mutating || !!bulkAction"
        @close="detailKey = null"
        @action="handleRowAction"
      />

      <BaseDrawer
        :show="showCreate"
        :title="
          t(
            editingKey
              ? 'management.apiKeySettings'
              : 'management.tenantApiKeyCreate'
          )
        "
        width="md"
        @close="closeCreate"
      >
        <form class="space-y-5" @submit.prevent="createKey">
          <fieldset :disabled="createSaving" class="space-y-5">
            <label class="block">
              <span class="mb-1 block text-sm font-medium text-ink-700">
                {{ t('management.apiKeyName') }}
                <span class="text-danger-600">*</span>
              </span>
              <input
                v-model="createForm.name"
                type="text"
                class="form-input"
                maxlength="150"
                :placeholder="t('management.apiKeyNamePlaceholder')"
              />
            </label>

            <label class="block">
              <span class="mb-1 block text-sm font-medium text-ink-700">{{
                t('management.apiKeyPurpose')
              }}</span>
              <input
                v-model="createForm.description"
                type="text"
                class="form-input"
                maxlength="1000"
                :placeholder="t('management.apiKeyPurposePlaceholder')"
              />
            </label>

            <div>
              <span class="mb-2 block text-sm font-medium text-ink-700">
                {{ t('management.apiKeyScope') }}
              </span>
              <div class="space-y-2">
                <label
                  class="flex items-start gap-2 rounded-md border border-line p-3"
                >
                  <input
                    v-model="createForm.scope"
                    type="radio"
                    value="all"
                    class="mt-1 h-4 w-4 text-brand-600 focus:ring-brand-500"
                  />
                  <span>
                    <span class="block text-sm font-medium text-ink-800">
                      {{ t('management.apiKeyAllAssistants') }}
                    </span>
                    <span class="block text-xs text-ink-500">
                      {{ t('management.apiKeyAllAssistantsHint') }}
                    </span>
                  </span>
                </label>
                <label
                  class="flex items-start gap-2 rounded-md border border-line p-3"
                >
                  <input
                    v-model="createForm.scope"
                    type="radio"
                    value="selected"
                    class="mt-1 h-4 w-4 text-brand-600 focus:ring-brand-500"
                  />
                  <span>
                    <span class="block text-sm font-medium text-ink-800">
                      {{ t('management.apiKeySelectedAssistantsLabel') }}
                    </span>
                    <span class="block text-xs text-ink-500">
                      {{ t('management.apiKeySelectedAssistantsHint') }}
                    </span>
                  </span>
                </label>
              </div>
              <div
                v-if="createForm.scope === 'selected'"
                class="mt-3 max-h-40 space-y-2 overflow-auto rounded-md border border-line bg-surface-sunken p-3"
              >
                <label
                  v-for="assistant in assistants"
                  :key="assistant.uuid"
                  class="flex items-center gap-2 text-sm text-ink-700"
                >
                  <input
                    v-model="createForm.assistantUuids"
                    type="checkbox"
                    :value="assistant.uuid"
                    class="h-4 w-4 rounded border-line text-brand-600 focus:ring-brand-500"
                  />
                  {{ assistant.name }}
                </label>
                <BaseLoading v-if="assistantLoading" />
                <div
                  v-else-if="assistantLoadFailed"
                  class="space-y-2 text-xs text-danger-700"
                  role="alert"
                >
                  <p>{{ t('management.apiKeyAssistantsFailed') }}</p>
                  <BaseButton
                    size="sm"
                    variant="outline"
                    @click="fetchAssistants"
                    >{{ t('common.refresh') }}</BaseButton
                  >
                </div>
                <p v-else-if="!assistants.length" class="text-xs text-ink-500">
                  {{ t('management.apiKeyNoAssistants') }}
                </p>
                <div
                  v-for="uuid in unavailableSelections"
                  :key="uuid"
                  class="flex items-center justify-between gap-2 text-xs text-ink-500"
                >
                  <span>{{
                    t('management.apiKeyInsights.unavailableAssistant')
                  }}</span>
                  <BaseButton
                    size="sm"
                    variant="outline"
                    @click="removeUnavailableSelection(uuid)"
                    >{{ t('management.apiKeyRemoveSelection') }}</BaseButton
                  >
                </div>
              </div>
            </div>

            <label v-if="!editingKey" class="block">
              <span class="mb-1 block text-sm font-medium text-ink-700">
                {{ t('management.apiKeyExpiry') }}
              </span>
              <BaseSelect v-model="createForm.expiresInDays" class="form-input">
                <option value="30">{{ t('management.apiKeyDays30') }}</option>
                <option value="90">{{ t('management.apiKeyDays90') }}</option>
                <option value="365">{{ t('management.apiKeyDays365') }}</option>
                <option value="0">{{ t('management.apiKeyNever') }}</option>
              </BaseSelect>
            </label>

            <p v-if="createError" class="text-sm text-danger-700">
              {{ createError }}
            </p>
          </fieldset>
        </form>
        <template #footer>
          <div class="flex justify-end gap-2">
            <BaseButton
              variant="outline"
              size="sm"
              :disabled="createSaving"
              @click="closeCreate"
            >
              {{ t('common.cancel') }}
            </BaseButton>
            <BaseButton
              variant="primary"
              size="sm"
              :loading="createSaving"
              :disabled="createSaving"
              @click="createKey"
            >
              {{
                t(editingKey ? 'common.save' : 'management.apiKeyCreateButton')
              }}
            </BaseButton>
          </div>
        </template>
      </BaseDrawer>

      <BaseDrawer
        :show="!!createdSecret"
        :title="
          t(
            secretRotated
              ? 'management.apiKeyRotated'
              : 'management.apiKeyCreatedTitle'
          )
        "
        width="md"
        @close="closeSecret"
      >
        <div class="space-y-4">
          <p class="text-sm text-ink-600">
            {{ t('management.apiKeyCreatedHint') }}
          </p>
          <p v-if="secretRotated" class="text-sm text-warning-700">
            {{ t('management.apiKeyOldInvalid') }}
          </p>
          <div class="rounded-md border border-line bg-surface-sunken p-3">
            <code class="block break-all font-mono text-sm text-ink-900">
              {{ createdSecret }}
            </code>
          </div>
          <BaseButton variant="primary" size="sm" @click="copySecret">
            {{ t('management.apiKeyCopy') }}
          </BaseButton>
        </div>
        <template #footer>
          <div class="flex justify-end">
            <BaseButton variant="outline" size="sm" @click="closeSecret">
              {{ t('common.close') }}
            </BaseButton>
          </div>
        </template>
      </BaseDrawer>
    </div>
  </AdminLayout>
</template>

<script setup>
import BaseSelect from '@/components/ui/BaseSelect.vue'
import {
  Ban,
  Eye,
  KeyRound,
  Plus,
  RefreshCw,
  Search,
  Settings2,
  Trash2
} from '@lucide/vue'
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { useI18n } from 'vue-i18n'

import AdminLayout from '@/admin/layout/AdminLayout.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseDrawer from '@/components/ui/BaseDrawer.vue'
import BaseLoading from '@/components/ui/BaseLoading.vue'
import RowActionMenu from '@/components/ui/RowActionMenu.vue'
import PaginationBar from '@/components/ui/PaginationBar.vue'
import ApiKeyBulkModal from './ApiKeyBulkModal.vue'
import ApiKeyDetailDrawer from './ApiKeyDetailDrawer.vue'
import ApiKeyRiskSummary from './ApiKeyRiskSummary.vue'
import {
  createTenantApiKey,
  getTenantApiKeys,
  revokeTenantApiKey,
  rotateTenantApiKey,
  updateTenantApiKey
} from '@/api/tenant'
import { listAssistants } from '@/api/lens'
import { useToast } from '@/composables/useToast'

const { t } = useI18n()
const { showSuccess, showError } = useToast()

const keys = ref([])
const assistants = ref([])
const assistantLoading = ref(false)
const assistantLoadFailed = ref(false)
let assistantRequest = 0
const loading = ref(false)
const error = ref('')
const searchInput = ref('')
const search = ref('')
const statusFilter = ref('')
const keyStatusTabs = [
  { value: '', label: 'management.statusAll' },
  { value: 'active', label: 'management.apiKeyActive' },
  { value: 'disabled', label: 'management.memberDisabled' },
  { value: 'expired', label: 'management.apiKeyExpired' },
  { value: 'revoked', label: 'management.apiKeyRevoked' }
]
let searchTimer
function queueSearch() {
  clearTimeout(searchTimer)
  searchTimer = setTimeout(applyFilters, 250)
}
onBeforeUnmount(() => {
  clearTimeout(searchTimer)
  keyRequest++
  assistantRequest++
})
const totalCount = ref(0)
const summary = ref(null)
const detailKey = ref(null)
const detailRevision = ref(0)
const currentPage = ref(1)
const pageSize = ref(20)
const mutating = ref(false)
const selectedKeys = ref([])
const bulkAction = ref('')
const bulkSelection = ref([])
const allSelected = computed(
  () =>
    keys.value.length > 0 &&
    keys.value.every((key) => selectedKeys.value.includes(key.uuid))
)
let keyRequest = 0

const showCreate = ref(false)
const createSaving = ref(false)
const createError = ref('')
const editingKey = ref(null)
const createForm = reactive({
  name: '',
  description: '',
  scope: 'all',
  assistantUuids: [],
  expiresInDays: '90'
})
const unavailableSelections = computed(() => {
  if (assistantLoading.value || assistantLoadFailed.value) return []
  const known = new Set(assistants.value.map((assistant) => assistant.uuid))
  return createForm.assistantUuids.filter((uuid) => !known.has(uuid))
})

function removeUnavailableSelection(uuid) {
  createForm.assistantUuids = createForm.assistantUuids.filter(
    (value) => value !== uuid
  )
}

const createdSecret = ref('')
const secretRotated = ref(false)

function selectPage(checked) {
  selectedKeys.value = checked
    ? keys.value.slice(0, 100).map((key) => key.uuid)
    : []
}

function openBulk(action) {
  if (
    loading.value ||
    mutating.value ||
    bulkAction.value ||
    !['enable', 'disable', 'extend', 'revoke'].includes(action)
  )
    return
  bulkSelection.value = keys.value
    .filter((key) => selectedKeys.value.includes(key.uuid))
    .map((key) => ({ ...key }))
  if (bulkSelection.value.length) bulkAction.value = action
}

function closeBulk(refresh = false) {
  bulkAction.value = ''
  bulkSelection.value = []
  if (refresh) fetchKeys()
}

const statusLabels = computed(() => ({
  active: t('management.apiKeyActive'),
  disabled: t('management.memberDisabled'),
  expired: t('management.apiKeyExpired'),
  revoked: t('management.apiKeyRevoked')
}))

function statusLabel(value) {
  return statusLabels.value[value] || value
}

function expiryLabel(key) {
  if (!key.expires_at) return t('management.apiKeyNever')
  return new Date(key.expires_at).toLocaleDateString()
}

function lastUsedLabel(value) {
  if (!value) return t('management.memberNever')
  return new Date(value).toLocaleString()
}

function rowActions(key) {
  const actions = [
    { key: 'detail', label: t('management.apiKeyDetail'), icon: Eye },
    {
      key: 'settings',
      label: t('management.apiKeySettings'),
      icon: Settings2,
      disabled:
        key.status === 'revoked' ||
        loading.value ||
        mutating.value ||
        !!bulkAction.value ||
        createSaving.value
    },
    {
      key: 'rotate',
      label: t('management.apiKeyRotate'),
      icon: RefreshCw,
      disabled:
        key.status === 'revoked' ||
        loading.value ||
        mutating.value ||
        !!bulkAction.value
    },
    {
      key: 'toggle',
      label:
        key.raw_status === 'disabled'
          ? t('management.enable')
          : t('management.disable'),
      icon: Ban,
      disabled:
        ['revoked', 'expired'].includes(key.status) ||
        loading.value ||
        mutating.value ||
        !!bulkAction.value,
      divider: true
    },
    {
      key: 'revoke',
      label: t('management.apiKeyRevoke'),
      icon: Trash2,
      variant: 'danger',
      divider: true,
      disabled:
        key.status === 'revoked' ||
        loading.value ||
        mutating.value ||
        !!bulkAction.value
    }
  ]
  return actions
}

async function handleRowAction(action, key) {
  if (rowActions(key).find((item) => item.key === action)?.disabled) return
  if (action === 'detail') {
    detailKey.value = { ...key }
    return
  }
  if (action === 'settings') {
    openSettings(key)
    return
  }
  if (action === 'rotate') {
    await rotateKey(key)
    return
  }
  if (action === 'toggle') {
    await toggleKey(key)
    return
  }
  if (action === 'revoke') {
    await revokeKey(key)
  }
}

async function fetchKeys() {
  const request = ++keyRequest
  selectedKeys.value = []
  loading.value = true
  error.value = ''
  try {
    const params = { page: currentPage.value, page_size: pageSize.value }
    if (search.value) params.search = search.value
    if (statusFilter.value) params.status = statusFilter.value
    const data = await getTenantApiKeys(params)
    if (request !== keyRequest) return
    keys.value = data?.results || []
    summary.value = data?.summary || null
    detailRevision.value++
    totalCount.value = data?.count ?? keys.value.length
    const lastPage = Math.max(1, Math.ceil(totalCount.value / pageSize.value))
    if (currentPage.value > lastPage) {
      currentPage.value = lastPage
      return fetchKeys()
    }
  } catch (err) {
    if (request !== keyRequest) return
    error.value =
      err?.response?.data?.detail || t('management.apiKeyLoadFailed')
  } finally {
    if (request === keyRequest) loading.value = false
  }
}

async function fetchAssistants() {
  const request = ++assistantRequest
  assistantLoading.value = true
  assistantLoadFailed.value = false
  assistants.value = []
  try {
    const result =
      (await listAssistants({
        managed: true,
        ...(editingKey.value ? { archived: 'all' } : {})
      })) || []
    if (request === assistantRequest) assistants.value = result
  } catch {
    if (request === assistantRequest) assistantLoadFailed.value = true
  } finally {
    if (request === assistantRequest) assistantLoading.value = false
  }
}

function selectKeyStatus(value) {
  statusFilter.value = value
  applyFilters()
}

function resetFilters() {
  searchInput.value = ''
  statusFilter.value = ''
  applyFilters()
}

function applyFilters() {
  clearTimeout(searchTimer)
  search.value = searchInput.value.trim()
  currentPage.value = 1
  fetchKeys()
}

function changePage(delta) {
  if (loading.value || bulkAction.value || mutating.value) return
  const next = currentPage.value + delta
  if (next < 1 || next > Math.ceil(totalCount.value / pageSize.value)) return
  currentPage.value = next
  fetchKeys()
}

function changePageSize(value) {
  pageSize.value = Number(value)
  applyFilters()
}

function openCreate() {
  if (createSaving.value || mutating.value) return
  editingKey.value = null
  createForm.name = ''
  createForm.description = ''
  createForm.scope = 'all'
  createForm.assistantUuids = []
  createForm.expiresInDays = '90'
  createError.value = ''
  showCreate.value = true
  fetchAssistants()
}

function openSettings(key) {
  if (createSaving.value || mutating.value || key.status === 'revoked') return
  editingKey.value = { uuid: key.uuid, updated_at: key.updated_at }
  createForm.name = key.name
  createForm.description = key.description || ''
  createForm.scope = key.scope
  createForm.assistantUuids = [...(key.assistant_uuids || [])]
  createError.value = ''
  detailKey.value = null
  showCreate.value = true
  fetchAssistants()
}

function closeCreate() {
  if (createSaving.value) return
  showCreate.value = false
  editingKey.value = null
}

async function createKey() {
  if (createSaving.value) return
  if (!createForm.name.trim()) {
    createError.value = t('management.apiKeyNameRequired')
    return
  }
  if (createForm.scope === 'selected' && !createForm.assistantUuids.length) {
    createError.value = t('management.apiKeySelectAssistantRequired')
    return
  }
  createSaving.value = true
  createError.value = ''
  try {
    const payload = {
      name: createForm.name.trim(),
      description: createForm.description.trim(),
      scope: createForm.scope,
      assistant_uuids:
        createForm.scope === 'selected' ? [...createForm.assistantUuids] : []
    }
    if (editingKey.value) {
      await updateTenantApiKey(editingKey.value.uuid, {
        ...payload,
        updated_at: editingKey.value.updated_at
      })
      showCreate.value = false
      editingKey.value = null
      showSuccess(t('management.apiKeySettingsSaved'))
      await fetchKeys()
      return
    }
    const data = await createTenantApiKey({
      ...payload,
      expires_in_days: Number(createForm.expiresInDays)
    })
    showCreate.value = false
    secretRotated.value = false
    createdSecret.value = data.key
    detailKey.value = null
    showSuccess(t('management.apiKeyCreated'))
    await fetchKeys()
  } catch (err) {
    createError.value =
      err?.response?.status === 409
        ? t('management.apiKeySettingsConflict')
        : err?.response?.data?.name === 'API_KEY_NAME_REQUIRED'
          ? t('management.apiKeyNameRequired')
          : err?.response?.data?.assistant_uuids === 'INVALID_ASSISTANT_SCOPE'
            ? t('management.apiKeySelectAssistantRequired')
            : t('management.apiKeySaveFailed')
  } finally {
    createSaving.value = false
  }
}

async function rotateKey(key) {
  if (mutating.value || key.status === 'revoked') return
  const ok = window.confirm(
    t(
      key.status === 'expired'
        ? 'management.apiKeyRotateExpiredConfirm'
        : 'management.apiKeyRotateConfirm',
      { name: key.name }
    )
  )
  if (!ok) return
  mutating.value = true
  try {
    const data = await rotateTenantApiKey(
      key.uuid,
      key.status === 'expired' ? { expires_in_days: 90 } : {}
    )
    createdSecret.value = data.key
    secretRotated.value = true
    detailKey.value = null
    showSuccess(t('management.apiKeyRotated'))
    await fetchKeys()
  } catch {
    showError(t('management.apiKeySaveFailed'))
  } finally {
    mutating.value = false
  }
}

async function toggleKey(key) {
  if (mutating.value || ['revoked', 'expired'].includes(key.status)) return
  const nextStatus = key.raw_status === 'disabled' ? 'active' : 'disabled'
  mutating.value = true
  try {
    await updateTenantApiKey(key.uuid, { status: nextStatus })
    showSuccess(t('management.apiKeySaved'))
    await fetchKeys()
  } catch {
    showError(t('management.apiKeySaveFailed'))
  } finally {
    mutating.value = false
  }
}

async function revokeKey(key) {
  if (mutating.value || key.status === 'revoked') return
  const ok = window.confirm(
    t('management.apiKeyRevokeConfirm', { name: key.name })
  )
  if (!ok) return
  mutating.value = true
  try {
    await revokeTenantApiKey(key.uuid)
    detailKey.value = null
    showSuccess(t('management.apiKeyRevoked'))
    await fetchKeys()
  } catch {
    showError(t('management.apiKeySaveFailed'))
  } finally {
    mutating.value = false
  }
}

function closeSecret() {
  createdSecret.value = ''
}

async function copySecret() {
  try {
    await navigator.clipboard.writeText(createdSecret.value)
    showSuccess(t('common.copied'))
  } catch {
    showError(t('common.error'))
  }
}

onMounted(() => {
  fetchKeys()
  fetchAssistants()
})
</script>
