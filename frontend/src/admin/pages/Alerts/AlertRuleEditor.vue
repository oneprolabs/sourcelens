<template>
  <BaseModal
    :show="show"
    :title="
      t(
        value?.uuid
          ? 'alertManagement.rules.editRule'
          : 'alertManagement.rules.addRule'
      )
    "
    @close="emit('close')"
  >
    <form id="alert-rule-form" class="space-y-4" @submit.prevent="submit">
      <BaseInput
        v-model="form.name"
        :label="t('alertManagement.rules.name')"
        required
      />
      <BaseSelect
        :label="t('alertManagement.unified.subject')"
        v-model="form.subject"
        @change="resetEvents"
      >
        <option
          v-for="subject in ['run', 'sync', 'processing', 'message']"
          :key="subject"
          :value="subject"
        >
          {{ t(`alertManagement.unified.subjects.${subject}`) }}
        </option>
      </BaseSelect>
      <BaseInput
        v-if="form.subject === 'message'"
        v-model="form.topic"
        :label="t('alertManagement.messages.topic')"
        placeholder="release.ready"
        required
      />
      <BaseSelect
        v-if="form.subject === 'run'"
        :label="t('alertManagement.unified.evaluationMode')"
        v-model="form.evaluation_mode"
        @change="resetEvents"
      >
        <option value="terminal">
          {{ t('alertManagement.unified.terminal') }}
        </option>
        <option value="running">
          {{ t('alertManagement.unified.running') }}
        </option>
      </BaseSelect>
      <fieldset class="rounded-lg border border-line bg-surface-sunken p-4">
        <legend class="mb-2 text-sm font-medium text-theme-secondary">
          {{ t('alertManagement.rules.events') }}
        </legend>
        <div class="grid gap-2 sm:grid-cols-2">
          <label
            v-for="event in eventOptions"
            :key="event"
            class="flex items-center gap-2 text-sm text-theme-secondary"
          >
            <input
              v-model="form.events"
              type="checkbox"
              class="h-4 w-4 shrink-0 rounded border-line text-primary-600 focus:ring-primary-500"
              :value="event"
            />{{ t(`alertManagement.unified.events.${event}`) }}
          </label>
        </div>
      </fieldset>
      <BaseInput
        v-if="form.events.includes('token_exceeded')"
        v-model="form.token_threshold"
        type="number"
        min="1"
        :label="t('alertManagement.rules.tokenThreshold')"
        required
      />
      <BaseInput
        v-if="form.events.includes('rounds_exceeded')"
        v-model="form.rounds_threshold"
        type="number"
        min="1"
        :label="t('alertManagement.rules.roundsThreshold')"
        required
      />
      <BaseInput
        v-if="form.events.includes('run_slow')"
        v-model="form.duration_threshold"
        type="number"
        min="1"
        :label="t('alertManagement.unified.durationThreshold')"
        required
      />
      <p
        v-if="form.evaluation_mode === 'running'"
        class="text-xs text-theme-muted"
      >
        {{ t('alertManagement.unified.runningHint') }}
      </p>
      <p v-if="form.subject !== 'message'" class="text-xs text-theme-muted">
        {{ t('alertManagement.unified.scopeHint') }}
      </p>
      <AlertScopeSelect
        v-if="form.subject === 'run'"
        v-model="form.assistants"
        :options="options.assistants"
        :label="t('alertManagement.rules.scope')"
      />
      <AlertScopeSelect
        v-else-if="form.subject !== 'message'"
        v-model="form.datasources"
        :options="options.datasources"
        :label="t('alertManagement.unified.datasources')"
      />
      <AlertScopeSelect
        v-if="form.subject !== 'message'"
        v-model="form.lensnodes"
        :options="options.lensnodes"
        :label="t('alertManagement.unified.lensnodes')"
      />
      <BaseInput
        v-if="['sync', 'processing'].includes(form.subject)"
        v-model="form.cooldown_seconds"
        type="number"
        min="0"
        :label="t('alertManagement.unified.cooldown')"
      />
      <template
        v-if="form.subject !== 'message' && form.evaluation_mode !== 'running'"
      >
        <BaseSelect
          :label="t('alertManagement.diagnosis.mode')"
          v-model="form.diagnosis_mode"
        >
          <option
            v-for="mode in ['direct', 'diagnose_first', 'notify_then_diagnose']"
            :key="mode"
            :value="mode"
          >
            {{ t(`alertManagement.diagnosis.modes.${mode}`) }}
          </option>
        </BaseSelect>
        <template v-if="form.diagnosis_mode !== 'direct'">
          <BaseInput
            v-model="form.diagnosis_timeout_seconds"
            type="number"
            min="5"
            max="300"
            required
            :label="t('alertManagement.diagnosis.timeout')"
          />
          <BaseInput
            v-model="form.diagnosis_max_tokens"
            type="number"
            min="256"
            max="8192"
            required
            :label="t('alertManagement.diagnosis.maxTokens')"
          />
          <p class="text-xs text-theme-muted">
            {{ t('alertManagement.diagnosis.fallbackHint') }}
          </p>
        </template>
      </template>
      <AlertDestinations
        v-model="form.subscriptions"
        :channels="options.channels"
        :users="options.users"
        :groups="options.groups"
      />
      <template v-if="['run', 'message'].includes(form.subject)">
        <label class="flex items-center gap-2 text-sm text-theme-secondary"
          ><input
            v-model="form.personal_subscription_enabled"
            type="checkbox"
            class="h-4 w-4 shrink-0 rounded border-line text-primary-600 focus:ring-primary-500"
          />{{ t('alertManagement.unified.allowPersonal') }}</label
        >
        <BaseSelect
          v-if="form.personal_subscription_enabled"
          :label="t('alertManagement.unified.personalChannel')"
          v-model="form.personal_channel_uuid"
          required
        >
          <option value="" disabled>
            {{ t('alertManagement.rules.channelPlaceholder') }}
          </option>
          <option
            v-for="channel in options.channels.filter(
              (item) => item.channel_type === 'email'
            )"
            :key="channel.uuid"
            :value="channel.uuid"
          >
            {{ channel.name }}
          </option>
        </BaseSelect>
      </template>
      <label class="flex items-center gap-2 text-sm"
        ><input
          v-model="form.enabled"
          type="checkbox"
          class="h-4 w-4 shrink-0 rounded border-line text-primary-600 focus:ring-primary-500"
        />{{ t('alertManagement.rules.enabled') }}</label
      >
      <p v-if="error" role="alert" class="break-words text-sm text-danger-700">
        {{ error }}
      </p>
    </form>
    <template #footer>
      <div class="flex w-full justify-end gap-3">
        <BaseButton
          type="button"
          variant="outline"
          :disabled="saving"
          @click="emit('close')"
          >{{ t('common.cancel') }}</BaseButton
        >
        <BaseButton type="submit" form="alert-rule-form" :loading="saving">{{
          t('common.save')
        }}</BaseButton>
      </div>
    </template>
  </BaseModal>
