<template>
  <AdminLayout>
    <div class="w-full max-w-full p-6">
      <div class="mb-4">
        <h1 class="text-lg font-semibold text-gray-900">
          {{ t('alertManagement.events.title') }}
        </h1>
        <p class="mt-1 text-sm text-gray-500">
          {{ t('alertManagement.events.subtitle') }}
        </p>
      </div>

      <div class="bg-white rounded-lg border border-gray-200 shadow-sm">
        <div class="p-6">
          <div class="flex flex-wrap items-center justify-between gap-3 mb-6">
            <BaseSelect v-model="eventFilter" class="w-56">
              <option value="">
                {{ t('alertManagement.events.allEvents') }}
              </option>
              <option value="run_failed">
                {{ t('alertManagement.events.eventRunFailed') }}
              </option>
              <option value="token_exceeded">
                {{ t('alertManagement.events.eventTokenExceeded') }}
              </option>
              <option value="rounds_exceeded">
                {{ t('alertManagement.events.eventRoundsExceeded') }}
              </option>
            </BaseSelect>
            <BaseButton
              variant="outline"
              size="sm"
              :loading="loading"
              @click="loadEvents"
            >
              {{ t('common.refresh') }}
            </BaseButton>
          </div>

          <BaseLoading v-if="loading" />
          <template v-else>
            <div
              v-if="events.length === 0"
              class="py-16 text-center rounded-lg border border-gray-200 bg-gray-50"
            >
              <p class="text-sm font-medium text-gray-600">
                {{ t('alertManagement.events.noEvents') }}
              </p>
            </div>
            <div
              v-else
              class="overflow-x-auto rounded-lg border border-gray-200"
            >
              <table class="min-w-full divide-y divide-gray-200">
                <thead class="bg-gray-50">
                  <tr>
                    <th
                      class="px-4 py-3 text-left text-xs font-semibold text-gray-700 uppercase tracking-wider"
                    >
                      {{ t('alertManagement.events.createdAt') }}
                    </th>
                    <th
                      class="px-4 py-3 text-left text-xs font-semibold text-gray-700 uppercase tracking-wider"
                    >
                      {{ t('alertManagement.events.eventType') }}
                    </th>
                    <th
                      class="px-4 py-3 text-left text-xs font-semibold text-gray-700 uppercase tracking-wider"
                    >
                      {{ t('alertManagement.events.rule') }}
                    </th>
                    <th
                      class="px-4 py-3 text-left text-xs font-semibold text-gray-700 uppercase tracking-wider"
                    >
                      {{ t('alertManagement.events.assistant') }}
                    </th>
                    <th
                      class="px-4 py-3 text-left text-xs font-semibold text-gray-700 uppercase tracking-wider"
                    >
                      {{ t('alertManagement.events.user') }}
                    </th>
                    <th
                      class="px-4 py-3 text-left text-xs font-semibold text-gray-700 uppercase tracking-wider"
                    >
                      {{ t('alertManagement.events.detail') }}
                    </th>
                    <th
                      class="px-4 py-3 text-left text-xs font-semibold text-gray-700 uppercase tracking-wider"
                    >
                      {{ t('alertManagement.events.status') }}
                    </th>
                  </tr>
                </thead>
                <tbody class="bg-white divide-y divide-gray-100">
                  <tr
                    v-for="row in events"
                    :key="row.uuid"
                    class="hover:bg-gray-50"
                  >
                    <td
                      class="px-4 py-3 whitespace-nowrap text-sm text-gray-700"
                    >
                      {{ formatDate(row.created_at) }}
                    </td>
                    <td
                      class="px-4 py-3 whitespace-nowrap text-sm text-gray-900"
                    >
                      {{ eventLabel(row.event_type) }}
                    </td>
                    <td class="px-4 py-3 text-sm text-gray-700">
                      {{ row.rule_name || '–' }}
                    </td>
                    <td class="px-4 py-3 text-sm text-gray-700">
                      {{ row.detail?.assistant_name || '–' }}
                    </td>
                    <td class="px-4 py-3 text-sm text-gray-700">
                      {{ row.detail?.username || '–' }}
                    </td>
                    <td class="px-4 py-3 text-sm text-gray-600 max-w-md">
                      {{ detailSummary(row) }}
                    </td>
                    <td class="px-4 py-3 whitespace-nowrap text-sm">
                      <span :class="statusClass(row.status)">
                        {{ statusLabel(row.status) }}
                      </span>
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
            <PaginationBar
              v-model:page-size="pageSize"
              :current-page="currentPage"
              :total="total"
              @page-size-change="handlePageSizeChange"
              @prev="goPrevPage"
              @next="goNextPage"
            />
          </template>
        </div>
      </div>
    </div>
  </AdminLayout>
