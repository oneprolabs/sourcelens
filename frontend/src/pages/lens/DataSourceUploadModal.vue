<template>
  <BaseModal :show="show" :title="t('lensAdmin.upload.title')" @close="close">
    <p class="mb-4 text-sm text-ink-500">{{ datasource?.name }}</p>
    <input
      ref="fileInput"
      type="file"
      class="hidden"
      multiple
      accept=".zip,application/zip"
      :disabled="uploading"
      @change="pickFiles"
    />
    <button
      type="button"
      class="upload-dropzone"
      :class="{ 'upload-dropzone-active': dragging }"
      :disabled="uploading"
      @click="fileInput?.click()"
      @dragover.prevent="dragging = !uploading"
      @dragleave.prevent="dragging = false"
      @drop.prevent="dropFiles"
    >
      <UploadCloudIcon class="h-8 w-8 text-brand-500" aria-hidden="true" />
      <span class="text-sm font-medium text-ink-800">
        {{ t('lensAdmin.upload.select') }}
      </span>
      <span class="text-xs leading-5 text-ink-500">
        {{ t('lensAdmin.upload.hint') }}
      </span>
      <span v-if="maxBytes" class="text-xs text-ink-500">
        {{ t('lensAdmin.upload.limit', { size: formatSize(maxBytes) }) }}
      </span>
    </button>
    <p v-if="error" role="alert" class="mt-3 text-sm text-danger-600">
      {{ error }}
    </p>
    <ul
      v-if="entries.length"
      class="mt-4 divide-y divide-line rounded-lg border border-line"
    >
      <li
        v-for="entry in entries"
        :key="entry.id"
        class="flex items-start gap-3 p-3"
      >
        <FileArchiveIcon
          class="mt-1 h-5 w-5 shrink-0 text-ink-500"
          aria-hidden="true"
        />
        <div class="min-w-0 flex-1">
          <p class="break-all text-sm font-medium text-ink-900">
            {{ entry.file.name }}
          </p>
          <p class="mt-1 text-xs text-ink-500">
            {{ formatSize(entry.file.size) }} ·
            {{ t(`lensAdmin.upload.${entry.status}`) }}
          </p>
          <p v-if="entry.error" class="mt-1 text-xs text-danger-600">
            {{ entry.error }}
          </p>
        </div>
        <BaseButton
          v-if="entry.status !== 'submitted'"
          size="sm"
          variant="outline"
          :disabled="uploading"
          :aria-label="t('lensAdmin.upload.remove', { name: entry.file.name })"
          @click="entries = entries.filter((item) => item.id !== entry.id)"
        >
          {{ t('common.delete') }}
        </BaseButton>
        <CheckCircleIcon
          v-else
          class="h-5 w-5 text-success-600"
          aria-hidden="true"
        />
      </li>
    </ul>
    <template #footer>
      <div class="flex items-center justify-between gap-3">
        <span class="text-xs text-ink-500">{{
          t('lensAdmin.upload.count', { count: entries.length })
        }}</span>
        <div class="flex gap-2">
          <BaseButton variant="outline" :disabled="uploading" @click="close">
            {{ t('common.close') }}
          </BaseButton>
          <BaseButton
            variant="primary"
            :loading="uploading"
            :disabled="!canSave"
            @click="save"
          >
            {{ t('common.save') }}
          </BaseButton>
        </div>
      </div>
    </template>
  </BaseModal>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import {
  CheckCircle as CheckCircleIcon,
  FileArchive as FileArchiveIcon,
  UploadCloud as UploadCloudIcon
} from '@lucide/vue'
import BaseModal from '@/components/ui/BaseModal.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import { getDataSourceUploadLimits, uploadDataSourceFile } from '@/api/lens'
import { extractErrorMessage } from '@/utils/api'

const props = defineProps({
  show: Boolean,
  datasource: { type: Object, default: null }
})
const emit = defineEmits(['close', 'uploaded'])
const { t, locale } = useI18n()
const fileInput = ref(null)
const entries = ref([])
const uploading = ref(false)
const dragging = ref(false)
const error = ref('')
const maxBytes = ref(0)
let nextId = 0
let requestId = 0
const canSave = computed(
  () =>
    !uploading.value &&
    maxBytes.value > 0 &&
    entries.value.some((entry) => entry.status !== 'submitted')
)

function formatSize(bytes) {
  return `${new Intl.NumberFormat(locale.value, { maximumFractionDigits: 1 }).format(bytes / 1024)} KB`
}

function addFiles(files) {
  if (uploading.value) return
  error.value = ''
  for (const file of files) {
    if (!file.name.toLowerCase().endsWith('.zip')) {
      error.value = t('lensAdmin.messages.uploadZipOnly')
      continue
    }
    if (maxBytes.value && file.size > maxBytes.value) {
      error.value = t('lensAdmin.messages.uploadTooLarge', {
        size: maxBytes.value / (1024 * 1024)
      })
      continue
    }
    if (
      entries.value.some(
        (entry) =>
          entry.file.name === file.name &&
          entry.file.size === file.size &&
          entry.file.lastModified === file.lastModified
      )
    )
      continue
    entries.value.push({ id: ++nextId, file, status: 'pending', error: '' })
  }
}

function pickFiles(event) {
  addFiles(Array.from(event.target.files || []))
  event.target.value = ''
}

function dropFiles(event) {
  dragging.value = false
  addFiles(Array.from(event.dataTransfer?.files || []))
}

function close() {
  if (!uploading.value) emit('close')
}

async function save() {
  if (!canSave.value) return
  uploading.value = true
  error.value = ''
  let submitted = false
  try {
    for (const entry of entries.value) {
      if (entry.status === 'submitted') continue
      entry.status = 'uploading'
      entry.error = ''
      try {
        if (entry.file.size > maxBytes.value) {
          throw new Error(
            t('lensAdmin.messages.uploadTooLarge', {
              size: maxBytes.value / (1024 * 1024)
            })
          )
        }
        await uploadDataSourceFile(props.datasource.uuid, entry.file)
        entry.status = 'submitted'
        submitted = true
      } catch (failure) {
        entry.status = 'failed'
        entry.error = extractErrorMessage(
          failure,
          t('lensAdmin.messages.uploadFailed')
        )
      }
    }
  } finally {
    uploading.value = false
    if (submitted)
      emit(
        'uploaded',
        props.datasource,
        entries.value.every((entry) => entry.status === 'submitted')
      )
  }
}

watch(
  () => [props.show, props.datasource?.uuid],
  async ([show]) => {
    const currentRequest = ++requestId
    if (!show) return
    entries.value = []
    error.value = ''
    maxBytes.value = 0
    dragging.value = false
    try {
      const limits = await getDataSourceUploadLimits()
      if (currentRequest === requestId) maxBytes.value = limits.max_bytes
    } catch (failure) {
      if (currentRequest === requestId)
        error.value = extractErrorMessage(
          failure,
          t('lensAdmin.messages.loadFailed')
        )
    }
  }
)
</script>

<style scoped>
.upload-dropzone {
  @apply flex w-full flex-col items-center justify-center gap-2 rounded-lg border border-dashed border-line bg-surface-sunken px-4 py-8 text-center transition-colors hover:border-brand-200 hover:bg-brand-50/40 focus:outline-none focus:ring-2 focus:ring-brand-500/20 disabled:cursor-not-allowed disabled:opacity-50;
}
.upload-dropzone-active {
  @apply border-brand-200 bg-brand-50;
}
</style>
