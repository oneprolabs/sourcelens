<template>
  <AdminLayout>
    <div class="w-full max-w-full space-y-4 p-4 md:p-6">
      <div>
        <h1 class="text-xl font-semibold text-ink-900">
          {{ t('alertManagement.events.title') }}
        </h1>
        <p class="mt-1 text-sm text-ink-500">
          {{ t('alertManagement.events.subtitle') }}
        </p>
      </div>
      <div class="flex flex-wrap items-center gap-3">
        <label class="text-sm text-ink-700"
          >{{ t('alertManagement.unified.subject') }}
          <BaseSelect
            v-model="subject"
            class="ml-2 rounded border border-line bg-surface p-2"
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
        </label>
        <label class="text-sm text-ink-700"
          >{{ t('alertManagement.events.eventType') }}
          <BaseSelect
            v-model="eventType"
            class="ml-2 rounded border border-line bg-surface p-2"
            @change="load(1)"
          >
            <option value="">
              {{ t('alertManagement.events.allEvents') }}
            </option>
            <option v-for="value in eventOptions" :key="value" :value="value">
              {{ t(`alertManagement.unified.events.${value}`) }}
            </option>
          </BaseSelect>
        </label>
        <BaseButton
          variant="outline"
          size="sm"
          :loading="loading"
          @click="load(page)"
          >{{ t('common.refresh') }}</BaseButton
        >
      </div>
      <p v-if="error" role="alert" class="text-sm text-danger-700">
        {{ error }}
      </p>
      <BaseLoading v-if="loading" />
      <div v-else class="rounded-lg border border-line bg-surface">
        <div class="overflow-x-auto">
          <table
            class="min-w-[48rem] w-full divide-y divide-line text-left text-sm"
          >
            <thead class="bg-surface-sunken text-ink-500">
              <tr>
                <th
                  v-for="key in [
                    'createdAt',
                    'eventType',
                    'rule',
                    'assistant',
                    'detail',
                    'status'
                  ]"
                  :key="key"
                  class="px-4 py-3 font-medium"
                >
                  {{
                    key === 'assistant'
                      ? t('alertManagement.unified.resourceScope')
                      : t(`alertManagement.events.${key}`)
                  }}
                </th>
              </tr>
            </thead>
            <tbody class="divide-y divide-line text-ink-700">
              <tr v-for="row in events" :key="row.uuid">
                <td class="whitespace-nowrap px-4 py-3">
                  {{ new Date(row.created_at).toLocaleString() }}
                </td>
                <td class="px-4 py-3">
                  {{ t(`alertManagement.unified.events.${row.event_type}`) }}
                </td>
                <td class="px-4 py-3">{{ row.rule_name || '–' }}</td>
                <td class="px-4 py-3">
                  {{
                    row.detail?.assistant_name ||
                    row.detail?.datasource_name ||
                    row.detail?.topic ||
                    '–'
                  }}
                </td>
                <td class="max-w-md px-4 py-3">
                  <div>
                    {{
                      row.detail?.matched_events
                        ?.map((event) =>
                          t(`alertManagement.unified.events.${event}`)
                        )
                        .join(' · ') ||
                      row.detail?.error ||
                      '–'
                    }}
                  </div>
                  <details v-if="row.deliveries.length" class="mt-2">
                    <summary class="cursor-pointer text-primary-600">
                      {{ t('alertManagement.unified.deliveryDetails') }} ({{
                        row.deliveries.length
                      }})
                    </summary>
                    <ul class="mt-2 space-y-2">
                      <li
                        v-for="delivery in row.deliveries"
                        :key="delivery.uuid"
                        class="break-words text-xs"
                      >
                        {{
                          delivery.destination.name ||
                          delivery.destination.recipients?.join(', ') ||
                          delivery.destination.channel_uuid
                        }}
                        ·
                        {{
                          t(
                            `alertManagement.unified.statuses.${delivery.status}`
                          )
                        }}
                        <span v-if="delivery.error_message">
                          · {{ delivery.error_message }}</span
                        >
                      </li>
                    </ul>
                    <button
                      v-if="
                        row.deliveries.some((item) => item.status === 'failed')
                      "
                      :disabled="retrying === row.uuid"
                      class="mt-2 text-primary-600 underline disabled:opacity-50"
                      @click="retry(row)"
                    >
                      {{ t('alertManagement.unified.retry') }}
                    </button>
                  </details>
                </td>
                <td
                  class="whitespace-nowrap px-4 py-3"
                  :class="row.status === 'failed' ? 'text-danger-700' : ''"
                >
                  {{ t(`alertManagement.unified.statuses.${row.status}`) }}
                </td>
              </tr>
              <tr v-if="!events.length">
                <td colspan="6" class="p-10 text-center text-ink-500">
                  {{ t('alertManagement.events.noEvents') }}
                </td>
              </tr>
            </tbody>
          </table>
        </div>
        <PaginationBar
          v-model:page-size="pageSize"
          :current-page="page"
          :total="total"
          @page-size-change="load(1)"
          @prev="load(page - 1)"
          @next="load(page + 1)"
        />
      </div>
    </div>
  </AdminLayout>
</template>
<script setup>
import BaseSelect from '@/components/ui/BaseSelect.vue'
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { alertsAdminApi } from '@/admin/api'
import AdminLayout from '@/admin/layout/AdminLayout.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseLoading from '@/components/ui/BaseLoading.vue'
import PaginationBar from '@/components/ui/PaginationBar.vue'
import { alertApiError } from './alertOptions'
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
const eventOptions = computed(() =>
  subject.value
    ? catalog.value[subject.value] || []
    : [...new Set(Object.values(catalog.value).flat())]
)
async function load(value = 1) {
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
    events.value = data.results || []
    total.value = data.count || 0
  } catch (err) {
    error.value = alertApiError(err)
  } finally {
    loading.value = false
  }
}
function resetFilters() {
  eventType.value = ''
  load(1)
}
async function retry(row) {
  retrying.value = row.uuid
  try {
    await alertsAdminApi.retryEvent(row.uuid)
    await load(page.value)
  } catch (err) {
    error.value = alertApiError(err)
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
