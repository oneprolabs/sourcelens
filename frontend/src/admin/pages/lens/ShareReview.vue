<template>
  <AdminLayout>
    <div class="w-full max-w-full p-6">
      <div class="mb-4">
        <h1 class="text-lg font-semibold text-gray-900">
          {{ t('lens.qa.adminTitle') }}
        </h1>
        <p class="mt-1 text-sm text-gray-500">
          {{ t('lens.qa.adminSubtitle') }}
        </p>
      </div>

      <div
        class="overflow-hidden rounded-lg border border-gray-200 bg-white shadow-sm"
      >
        <div
          class="flex flex-wrap items-center justify-between gap-3 border-b border-gray-200 px-6"
        >
          <div class="flex items-center gap-6">
            <button
              v-for="tab in tabs"
              :key="tab.key"
              type="button"
              class="qa-tab"
              :class="activeTab === tab.key ? 'qa-tab-active' : ''"
              @click="setTab(tab.key)"
            >
              {{ tab.label }}
            </button>
          </div>
          <BaseButton
            variant="outline"
            size="sm"
            :loading="loading"
            @click="load"
          >
            {{ t('common.refresh') }}
          </BaseButton>
        </div>

        <div class="p-6">
          <BaseLoading v-if="loading && !rows.length" />

          <div
            v-else-if="!rows.length"
            class="rounded-lg border border-gray-200 bg-gray-50 py-16 text-center"
          >
            <p class="text-sm font-medium text-gray-600">
              {{ t('common.noData') }}
            </p>
          </div>

          <div
            v-else
            class="share-review-table-wrap overflow-x-auto rounded-lg border border-line bg-surface"
          >
            <table
              class="min-w-[56rem] w-full table-fixed divide-y divide-line md:min-w-0"
            >
              <colgroup>
                <col style="width: 21%" />
                <col style="width: 18%" />
                <col style="width: 17%" />
                <col style="width: 10.5rem" />
                <col style="width: 4rem" />
                <col style="width: 20rem" />
              </colgroup>
              <thead class="bg-surface-sunken">
                <tr>
                  <th class="table-head">
                    {{ t('lens.qa.shareTitleLabel') }}
                  </th>
                  <th class="table-head">{{ t('lens.qa.assistant') }}</th>
                  <th class="table-head">{{ t('lens.qa.publishedBy') }}</th>
                  <th class="table-head">{{ t('lens.qa.publishedAt') }}</th>
                  <th class="table-head">{{ t('lens.qa.views') }}</th>
                  <th class="table-head text-right">
                    {{ t('common.actions') }}
                  </th>
                </tr>
              </thead>
              <tbody class="divide-y divide-gray-200 bg-white">
                <tr
                  v-for="row in rows"
                  :key="row.uuid"
                  class="cursor-pointer transition-colors hover:bg-gray-50"
                  @click="openDetail(row)"
                >
                  <td class="table-cell share-review-title-cell">
                    <div
                      class="share-review-truncate font-medium text-gray-800"
                      :title="row.title"
                    >
                      {{ row.title }}
                    </div>
                    <div
                      class="share-review-truncate text-xs text-gray-400"
                      :title="row.answer_snippet"
                    >
                      {{ row.answer_snippet }}
                    </div>
                  </td>
                  <td
                    class="table-cell share-review-assistant-cell text-gray-600"
                  >
                    <div class="flex min-w-0 items-center gap-2">
                      <span
                        class="inline-flex shrink-0 items-center gap-1 rounded-full border px-2 py-0.5 text-xs font-semibold"
                        :class="
                          row.assistant_visibility === 'private'
                            ? 'border-amber-300 bg-amber-100 text-amber-800'
                            : 'border-emerald-300 bg-emerald-100 text-emerald-800'
                        "
                        :title="
                          t(
                            `lensAdmin.visibility.${row.assistant_visibility === 'private' ? 'private' : 'public'}Desc`
                          )
                        "
                      >
                        <component
                          :is="
                            row.assistant_visibility === 'private'
                              ? LockIcon
                              : GlobeIcon
                          "
                          class="h-3.5 w-3.5"
                        />
                        {{
                          t(
                            `lensAdmin.visibility.${row.assistant_visibility === 'private' ? 'private' : 'public'}`
                          )
                        }}
                      </span>
                      <span
                        class="share-review-truncate min-w-0"
                        :title="row.assistant_name"
                      >
                        {{ row.assistant_name }}
                      </span>
                    </div>
                  </td>
                  <td
                    class="table-cell share-review-publisher-cell text-gray-600"
                  >
                    <span
                      class="share-review-truncate"
                      :title="row.published_by"
                    >
                      {{ row.published_by }}
                    </span>
                  </td>
                  <td class="table-cell whitespace-nowrap text-gray-500">
                    {{ formatDate(row.published_at, 'yyyy-MM-dd HH:mm') }}
                  </td>
                  <td class="table-cell text-gray-500">{{ row.view_count }}</td>
                  <td class="table-cell share-review-actions-cell" @click.stop>
                    <div
                      class="flex flex-nowrap items-center justify-end gap-2"
                    >
                      <button
                        type="button"
                        class="text-xs text-gray-500 hover:text-primary-600"
                        @click="openDetail(row)"
                      >
                        {{ t('lens.qa.viewDetail') }}
                      </button>
                      <a
                        :href="qaShareUrl(row.token)"
                        target="_blank"
                        rel="noopener"
                        class="text-xs text-gray-500 no-underline hover:text-primary-600"
                      >
                        {{ t('lens.qa.preview') }}
                      </a>
                      <BaseButton
                        v-if="!row.is_listed && row.status === 'published'"
                        variant="primary"
                        size="sm"
                        @click="apply(row, { is_listed: true })"
                      >
                        {{ t('lens.qa.approve') }}
                      </BaseButton>
                      <BaseButton
                        v-if="row.is_listed && row.status === 'published'"
                        variant="outline"
                        size="sm"
                        @click="apply(row, { is_listed: false })"
                      >
                        {{ t('lens.qa.unlist') }}
                      </BaseButton>
                      <BaseButton
                        v-if="row.status === 'published'"
                        variant="danger"
                        size="sm"
                        @click="apply(row, { status: 'hidden' })"
                      >
                        {{ t('lens.qa.hide') }}
                      </BaseButton>
                      <BaseButton
                        v-if="row.status === 'hidden'"
                        variant="outline"
                        size="sm"
                        @click="apply(row, { status: 'published' })"
                      >
                        {{ t('lens.qa.restore') }}
                      </BaseButton>
                    </div>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
          <PaginationBar
            v-if="!loading"
            v-model:page-size="pageSize"
            :current-page="currentPage"
            :total="totalCount"
            @page-size-change="handlePageSizeChange"
            @prev="goPrevPage"
            @next="goNextPage"
          />
        </div>
      </div>

      <BaseDrawer
        :show="drawerOpen"
        :title="t('lens.qa.detailTitle')"
        :subtitle="detail?.title || ''"
        width="2xl"
        @close="closeDetail"
      >
        <BaseLoading v-if="detailLoading" />
        <div v-else-if="detail" class="space-y-6 pt-1">
          <dl class="grid grid-cols-1 gap-x-4 gap-y-3 text-sm sm:grid-cols-2">
            <div>
              <dt class="text-gray-500">{{ t('lens.qa.assistant') }}</dt>
              <dd class="mt-0.5 text-gray-900">
                {{ detail.assistant_name || '-' }}
              </dd>
            </div>
            <div>
              <dt class="text-gray-500">{{ t('lens.qa.publishedBy') }}</dt>
              <dd class="mt-0.5 text-gray-900">
                {{ detail.published_by || '-' }}
              </dd>
            </div>
            <div>
              <dt class="text-gray-500">{{ t('lens.qa.publishedAt') }}</dt>
              <dd class="mt-0.5 text-gray-900">
                {{ formatDate(detail.published_at, 'yyyy-MM-dd HH:mm') }}
              </dd>
            </div>
            <div>
              <dt class="text-gray-500">{{ t('lens.qa.views') }}</dt>
              <dd class="mt-0.5 text-gray-900">{{ detail.view_count }}</dd>
            </div>
          </dl>

          <section>
            <h3 class="mb-2 text-sm font-semibold text-gray-900">
              {{ t('lens.qa.question') }}
            </h3>
            <div
              class="whitespace-pre-wrap rounded-lg bg-gray-50 p-4 text-sm text-gray-800"
            >
              {{ detail.question || '-' }}
            </div>
          </section>

          <section>
            <h3 class="mb-2 text-sm font-semibold text-gray-900">
              {{ t('lens.qa.answer') }}
            </h3>
            <MarkdownRenderer :content="detail.answer || ''" />
          </section>
        </div>

        <template v-if="detail" #footer>
          <div class="flex flex-wrap items-center justify-end gap-2">
            <BaseButton
              v-if="!detail.is_listed && detail.status === 'published'"
              variant="primary"
              size="sm"
              :loading="actionLoading"
              @click="apply(detail, { is_listed: true })"
            >
              {{ t('lens.qa.approve') }}
            </BaseButton>
            <BaseButton
              v-if="detail.is_listed && detail.status === 'published'"
              variant="outline"
              size="sm"
              :loading="actionLoading"
              @click="apply(detail, { is_listed: false })"
            >
              {{ t('lens.qa.unlist') }}
            </BaseButton>
            <BaseButton
              v-if="detail.status === 'published'"
              variant="danger"
              size="sm"
              :loading="actionLoading"
              @click="apply(detail, { status: 'hidden' })"
            >
              {{ t('lens.qa.hide') }}
            </BaseButton>
            <BaseButton
              v-if="detail.status === 'hidden'"
              variant="outline"
              size="sm"
              :loading="actionLoading"
              @click="apply(detail, { status: 'published' })"
            >
              {{ t('lens.qa.restore') }}
            </BaseButton>
            <BaseButton variant="secondary" size="sm" @click="closeDetail">
              {{ t('lens.qa.done') }}
            </BaseButton>
          </div>
        </template>
      </BaseDrawer>
    </div>
  </AdminLayout>
