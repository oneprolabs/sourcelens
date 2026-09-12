<template>
  <AdminLayout>
    <div class="flex max-w-full flex-col gap-4 py-4">
      <section class="admin-data-panel overflow-hidden">
        <div
          class="flex flex-col gap-4 border-b border-line px-5 py-4 lg:flex-row lg:items-start lg:justify-between"
        >
          <div class="min-w-0">
            <div class="flex flex-wrap items-center gap-2">
              <h1 class="admin-page-title">
                {{ t('lensAdmin.pages.lensnodes.title') }}
              </h1>
            </div>
          </div>
          <div class="flex flex-wrap items-center gap-2">
            <BaseButton
              variant="outline"
              size="sm"
              :loading="loading"
              @click="load"
            >
              {{ t('common.refresh') }}
            </BaseButton>
            <BaseButton variant="primary" size="sm" @click="startCreate">
              {{ t('lensAdmin.pages.lensnodes.action') }}
            </BaseButton>
          </div>
        </div>

        <section
          data-testid="lensnode-fleet-summary"
          class="border-b border-line bg-surface-sunken/50 px-5 py-4"
        >
          <div class="mb-3 flex flex-wrap items-end justify-between gap-2">
            <div>
              <h2 class="text-sm font-semibold text-ink-900">
                {{ t('lensAdmin.fleet.overviewTitle') }}
              </h2>
              <p class="mt-1 text-xs text-ink-500">
                {{ t('lensAdmin.fleet.overviewDescription') }}
              </p>
            </div>
            <div class="text-right">
              <div class="admin-metric-value">
                {{ formatOperationMetric(totalFleetNodes) }}
              </div>
              <div class="text-xs text-ink-500">
                {{ t('lensAdmin.fleet.totalNodes') }}
              </div>
            </div>
          </div>
          <div class="grid gap-3 lg:grid-cols-[1.1fr_1fr]">
            <div class="rounded-lg border border-line bg-surface px-4 py-3">
              <div class="flex items-center justify-between gap-3">
                <span class="text-xs font-medium text-ink-500">
                  {{ t('lensAdmin.fleet.healthOverview') }}
                </span>
                <span class="text-sm font-semibold tabular-nums text-ink-800">
                  {{ onlineRate }}
                </span>
              </div>
              <div class="mt-3 h-2 overflow-hidden rounded-full bg-line-soft">
                <div
                  class="h-full rounded-full bg-success-500 transition-all"
                  :style="{ width: onlineRateValue + '%' }"
                />
              </div>
              <div class="mt-3 grid grid-cols-3 gap-3">
                <div v-for="item in healthSummary" :key="item.label">
                  <div class="flex items-center gap-1.5 text-xs text-ink-500">
                    <span
                      class="h-1.5 w-1.5 rounded-full"
                      :class="item.dotClass"
                    />
                    {{ item.label }}
                  </div>
                  <div class="admin-metric-value mt-1">
                    {{ formatOperationMetric(item.value) }}
                  </div>
                </div>
              </div>
            </div>
            <div class="rounded-lg border border-line bg-surface px-4 py-3">
              <div class="text-xs font-medium text-ink-500">
                {{ t('lensAdmin.fleet.workloadOverview') }}
              </div>
              <div class="mt-3 grid grid-cols-3 gap-3">
                <div v-for="item in workloadSummary" :key="item.label">
                  <div class="text-xs text-ink-500">{{ item.label }}</div>
                  <div class="admin-metric-value mt-1">
                    {{ formatOperationMetric(item.value) }}
                  </div>
                  <div class="mt-1 text-[11px] text-ink-400">
                    {{ item.hint }}
                  </div>
                </div>
              </div>
            </div>
          </div>
        </section>

        <div class="px-5 py-4">
          <BaseLoading v-if="loading && lensnodes.length === 0" />

          <div
            v-else-if="lensnodes.length === 0"
            class="rounded-lg border border-line bg-surface-sunken py-16 text-center"
          >
            <p class="text-sm font-medium text-ink-500">
              {{ t('common.noData') }}
            </p>
          </div>

          <div
            v-else
            class="relative overflow-x-auto rounded-lg border border-line bg-surface"
          >
            <table class="min-w-full divide-y divide-line">
              <thead class="bg-surface-sunken">
                <tr>
                  <th
                    v-for="column in columns"
                    :key="column"
                    class="table-head"
                  >
                    {{ column }}
                  </th>
                </tr>
              </thead>
              <tbody class="divide-y divide-line bg-surface">
                <tr
                  v-for="row in lensnodes"
                  :key="row.uuid"
                  class="transition-colors hover:bg-line-soft"
                >
                  <td class="table-cell">
                    <button
                      type="button"
                      class="group text-left"
                      :title="t('lensAdmin.detail.title')"
                      @click="openDetail(row)"
                    >
                      <div
                        class="font-medium text-ink-900 group-hover:text-primary-600 group-hover:underline"
                      >
                        {{ row.name }}
                      </div>
                    </button>
                  </td>
                  <td class="table-cell">
                    <button
                      type="button"
                      class="text-left transition-colors hover:text-primary-600"
                      @click="openRuns(row)"
                    >
                      <div class="font-medium tabular-nums text-ink-800">
                        {{ formatOperationMetric(row.active_run_count) }} /
                        {{ formatOperationMetric(row.queued_run_count) }}
                      </div>
                      <div
                        v-if="hasOperationMetric(row.awaiting_resume_count)"
                        class="mt-1 text-xs text-ink-500"
                      >
                        {{
                          t('lensAdmin.fleet.awaitingResumeCount', {
                            count: row.awaiting_resume_count
                          })
                        }}
                      </div>
                      <div v-else class="mt-1 text-xs text-ink-400">
                        {{ t('lensAdmin.fleet.workloadUnavailable') }}
                      </div>
                    </button>
                  </td>
                  <td class="table-cell text-ink-600">
                    <div class="whitespace-nowrap">
                      <span class="text-xs text-ink-400">
                        {{ t('lensAdmin.table.runtimeVersion') }}
                      </span>
                      <span class="ml-1 font-mono text-xs">
                        {{ row.agent_version || EMPTY_VALUE }}
                      </span>
                    </div>
                    <div class="mt-1 whitespace-nowrap text-xs text-ink-400">
                      <span>{{ t('lensAdmin.table.protocolVersion') }}</span>
                      <span class="ml-1 font-mono">
                        {{ row.protocol_version || EMPTY_VALUE }}
                      </span>
                    </div>
                  </td>
                  <td class="table-cell whitespace-nowrap text-ink-600">
                    <time
                      :datetime="row.last_heartbeat_at || undefined"
                      :title="
                        row.last_heartbeat_at ||
                        t('lensAdmin.table.notRecorded')
                      "
                    >
                      {{ formatDateTime(row.last_heartbeat_at) }}
                    </time>
                  </td>
                  <td class="table-cell whitespace-nowrap">
                    <div class="flex flex-nowrap items-center gap-2">
                      <BaseButton
                        size="sm"
                        variant="outline"
                        @click="startEdit(row)"
                      >
                        {{ t('common.edit') }}
                      </BaseButton>
                      <BaseButton
                        size="sm"
                        variant="danger"
                        @click="deleteTarget = row"
                      >
                        {{ t('common.delete') }}
                      </BaseButton>
                      <button
                        type="button"
                        class="rounded-md p-1.5 text-ink-400 transition-colors hover:bg-surface-sunken hover:text-ink-700"
                        :title="t('common.more')"
                        @click="openMenu(row, $event)"
                      >
                        <MoreVertical :size="16" :stroke-width="2" />
                      </button>
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
            :total="totalLensNodes"
            @page-size-change="handlePageSizeChange"
            @prev="goPrevPage"
            @next="goNextPage"
          />
        </div>
      </section>

      <!-- LensNode create / edit (name + onboarding compose) -->
      <LensNodeFormDrawer
        :show="showLensNodeDrawer"
        :mode="mode"
        :node="editingLensNode"
        :settings="globalSettings"
        @close="closeLensNodeDrawer"
        @done="load"
      />

      <!-- LensNode Detail Drawer (info + lazy directory tree) -->
      <LensNodeDetailDrawer
        :show="showDetailDrawer"
        :node="detailNode"
        @close="closeDetail"
      />

      <!-- Re-issue credential drawer (shows fresh compose) -->
      <BaseDrawer
        :show="showReissueDrawer"
        :title="t('lensAdmin.detail.reissue')"
        :subtitle="reissueNode?.name || ''"
        @close="closeReissue"
      >
        <LensNodeComposePanel
          v-if="reissueNode"
          :compose-text="reissueComposeText"
          :missing-labels="reissueMissingLabels"
        />
      </BaseDrawer>

      <!-- Row action menu, teleported so the table overflow can't clip it -->
      <Teleport to="body">
        <template v-if="openMenuRow">
          <div class="fixed inset-0 z-40" @click="closeMenu" />
          <div
            class="fixed z-50 w-36 rounded-md border border-line bg-surface py-1 shadow-lg"
            :style="menuStyle"
          >
            <button
              type="button"
              class="block w-full px-3 py-1.5 text-left text-sm text-ink-700 transition-colors hover:bg-surface-sunken"
              @click="reissue(openMenuRow)"
            >
              {{ t('lensAdmin.detail.reissue') }}
            </button>
            <button
              type="button"
              class="block w-full px-3 py-1.5 text-left text-sm text-danger-600 transition-colors hover:bg-danger-50"
              @click="revokeCredential(openMenuRow)"
            >
              {{ t('lensAdmin.actions.revokeToken') }}
            </button>
          </div>
        </template>
      </Teleport>

      <!-- Delete node confirmation -->
      <BaseModal
        :show="!!deleteTarget"
        :title="t('lensAdmin.deleteConfirm.title')"
        @close="deleteTarget = null"
      >
        <p class="text-sm text-ink-600">
          {{
            t('lensAdmin.deleteConfirm.message', {
              name: deleteTarget?.name
            })
          }}
        </p>
        <template #footer>
          <div class="flex flex-row-reverse gap-2">
            <BaseButton variant="danger" @click="confirmDeleteNode">
              {{ t('common.delete') }}
            </BaseButton>
            <BaseButton variant="outline" @click="deleteTarget = null">
              {{ t('common.cancel') }}
            </BaseButton>
          </div>
        </template>
      </BaseModal>
    </div>
  </AdminLayout>
