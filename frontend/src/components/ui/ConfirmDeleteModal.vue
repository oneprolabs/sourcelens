<template>
  <BaseModal
    :show="show"
    :title="title"
    :icon="iconComponent"
    :icon-type="iconType"
    max-width="md"
    :close-on-backdrop="!loading"
    @close="handleCancel"
  >
    <p v-if="message" class="text-sm leading-6 text-ink-600">
      {{ message }}
    </p>
    <p v-if="name" class="mt-2 break-all text-sm font-medium text-ink-900">
      {{ name }}
    </p>

    <p
      v-if="warning"
      class="mt-3 text-sm leading-6 text-warning-700"
      role="alert"
    >
      {{ warning }}
    </p>

    <div v-if="$slots.default" class="mt-3">
      <slot />
    </div>

    <div v-if="confirmLabel" class="mt-4">
      <label class="block text-sm font-medium text-ink-700">
        {{ confirmLabel }}
      </label>
      <input
        v-model="typedConfirmation"
        class="form-input mt-1"
        :placeholder="confirmPlaceholder || name"
        :disabled="loading"
        autocomplete="off"
      />
      <p v-if="confirmHint" class="mt-1 text-xs leading-5 text-ink-500">
        {{ confirmHint }}
      </p>
    </div>

    <template #footer>
      <div class="flex flex-row-reverse gap-2">
        <BaseButton
          :variant="variant"
          :loading="loading"
          :disabled="!canConfirm"
          @click="handleConfirm"
        >
          {{ confirmText }}
        </BaseButton>
        <BaseButton variant="outline" :disabled="loading" @click="handleCancel">
          {{ cancelText }}
        </BaseButton>
      </div>
    </template>
  </BaseModal>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { AlertTriangle, Trash2 } from '@lucide/vue'
import { useI18n } from 'vue-i18n'

import BaseButton from './BaseButton.vue'
import BaseModal from './BaseModal.vue'

const { t } = useI18n()

const props = defineProps({
  show: {
    type: Boolean,
    default: false
  },
  title: {
    type: String,
    default: ''
  },
  message: {
    type: String,
    default: ''
  },
  name: {
    type: String,
    default: ''
  },
  warning: {
    type: String,
    default: ''
  },
  iconType: {
    type: String,
    default: 'error',
    validator: (value) =>
      ['info', 'success', 'warning', 'error'].includes(value)
  },
  variant: {
    type: String,
    default: 'danger',
    validator: (value) =>
      ['primary', 'secondary', 'danger', 'danger-outline'].includes(value)
  },
  confirmText: {
    type: String,
    default: ''
  },
  cancelText: {
    type: String,
    default: ''
  },
  loading: {
    type: Boolean,
    default: false
  },
  confirmLabel: {
    type: String,
    default: ''
  },
  confirmPlaceholder: {
    type: String,
    default: ''
  },
  confirmHint: {
    type: String,
    default: ''
  }
})

const emit = defineEmits(['confirm', 'cancel'])

const typedConfirmation = ref('')

const requiresTypedName = computed(
  () => Boolean(props.confirmLabel) && Boolean(props.name)
)

const canConfirm = computed(
  () => !requiresTypedName.value || typedConfirmation.value === props.name
)

const confirmText = computed(() => props.confirmText || t('common.delete'))
const cancelText = computed(() => props.cancelText || t('common.cancel'))

const iconComponent = computed(() =>
  props.iconType === 'warning' ? AlertTriangle : Trash2
)

watch(
  () => props.show,
  (show) => {
    if (!show) typedConfirmation.value = ''
  }
)

function handleConfirm() {
  if (!canConfirm.value || props.loading) return
  emit('confirm')
}

function handleCancel() {
  if (props.loading) return
  emit('cancel')
}
</script>