</template>

<script setup>
import { Globe as GlobeIcon, Lock as LockIcon } from '@lucide/vue'
import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'

import AdminLayout from '@/admin/layout/AdminLayout.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseDrawer from '@/components/ui/BaseDrawer.vue'
import BaseLoading from '@/components/ui/BaseLoading.vue'
import MarkdownRenderer from '@/components/ui/MarkdownRenderer.vue'
import PaginationBar from '@/components/ui/PaginationBar.vue'
import { getAdminShare, listAdminShares, updateAdminShare } from '@/api/lens'
import { formatDate } from '@/utils/formatting'
import { qaShareUrl } from '@/utils/lens'
import { useToast } from '@/composables/useToast'

const { t } = useI18n()
const { showSuccess, showError } = useToast()

const rows = ref([])
const totalCount = ref(0)
const loading = ref(true)
const activeTab = ref('pending')
const currentPage = ref(1)
const pageSize = ref(20)
const drawerOpen = ref(false)
const selectedUuid = ref(null)
const detail = ref(null)
const detailLoading = ref(false)
const actionLoading = ref(false)

const tabs = computed(() => [
  { key: 'pending', label: t('lens.qa.tabPending') },
  { key: 'listed', label: t('lens.qa.tabListed') },
  { key: 'hidden', label: t('lens.qa.tabHidden') }
])

