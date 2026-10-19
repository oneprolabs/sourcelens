<template>
  <details class="rounded-lg border border-line p-3">
    <summary class="cursor-pointer text-sm font-medium text-theme-secondary">
      {{ label }} ({{ modelValue.length }})
    </summary>
    <input
      v-model="search"
      type="search"
      :aria-label="label"
      :placeholder="label"
      class="mt-3 block w-full rounded-lg border border-line bg-surface px-3 py-2 text-sm text-theme shadow-sm focus:border-primary-500 focus:outline-none focus:ring-2 focus:ring-primary-500/20"
    />
    <div class="mt-2 max-h-36 space-y-1 overflow-y-auto">
      <label
        v-for="item in filtered"
        :key="item.value"
        class="flex items-center gap-2 text-sm text-theme-secondary"
      >
        <input
          type="checkbox"
          class="h-4 w-4 shrink-0 rounded border-line text-primary-600 focus:ring-primary-500"
          :value="item.value"
          :checked="modelValue.includes(item.value)"
          @change="toggle(item.value)"
        />
        {{ item.label }}
      </label>
      <p v-if="!filtered.length" class="text-sm text-theme-muted">
        {{ t('common.noData') }}
      </p>
    </div>
  </details>
</template>
<script setup>
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
const props = defineProps({
  modelValue: { type: Array, default: () => [] },
  options: { type: Array, default: () => [] },
  label: { type: String, required: true }
})
const emit = defineEmits(['update:modelValue'])
const { t } = useI18n()
const search = ref('')
const filtered = computed(() =>
  props.options.filter((item) =>
    item.label.toLowerCase().includes(search.value.toLowerCase())
  )
)
function toggle(value) {
  emit(
    'update:modelValue',
    props.modelValue.includes(value)
      ? props.modelValue.filter((item) => item !== value)
      : [...props.modelValue, value]
  )
}
</script>
