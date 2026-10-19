<template>
  <details class="rounded-lg border border-line p-3">
    <summary class="cursor-pointer text-sm font-medium text-theme-secondary">
      {{ label }}
      <span class="ml-1 text-xs font-normal text-theme-muted">
        {{
          !modelValue.length && emptyMeansAll
            ? t('alertManagement.unified.allResources')
            : t('alertManagement.unified.selectedCount', {
                count: modelValue.length
              })
        }}
      </span>
    </summary>
    <input
      v-model="search"
      type="search"
      :aria-label="label"
      :placeholder="label"
      class="mt-3 block w-full rounded-lg border border-line bg-surface px-3 py-2 text-sm text-theme shadow-sm focus:border-primary-500 focus:outline-none focus:ring-2 focus:ring-primary-500/20"
    />
    <div
      class="mt-2 flex items-center justify-between gap-3 border-b border-line pb-2"
    >
      <label
        class="flex min-h-9 cursor-pointer items-center gap-2 text-sm text-theme-secondary"
      >
        <BaseCheckbox
          :model-value="allSelected"
          :indeterminate="someSelected"
          :disabled="!filtered.length"
          @update:model-value="selectFiltered"
        />
        {{
          search
            ? t('alertManagement.unified.selectFiltered')
            : t('common.selectAll')
        }}
      </label>
      <BaseButton
        type="button"
        variant="ghost"
        size="sm"
        :disabled="!modelValue.length"
        @click="emit('update:modelValue', [])"
      >
        {{
          emptyMeansAll
            ? t('alertManagement.unified.allResources')
            : t('alertManagement.unified.clearSelection')
        }}
      </BaseButton>
    </div>
    <div class="mt-2 grid max-h-48 gap-1 overflow-y-auto sm:grid-cols-2">
      <label
        v-for="item in filtered"
        :key="item.value"
        class="flex min-h-9 cursor-pointer items-center gap-2 rounded px-2 text-sm text-theme-secondary hover:bg-surface-hover"
      >
        <BaseCheckbox
          :model-value="modelValue.includes(item.value)"
          @update:model-value="select(item.value, $event)"
        />
        <span class="min-w-0 break-words">{{ item.label }}</span>
      </label>
      <p v-if="!filtered.length" class="py-2 text-sm text-theme-muted">
        {{ t('common.noData') }}
      </p>
    </div>
  </details>
</template>
<script setup>
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import BaseCheckbox from '@/components/ui/BaseCheckbox.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import { setAlertSelection } from './alertOptions'
const props = defineProps({
  modelValue: { type: Array, default: () => [] },
  options: { type: Array, default: () => [] },
  label: { type: String, required: true },
  emptyMeansAll: Boolean
})
const emit = defineEmits(['update:modelValue'])
const { t } = useI18n()
const search = ref('')
const filtered = computed(() =>
  props.options.filter((item) =>
    item.label.toLowerCase().includes(search.value.toLowerCase())
  )
)
const allSelected = computed(
  () =>
    filtered.value.length > 0 &&
    filtered.value.every((item) => props.modelValue.includes(item.value))
)
const someSelected = computed(
  () =>
    !allSelected.value &&
    filtered.value.some((item) => props.modelValue.includes(item.value))
)
function select(value, checked) {
  emit(
    'update:modelValue',
    setAlertSelection(props.modelValue, [value], checked)
  )
}
function selectFiltered(checked) {
  emit(
    'update:modelValue',
    setAlertSelection(
      props.modelValue,
      filtered.value.map((item) => item.value),
      checked
    )
  )
}
</script>
