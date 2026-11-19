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
              {{ t('management.platformAuditTitle') }}
            </h1>
            <p class="max-w-3xl text-sm leading-6 text-ink-500">
              {{ t('management.platformAuditSubtitle') }}
            </p>
            <span
              class="inline-flex rounded-md border border-line bg-surface-sunken px-2 py-1 text-xs text-ink-500"
            >
              {{ t('management.platformAuditCount', { count: totalCount }) }}
            </span>
          </div>
          <BaseButton variant="outline" size="sm" @click="exportCsv">
            {{ t('common.export') }}
          </BaseButton>
        </div>

        <div class="flex min-h-0 flex-1 flex-col px-5 py-4">
          <form
            class="mb-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-[minmax(0,1fr)_150px_180px_130px_auto_auto]"
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
                :placeholder="t('management.auditSearch')"
              />
            </label>
            <label>
              <span class="mb-1 block text-sm font-medium text-ink-700">{{
                t('management.auditType')
              }}</span>
              <BaseSelect v-model="actionFilter" class="form-input">
                <option value="">{{ t('management.auditAllTypes') }}</option>
                <option value="delegation">delegation</option>
                <option value="member">member</option>
                <option value="api_key">api_key</option>
                <option value="tenant">tenant</option>
                <option value="catalog">
                  {{ t('catalog.officialDirectory') }}
                </option>
                <option value="node">
                  {{ t('lensAdmin.pages.lensnodes.title') }}
                </option>
                <option value="notification_channel.">
                  {{ t('notificationManagement.channels.title') }}
                </option>
                <option value="notification_rule.">
                  {{ t('alertManagement.rules.title') }}
                </option>
                <option value="notification_event.">
                  {{ t('alertManagement.events.title') }}
                </option>
                <option value="notification_message.">
                  {{ t('alertManagement.messages.title') }}
                </option>
                <option value="notification_config.">
                  {{ t('notificationManagement.settings.title') }}
                </option>
                <option value="task.">
                  {{ t('taskManagement.list.title') }}
                </option>
                <option value="task_config.">
                  {{ t('taskManagement.settings.title') }}
                </option>
                <option value="global_setting">
                  {{ t('lensAdmin.pages.settings.title') }}
                </option>
                <option value="llm_config.">{{ t('llm.config.title') }}</option>
                <option value="metering_config.">
                  {{ t('llm.dataSettings.title') }}
                </option>
              </BaseSelect>
            </label>
            <label>
              <span class="mb-1 block text-sm font-medium text-ink-700">{{
                t('management.platformTenant')
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
            <label>
              <span class="mb-1 block text-sm font-medium text-ink-700">{{
                t('management.auditResult')
              }}</span>
              <BaseSelect v-model="resultFilter" class="form-input">
                <option value="">{{ t('management.auditAllResults') }}</option>
                <option value="success">
                  {{ t('management.auditSuccess') }}
                </option>
                <option value="denied">
                  {{ t('management.auditDenied') }}
                </option>
                <option value="failed">
                  {{ t('management.auditFailed') }}
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
              {{ t('management.auditNoRecords') }}
            </p>
          </div>

          <div
            v-else
            class="relative min-h-0 flex-1 overflow-auto rounded-lg border border-line bg-surface"
          >
            <table class="w-full min-w-[900px] divide-y divide-line">
              <colgroup>
                <col class="w-[16%]" />
                <col class="w-[18%]" />
                <col class="w-[14%]" />
                <col class="w-[12%]" />
                <col class="w-[10%]" />
                <col class="w-[16%]" />
                <col class="w-[14%]" />
              </colgroup>
              <thead class="sticky top-0 z-10 bg-surface-sunken">
                <tr>
                  <th class="table-head">{{ t('management.auditTime') }}</th>
                  <th class="table-head">{{ t('management.auditAction') }}</th>
                  <th class="table-head">
                    {{ t('management.platformTenant') }}
                  </th>
                  <th class="table-head">{{ t('management.auditActor') }}</th>
                  <th class="table-head">{{ t('management.auditResult') }}</th>
                  <th class="table-head">
                    {{ t('management.auditResource') }}
                  </th>
                  <th class="table-head">{{ t('management.auditIp') }}</th>
                </tr>
              </thead>
              <tbody class="divide-y divide-line bg-surface">
                <tr
                  v-for="row in rows"
                  :key="row.uuid"
                  class="hover:bg-line-soft"
                >
                  <td class="px-4 py-3 text-sm text-ink-500">
                    {{ formatDate(row.created_at) }}
                  </td>
                  <td class="px-4 py-3 text-sm font-medium text-ink-800">
                    {{ actionLabel(row.action) }}
                  </td>
                  <td class="px-4 py-3 text-sm text-ink-600">
                    {{ row.tenant?.name || '—' }}
                  </td>
                  <td class="px-4 py-3 text-sm text-ink-600">
                    {{ row.actor }}
                  </td>
                  <td class="px-4 py-3 text-sm">
                    <span :class="resultClass(row.result)">{{
                      resultLabel(row.result)
                    }}</span>
                  </td>
                  <td class="px-4 py-3 font-mono text-xs text-ink-500">
                    {{ row.resource_type || '—'
                    }}<span v-if="row.resource_id">
                      · {{ row.resource_id }}</span
                    >
                  </td>
                  <td class="px-4 py-3 font-mono text-xs text-ink-500">
                    {{ row.ip_address || '—' }}
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
    </div>
  </AdminLayout>
</template>

<script setup>
import BaseSelect from '@/components/ui/BaseSelect.vue'
import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'

import AdminLayout from '@/admin/layout/AdminLayout.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseLoading from '@/components/ui/BaseLoading.vue'
import PaginationBar from '@/components/ui/PaginationBar.vue'
import { getPlatformAudit, getPlatformTenants } from '@/api/platform'

const { t } = useI18n()
const rows = ref([])
const tenants = ref([])
const loading = ref(false)
const error = ref('')
const totalCount = ref(0)
const currentPage = ref(1)
const pageSize = ref(20)
const searchInput = ref('')
const search = ref('')
const actionFilter = ref('')
const tenantFilter = ref('')
const resultFilter = ref('')

const hasFilters = computed(
  () =>
    !!search.value ||
    !!actionFilter.value ||
    !!tenantFilter.value ||
    !!resultFilter.value
)

function formatDate(value) {
  if (!value) return '—'
  return new Date(value).toLocaleString()
}

function actionLabel(action) {
  return t(
    `management.auditActions.${String(action || '').replaceAll('.', '_')}`,
    action
  )
}

function resultLabel(result) {
  if (result === 'denied') return t('management.auditDenied')
  if (result === 'failed') return t('management.auditFailed')
  return t('management.auditSuccess')
}

function resultClass(result) {
  if (result === 'denied' || result === 'failed') return 'text-danger-700'
  return 'text-success-700'
}

async function fetchAudit() {
  loading.value = true
  error.value = ''
  try {
    const params = {
      page: currentPage.value,
      page_size: pageSize.value
    }
    if (search.value) params.search = search.value
    if (actionFilter.value) params.action = actionFilter.value
    if (tenantFilter.value) params.tenant = tenantFilter.value
    if (resultFilter.value) params.result = resultFilter.value
    const data = await getPlatformAudit(params)
    rows.value = data?.results || []
    totalCount.value = data?.count || 0
  } catch (err) {
    error.value =
      err?.response?.data?.detail || t('management.platformAuditLoadFailed')
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
  fetchAudit()
}

function resetFilters() {
  searchInput.value = ''
  search.value = ''
  actionFilter.value = ''
  tenantFilter.value = ''
  resultFilter.value = ''
  currentPage.value = 1
  fetchAudit()
}

function goPrevPage() {
  if (currentPage.value <= 1) return
  currentPage.value -= 1
  fetchAudit()
}

function goNextPage() {
  if (currentPage.value * pageSize.value >= totalCount.value) return
  currentPage.value += 1
  fetchAudit()
}

function handlePageSizeChange(value) {
  pageSize.value = value
  currentPage.value = 1
  fetchAudit()
}

function exportCsv() {
  const header = [
    'time',
    'action',
    'tenant',
    'actor',
    'result',
    'resource',
    'ip'
  ]
  const lines = rows.value.map((row) => [
    row.created_at,
    row.action,
    row.tenant?.name || '',
    row.actor,
    row.result,
    `${row.resource_type || ''} ${row.resource_id || ''}`.trim(),
    row.ip_address || ''
  ])
  const csv = [header, ...lines]
    .map((line) =>
      line.map((value) => `"${String(value).replaceAll('"', '""')}"`).join(',')
    )
    .join('\n')
  const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = 'platform-audit.csv'
  link.click()
  URL.revokeObjectURL(url)
}

onMounted(() => {
  fetchTenants()
  fetchAudit()
})
</script>