</template>
<script setup>
import BaseSelect from '@/components/ui/BaseSelect.vue'
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { alertsAdminApi } from '@/admin/api'
import BaseModal from '@/components/ui/BaseModal.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseInput from '@/components/ui/BaseInput.vue'
import AlertScopeSelect from './AlertScopeSelect.vue'
import AlertDestinations from './AlertDestinations.vue'
import {
  alertApiError,
  alertRulePayload,
  eventsForSubject
} from './alertOptions'
const props = defineProps({
  show: Boolean,
  value: { type: Object, default: null },
  options: { type: Object, required: true },
  catalog: { type: Object, required: true }
})
const emit = defineEmits(['close', 'saved'])
const { t } = useI18n()
const form = ref({})
const error = ref('')
const saving = ref(false)
const eventOptions = computed(() =>
  eventsForSubject(
    props.catalog,
    form.value.subject,
    form.value.evaluation_mode
  )
)
watch(
  () => props.show,
  (show) => {
    if (!show) return
    const value = props.value || {}
    form.value = {
      name: '',
      subject: 'run',
      topic: '',
      events: [],
      enabled: true,
      evaluation_mode: 'terminal',
      diagnosis_mode: 'direct',
      diagnosis_timeout_seconds: 60,
      diagnosis_max_tokens: 2048,
      assistants: [],
      datasources: [],
      lensnodes: [],
      cooldown_seconds: 0,
      personal_subscription_enabled: false,
      personal_channel_uuid: '',
      ...JSON.parse(JSON.stringify(value)),
      subscriptions: (value.subscriptions || []).map((item) => ({
        ...item,
        email_recipients: (item.email_recipients || []).join('\n')
      }))
    }
    error.value = ''
  },
  { immediate: true }
)
function resetEvents() {
  if (form.value.subject !== 'run') form.value.evaluation_mode = 'terminal'
  if (
    form.value.subject === 'message' ||
    form.value.evaluation_mode === 'running'
  )
    form.value.diagnosis_mode = 'direct'
  form.value.events = form.value.events.filter((event) =>
    eventOptions.value.includes(event)
  )
}
async function submit() {
  if (!form.value.events.length) {
    error.value = t('alertManagement.rules.eventsRequired')
    return
  }
  saving.value = true
  error.value = ''
  try {
    const body = alertRulePayload(form.value)
    if (props.value?.uuid)
      await alertsAdminApi.updateRule(props.value.uuid, body)
    else await alertsAdminApi.createRule(body)
    emit('saved')
  } catch (err) {
    error.value = alertApiError(err)
  } finally {
    saving.value = false
  }
}
</script>
