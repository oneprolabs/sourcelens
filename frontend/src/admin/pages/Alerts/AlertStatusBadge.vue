<template>
  <span
    class="inline-flex items-center whitespace-nowrap rounded px-2 py-0.5 text-xs font-medium"
    :class="tone"
  >
    {{ t(`alertManagement.unified.statuses.${status}`) }}
  </span>
</template>

<script setup>
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

const props = defineProps({ status: { type: String, required: true } })
const { t } = useI18n()
const tone = computed(
  () =>
    ({
      sent: 'bg-success-100 text-success-700',
      failed: 'bg-danger-100 text-danger-700',
      partial: 'bg-warning-100 text-warning-700',
      pending: 'bg-primary-100 text-primary-700',
      dispatched: 'bg-primary-100 text-primary-700'
    })[props.status] || 'bg-surface-sunken text-theme-secondary'
)
</script>
