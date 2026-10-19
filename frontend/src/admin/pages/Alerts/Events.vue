<template>
  <AdminLayout>
    <div class="w-full max-w-full space-y-4 p-4 md:p-6">
      <div>
        <h1 class="admin-page-title">
          {{ t('alertManagement.events.title') }}
        </h1>
        <p class="admin-page-subtitle">
          {{ t('alertManagement.events.subtitle') }}
        </p>
      </div>
      <BaseCard class="[&>.card-body]:p-4 md:[&>.card-body]:p-6">
        <div class="mb-6 flex flex-wrap items-end gap-3">
          <BaseSelect
            v-model="subject"
            :label="t('alertManagement.unified.subject')"
            class="w-full sm:w-48"
            :full-width="false"
            @change="resetFilters"
          >
            <option value="">
              {{ t('alertManagement.unified.allResources') }}
            </option>
            <option
              v-for="value in Object.keys(catalog)"
              :key="value"
              :value="value"
            >
              {{ t(`alertManagement.unified.subjects.${value}`) }}
            </option>
          </BaseSelect>
          <BaseSelect
            v-model="eventType"
            :label="t('alertManagement.events.eventType')"
            class="w-full sm:w-64"
            :full-width="false"
            @change="load(1)"
          >
            <option value="">
              {{ t('alertManagement.events.allEvents') }}
            </option>
            <option v-for="value in eventOptions" :key="value" :value="value">
              {{ t(`alertManagement.unified.events.${value}`) }}
            </option>
          </BaseSelect>
          <BaseButton
            variant="outline"
            :loading="loading"
            @click="load(page)"
            >{{ t('common.refresh') }}</BaseButton
          >
        </div>
        <p v-if="error" role="alert" class="mb-3 text-sm text-danger-700">
          {{ error }}
        </p>
        <BaseLoading v-if="loading" />
        <AlertEmptyState
          v-else-if="!events.length"
          :text="t('alertManagement.events.noEvents')"
        />
        <template v-else>
          <div class="overflow-hidden rounded-lg border border-line bg-surface">
            <div class="overflow-x-auto">
              <table
                class="min-w-[64rem] w-full table-fixed divide-y divide-line text-left text-sm leading-5"
              >
                <thead class="bg-surface-sunken text-theme-muted">
                  <tr>
                    <th
                      class="px-4 py-3 text-xs font-semibold uppercase tracking-wide text-theme-muted"
                    >
                      {{ t('alertManagement.events.notificationTitle') }}
                    </th>
                    <th
                      class="w-44 px-4 py-3 text-xs font-semibold uppercase tracking-wide text-theme-muted"
                    >
                      {{ t('alertManagement.events.eventType') }}
                    </th>
                    <th
                      class="w-36 px-4 py-3 text-xs font-semibold uppercase tracking-wide text-theme-muted"
                    >
                      {{ t('alertManagement.events.sourceUser') }}
                    </th>
                    <th
                      class="w-40 px-4 py-3 text-xs font-semibold uppercase tracking-wide text-theme-muted"
                    >
                      {{ t('alertManagement.rules.channel') }}
                    </th>
                    <th
                      class="w-44 px-4 py-3 text-xs font-semibold uppercase tracking-wide text-theme-muted"
                    >
                      {{ t('alertManagement.events.createdAt') }}
                    </th>
                    <th
                      class="w-24 px-4 py-3 text-xs font-semibold uppercase tracking-wide text-theme-muted"
                    >
                      {{ t('alertManagement.events.status') }}
                    </th>
                  </tr>
                </thead>
                <tbody
                  class="divide-y divide-line bg-surface text-theme-secondary"
                >
                  <tr
                    v-for="row in events"
                    :key="row.uuid"
                    class="transition-colors hover:bg-surface-hover"
                  >
                    <td class="px-4 py-3">
                      <button
                        type="button"
                        class="line-clamp-2 max-w-full break-words text-left text-sm font-medium leading-5 text-primary-600 hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary-500"
                        :title="alertEventTitle(row, t)"
                        @click="openDetail(row)"
                      >
                        {{ alertEventTitle(row, t) }}
                      </button>
                    </td>
                    <td class="px-4 py-3">
                      <p
                        class="truncate"
                        :title="
                          t(`alertManagement.unified.events.${row.event_type}`)
                        "
                      >
                        {{
                          t(`alertManagement.unified.events.${row.event_type}`)
                        }}
                      </p>
                    </td>
                    <td class="px-4 py-3">
                      <p class="truncate" :title="alertEventSource(row, t)">
                        {{ alertEventSource(row, t) }}
                      </p>
                    </td>
                    <td class="px-4 py-3">
                      <p class="truncate" :title="channelNames(row)">
                        {{ channelNames(row) }}
                      </p>
                    </td>
                    <td class="whitespace-nowrap px-4 py-3 tabular-nums">
                      {{
                        new Date(
                          row.detail?.occurred_at || row.created_at
                        ).toLocaleString()
                      }}
                    </td>
                    <td class="px-4 py-3">
                      <AlertStatusBadge :status="row.status" compact />
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>
          <PaginationBar
            v-model:page-size="pageSize"
            :current-page="page"
            :total="total"
            @page-size-change="load(1)"
            @prev="load(page - 1)"
            @next="load(page + 1)"
          />
        </template>
      </BaseCard>
      <AlertEventDetail
        :show="!!selectedUuid"
        :event="detail"
        :loading="detailLoading"
        :error="detailError"
        :retrying="!!retrying"
        @close="closeDetail"
        @refresh="fetchDetail"
        @retry="retry(detail)"
      />
    </div>
  </AdminLayout>
