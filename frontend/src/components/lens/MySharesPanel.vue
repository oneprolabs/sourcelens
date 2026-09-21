<template>
  <div class="min-h-0 flex-1 overflow-y-auto">
    <div class="mx-auto w-full max-w-5xl px-6 py-6">
      <BaseLoading v-if="loading && !shares.length" />

      <div
        v-else-if="!shares.length"
        class="rounded-xl border border-line bg-surface-sunken py-16 text-center"
      >
        <p class="text-sm font-medium text-ink-500">
          {{ t('lens.qa.mineEmpty') }}
        </p>
      </div>

      <div v-else class="space-y-1">
        <button
          v-for="row in shares"
          :key="row.uuid"
          type="button"
          class="group flex w-full items-center gap-3 rounded-xl px-3 py-2.5 text-left transition-colors hover:bg-line-soft"
          @click="openDetail(row)"
        >
          <div class="min-w-0 flex-1">
            <div class="truncate text-sm font-medium text-ink-900">
              {{ row.title }}
            </div>
            <p
              v-if="row.answer"
              class="mt-0.5 line-clamp-2 text-xs text-ink-500"
            >
              {{ preview(row.answer) }}
            </p>
            <div
              class="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-ink-400"
            >
              <span
                class="inline-flex rounded-full px-1.5 py-0.5 font-medium"
                :class="badgeClass(row)"
                :title="statusHint(row)"
              >
                {{ statusLabel(row) }}
              </span>
              <span>{{ row.assistant_name }}</span>
              <span aria-hidden="true">·</span>
              <span>{{
                formatDate(row.published_at, 'yyyy-MM-dd HH:mm')
              }}</span>
              <span aria-hidden="true">·</span>
              <span>{{
                t('lens.qa.viewCount', { count: row.view_count })
              }}</span>
            </div>
          </div>
          <ChevronRight
            :size="18"
            :stroke-width="2"
            class="shrink-0 text-ink-300 transition-colors group-hover:text-ink-500"
            aria-hidden="true"
          />
        </button>
      </div>
    </div>

    <BaseDrawer
      :show="drawerOpen"
      :title="t('lens.qa.detailTitle')"
      :subtitle="current?.title || ''"
      width="2xl"
      @close="closeDrawer"
    >
      <div v-if="current" class="space-y-6 pt-2">
        <section>
          <label
            class="mb-2 block text-xs font-semibold uppercase tracking-wider text-ink-600"
          >
            {{ t('lens.qa.shareTitleLabel') }}
          </label>
          <div class="flex items-center gap-2">
            <input
              v-model="editTitle"
              type="text"
              class="w-full rounded-md border border-line bg-surface px-3 py-2 text-sm text-ink-800 focus:border-primary-500 focus:outline-none"
            />
            <BaseButton
              variant="primary"
              size="sm"
              :disabled="!titleDirty"
              :loading="savingTitle"
              @click="saveTitle"
            >
              {{ t('lens.qa.save') }}
            </BaseButton>
          </div>
        </section>

        <section class="space-y-1.5">
          <div class="flex flex-wrap items-center gap-2 text-xs text-ink-400">
            <span
              class="inline-flex rounded-full px-2 py-0.5 font-medium"
              :class="badgeClass(current)"
            >
              {{ statusLabel(current) }}
            </span>
            <span>
              {{ formatDate(current.published_at, 'yyyy-MM-dd HH:mm') }}
            </span>
            <span aria-hidden="true">·</span>
            <span>
              {{ t('lens.qa.viewCount', { count: current.view_count }) }}
            </span>
          </div>
          <p class="text-xs text-ink-500">{{ statusHint(current) }}</p>
        </section>

        <section>
          <label
            class="mb-2 block text-xs font-semibold uppercase tracking-wider text-ink-600"
          >
            {{ t('lens.qa.linkLabel') }}
          </label>
          <div class="flex items-center gap-2">
            <a
              :href="qaShareUrl(current.token)"
              target="_blank"
              rel="noopener"
              class="min-w-0 flex-1 truncate font-mono text-xs text-ink-500 no-underline transition-colors hover:text-primary-600 hover:underline"
              :title="qaShareUrl(current.token)"
            >
              {{ qaShareUrl(current.token) }}
            </a>
            <button
              type="button"
              class="shrink-0 rounded-md p-1.5 text-ink-400 transition-colors hover:bg-surface-sunken hover:text-primary-600"
              :title="t('lens.share.copyLink')"
              @click="copyLink"
            >
              <Copy :size="15" :stroke-width="2" aria-hidden="true" />
            </button>
          </div>
        </section>

        <section>
          <h3 class="mb-2 text-sm font-semibold text-ink-900">
            {{ t('lens.qa.answer') }}
          </h3>
          <MarkdownRenderer :content="current.answer || ''" />
        </section>
      </div>

      <template #footer>
        <div class="flex items-center justify-between gap-2">
          <BaseButton
            variant="danger"
            size="sm"
            @click="requestRemove(current)"
          >
            {{ t('lens.qa.unshare') }}
          </BaseButton>
          <BaseButton variant="secondary" size="sm" @click="closeDrawer">
            {{ t('lens.qa.done') }}
          </BaseButton>
        </div>
      </template>
    </BaseDrawer>

    <ConfirmDeleteModal
      :show="Boolean(deleteTarget)"
      :title="t('lens.qa.unshare')"
      :name="deleteTarget?.title"
      :message="t('lens.qa.unshareConfirm')"
      :confirm-text="t('lens.qa.unshare')"
      :loading="deleting"
      @confirm="confirmRemove"
      @cancel="deleteTarget = null"
    />
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { ChevronRight, Copy } from '@lucide/vue'

