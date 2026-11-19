<template>
  <BaseDrawer
    :show="show"
    :title="
      t(`lensAdmin.knowledgeBulk.titles.${action}`, { count: targets.length })
    "
    panel-class="knowledge-bulk-drawer"
    :close-on-backdrop="!busy"
    @close="emit('close')"
  >
    <template v-if="!started">
      <p class="knowledge-bulk-lead">
        {{
          t(
            `lensAdmin.knowledgeBulk.${action === 'policy' ? 'policyLead' : 'deleteLead'}`
          )
        }}
      </p>
      <fieldset
        v-if="action === 'policy'"
        class="knowledge-policy-options"
        :disabled="busy"
      >
        <legend class="sr-only">{{ t('lensAdmin.fields.syncPolicy') }}</legend>
        <label
          v-for="key in ['hourly', 'sixHourly', 'daily', 'manual']"
          :key="key"
          :class="{ selected: preset === key }"
        >
          <input
            v-model="preset"
            type="radio"
            name="datasource-bulk-policy"
            :value="key"
          />
          <span>{{ t(`lensAdmin.knowledgeBulk.presets.${key}`) }}</span>
        </label>
      </fieldset>
      <p v-if="action === 'policy' && skippedCount" class="knowledge-bulk-note">
        {{
          t('lensAdmin.knowledgeBulk.policySkips', {
            count: skippedCount,
            eligible: eligibleCount
          })
        }}
      </p>
      <p
        v-if="action === 'policy' && preset === 'hourly'"
        class="knowledge-bulk-note"
      >
        {{ t('lensAdmin.knowledgeBulk.hourlyHint') }}
      </p>
      <p
        v-if="action === 'policy' && preset === 'daily'"
        class="knowledge-bulk-note"
      >
        {{ t('lensAdmin.knowledgeBulk.dailyTimezone') }}
      </p>
      <ul v-if="action === 'delete'" class="knowledge-bulk-targets">
        <li v-for="row in targets" :key="row.uuid">
          {{ row.name
          }}<small v-if="skipReason(row)">{{
            t(`lensAdmin.knowledgeBulk.reasons.${skipReason(row)}`)
          }}</small>
        </li>
      </ul>
    </template>
    <template v-else>
      <p role="status" aria-live="polite" class="knowledge-bulk-lead">
        {{
          t(`lensAdmin.knowledgeBulk.${busy ? 'progress' : 'summary'}`, {
            count: results.length,
            total: targets.length,
            ...counts
          })
        }}
      </p>
      <ol class="knowledge-bulk-results">
        <li
          v-for="result in results"
          :key="result.uuid"
          :data-result="result.status"
        >
          <b>{{ result.name }}</b>
          <span>{{ resultLabel(result) }}</span>
          <code v-if="result.taskId">{{ result.taskId }}</code>
        </li>
      </ol>
    </template>
    <template #footer>
      <div class="knowledge-bulk-footer">
        <BaseButton variant="outline" :disabled="busy" @click="emit('close')">{{
          t(started ? 'common.close' : 'common.cancel')
        }}</BaseButton>
        <BaseButton
          v-if="!started"
          :variant="action === 'delete' ? 'danger' : 'primary'"
          :disabled="!eligibleCount || blocked"
          :loading="busy"
          @click="emit('confirm', selectedPolicy)"
          >{{
            t(
              `lensAdmin.knowledgeBulk.${action === 'policy' ? 'apply' : 'confirmDelete'}`,
              { count: eligibleCount }
            )
          }}</BaseButton
        >
      </div>
    </template>
  </BaseDrawer>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import BaseDrawer from '@/components/ui/BaseDrawer.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import { extractErrorMessage } from '@/utils/api'
import { dataSourceBulkSkipReason } from './datasourceBulk.js'

const props = defineProps({
  show: Boolean,
  action: { type: String, default: 'policy' },
  targets: { type: Array, default: () => [] },
  results: { type: Array, default: () => [] },
  busy: Boolean,
  started: Boolean,
  blocked: Boolean
})
const emit = defineEmits(['close', 'confirm'])
const { t } = useI18n()
const preset = ref('daily')
watch(
  () => props.show,
  (value) => {
    if (value) preset.value = 'daily'
  }
)
const skipReason = (row) => dataSourceBulkSkipReason(row, props.action)
const skippedCount = computed(() => props.targets.filter(skipReason).length)
const eligibleCount = computed(() => props.targets.length - skippedCount.value)
const selectedPolicy = computed(() => {
  if (preset.value === 'manual') return { mode: 'manual' }
  if (preset.value === 'daily')
    return { mode: 'crontab', cron: '0 2 * * *', timezone: 'Asia/Shanghai' }
  return {
    mode: 'interval',
    interval_seconds: preset.value === 'hourly' ? 3600 : 21600
  }
})
const counts = computed(() =>
  Object.fromEntries(
    ['success', 'failed', 'skipped'].map((status) => [
      status,
      props.results.filter((r) => r.status === status).length
    ])
  )
)
function resultLabel(result) {
  if (result.status === 'skipped')
    return t('lensAdmin.knowledgeBulk.skipped', {
      reason: t(`lensAdmin.knowledgeBulk.reasons.${result.reason}`)
    })
  if (result.status === 'failed')
    return extractErrorMessage(result.error, t('lensAdmin.messages.saveFailed'))
  return t(
    `lensAdmin.knowledgeBulk.${['sync', 'retry'].includes(props.action) ? 'submitted' : 'success'}`
  )
}
</script>