</template>

<script setup>
import { onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'

import { alertsAdminApi } from '@/admin/api'
import AdminLayout from '@/admin/layout/AdminLayout.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseLoading from '@/components/ui/BaseLoading.vue'
import BaseSelect from '@/components/ui/BaseSelect.vue'
import PaginationBar from '@/components/ui/PaginationBar.vue'

const { t } = useI18n()

const loading = ref(false)
const events = ref([])
const total = ref(0)
const currentPage = ref(1)
const pageSize = ref(20)
const eventFilter = ref('')

function eventLabel(value) {
  const labels = {
    run_failed: t('alertManagement.events.eventRunFailed'),
    token_exceeded: t('alertManagement.events.eventTokenExceeded'),
    rounds_exceeded: t('alertManagement.events.eventRoundsExceeded')
  }
  return labels[value] || value
}

function statusLabel(value) {
  const labels = {
    dispatched: t('alertManagement.events.statusDispatched'),
    skipped: t('alertManagement.events.statusSkipped'),
    failed: t('alertManagement.events.statusFailed')
  }
  return labels[value] || value
}

function statusClass(value) {
  if (value === 'dispatched') return 'text-green-600'
  if (value === 'failed') return 'text-red-600'
  return 'text-gray-500'
}

function formatDate(value) {
  if (!value) return '–'
  return new Date(value).toLocaleString()
}

function detailSummary(row) {
  const detail = row.detail || {}
  const parts = []
  if (row.event_type === 'token_exceeded') {
    parts.push(
      `${t('alertManagement.events.tokens')}: ${detail.total_tokens} / ${detail.token_threshold}`
    )
  } else if (row.event_type === 'rounds_exceeded') {
    parts.push(
      `${t('alertManagement.events.rounds')}: ${detail.rounds} / ${detail.rounds_threshold}`
    )
  } else if (row.event_type === 'run_failed') {
    parts.push(detail.error || t('alertManagement.events.unknownError'))
  }
  if (detail.question) parts.push(detail.question)
  return parts.join(' · ') || '–'
}

function handlePageSizeChange() {
  currentPage.value = 1
  loadEvents()
}

function goPrevPage() {
  if (currentPage.value <= 1) return
  currentPage.value -= 1
  loadEvents()
}

function goNextPage() {
  if (currentPage.value * pageSize.value >= total.value) return
  currentPage.value += 1
  loadEvents()
}

async function loadEvents() {
  loading.value = true
  try {
    const params = {
      page: currentPage.value,
      page_size: pageSize.value
    }
    if (eventFilter.value) params.event_type = eventFilter.value
    const data = await alertsAdminApi.getEvents(params)
    events.value = Array.isArray(data) ? data : data?.results || []
    total.value = Array.isArray(data) ? data.length : data?.count || 0
  } catch {
    events.value = []
    total.value = 0
  } finally {
    loading.value = false
  }
}

watch(eventFilter, () => {
  currentPage.value = 1
  loadEvents()
})

onMounted(() => {
  loadEvents()
})
</script>
