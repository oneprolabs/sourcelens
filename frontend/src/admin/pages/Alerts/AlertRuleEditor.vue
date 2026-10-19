<template>
  <BaseModal
    :show="show"
    max-width="3xl"
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
      <div class="grid gap-4 sm:grid-cols-2">
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
            v-for="subject in subjectOptions"
            :key="subject"
            :value="subject"
          >
            {{ t(`alertManagement.unified.subjects.${subject}`) }}
          </option>
        </BaseSelect>
      </div>
      <BaseInput
        v-if="form.subject === 'message'"
        v-model="form.topic"
        :label="t('alertManagement.messages.topic')"
        placeholder="release.ready"
        required
      />

      <fieldset class="rounded-lg border border-line bg-surface-sunken p-4">
        <legend class="mb-2 text-sm font-medium text-theme-secondary">
          {{ t('alertManagement.rules.events') }}
        </legend>
        <div
          class="mb-3 flex flex-wrap items-center justify-between gap-3 border-b border-line pb-3"
        >
          <label
            class="flex min-h-9 cursor-pointer items-center gap-2 text-sm text-theme-secondary"
          >
            <BaseCheckbox
              :model-value="allEventsSelected"
              :indeterminate="someEventsSelected"
              :disabled="!eventOptions.length"
              @update:model-value="setAllEvents"
            />
            {{ t('common.selectAll') }}
          </label>
          <div class="flex items-center gap-3">
            <span class="text-xs text-theme-muted">{{
              t('alertManagement.unified.selectedCount', {
                count: form.events.length
              })
            }}</span>
            <BaseButton
              type="button"
              variant="ghost"
              size="sm"
              :disabled="!form.events.length"
              @click="form.events = []"
              >{{ t('alertManagement.unified.clearSelection') }}</BaseButton
            >
          </div>
        </div>
        <div class="grid gap-2 sm:grid-cols-2">
          <label
            v-for="event in eventOptions"
            :key="event"
            class="flex min-h-10 cursor-pointer items-center gap-2 rounded-lg border border-line bg-surface px-3 py-2 text-sm text-theme-secondary hover:bg-surface-hover"
          >
            <BaseCheckbox
              :model-value="form.events.includes(event)"
              @update:model-value="setEvent(event, $event)"
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

      <p v-if="form.subject !== 'message'" class="text-xs text-theme-muted">
        {{ t('alertManagement.unified.scopeHint') }}
      </p>
      <AlertScopeSelect
        v-if="form.subject === 'run'"
        v-model="form.assistants"
        empty-means-all
        :options="options.assistants"
        :label="t('alertManagement.rules.scope')"
      />
      <AlertScopeSelect
        v-else-if="form.subject !== 'message'"
        v-model="form.datasources"
        empty-means-all
        :options="options.datasources"
        :label="t('alertManagement.unified.datasources')"
      />

      <AlertDestinations
        v-model="form.subscriptions"
        :channels="options.channels"
        :users="options.users"
        :groups="options.groups"
      />
      <details class="rounded-lg border border-line p-4">
        <summary
          class="cursor-pointer text-sm font-medium text-theme-secondary"
        >
          {{ t('alertManagement.unified.advancedOptions') }}
        </summary>
        <div class="mt-4 space-y-4">
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
          <p
            v-if="form.evaluation_mode === 'running'"
            class="text-xs text-theme-muted"
          >
            {{ t('alertManagement.unified.runningHint') }}
          </p>
          <AlertScopeSelect
            v-if="form.subject !== 'message'"
            v-model="form.lensnodes"
            empty-means-all
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
            v-if="
              form.subject !== 'message' && form.evaluation_mode !== 'running'
            "
          >
            <BaseSelect
              :label="t('alertManagement.diagnosis.mode')"
              v-model="form.diagnosis_mode"
            >
              <option
                v-for="mode in [
                  'direct',
                  'diagnose_first',
                  'notify_then_diagnose'
                ]"
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
          <template
            v-if="
              ['run', 'message'].includes(form.subject) &&
              (emailChannels.length || form.personal_subscription_enabled)
            "
          >
            <label class="flex items-center gap-2 text-sm text-theme-secondary"
              ><BaseCheckbox v-model="form.personal_subscription_enabled" />{{
                t('alertManagement.unified.allowPersonal')
              }}</label
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
                v-for="channel in emailChannels"
                :key="channel.uuid"
                :value="channel.uuid"
              >
                {{ channel.name }}
              </option>
            </BaseSelect>
          </template>
        </div>
      </details>
      <label class="flex items-center gap-2 text-sm"
        ><BaseCheckbox v-model="form.enabled" />{{
          t('alertManagement.rules.enabled')
        }}</label
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
import BaseCheckbox from '@/components/ui/BaseCheckbox.vue'
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
  createAlertRuleForm,
  eventsForSubject,
  setAlertSelection
} from './alertOptions'
const props = defineProps({
  show: Boolean,
  value: { type: Object, default: null },
  options: { type: Object, required: true },
  catalog: { type: Object, required: true }
})
const emit = defineEmits(['close', 'saved'])
const { t, locale } = useI18n()
const form = ref({})
const subjectOptions = computed(() =>
  props.value?.subject === 'message'
    ? ['run', 'sync', 'processing', 'message']
    : ['run', 'sync', 'processing']
)
const emailChannels = computed(() =>
  props.options.channels.filter((channel) => channel.channel_type === 'email')
)
const error = ref('')
const saving = ref(false)
const eventOptions = computed(() =>
  eventsForSubject(
    props.catalog,
    form.value.subject,
    form.value.evaluation_mode
  )
)
const allEventsSelected = computed(
  () =>
    eventOptions.value.length > 0 &&
    eventOptions.value.every((event) => form.value.events?.includes(event))
)
const someEventsSelected = computed(
  () =>
    !allEventsSelected.value &&
    eventOptions.value.some((event) => form.value.events?.includes(event))
)
function setEvent(event, checked) {
  form.value.events = setAlertSelection(form.value.events, [event], checked)
}
function setAllEvents(checked) {
  form.value.events = setAlertSelection(
    form.value.events,
    eventOptions.value,
    checked
  )
}
watch(
  () => props.show,
  (show) => {
    if (!show) return
    form.value = createAlertRuleForm(props.value, locale.value)
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
  if (!form.value.events.length && form.value.evaluation_mode === 'terminal') {
    form.value.events = eventOptions.value.filter((event) =>
      /_(failed|partial|timeout)$/.test(event)
    )
  }
}
async function submit() {
  if (!form.value.events.length) {
    error.value = t('alertManagement.rules.eventsRequired')
    return
  }
  if (
    form.value.enabled &&
    !form.value.personal_subscription_enabled &&
    !form.value.subscriptions.some((item) => item.enabled && item.channel_uuid)
  ) {
    error.value = t('alertManagement.unified.destinationRequired')
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
