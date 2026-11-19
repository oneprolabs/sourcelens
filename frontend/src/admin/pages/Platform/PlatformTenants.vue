<template>
  <AdminLayout>
    <div class="flex h-full min-h-0 max-w-full flex-col gap-4 py-4">
      <section
        class="tenant-directory flex max-h-full min-h-0 w-full flex-col overflow-hidden rounded-lg border border-line bg-surface shadow-sm"
      >
        <div class="flex-shrink-0 border-b border-line px-3 py-4 md:px-5">
          <h1 class="text-xl font-semibold text-ink-900">
            {{ t('management.platformTenantsTitle') }}
          </h1>
          <p class="mt-2 max-w-3xl text-sm leading-6 text-ink-500">
            {{ t('management.platformTenantsSubtitle') }}
          </p>
        </div>

        <div class="flex min-h-0 flex-1 flex-col px-3 py-4 md:px-5">
          <form
            class="tenant-filters mb-4 grid shrink-0 gap-2.5"
            @submit.prevent="fetchTenants"
          >
            <label class="min-w-0">
              <span class="filter-label">
                {{ t('common.search') }}
              </span>
              <input
                v-model="searchInput"
                type="text"
                class="form-input bg-surface text-theme"
                :placeholder="t('management.platformTenantSearch')"
              />
            </label>
            <div class="min-w-0">
              <label for="platform-tenant-status" class="filter-label">
                {{ t('management.memberStatus') }}
              </label>
              <BaseSelect id="platform-tenant-status" v-model="statusFilter">
                <option value="">{{ t('management.allStatuses') }}</option>
                <option value="active">
                  {{ t('management.tenantActive') }}
                </option>
                <option value="suspended">
                  {{ t('management.tenantSuspended') }}
                </option>
                <option value="pending">
                  {{ t('management.tenantPending') }}
                </option>
              </BaseSelect>
            </div>
            <div class="tenant-filter-actions">
              <BaseButton
                type="submit"
                :loading="loading"
                class="tenant-button"
                @click.prevent="fetchTenants"
              >
                <span>{{ t('common.search') }}</span>
              </BaseButton>
              <BaseButton
                variant="outline"
                class="tenant-button"
                :disabled="loading || (!searchInput && !statusFilter)"
                @click="resetFilters"
              >
                {{ t('management.resetFilters') }}
              </BaseButton>
            </div>
          </form>

          <BaseLoading v-if="loading && !tenants.length" />

          <div
            v-else-if="error"
            class="flex min-h-0 flex-1 items-center justify-center rounded-lg border border-line bg-surface-sunken py-16 text-center"
          >
            <p class="text-sm font-medium text-danger-700">{{ error }}</p>
          </div>

          <div
            v-else
            class="relative min-h-0 flex-1 overflow-auto rounded-lg border border-line bg-surface"
            role="region"
            :aria-label="t('management.platformTenantsTitle')"
            :aria-busy="loading"
            tabindex="0"
          >
            <table class="tenant-table w-full divide-y divide-line">
              <colgroup>
                <col class="w-full" />
                <col span="7" />
              </colgroup>
              <thead class="sticky top-0 z-10 bg-surface-sunken">
                <tr>
                  <th scope="col">
                    {{ t('management.platformTenant') }}
                  </th>
                  <th scope="col">{{ t('management.memberStatus') }}</th>
                  <th scope="col" class="text-right">
                    {{ t('management.groupMembers') }}
                  </th>
                  <th scope="col" class="text-right">
                    {{ t('management.platformAssistants') }}
                  </th>
                  <th scope="col" class="text-right">
                    {{ t('management.platformDatasources') }}
                  </th>
                  <th scope="col" class="text-right">
                    {{ t('management.platformRuns7d') }}
                  </th>
                  <th scope="col">
                    {{ t('management.tenantCreatedAt') }}
                  </th>
                  <th scope="col" class="text-right">
                    {{ t('common.actions') }}
                  </th>
                </tr>
              </thead>
              <tbody class="divide-y divide-line bg-surface">
                <tr
                  v-for="tenant in tenants"
                  :key="tenant.uuid"
                  class="hover:bg-line-soft"
                >
                  <td class="tenant-identity">
                    <div class="flex items-center gap-2.5">
                      <span
                        aria-hidden="true"
                        class="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-brand-50 text-sm font-semibold text-brand-700"
                      >
                        {{ tenantInitial(tenant) }}
                      </span>
                      <div class="w-0 min-w-0 flex-1 leading-5">
                        <p
                          class="truncate font-medium text-ink-900"
                          :title="tenant.name"
                        >
                          {{ tenant.name }}
                        </p>
                        <p
                          class="truncate font-mono text-xs text-ink-500"
                          :title="tenant.slug"
                        >
                          {{ tenant.slug }}
                        </p>
                      </div>
                    </div>
                  </td>
                  <td>
                    <span
                      class="inline-flex rounded-full px-2 py-0.5 text-xs font-medium"
                      :class="tenantStatusClass(tenant.status)"
                    >
                      {{ tenantStatusLabel(tenant.status) }}
                    </span>
                  </td>
                  <td class="text-right tabular-nums text-ink-700">
                    {{ tenant.member_count }}
                  </td>
                  <td class="text-right tabular-nums text-ink-700">
                    {{ tenant.assistant_count }}
                  </td>
                  <td class="text-right tabular-nums text-ink-700">
                    {{ tenant.datasource_count }}
                  </td>
                  <td class="text-right tabular-nums text-ink-700">
                    {{ tenant.run_count_7d }}
                  </td>
                  <td class="tabular-nums text-ink-500">
                    {{ formatDate(tenant.created_at) }}
                  </td>
                  <td class="text-right">
                    <BaseButton
                      size="sm"
                      variant="outline"
                      class="tenant-button"
                      :loading="startingTenantId === tenant.uuid"
                      :disabled="!!startingTenantId"
                      :aria-busy="startingTenantId === tenant.uuid"
                      @click="enterTenant(tenant.uuid)"
                    >
                      <span>{{ t('management.enterTenant') }}</span>
                    </BaseButton>
                  </td>
                </tr>
              </tbody>
            </table>
            <p
              v-if="!tenants.length"
              class="px-3 py-10 text-center text-sm text-ink-500"
              role="status"
            >
              {{ t('common.noData') }}
            </p>
          </div>
        </div>
      </section>
    </div>
  </AdminLayout>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute } from 'vue-router'

