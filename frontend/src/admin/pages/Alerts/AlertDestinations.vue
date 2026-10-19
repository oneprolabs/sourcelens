<template>
  <section class="space-y-3">
    <div class="flex items-center justify-between gap-2">
      <h2 class="text-sm font-semibold text-theme">
        {{ t('alertManagement.unified.destinations') }}
      </h2>
      <BaseButton type="button" size="sm" variant="outline" @click="add">{{
        t('alertManagement.unified.addDestination')
      }}</BaseButton>
    </div>
    <p v-if="!modelValue.length" class="text-sm text-theme-muted">
      {{ t('alertManagement.unified.noDestinations') }}
    </p>
    <fieldset
      v-for="(item, index) in modelValue"
      :key="item.uuid || index"
      class="space-y-3 rounded-lg border border-line p-3"
    >
      <legend class="px-1 text-sm text-theme-secondary">
        {{
          t('alertManagement.unified.destinationNumber', { number: index + 1 })
        }}
      </legend>
      <BaseInput
        :model-value="item.name"
        :label="t('alertManagement.rules.name')"
        @update:model-value="change(index, 'name', $event)"
      />
      <BaseSelect
        :label="t('alertManagement.rules.channel')"
        :model-value="item.channel_uuid"
        required
        @update:model-value="change(index, 'channel_uuid', $event)"
      >
        <option value="" disabled>
          {{ t('alertManagement.rules.channelPlaceholder') }}
        </option>
        <option
          v-for="channel in channels"
          :key="channel.uuid"
          :value="channel.uuid"
        >
          {{ channel.name }} ({{
            notificationTargetLabel(channel.channel_type, t)
          }})
        </option>
      </BaseSelect>
      <BaseSelect
        :label="t('alertManagement.diagnosis.language')"
        :model-value="item.language || 'en-US'"
        @update:model-value="change(index, 'language', $event)"
      >
        <option value="zh-CN">简体中文</option>
        <option value="en-US">English</option>
        <option value="es">Español</option>
      </BaseSelect>
      <template
        v-if="
          channels.find((channel) => channel.uuid === item.channel_uuid)
            ?.channel_type === 'email'
        "
      >
        <label class="block text-sm font-medium text-theme">
          {{ t('alertManagement.rules.emailRecipients') }}
          <textarea
            :value="item.email_recipients"
            rows="2"
            class="mt-1 block w-full rounded-lg border border-line bg-surface px-3 py-2 text-sm text-theme shadow-sm focus:border-primary-500 focus:outline-none focus:ring-2 focus:ring-primary-500/20"
            :placeholder="t('alertManagement.rules.emailRecipientsPlaceholder')"
            @input="change(index, 'email_recipients', $event.target.value)"
          />
        </label>
        <AlertScopeSelect
          :model-value="item.users"
          :options="users"
          :label="t('alertManagement.unified.users')"
          @update:model-value="change(index, 'users', $event)"
        />
        <AlertScopeSelect
          :model-value="item.groups"
          :options="groups"
          :label="t('alertManagement.unified.groups')"
          @update:model-value="change(index, 'groups', $event)"
        />
      </template>
      <div class="flex items-center justify-between gap-2">
        <label class="flex items-center gap-2 text-sm"
          ><input
            type="checkbox"
            class="h-4 w-4 shrink-0 rounded border-line text-primary-600 focus:ring-primary-500"
            :checked="item.enabled"
            @change="change(index, 'enabled', $event.target.checked)"
          />{{ t('alertManagement.rules.enabled') }}</label
        >
        <BaseButton
          type="button"
          variant="outline"
          size="sm"
          @click="
            emit(
              'update:modelValue',
              modelValue.filter((_, position) => position !== index)
            )
          "
          >{{ t('common.delete') }}</BaseButton
        >
      </div>
    </fieldset>
  </section>
</template>
<script setup>
import { notificationTargetLabel } from '@/admin/utils/notificationTargets'
import BaseSelect from '@/components/ui/BaseSelect.vue'
import { useI18n } from 'vue-i18n'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseInput from '@/components/ui/BaseInput.vue'
import AlertScopeSelect from './AlertScopeSelect.vue'
const props = defineProps({
  modelValue: { type: Array, required: true },
  channels: { type: Array, required: true },
  users: { type: Array, required: true },
  groups: { type: Array, required: true }
})
const emit = defineEmits(['update:modelValue'])
const { t } = useI18n()
function change(index, key, value) {
  emit(
    'update:modelValue',
    props.modelValue.map((row, position) =>
      position === index ? { ...row, [key]: value } : row
    )
  )
}
function add() {
  emit('update:modelValue', [
    ...props.modelValue,
    {
      name: '',
      enabled: true,
      language: 'en-US',
      channel_uuid: '',
      email_recipients: '',
      users: [],
      groups: []
    }
  ])
}
</script>
