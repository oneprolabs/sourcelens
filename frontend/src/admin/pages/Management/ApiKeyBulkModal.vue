<template>
  <BaseModal :show="true" :title="t(`${prefix}.${action}`)" @close="close">
    <div v-if="!result" class="space-y-4">
      <p class="text-sm text-ink-600">
        {{
          t(
            `${prefix}.${platform ? `platform${action}Hint` : `${action}Hint`}`,
            { count: keys.length }
          )
        }}
      </p>
      <label v-if="action === 'extend'" class="block">
        <span class="mb-1 block text-sm font-medium text-ink-700">
          {{ t(`${prefix}.duration`) }}
        </span>
        <BaseSelect v-model="days" :disabled="busy || stale" class="form-input">
          <option :value="30">{{ t('management.apiKeyDays30') }}</option>
          <option :value="90">{{ t('management.apiKeyDays90') }}</option>
          <option :value="365">{{ t('management.apiKeyDays365') }}</option>
        </BaseSelect>
      </label>
      <ul class="max-h-48 space-y-1 overflow-auto text-sm text-ink-700">
        <li v-for="key in keys" :key="key.uuid">
          {{ platform && key.tenant?.name ? `${key.tenant.name} · ` : ''
          }}{{ key.name }}
        </li>
      </ul>
    </div>
    <div v-else class="space-y-3" role="status">
      <p class="text-sm text-ink-700">{{ t(`${prefix}.result`, result) }}</p>
      <ul
        v-if="result.skipped_items?.length"
        class="max-h-64 space-y-2 overflow-auto text-sm"
      >
        <li
          v-for="item in result.skipped_items"
          :key="item.uuid"
          class="rounded-md border border-line p-3"
        >
          <strong>{{ keyName(item.uuid) }}</strong>
          <p class="mt-1 text-ink-600">{{ t(`${prefix}.${item.reason}`) }}</p>
        </li>
      </ul>
    </div>
    <p v-if="error" class="mt-3 text-sm text-danger-700" role="alert">
      {{ error }}
    </p>
    <template #footer>
      <BaseButton
        v-if="!result"
        :variant="action === 'revoke' ? 'danger' : 'primary'"
        :loading="busy"
        :disabled="busy || stale"
        @click="save"
      >
        {{ t(`${prefix}.${action}`) }}
      </BaseButton>
      <BaseButton variant="outline" :disabled="busy" @click="close">
        {{ t(result || stale ? 'common.close' : 'common.cancel') }}
      </BaseButton>
    </template>
  </BaseModal>
</template>

<script setup>
import { ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { bulkUpdateTenantApiKeys } from '@/api/tenant'
import { platformBulkApiKeyAction } from '@/api/platform'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseModal from '@/components/ui/BaseModal.vue'
import BaseSelect from '@/components/ui/BaseSelect.vue'

const props = defineProps({
  action: { type: String, required: true },
  keys: { type: Array, required: true },
  platform: { type: Boolean, default: false }
})
const emit = defineEmits(['close', 'updated'])
const { t } = useI18n()
const prefix = 'management.apiKeyBulk'
const days = ref(90)
const busy = ref(false)
const error = ref('')
const stale = ref(false)
const result = ref(null)

function keyName(uuid) {
  return props.keys.find((key) => key.uuid === uuid)?.name || uuid
}

function close() {
  if (!busy.value) emit('close', stale.value)
}

async function save() {
  if (
    busy.value ||
    stale.value ||
    result.value ||
    !props.keys.length ||
    (props.platform && !['disable', 'revoke'].includes(props.action))
  )
    return
  busy.value = true
  error.value = ''
  try {
    const saveKeys = props.platform
      ? platformBulkApiKeyAction
      : bulkUpdateTenantApiKeys
    result.value = await saveKeys({
      action: props.action,
      keys: props.keys.map(({ uuid, updated_at }) => ({ uuid, updated_at })),
      ...(props.action === 'extend' ? { days: Number(days.value) } : {})
    })
    emit('updated', result.value)
  } catch (failure) {
    stale.value = [404, 409].includes(failure?.response?.status)
    error.value = t(
      stale.value ? `${prefix}.stale` : 'management.apiKeySaveFailed'
    )
  } finally {
    busy.value = false
  }
}
</script>
