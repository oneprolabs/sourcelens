<template>
  <BaseDrawer
    :show="show"
    :title="t('alertManagement.events.detailsTitle')"
    :subtitle="event ? alertEventTitle(event, t) : ''"
    width="2xl"
    @close="emit('close')"
  >
    <template #actions>
      <BaseButton
        variant="ghost"
        size="sm"
        :loading="loading"
        :title="t('common.refresh')"
        :aria-label="t('common.refresh')"
        @click="emit('refresh')"
        ><RefreshCw class="h-4 w-4" aria-hidden="true"
      /></BaseButton>
    </template>
    <p
      v-if="error"
      role="alert"
      class="mb-4 rounded-lg border border-danger-200 bg-danger-50 p-3 text-sm text-danger-700"
    >
      {{ error }}
    </p>
    <BaseLoading v-if="loading && !event" />
    <div
      v-if="event"
      class="min-w-0 space-y-6 text-sm leading-6 text-theme-secondary"
      :aria-busy="loading"
    >
      <section class="space-y-4">
        <div class="flex flex-wrap items-center gap-2">
          <AlertStatusBadge :status="event.status" /><span
            class="text-xs leading-4 text-theme-muted"
            >{{ t(`alertManagement.unified.subjects.${event.subject}`) }}</span
          >
        </div>
        <dl class="grid gap-4 sm:grid-cols-2">
          <div v-for="field in overview" :key="field.label" class="min-w-0">
            <dt class="text-xs leading-4 text-theme-muted">
              {{ field.label }}
            </dt>
            <dd class="mt-1 whitespace-pre-wrap break-words">
              {{ field.value }}
            </dd>
          </div>
        </dl>
        <p
          v-if="event.detail?.matched_events?.length"
          class="break-words text-xs leading-4 text-theme-muted"
        >
          {{
            event.detail.matched_events
              .map((value) => t(`alertManagement.unified.events.${value}`))
              .join(' · ')
          }}
        </p>
      </section>
      <section
        v-if="event.subject === 'run'"
        class="space-y-2 border-t border-line pt-5"
      >
        <h3 class="text-sm font-semibold leading-5 text-theme">
          {{ t('alertManagement.events.question') }}
        </h3>
        <p
          class="whitespace-pre-wrap break-words rounded-lg bg-surface-sunken p-4"
        >
          {{ event.detail?.question || '–' }}
        </p>
      </section>
      <section
        v-if="event.subject === 'message'"
        class="space-y-2 border-t border-line pt-5"
      >
        <h3 class="break-words text-sm font-semibold leading-5 text-theme">
          {{
            event.detail?.title || t('alertManagement.events.messageContent')
          }}
        </h3>
        <p class="whitespace-pre-wrap break-words">
          {{ event.detail?.body || '–' }}
        </p>
      </section>
      <section
        v-if="event.detail?.error || event.error_message"
        class="space-y-2 border-t border-line pt-5"
      >
        <h3 class="text-sm font-semibold leading-5 text-theme">
          {{ t('alertManagement.events.errorInfo') }}
        </h3>
        <p
          class="whitespace-pre-wrap break-words rounded-lg bg-surface-sunken p-4"
        >
          {{ event.detail?.error || event.error_message }}
        </p>
      </section>
      <dl
        v-if="metrics.length"
        class="grid grid-cols-2 gap-4 rounded-lg border border-line p-4"
      >
        <div v-for="metric in metrics" :key="metric.label" class="min-w-0">
          <dt class="text-xs leading-4 text-theme-muted">{{ metric.label }}</dt>
          <dd class="mt-1 break-words font-medium text-theme">
            {{ metric.value }}
          </dd>
        </div>
      </dl>
      <section class="space-y-3 border-t border-line pt-5">
        <h3 class="text-sm font-semibold leading-5 text-theme">
          {{ t('alertManagement.unified.deliveryDetails') }}
        </h3>
        <p v-if="!event.deliveries?.length" class="text-theme-muted">
          {{ t('alertManagement.events.noDeliveries') }}
        </p>
        <article
          v-for="delivery in event.deliveries || []"
          :key="delivery.uuid"
          class="space-y-3 rounded-lg border border-line p-4"
        >
          <div class="flex flex-wrap items-start justify-between gap-2">
            <div class="min-w-0 flex-1">
              <h4 class="break-words font-medium text-theme">
                {{
                  delivery.destination?.name ||
                  notificationTargetLabel(delivery.destination?.channel_type, t)
                }}
              </h4>
              <p class="mt-1 text-xs leading-4 text-theme-muted">
                {{
                  notificationTargetLabel(delivery.destination?.channel_type, t)
                }}
                ·
                {{
                  t('alertManagement.unified.attempts', {
                    count: delivery.attempts
                  })
                }}<span v-if="delivery.phase === 'diagnosis'">
                  · {{ t('alertManagement.diagnosis.supplement') }}</span
                >
              </p>
            </div>
            <AlertStatusBadge :status="delivery.status" />
          </div>
          <p
            v-if="delivery.destination?.recipients?.length"
            class="break-words"
          >
            {{ delivery.destination.recipients.join(', ') }}
          </p>
          <p v-if="delivery.sent_at" class="text-xs leading-4 text-theme-muted">
            {{ t('alertManagement.events.sentAt') }} ·
            {{ formatDate(delivery.sent_at) }}
          </p>
          <p
            v-else-if="
              delivery.status === 'pending' && delivery.next_attempt_at
            "
            class="text-xs leading-4 text-theme-muted"
          >
            {{ t('alertManagement.events.nextAttemptAt') }} ·
            {{ formatDate(delivery.next_attempt_at) }}
          </p>
          <p
            v-if="delivery.error_message"
            class="whitespace-pre-wrap break-words text-danger-700"
          >
            {{ delivery.error_message }}
          </p>
          <div
            v-if="delivery.diagnosis"
            class="space-y-2 border-t border-line pt-3"
          >
            <h4 class="font-medium text-theme">
              {{ t('alertManagement.diagnosis.title')
              }}<span class="ml-2 text-xs font-normal text-theme-muted">{{
                t(
                  `alertManagement.diagnosis.statuses.${delivery.diagnosis.status}`
                )
              }}</span>
            </h4>
            <p
              v-if="delivery.diagnosis.result?.summary"
              class="whitespace-pre-wrap break-words"
            >
              {{ delivery.diagnosis.result.summary }}
            </p>
            <p
              v-if="delivery.diagnosis.result?.root_cause?.description"
              class="whitespace-pre-wrap break-words"
            >
              {{ delivery.diagnosis.result.root_cause.description }}
            </p>
            <ul
              v-if="delivery.diagnosis.result?.recommendations?.length"
              class="list-disc space-y-1 pl-5"
            >
              <li
                v-for="(item, index) in delivery.diagnosis.result
                  .recommendations"
                :key="index"
                class="whitespace-pre-wrap break-words"
              >
                {{ item.action }}
              </li>
            </ul>
            <p
              v-if="delivery.diagnosis.error_code"
              class="text-xs leading-4 text-theme-muted"
            >
              {{ t('alertManagement.diagnosis.fallbackHint') }}
            </p>
          </div>
        </article>
      </section>
    </div>
    <template #footer>
      <div class="flex flex-wrap justify-end gap-2">
        <BaseButton
          v-if="event?.deliveries?.some((item) => item.status === 'failed')"
          variant="outline"
          :loading="retrying"
          class="whitespace-nowrap"
          @click="emit('retry')"
          >{{ t('alertManagement.unified.retry') }}</BaseButton
        >
        <BaseButton
          v-if="event?.run_uuid"
          class="whitespace-nowrap"
          @click="
            router.push({
              name: 'LensRunObservation',
              query: { run: event.run_uuid }
            })
          "
          ><ExternalLink class="h-4 w-4" aria-hidden="true" />{{
            t('alertManagement.events.viewRun')
          }}</BaseButton
        >
        <BaseButton v-else variant="outline" @click="emit('close')">{{
          t('common.close')
        }}</BaseButton>
      </div>
    </template>
  </BaseDrawer>