const totalPages = computed(() =>
  Math.max(1, Math.ceil(totalCount.value / pageSize.value))
)

function handlePageSizeChange() {
  currentPage.value = 1
  load()
}

function goPrevPage() {
  if (currentPage.value <= 1) return
  currentPage.value -= 1
  load()
}

function goNextPage() {
  if (currentPage.value >= totalPages.value) return
  currentPage.value += 1
  load()
}

const TAB_PARAMS = {
  pending: { listed: 'false', status: 'published' },
  listed: { listed: 'true', status: 'published' },
  hidden: { status: 'hidden' }
}

function extractRows(data) {
  if (Array.isArray(data)) {
    return data
  }
  return data?.results || []
}

async function load() {
  loading.value = true
  try {
    const data = await listAdminShares({
      ...TAB_PARAMS[activeTab.value],
      page: currentPage.value,
      page_size: pageSize.value
    })
    rows.value = extractRows(data)
    totalCount.value = Array.isArray(data)
      ? data.length
      : Number(data?.count ?? rows.value.length)
  } catch {
    rows.value = []
    totalCount.value = 0
  } finally {
    loading.value = false
  }
}

function setTab(key) {
  activeTab.value = key
  currentPage.value = 1
  load()
}

async function openDetail(row) {
  selectedUuid.value = row.uuid
  drawerOpen.value = true
  detail.value = null
  await loadDetail()
}

function closeDetail() {
  drawerOpen.value = false
  selectedUuid.value = null
  detail.value = null
}

async function loadDetail() {
  if (!selectedUuid.value) return
  detailLoading.value = true
  try {
    detail.value = await getAdminShare(selectedUuid.value)
  } catch {
    detail.value = null
    showError(t('lens.qa.detailFailed'))
  } finally {
    detailLoading.value = false
  }
}

async function apply(row, payload) {
  actionLoading.value = true
  try {
    await updateAdminShare(row.uuid, payload)
    showSuccess(t('lens.qa.actionDone'))
    await load()
    if (drawerOpen.value && selectedUuid.value === row.uuid) {
      await loadDetail()
    }
  } catch {
    showError(t('lens.qa.shareFailed'))
  } finally {
    actionLoading.value = false
  }
}

onMounted(load)
</script>

<style scoped>
.table-head {
  @apply border-b border-line px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-ink-500;
}

.table-cell {
  @apply px-4 py-3 align-top text-sm text-ink-700;
}

.qa-tab {
  @apply border-b-2 border-transparent py-3 text-sm font-medium text-gray-500 transition-colors;
}

.qa-tab:hover {
  @apply text-gray-700;
}

.qa-tab-active {
  @apply border-primary-500 text-primary-600;
}

.share-review-title-cell,
.share-review-assistant-cell,
.share-review-publisher-cell {
  max-width: 0;
  overflow: hidden;
}

.share-review-truncate {
  display: block;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.share-review-actions-cell {
  overflow: visible;
  white-space: nowrap;
}
</style>
