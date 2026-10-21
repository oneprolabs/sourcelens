<template>
  <button
    type="button"
    class="deep-thinking-toggle"
    :class="{ 'is-active': modelValue }"
    :aria-pressed="modelValue"
    :title="t('lens.chat.deepThinkingHint')"
    @click="$emit('update:modelValue', !modelValue)"
  >
    <Brain :size="16" aria-hidden="true" />
    <span>{{ t('lens.chat.deepThinking') }}</span>
  </button>
</template>

<script setup>
import { Brain } from '@lucide/vue'
import { useI18n } from 'vue-i18n'

defineProps({ modelValue: { type: Boolean, default: false } })
defineEmits(['update:modelValue'])
const { t } = useI18n()
</script>

<style scoped>
.deep-thinking-toggle {
  @apply inline-flex min-h-11 items-center gap-2 rounded-full border border-transparent px-3 py-2
    text-sm text-theme-muted transition-colors;
}

.deep-thinking-toggle:hover {
  @apply bg-surface-sunken text-theme;
}

.deep-thinking-toggle.is-active {
  @apply border-primary-200 bg-primary-50 text-primary-700;
}

.deep-thinking-toggle:focus-visible {
  @apply outline-none ring-2 ring-primary-500/40;
}
</style>
