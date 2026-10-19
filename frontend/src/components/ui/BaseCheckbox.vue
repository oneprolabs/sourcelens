<template>
  <span class="relative inline-flex h-4 w-4 shrink-0 align-middle">
    <input
      v-bind="$attrs"
      type="checkbox"
      class="peer h-4 w-4 cursor-pointer opacity-0 disabled:cursor-not-allowed"
      :checked="modelValue"
      :indeterminate="indeterminate"
      :disabled="disabled"
      :aria-checked="indeterminate ? 'mixed' : modelValue"
      @change="change"
    />
    <span
      aria-hidden="true"
      class="pointer-events-none absolute inset-0 flex items-center justify-center rounded border transition-colors peer-focus-visible:ring-2 peer-focus-visible:ring-primary-500 peer-focus-visible:ring-offset-2 peer-focus-visible:ring-offset-surface peer-disabled:opacity-50"
      :class="
        modelValue || indeterminate
          ? 'border-primary-600 bg-primary-600 text-white'
          : 'border-line-strong bg-surface'
      "
    >
      <svg v-if="indeterminate" class="h-3 w-3" viewBox="0 0 16 16" fill="none">
        <path
          d="M3 8h10"
          stroke="currentColor"
          stroke-width="2"
          stroke-linecap="round"
        />
      </svg>
      <svg
        v-else-if="modelValue"
        class="h-3 w-3"
        viewBox="0 0 16 16"
        fill="none"
      >
        <path
          d="m3 8 3 3 7-7"
          stroke="currentColor"
          stroke-width="2"
          stroke-linecap="round"
          stroke-linejoin="round"
        />
      </svg>
    </span>
  </span>
</template>
<script setup>
defineOptions({ inheritAttrs: false })
defineProps({
  modelValue: { type: Boolean, default: false },
  indeterminate: { type: Boolean, default: false },
  disabled: { type: Boolean, default: false }
})
const emit = defineEmits(['update:modelValue', 'change'])
function change(event) {
  emit('update:modelValue', event.target.checked)
  emit('change', event.target.checked)
}
</script>
