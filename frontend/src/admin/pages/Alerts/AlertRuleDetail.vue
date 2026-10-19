<template>
  <BaseModal
    :show="!!rule"
    :title="t('alertManagement.rules.details')"
    max-width="3xl"
    @close="emit('close')"
  >
    <div v-if="rule" class="space-y-5 text-sm leading-6 text-theme-secondary">
      <div class="flex flex-wrap items-start justify-between gap-3">
        <div class="min-w-0 flex-1">
          <h2 class="break-words text-base font-semibold text-theme">
            {{ rule.name }}
          </h2>
          <p class="mt-1 text-xs leading-4 text-theme-muted">
            {{ t(`alertManagement.unified.subjects.${rule.subject}`) }}
          </p>
        </div>
        <StatusBadge :status="rule.enabled ? 'enabled' : 'disabled'" />
      </div>

      <section class="space-y-2">
        <h3 class="text-sm font-semibold leading-5 text-theme">
          {{ t('alertManagement.rules.channel') }}
        </h3>
        <p v-if="!rule.subscriptions?.length" class="text-theme-muted">
          {{ t('alertManagement.rules.noChannel') }}
        </p>
        <div
          v-for="destination in rule.subscriptions"
          :key="destination.uuid"
          class="space-y-2 rounded-lg border border-line p-3"
        >
          <div class="flex flex-wrap items-center justify-between gap-2">
            <div class="min-w-0 break-words">
              <span class="font-medium text-theme">{{
                channelFor(destination.channel_uuid)?.name ||
                t('alertManagement.rules.noChannel')
              }}</span>
              <span
                v-if="channelFor(destination.channel_uuid)"
                class="ml-2 text-xs leading-4 text-theme-muted"
                >{{
                  notificationTargetLabel(
                    channelFor(destination.channel_uuid).channel_type,
                    t
                  )
                }}</span
              >
            </div>
            <StatusBadge
              :status="
                destination.enabled &&
                channelFor(destination.channel_uuid)?.is_active
                  ? 'enabled'
                  : 'disabled'
              "
            />
          </div>
          <p v-if="destination.name" class="break-words">
            {{ destination.name }}
          </p>
          <p class="text-xs leading-4 text-theme-muted">
            {{ t('alertManagement.diagnosis.language') }} ·
            {{ languageLabel(destination.language) }}
          </p>
          <p v-if="destination.email_recipients?.length" class="break-words">
            {{ t('alertManagement.rules.emailRecipients') }} ·
            {{ destination.email_recipients.join(', ') }}
          </p>
          <p v-if="destination.users?.length" class="break-words">
            {{ t('alertManagement.unified.users') }} ·
            {{ scopeLabel(destination.users, options.users) }}
          </p>
          <p v-if="destination.groups?.length" class="break-words">
            {{ t('alertManagement.unified.groups') }} ·
            {{ scopeLabel(destination.groups, options.groups) }}
          </p>
        </div>
        <p
          v-if="rule.personal_subscription_enabled"
          class="break-words text-xs leading-4 text-theme-muted"
        >
          {{ t('alertManagement.unified.allowPersonal') }} ·
          {{
            channelFor(rule.personal_channel_uuid)?.name ||
            t('alertManagement.rules.noChannel')
          }}
        </p>
      </section>

      <section class="space-y-2">
        <h3 class="text-sm font-semibold leading-5 text-theme">
          {{ t('alertManagement.rules.events') }}
        </h3>
        <ul class="flex flex-wrap gap-2">
          <li
            v-for="event in rule.events"
            :key="event"
            class="rounded-md bg-surface-sunken px-2.5 py-1.5"
          >
            {{ t(`alertManagement.unified.events.${event}`) }}
          </li>
        </ul>
        <dl class="grid gap-2 sm:grid-cols-2">
          <div
            v-for="threshold in thresholds"
            :key="threshold.key"
            class="rounded-lg border border-line p-3"
          >
            <dt class="text-xs leading-4 text-theme-muted">
              {{ t(threshold.label) }}
            </dt>
            <dd class="mt-1 font-medium text-theme">
              {{ rule[threshold.key] }}
            </dd>
          </div>
        </dl>
      </section>

      <section class="space-y-2">
        <h3 class="text-sm font-semibold leading-5 text-theme">
          {{ t('alertManagement.unified.resourceScope') }}
        </h3>
        <dl class="space-y-3 rounded-lg border border-line p-3">
          <div v-if="rule.subject === 'message'">
            <dt class="text-xs leading-4 text-theme-muted">
              {{ t('alertManagement.messages.topic') }}
            </dt>
            <dd class="mt-1 break-words">{{ rule.topic }}</dd>
          </div>
          <template v-else>
            <div>
              <dt class="text-xs leading-4 text-theme-muted">
                {{
                  t(
                    rule.subject === 'run'
                      ? 'alertManagement.rules.scope'
                      : 'alertManagement.unified.datasources'
                  )
                }}
              </dt>
              <dd class="mt-1 break-words">
                {{
                  rule.subject === 'run'
                    ? scopeLabel(rule.assistants, options.assistants)
                    : scopeLabel(rule.datasources, options.datasources)
                }}
              </dd>
            </div>
            <div>
              <dt class="text-xs leading-4 text-theme-muted">
                {{ t('alertManagement.unified.lensnodes') }}
              </dt>
              <dd class="mt-1 break-words">
                {{ scopeLabel(rule.lensnodes, options.lensnodes) }}
              </dd>
            </div>
          </template>
        </dl>
      </section>

      <details class="rounded-lg border border-line p-3">
        <summary class="cursor-pointer font-medium text-theme">
          {{ t('alertManagement.unified.advancedOptions') }}
        </summary>
        <dl class="mt-3 grid gap-4 sm:grid-cols-2">
          <div v-if="rule.subject === 'run'">
            <dt class="text-xs leading-4 text-theme-muted">
              {{ t('alertManagement.unified.evaluationMode') }}
            </dt>
            <dd class="mt-1">
              {{
                t(
                  `alertManagement.unified.${rule.evaluation_mode || 'terminal'}`
                )
              }}
            </dd>
          </div>
          <div>
            <dt class="text-xs leading-4 text-theme-muted">
              {{ t('alertManagement.diagnosis.mode') }}
            </dt>
            <dd class="mt-1">
              {{
                t(
                  `alertManagement.diagnosis.modes.${rule.diagnosis_mode || 'direct'}`
                )
              }}
            </dd>
          </div>
          <template
            v-if="rule.diagnosis_mode && rule.diagnosis_mode !== 'direct'"
          >
            <div>
              <dt class="text-xs leading-4 text-theme-muted">
                {{ t('alertManagement.diagnosis.timeout') }}
              </dt>
              <dd class="mt-1">{{ rule.diagnosis_timeout_seconds }}</dd>
            </div>
            <div>
              <dt class="text-xs leading-4 text-theme-muted">
                {{ t('alertManagement.diagnosis.maxTokens') }}
              </dt>
              <dd class="mt-1">{{ rule.diagnosis_max_tokens }}</dd>
            </div>
          </template>
          <div v-if="rule.subject !== 'message'">
            <dt class="text-xs leading-4 text-theme-muted">
              {{ t('alertManagement.unified.cooldown') }}
            </dt>
            <dd class="mt-1">{{ rule.cooldown_seconds || 0 }}</dd>
          </div>
        </dl>
      </details>
    </div>
    <template #footer>
      <div class="flex justify-end gap-3">
        <BaseButton variant="outline" @click="emit('close')">{{
          t('common.close')
        }}</BaseButton>
        <BaseButton v-if="rule" @click="emit('edit', rule)"
          ><Pencil class="h-4 w-4" aria-hidden="true" />{{
            t('common.edit')
          }}</BaseButton
        >
      </div>
    </template>
  </BaseModal>
