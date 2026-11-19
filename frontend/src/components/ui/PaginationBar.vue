<template>
  <div v-if="prototype && total > 0" class="admin-prototype-pagination">
    <span>{{ t('common.pagination.total', { total }) }}</span>
    <div class="admin-pagination-controls">
      <template v-if="normalizedTotalPages > 1">
        <BaseButton
          variant="ghost"
          size="sm"
          :disabled="currentPage <= 1"
          :aria-label="t('common.pagination.previous')"
          @click="$emit('prev')"
          ><ChevronLeft :size="14" aria-hidden="true"
        /></BaseButton>
        <span>{{ currentPage }} / {{ normalizedTotalPages }}</span>
        <BaseButton
          variant="ghost"
          size="sm"
          :disabled="currentPage >= normalizedTotalPages"
          :aria-label="t('common.pagination.next')"
          @click="$emit('next')"
          ><ChevronRight :size="14" aria-hidden="true"
        /></BaseButton>
      </template>
      <BaseSelect
        :model-value="pageSize"
        :aria-label="t('common.pagination.itemsPerPage')"
        :full-width="false"
        mobile-touch
        size="sm"
        @update:model-value="updatePageSize"
      >
        <option v-for="size in pageSizeOptions" :key="size" :value="size">
          {{ t('common.pagination.size', { size }) }}
        </option>
      </BaseSelect>
    </div>
  </div>
  <div
    v-else-if="total > 0"
    class="mt-4 flex flex-shrink-0 flex-col items-start justify-between gap-4 border-t border-line pt-4 md:flex-row md:items-center"
  >
    <div class="text-sm font-medium text-theme-secondary">
      {{ t('common.pagination.showing', showing) }}
    </div>
    <div
      class="flex w-full flex-col gap-3 md:w-auto md:flex-row md:items-center"
    >
      <div class="flex items-center gap-3">
        <label class="text-sm text-theme-secondary">
          {{ t('common.pagination.itemsPerPage') }}:
        </label>
        <BaseSelect
          :model-value="pageSize"
          class="min-w-[4.5rem]"
          :full-width="false"
          mobile-touch
          size="sm"
          @update:model-value="updatePageSize"
        >
          <option v-for="size in pageSizeOptions" :key="size" :value="size">
            {{ size }}
          </option>
        </BaseSelect>
      </div>
      <div
        class="grid w-full grid-cols-[2.75rem_1fr_2.75rem] items-center gap-2 md:flex md:w-auto md:gap-3"
      >
        <BaseButton
          variant="outline"
          size="sm"
          :disabled="currentPage <= 1"
          :title="t('common.pagination.previous')"
          @click="$emit('prev')"
        >
          <svg
            class="h-4 w-4"
            fill="none"
            stroke="currentColor"
            viewBox="0 0 24 24"
          >
            <path
              stroke-linecap="round"
              stroke-linejoin="round"
              stroke-width="2"
              d="M15 19l-7-7 7-7"
            />
          </svg>
          <span class="sr-only">{{ t('common.pagination.previous') }}</span>
        </BaseButton>
        <span
          class="inline-flex min-h-11 items-center justify-center rounded-md border border-line bg-surface-sunken px-3 py-1.5 text-sm font-semibold text-theme-secondary md:min-h-0"
        >
          {{
            t('common.pagination.page', {
              current: currentPage,
              total: normalizedTotalPages
            })
          }}
        </span>
        <BaseButton
          variant="outline"
          size="sm"
          :disabled="currentPage >= normalizedTotalPages"
          :title="t('common.pagination.next')"
          @click="$emit('next')"
        >
          <svg
            class="h-4 w-4"
            fill="none"
            stroke="currentColor"
            viewBox="0 0 24 24"
          >
            <path
              stroke-linecap="round"
              stroke-linejoin="round"
              stroke-width="2"
              d="M9 5l7 7-7 7"
            />
          </svg>
          <span class="sr-only">{{ t('common.pagination.next') }}</span>
        </BaseButton>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { ChevronLeft, ChevronRight } from '@lucide/vue'
import { useI18n } from 'vue-i18n'

import BaseButton from './BaseButton.vue'
import BaseSelect from './BaseSelect.vue'

const props = defineProps({
  prototype: { type: Boolean, default: false },
  currentPage: { type: Number, required: true },
  pageSize: { type: Number, required: true },
  total: { type: Number, required: true },
  pageSizeOptions: {
    type: Array,
    default: () => [10, 20, 50, 100]
  }
})

const emit = defineEmits([
  'update:pageSize',
  'prev',
  'next',
  'page-size-change'
])
const { t } = useI18n()

const normalizedTotalPages = computed(() =>
  Math.max(1, Math.ceil(props.total / props.pageSize))
)

const showing = computed(() => ({
  from: props.total === 0 ? 0 : (props.currentPage - 1) * props.pageSize + 1,
  to: Math.min(props.currentPage * props.pageSize, props.total),
  total: props.total
}))

function updatePageSize(value) {
  emit('update:pageSize', Number(value))
  emit('page-size-change')
}
</script>
