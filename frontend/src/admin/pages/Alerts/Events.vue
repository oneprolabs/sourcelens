<template>
  <AdminLayout>
    <div class="w-full max-w-full space-y-4 p-4 md:p-6">
      <div>
        <h1 class="text-lg font-semibold text-theme">
          {{ t('alertManagement.events.title') }}
        </h1>
        <p class="mt-1 text-sm text-theme-muted">
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
          <BaseButton variant="outline" :loading="loading" @click="load(page)">
            {{ t('common.refresh') }}
          </BaseButton>
        </div>
        <p v-if="error" role="alert" class="text-sm text-danger-700">
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
                class="min-w-[48rem] w-full divide-y divide-line text-left text-sm"
              >
                <thead class="bg-surface-sunken text-theme-muted">
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
                      class="px-4 py-3 text-xs font-semibold uppercase tracking-wider text-theme-secondary"
                    >
                      {{
                        key === 'assistant'
                          ? t('alertManagement.unified.resourceScope')
                          : t(`alertManagement.events.${key}`)
                      }}
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
                    <td class="whitespace-nowrap px-4 py-3">
                      {{ new Date(row.created_at).toLocaleString() }}
                    </td>
                    <td class="px-4 py-3">
                      {{
                        t(`alertManagement.unified.events.${row.event_type}`)
                      }}
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
                    <td class="max-w-md break-words px-4 py-3">
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
                            <AlertStatusBadge
                              :status="delivery.status"
                              class="ml-1"
                            />
                            <span class="ml-1">{{
                              delivery.destination.language || 'en-US'
                            }}</span>
                            <span
                              v-if="delivery.phase === 'diagnosis'"
                              class="ml-1"
                              >{{
                                t('alertManagement.diagnosis.supplement')
                              }}</span
                            >
                            <div
                              v-if="delivery.diagnosis"
                              class="mt-2 rounded-lg border border-line bg-surface-sunken p-3 text-theme-secondary"
                            >
                              {{ t('alertManagement.diagnosis.title') }} ·
                              {{
                                t(
                                  `alertManagement.diagnosis.statuses.${delivery.diagnosis.status}`
                                )
                              }}
                              <p
                                v-if="delivery.diagnosis.result?.summary"
                                class="mt-1 whitespace-pre-wrap"
                              >
                                {{ delivery.diagnosis.result.summary }}
                              </p>
                              <p
                                v-if="
                                  delivery.diagnosis.result?.root_cause
                                    ?.description
                                "
                                class="mt-1"
                              >
                                {{
                                  delivery.diagnosis.result.root_cause
                                    .description
                                }}
                              </p>
                              <p
                                v-for="(item, index) in delivery.diagnosis
                                  .result?.recommendations || []"
                                :key="index"
                                class="mt-1"
                              >
                                {{ item.action }}
                              </p>
                              <p
                                v-if="delivery.diagnosis.error_code"
                                class="mt-1"
                              >
                                {{
                                  t('alertManagement.diagnosis.fallbackHint')
                                }}
                              </p>
                            </div>
                            <span v-if="delivery.error_message">
                              · {{ delivery.error_message }}</span
                            >
                          </li>
                        </ul>
                        <BaseButton
                          v-if="
                            row.deliveries.some(
                              (item) => item.status === 'failed'
                            )
                          "
                          :disabled="retrying === row.uuid"
                          variant="outline"
                          size="sm"
                          class="mt-2"
                          @click="retry(row)"
                        >
                          {{ t('alertManagement.unified.retry') }}
                        </BaseButton>
                      </details>
                    </td>
                    <td class="whitespace-nowrap px-4 py-3">
                      <AlertStatusBadge :status="row.status" />
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
import BaseCard from '@/components/ui/BaseCard.vue'
import AlertEmptyState from './AlertEmptyState.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseLoading from '@/components/ui/BaseLoading.vue'
import AlertStatusBadge from './AlertStatusBadge.vue'
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