</template>

<script setup>
import { MoreVertical } from '@lucide/vue'
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRouter } from 'vue-router'

import {
  formatOperationMetric,
  hasOperationMetric,
  resolveFleetSummary
} from '@/admin/utils/operationsSummary'
import { extractErrorMessage } from '@/utils/api'
import AdminLayout from '@/admin/layout/AdminLayout.vue'
import {
  approveLensNode,
  deleteLensNode,
  issueLensNodeToken,
  listGlobalSettings,
  listLensNodePage,
  rejectLensNode,
  revokeLensNodeToken
} from '@/api/lens'
import { useToast } from '@/composables/useToast'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseDrawer from '@/components/ui/BaseDrawer.vue'
import BaseLoading from '@/components/ui/BaseLoading.vue'
import BaseModal from '@/components/ui/BaseModal.vue'
import PaginationBar from '@/components/ui/PaginationBar.vue'
import LensNodeComposePanel from './LensNodeComposePanel.vue'
import LensNodeDetailDrawer from './LensNodeDetailDrawer.vue'
import LensNodeFormDrawer from './LensNodeFormDrawer.vue'
import {
  EMPTY_VALUE,
  buildLensNodeCompose,
  lensNodeComposeSettings,
  normalizeList
} from './adminHelpers'
import { useShortDateTime } from './useShortDateTime'