</template>

<script setup>
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { Pencil } from '@lucide/vue'
import { notificationTargetLabel } from '@/admin/utils/notificationTargets'
import BaseModal from '@/components/ui/BaseModal.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import StatusBadge from '@/components/ui/StatusBadge.vue'

const props = defineProps({
  rule: { type: Object, default: null },
  options: { type: Object, required: true },
  channels: { type: Array, required: true }
})
const emit = defineEmits(['close', 'edit'])
const { t } = useI18n()
const thresholds = computed(() =>
  [
    {
      event: 'token_exceeded',
      key: 'token_threshold',
      label: 'alertManagement.rules.tokenThreshold'
    },
    {
      event: 'rounds_exceeded',
      key: 'rounds_threshold',
      label: 'alertManagement.rules.roundsThreshold'
    },
    {
      event: 'run_slow',
      key: 'duration_threshold',
      label: 'alertManagement.unified.durationThreshold'
    }
  ].filter((threshold) => props.rule?.events?.includes(threshold.event))
)
function channelFor(uuid) {
  return props.channels.find((channel) => channel.uuid === uuid)
}
function scopeLabel(selected = [], available = []) {
  return selected.length
    ? selected
        .map(
          (uuid) => available.find((item) => item.value === uuid)?.label || uuid
        )
        .join(', ')
    : t('alertManagement.unified.allResources')
}
function languageLabel(language) {
  return (
    { 'zh-CN': '简体中文', 'en-US': 'English', es: 'Español' }[language] ||
    language
  )
}
</script>
