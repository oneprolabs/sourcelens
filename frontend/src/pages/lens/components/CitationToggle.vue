<template>
  <button
    type="button"
    class="citation-toggle"
    :class="{ 'is-active': modelValue }"
    :aria-pressed="modelValue"
    :title="t('lens.chat.showCitationsHint')"
    @click="$emit('update:modelValue', !modelValue)"
  >
    <Quote :size="16" aria-hidden="true" />
    <span>{{ t('lens.chat.showCitations') }}</span>
  </button>
</template>

<script setup>
import { Quote } from '@lucide/vue'
import { useI18n } from 'vue-i18n'

defineProps({ modelValue: { type: Boolean, default: true } })
defineEmits(['update:modelValue'])
const { t } = useI18n()
</script>

<style scoped>
.citation-toggle {
  @apply inline-flex min-h-11 items-center gap-2 rounded-full border border-transparent px-3 py-2
    text-sm text-theme-muted transition-colors;
}

.citation-toggle:hover {
  @apply bg-surface-sunken text-theme;
}

.citation-toggle.is-active {
  @apply border-primary-200 bg-primary-50 text-primary-700;
}

.citation-toggle:focus-visible {
  @apply outline-none ring-2 ring-primary-500/40;
}
</style>
