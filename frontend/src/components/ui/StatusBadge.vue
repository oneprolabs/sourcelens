<template>
  <span
    :class="getStatusClass(status)"
    class="inline-flex items-center gap-1.5 whitespace-nowrap text-sm font-medium"
  >
    <span :class="dotClass" class="h-1.5 w-1.5 shrink-0 rounded-full" />
    {{ getStatusText(status) }}
  </span>
</template>

<script setup>
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

const { t } = useI18n()

const props = defineProps({
  status: {
    type: String,
    default: 'unknown'
  }
})

const dotClass = computed(() => {
  const classes = {
    success: 'bg-success-600',
    failed: 'bg-danger-600',
    processing: 'bg-warning-600',
    running: 'bg-warning-600',
    fetched: 'bg-primary-600',
    pending: 'bg-warning-600',
    completed: 'bg-success-600',
    active: 'bg-success-600',
    archived: 'bg-ink-400',
    enabled: 'bg-success-600',
    disabled: 'bg-ink-400',
    inactive: 'bg-ink-400',
    cancelled: 'bg-ink-400',
    online: 'bg-success-600',
    unresponsive: 'bg-danger-600',
    offline: 'bg-ink-400',
    draining: 'bg-warning-600',
    approved: 'bg-success-600',
    rejected: 'bg-danger-600'
  }
  return classes[props.status] || 'bg-ink-400'
})

const getStatusClass = (status) => {
  const classes = {
    success: 'text-success-700',
    failed: 'text-danger-700',
    processing: 'text-warning-700',
    running: 'text-warning-700',
    fetched: 'text-primary-700',
    pending: 'text-warning-700',
    completed: 'text-success-700',
    active: 'text-success-700',
    archived: 'text-ink-500',
    enabled: 'text-success-700',
    disabled: 'text-ink-500',
    inactive: 'text-ink-500',
    cancelled: 'text-ink-500',
    online: 'text-success-700',
    unresponsive: 'text-danger-700',
    offline: 'text-ink-500',
    draining: 'text-warning-700',
    approved: 'text-success-700',
    rejected: 'text-danger-700'
  }
  return classes[status] || 'text-ink-500'
}

const getStatusText = (status) => {
  const statusTexts = {
    success: t('common.status.success'),
    failed: t('common.status.failed'),
    processing: t('common.status.processing'),
    running: t('common.status.processing'),
    fetched: t('common.status.fetched'),
    pending: t('common.status.pending'),
    completed: t('common.status.completed'),
    active: t('common.status.active'),
    archived: t('common.status.archived'),
    enabled: t('common.status.enabled'),
    disabled: t('common.status.disabled'),
    inactive: t('common.status.disabled'),
    cancelled: t('common.status.cancelled'),
    online: t('common.status.online'),
    unresponsive: t('common.status.unresponsive'),
    offline: t('common.status.offline'),
    draining: t('common.status.draining'),
    approved: t('common.status.approved'),
    rejected: t('common.status.rejected')
  }
  return statusTexts[status] || status || t('common.status.unknown')
}
</script>
