<template>
  <BaseModal
    :show="true"
    :title="t(`${prefix}.${enabled ? 'enable' : 'disable'}`)"
    @close="close"
  >
    <p v-if="!result" class="text-sm text-ink-600">
      {{
        t(`${prefix}.${enabled ? 'enableHint' : 'disableHint'}`, {
          count: assistants.length
        })
      }}
    </p>
    <div v-else class="space-y-3" role="status">
      <p class="text-sm">{{ t(`${prefix}.result`, result) }}</p>
      <ul
        v-if="result.skipped_items?.length"
        class="max-h-72 space-y-2 overflow-auto text-sm"
      >
        <li
          v-for="item in result.skipped_items"
          :key="item.uuid"
          class="rounded-lg border border-line p-3"
        >
          <strong>{{ assistantName(item.uuid) }}</strong>
          <p class="mt-1 text-ink-600">{{ t(`${prefix}.${item.reason}`) }}</p>
        </li>
      </ul>
    </div>
    <p v-if="error" class="mt-3 text-sm text-red-700" role="alert">
      {{ error }}
    </p>
    <template #footer>
      <BaseButton
        v-if="!result"
        :variant="enabled ? 'primary' : 'danger'"
        :loading="busy"
        :disabled="readonly"
        @click="save"
      >
        {{ t(`${prefix}.${enabled ? 'enable' : 'disable'}`) }}
      </BaseButton>
      <BaseButton variant="outline" :disabled="busy" @click="close">
        {{ t(result ? 'common.close' : 'common.cancel') }}
      </BaseButton>
    </template>
  </BaseModal>
</template>

<script setup>
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { bulkUpdateAssistantEnablement } from '@/api/lens'
import BaseModal from '@/components/ui/BaseModal.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import { useUserStore } from '@/store/user'
import { extractErrorMessage } from '@/utils/api'

const props = defineProps({
  assistants: { type: Array, required: true },
  enabled: { type: Boolean, required: true }
})
const emit = defineEmits(['close', 'updated'])
const { t } = useI18n()
const userStore = useUserStore()
const prefix = 'lensAdmin.enablement'
const readonly = computed(() => userStore.delegation?.mode === 'readonly')
const busy = ref(false)
const error = ref('')
const result = ref(null)

function assistantName(uuid) {
  return props.assistants.find((item) => item.uuid === uuid)?.name || uuid
}

function close() {
  if (!busy.value) emit('close')
}

async function save() {
  if (busy.value || readonly.value || result.value || !props.assistants.length)
    return
  busy.value = true
  error.value = ''
  try {
    result.value = await bulkUpdateAssistantEnablement({
      assistants: props.assistants.map(({ uuid, updated_at }) => ({
        uuid,
        updated_at
      })),
      enabled: props.enabled
    })
    emit('updated', result.value)
  } catch (failure) {
    error.value = extractErrorMessage(
      failure,
      t('lensAdmin.messages.saveFailed')
    )
  } finally {
    busy.value = false
  }
}
</script>