const { t } = useI18n()
const router = useRouter()
const { showSuccess, showError } = useToast()
const formatDateTime = useShortDateTime()

const lensnodes = ref([])
const totalLensNodes = ref(0)
const fleetSummaryData = ref({})
const currentPage = ref(1)
const pageSize = ref(20)
const globalSettings = ref([])
const loading = ref(false)
const mode = ref('create')
const showLensNodeDrawer = ref(false)
const editingLensNode = ref(null)
const showDetailDrawer = ref(false)
const detailNode = ref(null)
const openMenuRow = ref(null)
const menuStyle = ref({})
const deleteTarget = ref(null)
const showReissueDrawer = ref(false)
const reissueNode = ref(null)
const reissueToken = ref('')

const columns = computed(() =>
  ['lensnode', 'workload', 'version', 'heartbeat', 'actions'].map((column) =>
    t(`lensAdmin.columns.${column}`)
  )
)

const healthSummary = computed(() => [
  {
    label: t('lensAdmin.fleet.online'),
    value: fleetSummaryData.value.online,
    dotClass: 'bg-success-500'
  },
  {
    label: t('lensAdmin.fleet.offline'),
    value: fleetSummaryData.value.offline,
    dotClass: 'bg-ink-400'
  },
  {
    label: t('lensAdmin.fleet.draining'),
    value: fleetSummaryData.value.draining,
    dotClass: 'bg-warning-500'
  }
])

