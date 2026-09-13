<template>
  <BaseSelect
    id="composer-rounds"
    :model-value="modelValue"
    class="reasoning-depth-select"
    variant="unstyled"
    :full-width="false"
    :menu-min-width="272"
    :aria-label="`${t('lens.chat.reasoningDepth')}: ${selectedTier.label}`"
    @update:model-value="$emit('update:modelValue', $event)"
  >
    <option v-for="tier in tiers" :key="tier.value" :value="tier.value">
      {{ tier.label }}
    </option>
    <template #selected>
      <span class="reasoning-depth-value">
        <SlidersHorizontal :size="15" aria-hidden="true" />
        <span>{{ selectedTier.label }}</span>
      </span>
    </template>
    <template #option="{ option }">
      <span class="reasoning-depth-option">
        <span class="reasoning-depth-option-label">{{ option.label }}</span>
        <span class="reasoning-depth-description">
          {{ tiers.find((tier) => tier.value === option.value).description }}
        </span>
      </span>
    </template>
  </BaseSelect>
</template>

<script setup>
import { computed } from 'vue'
import { SlidersHorizontal } from '@lucide/vue'
import { useI18n } from 'vue-i18n'

import BaseSelect from '@/components/ui/BaseSelect.vue'

const props = defineProps({
  modelValue: { type: String, default: '' }
})
defineEmits(['update:modelValue'])

const { t } = useI18n()
const tiers = computed(() =>
  [
    ['', 'Default'],
    ['flash', 'Flash'],
    ['fast', 'Fast'],
    ['balanced', 'Balanced'],
    ['deep', 'Deep'],
    ['max', 'Max']
  ].map(([value, key]) => ({
    value,
    label: t(`lens.chat.reasoning${key}`),
    description: t(`lens.chat.reasoning${key}Description`)
  }))
)
const selectedTier = computed(
  () =>
    tiers.value.find((tier) => tier.value === props.modelValue) ||
    tiers.value[0]
)
</script>

<style scoped>
.reasoning-depth-select {
  @apply max-w-full;
}

.reasoning-depth-select :deep([role='combobox']) {
  @apply min-h-11 rounded-full border border-transparent py-2 pl-3 pr-9
    text-sm text-theme-muted transition-colors;
}

.reasoning-depth-select :deep([role='combobox']:hover),
.reasoning-depth-select :deep([aria-expanded='true']) {
  @apply bg-surface-sunken text-theme;
}

.reasoning-depth-select :deep([role='combobox']:focus-visible) {
  @apply ring-2 ring-primary-500/40;
}

.reasoning-depth-value {
  @apply flex items-center gap-2;
}

.reasoning-depth-value svg {
  @apply shrink-0;
}

.reasoning-depth-option {
  @apply flex flex-col gap-0.5;
}

.reasoning-depth-option-label {
  @apply text-sm font-medium text-theme;
}

.reasoning-depth-description {
  @apply whitespace-normal text-xs font-normal leading-5 text-theme-muted;
}

:global(#composer-rounds-listbox) {
  @apply rounded-2xl p-1.5;
}

:global(#composer-rounds-listbox [role='option']) {
  @apply gap-4 rounded-xl px-3 py-2;
}

:global(#composer-rounds-listbox [aria-selected='true']) {
  @apply bg-surface-sunken;
}

:global(#composer-rounds-listbox [role='option'] > svg) {
  @apply text-theme;
}
</style>