import AdminLayout from '@/admin/layout/AdminLayout.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseLoading from '@/components/ui/BaseLoading.vue'
import BaseSelect from '@/components/ui/BaseSelect.vue'
import { getPlatformTenants } from '@/api/platform'
import { useEnterTenant } from './useEnterTenant'

const { t } = useI18n()
const { enterTenant, startingTenantId } = useEnterTenant()

const tenants = ref([])
const loading = ref(false)
const error = ref('')
const searchInput = ref('')
const initialStatus = useRoute().query.status
const statusFilter = ref(
  ['active', 'pending', 'suspended'].includes(initialStatus)
    ? initialStatus
    : ''
)

function tenantInitial(tenant) {
  return (tenant.name || tenant.slug || 'T').trim().charAt(0).toUpperCase()
}

function tenantStatusLabel(status) {
  if (status === 'suspended') return t('management.tenantSuspended')
  if (status === 'pending') return t('management.tenantPending')
  return t('management.tenantActive')
}

function tenantStatusClass(status) {
  if (status === 'suspended') return 'bg-surface-sunken text-ink-600'
  if (status === 'pending') return 'bg-warning-50 text-warning-700'
  return 'bg-success-50 text-success-700'
}

function formatDate(value) {
  if (!value) return '—'
  return new Date(value).toLocaleDateString()
}

async function fetchTenants() {
  if (loading.value) return
  loading.value = true
  error.value = ''
  try {
    const data = await getPlatformTenants({
      search: searchInput.value.trim() || undefined,
      status: statusFilter.value || undefined
    })
    tenants.value = data?.results || []
  } catch (err) {
    error.value =
      err?.response?.data?.detail || t('management.platformTenantsLoadFailed')
  } finally {
    loading.value = false
  }
}

function resetFilters() {
  searchInput.value = ''
  statusFilter.value = ''
  fetchTenants()
}

onMounted(fetchTenants)
</script>

<style scoped>
.tenant-directory {
  container-type: inline-size;
  --tenant-control-height: 44px;
  --tenant-field-padding: 12px;
}

.tenant-filters {
  grid-template-columns: minmax(0, 1fr);
  align-items: end;
}

.filter-label {
  display: block;
  margin-bottom: 4px;
  color: var(--sl-text-secondary);
  font-size: 13px;
  font-weight: 500;
}

.tenant-filter-actions {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
}

.layout-admin .tenant-filters .form-input,
.tenant-filters :deep([role='combobox']) {
  box-sizing: border-box;
  height: var(--tenant-control-height);
  padding-block: 0;
  padding-inline-start: var(--tenant-field-padding);
  border-color: var(--sl-form-border);
  box-shadow: none;
  font-size: 13px;
}

.layout-admin .tenant-filters .form-input:focus,
.tenant-filters :deep([role='combobox']:focus-visible) {
  border-color: var(--sl-accent);
}

:root:has(.layout-admin) .tenant-button[data-ui-button] {
  position: relative;
  height: var(--tenant-control-height);
  border-radius: 8px;
  padding-block: 0;
  white-space: nowrap;
}

.tenant-button:disabled > svg {
  position: absolute;
  inset: 50% auto auto 50%;
  margin: 0;
  translate: -50% -50%;
}

.tenant-button:disabled:has(> svg) > span {
  visibility: hidden;
}

.tenant-table :is(th, td) {
  padding: 8px 12px;
  vertical-align: middle;
  white-space: nowrap;
}

.tenant-table th {
  font-size: 12px;
  font-weight: 600;
  color: var(--sl-text-secondary);
}

.tenant-table td {
  font-size: 13px;
}

.tenant-table th:not(.text-right) {
  text-align: left;
}

.tenant-identity {
  min-width: 13rem;
}

@container (min-width: 29rem) {
  .tenant-filters {
    grid-template-columns: minmax(0, 1fr) minmax(10rem, 0.5fr);
  }

  .tenant-filter-actions {
    grid-column: 1 / -1;
    display: flex;
  }
}

@container (min-width: 40rem) {
  .tenant-filters {
    grid-template-columns: minmax(0, 20rem) 11rem auto;
    justify-content: start;
  }

  .tenant-filter-actions {
    grid-column: auto;
  }
}

@media (min-width: 821px) {
  .tenant-directory {
    --tenant-control-height: 34px;
  }
}
</style>