const workloadSummary = computed(() => [
  {
    label: t('lensAdmin.fleet.activeRuns'),
    value: fleetSummaryData.value.active_runs,
    hint: t('lensAdmin.fleet.activeRunsHint')
  },
  {
    label: t('lensAdmin.fleet.queuedRuns'),
    value: fleetSummaryData.value.queued_runs,
    hint: t('lensAdmin.fleet.queuedRunsHint')
  },
  {
    label: t('lensAdmin.fleet.awaitingResume'),
    value: fleetSummaryData.value.awaiting_resume,
    hint: t('lensAdmin.fleet.awaitingResumeHint')
  }
])

const totalFleetNodes = computed(() => {
  const values = healthSummary.value.map((item) => item.value)
  return values.every((value) => hasOperationMetric(value))
    ? values.reduce((total, value) => total + Number(value), 0)
    : totalLensNodes.value || null
})

const onlineRateValue = computed(() => {
  if (!hasOperationMetric(totalFleetNodes.value)) return 0
  return Math.round(
    (Number(fleetSummaryData.value.online || 0) /
      Number(totalFleetNodes.value)) *
      100
  )
})

const onlineRate = computed(() =>
  hasOperationMetric(totalFleetNodes.value)
    ? `${onlineRateValue.value}% ${t('lensAdmin.fleet.onlineRate')}`
    : EMPTY_VALUE
)

