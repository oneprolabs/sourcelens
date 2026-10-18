<template>
  <details class="rounded-lg border border-line p-3">
    <summary class="cursor-pointer text-sm font-medium text-ink-700">
      {{ label }} ({{ modelValue.length }})
    </summary>
    <input
      v-model="search"
      type="search"
      :aria-label="label"
      :placeholder="label"
      class="mt-2 w-full rounded border border-line bg-surface px-2 py-1 text-sm"
    />
    <div class="mt-2 max-h-36 space-y-1 overflow-y-auto">
      <label
        v-for="item in filtered"
        :key="item.value"
        class="flex items-center gap-2 text-sm text-ink-700"
      >
        <input
          type="checkbox"
          class="h-4 w-4 shrink-0"
          :value="item.value"
          :checked="modelValue.includes(item.value)"
          @change="toggle(item.value)"
        />
        {{ item.label }}
      </label>
      <p v-if="!filtered.length" class="text-sm text-ink-500">
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
