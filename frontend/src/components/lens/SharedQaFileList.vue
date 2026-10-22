<script setup>
import { Download, Eye, FileText } from '@lucide/vue'
import { useI18n } from 'vue-i18n'

import { extensionOf, isPreviewable } from '@/utils/filePreview'

defineProps({
  files: { type: Array, default: () => [] },
  label: { type: String, required: true }
})

const emit = defineEmits(['preview', 'download'])
const { t } = useI18n()

function formatBytes(size) {
  if (!size) return ''
  if (size < 1024) return `${size} B`
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`
  return `${(size / (1024 * 1024)).toFixed(1)} MB`
}

function fileTypeLabel(file) {
  const extension = extensionOf(file.filename).toUpperCase()
  return [extension, formatBytes(file.byte_size)].filter(Boolean).join(' · ')
}

function open(file) {
  emit(isPreviewable(file) ? 'preview' : 'download', file)
}
</script>

<template>
  <div class="space-y-2" role="list" :aria-label="label">
    <div
      v-for="file in files"
      :key="file.uuid"
      class="flex items-center gap-2 rounded-lg border border-line bg-surface px-3 py-2"
      role="listitem"
    >
      <button
        type="button"
        class="flex min-w-0 flex-1 items-center gap-3 text-left"
        :aria-label="t('lens.qa.openFile', { name: file.filename })"
        @click="open(file)"
      >
        <span
          class="flex h-9 w-9 shrink-0 items-center justify-center rounded-md bg-surface-sunken text-ink-500"
        >
          <FileText :size="19" aria-hidden="true" />
        </span>
        <span class="min-w-0">
          <span
            class="block break-words text-sm font-medium text-ink-800 sm:truncate"
          >
            {{ file.filename }}
          </span>
          <span class="block text-xs text-ink-400">
            {{ fileTypeLabel(file) }}
          </span>
        </span>
      </button>

      <button
        v-if="isPreviewable(file)"
        type="button"
        class="inline-flex min-h-10 min-w-10 shrink-0 items-center justify-center rounded-md p-2 text-ink-400 transition-colors hover:bg-surface-sunken hover:text-primary-600"
        :aria-label="t('lens.qa.previewFile', { name: file.filename })"
        @click="emit('preview', file)"
      >
        <Eye :size="18" aria-hidden="true" />
      </button>
      <button
        type="button"
        class="inline-flex min-h-10 min-w-10 shrink-0 items-center justify-center rounded-md p-2 text-ink-400 transition-colors hover:bg-surface-sunken hover:text-primary-600"
        :aria-label="t('lens.qa.downloadFile', { name: file.filename })"
        @click="emit('download', file)"
      >
        <Download :size="18" aria-hidden="true" />
      </button>
    </div>
  </div>
</template>