</template>

<script setup>
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRouter } from 'vue-router'
import { ExternalLink, RefreshCw } from '@lucide/vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseDrawer from '@/components/ui/BaseDrawer.vue'
import BaseLoading from '@/components/ui/BaseLoading.vue'
import { notificationTargetLabel } from '@/admin/utils/notificationTargets'
import AlertStatusBadge from './AlertStatusBadge.vue'
import { alertEventTitle } from './alertOptions'

const props = defineProps({
  show: Boolean,
  event: { type: Object, default: null },
  loading: Boolean,
  error: { type: String, default: '' },
  retrying: Boolean
})
const emit = defineEmits(['close', 'refresh', 'retry'])
const { t } = useI18n()
const router = useRouter()
function formatDate(value) {
  return value ? new Date(value).toLocaleString() : '–'
}
const overview = computed(() => {
  const event = props.event
  if (!event) return []
  const detail = event.detail || {}
  const fields = [
    { label: t('alertManagement.events.rule'), value: event.rule_name || '–' },
    {
      label: t('alertManagement.unified.resourceScope'),
      value:
        detail.assistant_name || detail.datasource_name || detail.topic || '–'
    },
    {
      label: t('alertManagement.events.createdAt'),
      value: formatDate(detail.occurred_at || event.created_at)
    }
  ]
  if (event.subject === 'run') {
    fields.push({
      label: t('alertManagement.events.startedAt'),
      value: formatDate(detail.started_at)
    })
    const username = detail.username || ''
    const name = detail.user_display_name || username || '–'
    fields.push({
      label: t('alertManagement.events.user'),
      value: username && name !== username ? `${name} (${username})` : name
    })
  }
  return fields
})
const metrics = computed(() =>
  [
    ['total_tokens', 'events.tokens'],
    ['rounds', 'events.rounds'],
    ['duration_seconds', 'events.duration'],
    ['failed_files', 'events.failedFiles'],
    ['token_threshold', 'rules.tokenThreshold'],
    ['rounds_threshold', 'rules.roundsThreshold'],
    ['duration_threshold', 'unified.durationThreshold']
  ]
    .filter(([key]) => props.event?.detail?.[key] != null)
    .map(([key, label]) => ({
      label: t(`alertManagement.${label}`),
      value: props.event.detail[key]
    }))
)
</script>