import BaseButton from '@/components/ui/BaseButton.vue'
import BaseLoading from '@/components/ui/BaseLoading.vue'
import BaseDrawer from '@/components/ui/BaseDrawer.vue'
import ConfirmDeleteModal from '@/components/ui/ConfirmDeleteModal.vue'
import MarkdownRenderer from '@/components/ui/MarkdownRenderer.vue'
import { listMyShares, updateMyShare, deleteShare } from '@/api/lens'
import { copyToClipboard } from '@/utils/clipboard'
import { formatDate } from '@/utils/formatting'
import { qaShareUrl } from '@/utils/lens'
import { useToast } from '@/composables/useToast'

const { t } = useI18n()
const { showSuccess, showError } = useToast()

const shares = ref([])
const loading = ref(true)
const drawerOpen = ref(false)
const current = ref(null)
const editTitle = ref('')
const deleteTarget = ref(null)
const deleting = ref(false)
const savingTitle = ref(false)

const titleDirty = computed(
  () =>
    !!current.value && editTitle.value.trim() !== (current.value.title || '')
)

function openDetail(row) {
  current.value = row
  editTitle.value = row.title || ''
  drawerOpen.value = true
}

function closeDrawer() {
  drawerOpen.value = false
}

function preview(text) {
  return (text || '').replace(/\s+/g, ' ').trim()
}

async function load() {
  loading.value = true
  try {
    shares.value = await listMyShares()
  } catch {
    shares.value = []
  } finally {
    loading.value = false
  }
}

function statusLabel(row) {
  if (row.status === 'hidden') {
    return t('lens.qa.statusHidden')
  }
  return row.is_listed ? t('lens.qa.statusListed') : t('lens.qa.statusLinkOnly')
}

function statusHint(row) {
  if (row.status === 'hidden') {
    return t('lens.qa.statusHiddenHint')
  }
  return row.is_listed
    ? t('lens.qa.statusListedHint')
    : t('lens.qa.statusLinkOnlyHint')
}

function badgeClass(row) {
  if (row.status === 'hidden') {
    return 'bg-ink-100 text-ink-500'
  }
  return row.is_listed
    ? 'bg-success/10 text-success'
    : 'bg-primary-50 text-primary-600'
}

async function saveTitle() {
  if (!current.value || !titleDirty.value) {
    return
  }
  savingTitle.value = true
  try {
    const updated = await updateMyShare(current.value.uuid, {
      title: editTitle.value.trim()
    })
    current.value.title = updated.title
    editTitle.value = updated.title || ''
    showSuccess(t('lens.qa.actionDone'))
  } catch {
    showError(t('lens.qa.shareFailed'))
  } finally {
    savingTitle.value = false
  }
}

async function copyLink() {
  if (!current.value) {
    return
  }
  if (await copyToClipboard(qaShareUrl(current.value.token))) {
    showSuccess(t('lens.qa.copied'))
  } else {
    showError(t('lens.qa.copyFailed'))
  }
}

function requestRemove(row) {
  if (!row) return
  deleteTarget.value = row
}

async function confirmRemove() {
  const row = deleteTarget.value
  if (!row) return
  deleting.value = true
  try {
    await deleteShare(row.uuid)
    shares.value = shares.value.filter((item) => item.uuid !== row.uuid)
    showSuccess(t('lens.qa.unshared'))
    deleteTarget.value = null
    closeDrawer()
  } catch {
    showError(t('lens.qa.shareFailed'))
  } finally {
    deleting.value = false
  }
}

onMounted(load)
</script>