const totalPages = computed(() =>
  Math.max(1, Math.ceil(totalLensNodes.value / pageSize.value))
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

const composeConfig = computed(() =>
  lensNodeComposeSettings(globalSettings.value)
)

const reissueComposeText = computed(() =>
  buildLensNodeCompose({
    name: reissueNode.value?.name,
    token: reissueToken.value,
    ...composeConfig.value
  })
)

const reissueMissingLabels = computed(() =>
  composeConfig.value.serverUrl
    ? []
    : [t('lensAdmin.settings.publicBaseUrlTitle')]
)

async function load() {
  loading.value = true
  try {
    const [lensnodeRows, settingRows] = await Promise.all([
      listLensNodePage({
        page: currentPage.value,
        page_size: pageSize.value
      }),
      listGlobalSettings()
    ])
    lensnodes.value = normalizeList(lensnodeRows)
    const total = lensnodeRows?.count ?? lensnodes.value.length
    totalLensNodes.value = Number(total)
    fleetSummaryData.value = resolveFleetSummary(lensnodeRows)
    globalSettings.value = normalizeList(settingRows)
  } catch (error) {
    showError(extractErrorMessage(error, t('lensAdmin.messages.loadFailed')))
  } finally {
    loading.value = false
  }
}

function startCreate() {
  mode.value = 'create'
  editingLensNode.value = null
  showLensNodeDrawer.value = true
}

function startEdit(row) {
  mode.value = 'edit'
  editingLensNode.value = row
  showLensNodeDrawer.value = true
}

function closeLensNodeDrawer() {
  showLensNodeDrawer.value = false
  editingLensNode.value = null
}

function openDetail(row) {
  detailNode.value = row
  showDetailDrawer.value = true
}

function openRuns(row) {
  router.push({
    name: 'LensRunObservation',
    query: { lensnode: row.uuid }
  })
}

function closeDetail() {
  showDetailDrawer.value = false
  detailNode.value = null
}

function closeMenu() {
  openMenuRow.value = null
  window.removeEventListener('scroll', closeMenu, true)
  window.removeEventListener('resize', closeMenu)
}

function openMenu(row, event) {
  if (openMenuRow.value?.uuid === row.uuid) {
    closeMenu()
    return
  }
  const rect = event.currentTarget.getBoundingClientRect()
  menuStyle.value = {
    top: `${rect.bottom + 4}px`,
    left: `${rect.right - 144}px`
  }
  openMenuRow.value = row
  // Close on scroll/resize so the fixed-position menu can't detach from the
  // trigger (the table scrolls horizontally under it).
  window.addEventListener('scroll', closeMenu, true)
  window.addEventListener('resize', closeMenu)
}

function confirmDeleteNode() {
  const row = deleteTarget.value
  deleteTarget.value = null
  if (row) {
    remove(row)
  }
}

function closeReissue() {
  showReissueDrawer.value = false
  reissueNode.value = null
  reissueToken.value = ''
}

async function reissue(row) {
  closeMenu()
  if (row.enrollment_status !== 'approved') {
    showError(t('lensAdmin.messages.approveBeforeToken'))
    return
  }
  try {
    const result = await issueLensNodeToken(row.uuid)
    reissueToken.value = result?.token || result?.auth_token || ''
    reissueNode.value = row
    showReissueDrawer.value = true
    await load()
  } catch (error) {
    showError(
      extractErrorMessage(error, t('lensAdmin.messages.tokenIssueFailed'))
    )
  }
}

async function revokeCredential(row) {
  closeMenu()
  try {
    await revokeLensNodeToken(row.uuid)
    showSuccess(t('lensAdmin.messages.tokenRevoked'))
    await load()
  } catch (error) {
    showError(
      extractErrorMessage(error, t('lensAdmin.messages.tokenRevokeFailed'))
    )
  }
}

async function remove(row) {
  try {
    await deleteLensNode(row.uuid)
    showSuccess(t('lensAdmin.messages.deleteSuccess'))
    await load()
  } catch (error) {
    showError(extractErrorMessage(error, t('lensAdmin.messages.deleteFailed')))
  }
}

async function approve(row) {
  try {
    await approveLensNode(row.uuid)
    showSuccess(t('lensAdmin.messages.approveSuccess'))
    await load()
  } catch (error) {
    showError(extractErrorMessage(error, t('lensAdmin.messages.approveFailed')))
  }
}

async function reject(row) {
  try {
    await rejectLensNode(row.uuid)
    showSuccess(t('lensAdmin.messages.rejectSuccess'))
    await load()
  } catch (error) {
    showError(extractErrorMessage(error, t('lensAdmin.messages.rejectFailed')))
  }
}

onMounted(load)
onUnmounted(closeMenu)
</script>

<style scoped>
.table-head {
  @apply border-b border-line px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-ink-500;
}

.table-cell {
  @apply px-4 py-3 align-top text-sm text-ink-700;
}

.fleet-stat {
  @apply rounded-lg border border-line bg-surface px-4 py-3;
  box-shadow: 0 1px 2px rgb(17 24 39 / 0.035);
}
</style>