</template>
<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { alertsAdminApi } from '@/admin/api'
import AdminLayout from '@/admin/layout/AdminLayout.vue'
import { notificationTargetLabel } from '@/admin/utils/notificationTargets'
import BaseCard from '@/components/ui/BaseCard.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseLoading from '@/components/ui/BaseLoading.vue'
import BaseSelect from '@/components/ui/BaseSelect.vue'
import PaginationBar from '@/components/ui/PaginationBar.vue'
import AlertEmptyState from './AlertEmptyState.vue'
import AlertEventDetail from './AlertEventDetail.vue'
import AlertStatusBadge from './AlertStatusBadge.vue'
import {
  alertApiError,
  alertEventSource,
  alertEventTitle
} from './alertOptions'

const { t } = useI18n()
const route = useRoute()
const events = ref([]),
  catalog = ref({}),
  subject = ref(String(route.query.subject || '')),
  eventType = ref(''),
  loading = ref(false),
  error = ref(''),
  retrying = ref('')
const page = ref(1),
  pageSize = ref(20),
  total = ref(0)
const selectedUuid = ref(''),
  detail = ref(null),
  detailLoading = ref(false),
  detailError = ref('')
let loadRequestId = 0,
  detailRequestId = 0
const eventOptions = computed(() =>
  subject.value
    ? catalog.value[subject.value] || []
    : [...new Set(Object.values(catalog.value).flat())]
)

function channelNames(row) {
  return (
    [
      ...new Set(
        (row.deliveries || []).map(
          (item) =>
            item.destination?.name ||
            notificationTargetLabel(item.destination?.channel_type, t)
        )
      )
    ].join(' · ') || '–'
  )
}
async function load(value = 1) {
  const requestId = ++loadRequestId
  page.value = Math.max(1, value)
  loading.value = true
  error.value = ''
  try {
    const data = await alertsAdminApi.getEvents({
      page: page.value,
      page_size: pageSize.value,
      subject: subject.value,
      event_type: eventType.value,
      source_uuid: route.query.source_uuid || undefined
    })
    if (requestId !== loadRequestId) return
    events.value = data.results || []
    total.value = data.count || 0
  } catch (err) {
    if (requestId === loadRequestId) error.value = alertApiError(err)
  } finally {
    if (requestId === loadRequestId) loading.value = false
  }
}
function openDetail(row) {
  selectedUuid.value = row.uuid
  detail.value = row
  void fetchDetail()
}
function closeDetail() {
  selectedUuid.value = ''
  detail.value = null
}
async function fetchDetail() {
  const uuid = selectedUuid.value
  if (!uuid) return
  const requestId = ++detailRequestId
  detailLoading.value = true
  detailError.value = ''
  try {
    const data = await alertsAdminApi.getEvent(uuid)
    if (selectedUuid.value !== uuid || requestId !== detailRequestId) return
    detail.value = data
    events.value = events.value.map((row) => (row.uuid === uuid ? data : row))
  } catch (err) {
    if (selectedUuid.value === uuid && requestId === detailRequestId)
      detailError.value = alertApiError(err)
  } finally {
    if (selectedUuid.value === uuid && requestId === detailRequestId)
      detailLoading.value = false
  }
}
function resetFilters() {
  eventType.value = ''
  load(1)
}
async function retry(row) {
  if (!row || retrying.value) return
  retrying.value = row.uuid
  detailError.value = ''
  try {
    await alertsAdminApi.retryEvent(row.uuid)
    await load(page.value)
    if (selectedUuid.value === row.uuid) await fetchDetail()
  } catch (err) {
    if (selectedUuid.value === row.uuid) detailError.value = alertApiError(err)
  } finally {
    retrying.value = ''
  }
}
watch(
  () => route.query,
  () => {
    subject.value = String(route.query.subject || '')
    load(1)
  }
)
onMounted(async () => {
  try {
    catalog.value = await alertsAdminApi.getCatalog()
    await load()
  } catch (err) {
    error.value = alertApiError(err)
  }
})
</script>
