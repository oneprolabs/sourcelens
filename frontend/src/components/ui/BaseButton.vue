<template>
  <button
    data-ui-button
    :data-variant="variant"
    :type="type"
    :disabled="disabled || loading"
    :class="buttonClasses"
    :aria-label="loading ? t('common.loading') : undefined"
    :aria-disabled="disabled || loading"
    @click="handleClick"
    @keydown="handleKeydown"
  >
    <svg
      v-if="loading"
      class="animate-spin -ml-1 mr-2 h-4 w-4"
      xmlns="http://www.w3.org/2000/svg"
      fill="none"
      viewBox="0 0 24 24"
    >
      <circle
        class="opacity-25"
        cx="12"
        cy="12"
        r="10"
        stroke="currentColor"
        stroke-width="4"
      ></circle>
      <path
        class="opacity-75"
        fill="currentColor"
        d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"
      ></path>
    </svg>
    <slot />
  </button>
</template>

<script setup>
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

const { t } = useI18n()
const props = defineProps({
  variant: {
    type: String,
    default: 'primary',
    validator: (value) =>
      [
        'primary',
        'secondary',
        'danger',
        'danger-outline',
        'outline',
        'ghost'
      ].includes(value)
  },
  size: {
    type: String,
    default: 'md',
    validator: (value) => ['sm', 'md', 'lg'].includes(value)
  },
  type: {
    type: String,
    default: 'button'
  },
  disabled: {
    type: Boolean,
    default: false
  },
  loading: {
    type: Boolean,
    default: false
  },
  block: {
    type: Boolean,
    default: false
  }
})

const emit = defineEmits(['click'])

const dangerOutlineClasses = [
  'border-danger-600 bg-transparent text-danger-700',
  'hover:border-danger-700 hover:bg-danger-600 hover:text-white',
  'focus:ring-danger-500/30'
].join(' ')

const handleClick = (event) => {
  if (!props.disabled && !props.loading) {
    emit('click', event)
  }
}

const handleKeydown = (event) => {
  if (
    (event.key === 'Enter' || event.key === ' ') &&
    !props.disabled &&
    !props.loading
  ) {
    event.preventDefault()
    emit('click', event)
  }
}

const buttonClasses = computed(() => {
  const baseClasses =
    'inline-flex min-w-11 items-center justify-center gap-2 rounded-lg border text-sm font-medium transition-colors focus:outline-none focus:ring-2 disabled:cursor-not-allowed disabled:opacity-50 md:min-w-0'
  const variantClasses = {
    primary:
      'border-primary-600 bg-primary-600 text-white hover:border-primary-700 hover:bg-primary-700 focus:ring-primary-500/30',
    secondary:
      'border-line bg-surface text-ink-700 hover:border-line/80 hover:bg-line-soft focus:ring-primary-500/20',
    danger:
      'border-danger-600 bg-danger-600 text-white hover:border-danger-700 hover:bg-danger-700 focus:ring-danger-500/30',
    'danger-outline': dangerOutlineClasses,
    outline:
      'border-line bg-transparent text-ink-700 hover:border-primary-300 hover:bg-primary-50 hover:text-primary-700 focus:ring-primary-500/20',
    ghost:
      'border-transparent bg-transparent text-ink-600 hover:bg-line-soft hover:text-ink-900 focus:ring-primary-500/20'
  }
  const sizeClasses = {
    sm: 'h-11 px-3 py-1.5 text-xs md:h-8',
    md: 'h-11 px-4 py-2 text-sm md:h-9',
    lg: 'px-5 py-3 text-base h-11'
  }
  const blockClass = props.block ? 'w-full' : ''

  return [
    baseClasses,
    variantClasses[props.variant],
    sizeClasses[props.size],
    blockClass
  ]
    .filter(Boolean)
    .join(' ')
})
</script>
