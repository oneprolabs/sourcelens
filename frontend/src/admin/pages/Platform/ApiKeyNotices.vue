<template>
  <section class="space-y-3 rounded-lg border border-line bg-surface p-4">
    <div class="flex items-center justify-between gap-3">
      <h2 class="text-sm font-semibold text-ink-900">
        {{ t(`${prefix}.title`) }}
      </h2>
      <BaseButton
        size="sm"
        variant="outline"
        :loading="loading"
        :disabled="!!busy"
        @click="refresh"
      >
        {{ t(`${prefix}.refresh`) }}
      </BaseButton>
    </div>
    <p v-if="error" class="text-sm text-danger-700" role="alert">{{ error }}</p>
    <p
      v-if="!notices.length && !loading && !error"
      class="text-sm text-ink-500"
    >
      {{ t(`${prefix}.empty`) }}
    </p>
    <ul
      v-if="notices.length"
      class="max-h-64 divide-y divide-line overflow-auto"
      aria-live="polite"
    >
      <li
        v-for="notice in notices"
        :key="notice.uuid"
        class="flex flex-wrap items-center justify-between gap-3 py-2 text-sm"
      >
        <div class="min-w-0">
          <p class="font-medium text-ink-800">
            {{ notice.tenant_name || notice.tenant_uuid }}
          </p>
          <p class="text-xs text-ink-500">
            {{ t(`management.apiKeyBulk.${notice.action || 'disable'}`) }}
            <span v-if="notice.created_at">
              · {{ new Date(notice.created_at).toLocaleString() }}</span
            >
          </p>
          <p v-if="notice.error_code" class="mt-1 text-xs text-danger-700">
            {{ t(`${prefix}.${notice.error_code}`) }}
          </p>
        </div>
        <div class="flex items-center gap-3">
          <span
            :class="
              notice.status === 'failed' ? 'text-danger-700' : 'text-ink-600'
            "
            >{{ t(`${prefix}.${notice.status}`) }}</span
          >
          <BaseButton
            v-if="notice.status === 'failed'"
            size="sm"
            variant="outline"
            :loading="busy === notice.uuid"
            :disabled="!!busy || loading"
            @click="retry(notice)"
          >
            {{ t(`${prefix}.retry`) }}
          </BaseButton>
        </div>
      </li>
    </ul>
    <PaginationBar
      v-if="!loading && !error"
      :current-page="currentPage"
      :page-size="pageSize"
      :total="totalCount"
      @prev="changePage(-1)"
      @next="changePage(1)"
      @page-size-change="changePageSize"
    />
  </section>
</template>

<script setup>
import { onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import {
  getPlatformApiKeyNotices,
  retryPlatformApiKeyNotice
} from '@/api/platform'
import BaseButton from '@/components/ui/BaseButton.vue'
import PaginationBar from '@/components/ui/PaginationBar.vue'

const props = defineProps({ notices: { type: Array, default: () => [] } })
const emit = defineEmits(['updated'])
const { t } = useI18n()
const prefix = 'management.apiKeyNotices'
const loading = ref(false)
const busy = ref('')
const error = ref('')
const currentPage = ref(1)
const pageSize = ref(20)
const totalCount = ref(0)
let requestVersion = 0

watch(
  () => props.notices,
  () => {
    requestVersion++
    loading.value = false
  }
)

async function refresh() {
  if (loading.value || busy.value) return
  const version = ++requestVersion
  loading.value = true
  error.value = ''
  try {
    const data = await getPlatformApiKeyNotices({
      page: currentPage.value,
      page_size: pageSize.value
    })
    if (version === requestVersion) {
      totalCount.value = data?.count || 0
      emit('updated', data?.results || [])
    }
  } catch {
    if (version === requestVersion) error.value = t(`${prefix}.loadFailed`)
  } finally {
    if (version === requestVersion) loading.value = false
  }
}

async function retry(notice) {
  if (busy.value || loading.value || notice.status !== 'failed') return
  busy.value = notice.uuid
  error.value = ''
  try {
    const updated = await retryPlatformApiKeyNotice(notice.uuid)
    emit(
      'updated',
      props.notices.map((item) =>
        item.uuid === notice.uuid ? { ...item, ...updated } : item
      )
    )
  } catch {
    error.value = t(`${prefix}.retryFailed`)
  } finally {
    busy.value = ''
  }
}

function changePage(delta) {
  if (loading.value || busy.value) return
  const page = currentPage.value + delta
  if (page < 1 || page > Math.ceil(totalCount.value / pageSize.value)) return
  currentPage.value = page
  return refresh()
}

function changePageSize(size) {
  if (loading.value || busy.value) return
  pageSize.value = Number(size)
  currentPage.value = 1
  return refresh()
}

onMounted(refresh)
</script>
