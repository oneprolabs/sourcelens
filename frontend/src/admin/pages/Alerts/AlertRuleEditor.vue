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
    <form class="space-y-4" @submit.prevent="submit">
      <BaseInput
        v-model="form.name"
        :label="t('alertManagement.rules.name')"
        required
      />
      <label class="block text-sm text-ink-700"
        >{{ t('alertManagement.unified.subject') }}
        <BaseSelect
          v-model="form.subject"
          class="mt-1 w-full rounded border border-line bg-surface p-2"
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
      </label>
      <BaseInput
        v-if="form.subject === 'message'"
        v-model="form.topic"
        :label="t('alertManagement.messages.topic')"
        placeholder="release.ready"
        required
      />
      <label v-if="form.subject === 'run'" class="block text-sm text-ink-700"
        >{{ t('alertManagement.unified.evaluationMode') }}
        <BaseSelect
          v-model="form.evaluation_mode"
          class="mt-1 w-full rounded border border-line bg-surface p-2"
          @change="resetEvents"
        >
          <option value="terminal">
            {{ t('alertManagement.unified.terminal') }}
          </option>
          <option value="running">
            {{ t('alertManagement.unified.running') }}
          </option>
        </BaseSelect>
      </label>
      <fieldset class="space-y-2">
        <legend class="mb-2 text-sm font-medium text-ink-700">
          {{ t('alertManagement.rules.events') }}
        </legend>
        <label
          v-for="event in eventOptions"
          :key="event"
          class="flex items-center gap-2 text-sm text-ink-700"
        >
          <input
            v-model="form.events"
            type="checkbox"
            class="h-4 w-4 shrink-0"
            :value="event"
          />{{ t(`alertManagement.unified.events.${event}`) }}
        </label>
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
      <p v-if="form.evaluation_mode === 'running'" class="text-xs text-ink-500">
        {{ t('alertManagement.unified.runningHint') }}
      </p>
      <p v-if="form.subject !== 'message'" class="text-xs text-ink-500">
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
      <AlertDestinations
        v-model="form.subscriptions"
        :channels="options.channels"
        :users="options.users"
        :groups="options.groups"
      />
      <template v-if="['run', 'message'].includes(form.subject)">
        <label class="flex items-center gap-2 text-sm text-ink-700"
          ><input
            v-model="form.personal_subscription_enabled"
            type="checkbox"
            class="h-4 w-4 shrink-0"
          />{{ t('alertManagement.unified.allowPersonal') }}</label
        >
        <label
          v-if="form.personal_subscription_enabled"
          class="block text-sm text-ink-700"
          >{{ t('alertManagement.unified.personalChannel') }}
          <BaseSelect
            v-model="form.personal_channel_uuid"
            required
            class="mt-1 w-full rounded border border-line bg-surface p-2"
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
        </label>
      </template>
      <label class="flex items-center gap-2 text-sm"
        ><input
          v-model="form.enabled"
          type="checkbox"
          class="h-4 w-4 shrink-0"
        />{{ t('alertManagement.rules.enabled') }}</label
      >
      <p v-if="error" role="alert" class="break-words text-sm text-danger-700">
        {{ error }}
      </p>
      <div class="flex justify-end gap-3 border-t border-line pt-3">
        <BaseButton type="button" variant="outline" @click="emit('close')">{{
          t('common.cancel')
        }}</BaseButton>
        <BaseButton type="submit" :loading="saving">{{
          t('common.save')
        }}</BaseButton>
      </div>
    </form>
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
