<template>
  <AdminLayout>
    <div class="w-full max-w-full space-y-4 p-4 md:p-6">
      <div class="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 class="admin-page-title">
            {{ t('alertManagement.rules.title') }}
          </h1>
          <p class="admin-page-subtitle">
            {{ t('alertManagement.rules.subtitle') }}
          </p>
        </div>
      </div>
      <BaseCard class="[&>.card-body]:p-4 md:[&>.card-body]:p-6">
        <div class="mb-6 flex flex-wrap items-center justify-end gap-3">
          <BaseButton size="sm" :disabled="!ready" @click="edit(null)">{{
            t('alertManagement.rules.addRule')
          }}</BaseButton>
        </div>
        <p v-if="error" role="alert" class="text-sm text-danger-700">
          {{ error }}
          <button class="underline" @click="initialize">
            {{ t('common.refresh') }}
          </button>
        </p>
        <BaseLoading v-if="loading" />
        <AlertEmptyState
          v-else-if="!rules.length"
          :text="t('alertManagement.rules.noRules')"
        />
        <template v-else>
          <div class="overflow-hidden rounded-lg border border-line bg-surface">
            <div class="overflow-x-auto">
              <table
                class="w-full min-w-[36rem] table-fixed divide-y divide-line text-left text-sm leading-5"
              >
                <colgroup>
                  <col class="w-[30%]" />
                  <col />
                  <col class="w-24" />
                  <col class="w-48" />
                </colgroup>
                <thead class="bg-surface-sunken text-theme-muted">
                  <tr>
                    <th
                      v-for="key in ['name', 'channel', 'enabled', 'actions']"
                      :key="key"
                      class="px-4 py-3 text-xs font-semibold uppercase tracking-wide text-theme-muted"
                      :class="key === 'actions' ? 'text-right' : ''"
                    >
                      {{
                        key === 'enabled'
                          ? t('alertManagement.events.status')
                          : t(`alertManagement.rules.${key}`)
                      }}
                    </th>
                  </tr>
                </thead>
                <tbody
                  class="divide-y divide-line bg-surface text-theme-secondary"
                >
                  <tr
                    v-for="rule in rules"
                    :key="rule.uuid"
                    class="transition-colors hover:bg-surface-hover"
                  >
                    <td class="px-4 py-3 align-middle">
                      <button
                        type="button"
                        class="block max-w-full truncate rounded text-left font-medium text-primary-600 underline-offset-4 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-500"
                        :title="rule.name"
                        @click="selectedRule = rule"
                      >
                        {{ rule.name }}
                      </button>
                      <span class="text-xs text-theme-muted">{{
                        t(`alertManagement.unified.subjects.${rule.subject}`)
                      }}</span>
                    </td>
                    <td class="px-4 py-3 align-middle">
                      <div class="flex min-w-0 flex-wrap gap-1.5">
                        <span
                          v-for="channel in ruleChannels(rule)"
                          :key="channel.uuid"
                          :title="channel.name"
                          class="inline-flex max-w-full items-center gap-1.5 rounded-md border border-line bg-surface-sunken px-2 py-1 text-xs"
                        >
                          <span class="truncate">{{ channel.name }}</span>
                          <span
                            v-if="channel.channel_type"
                            class="shrink-0 text-theme-muted"
                            >{{
                              notificationTargetLabel(channel.channel_type, t)
                            }}</span
                          >
                        </span>
                        <span
                          v-if="
                            !rule.subscriptions?.length &&
                            !rule.personal_subscription_enabled
                          "
                          class="text-theme-muted"
                          >{{ t('alertManagement.rules.noChannel') }}</span
                        >
                        <span
                          v-if="rule.personal_subscription_enabled"
                          class="rounded-md border border-line px-2 py-1 text-xs text-theme-muted"
                          >{{
                            t('alertManagement.unified.allowPersonal')
                          }}</span
                        >
                      </div>
                    </td>
                    <td class="px-4 py-3 align-middle">
                      <StatusBadge
                        :status="rule.enabled ? 'enabled' : 'disabled'"
                      />
                    </td>
                    <td class="px-4 py-3 align-middle">
                      <div
                        class="flex items-center justify-end gap-2 whitespace-nowrap"
                      >
                        <BaseButton
                          variant="outline"
                          size="sm"
                          class="shrink-0 whitespace-nowrap"
                          @click="edit(rule)"
                          ><Pencil class="h-3.5 w-3.5" aria-hidden="true" />{{
                            t('common.edit')
                          }}</BaseButton
                        >
                        <BaseButton
                          variant="ghost"
                          size="sm"
                          class="shrink-0 whitespace-nowrap text-danger-700 hover:bg-danger-50 hover:text-danger-800"
                          @click="deleteTarget = rule"
                          ><Trash2 class="h-3.5 w-3.5" aria-hidden="true" />{{
                            t('common.delete')
                          }}</BaseButton
                        >
                      </div>
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>
          <PaginationBar
            v-model:page-size="pageSize"
            :current-page="page"
            :total="total"
            @page-size-change="changePage(1)"
            @prev="changePage(page - 1)"
            @next="changePage(page + 1)"
          />
        </template>
      </BaseCard>
      <AlertRuleDetail
        :rule="selectedRule"
        :options="options"
        :channels="channels"
        @close="selectedRule = null"
        @edit="edit"
      />
      <AlertRuleEditor
        :show="showEditor"
        :value="editing"
        :options="options"
        :catalog="catalog"
        @close="showEditor = false"
        @saved="saved"
      />
      <BaseModal
        :show="!!deleteTarget"
        :title="t('alertManagement.rules.delete')"
        @close="deleteTarget = null"
      >
        <p class="mb-4 text-sm text-theme-secondary">
          {{ t('alertManagement.rules.confirmDelete') }}
        </p>
        <div class="flex justify-end gap-3">
          <BaseButton variant="outline" @click="deleteTarget = null">{{
            t('common.cancel')
          }}</BaseButton
          ><BaseButton variant="danger" :loading="deleting" @click="remove">{{
            t('common.delete')
          }}</BaseButton>
        </div>
      </BaseModal>
    </div>
  </AdminLayout>
</template>
<script setup>
import { onMounted, ref } from 'vue'
import { Pencil, Trash2 } from '@lucide/vue'
import { notificationTargetLabel } from '@/admin/utils/notificationTargets'
import { useI18n } from 'vue-i18n'
import { alertsAdminApi, managementApi } from '@/admin/api'
import { listAssistants, listDataSources, listLensNodes } from '@/api/lens'
import { collectPaginatedResults } from '@/api/pagination'
import AdminLayout from '@/admin/layout/AdminLayout.vue'
import BaseCard from '@/components/ui/BaseCard.vue'
import AlertEmptyState from './AlertEmptyState.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseLoading from '@/components/ui/BaseLoading.vue'
import BaseModal from '@/components/ui/BaseModal.vue'
import StatusBadge from '@/components/ui/StatusBadge.vue'
import PaginationBar from '@/components/ui/PaginationBar.vue'
import AlertRuleEditor from './AlertRuleEditor.vue'
import AlertRuleDetail from './AlertRuleDetail.vue'
import { alertApiError } from './alertOptions'
const { t } = useI18n()
const channels = ref([])
const selectedRule = ref(null)
const rules = ref([]),
  catalog = ref({}),
  options = ref({
    assistants: [],
    datasources: [],
    lensnodes: [],
    channels: [],
    users: [],
    groups: []
  })
const loading = ref(false),
  ready = ref(false),
  error = ref(''),
  showEditor = ref(false),
  editing = ref(null),
  deleteTarget = ref(null),
  deleting = ref(false)
const page = ref(1),
  pageSize = ref(20),
  total = ref(0)
const choices = (rows, identity = 'uuid') =>
  rows.map((row) => ({
    value: row[identity],
    label: row.name || row.username || row.email || String(row[identity])
  }))
async function availableScopes(request) {
  try {
    return await request
  } catch (error) {
    if (error?.response?.status === 403) return []
    throw error
  }
}
async function initialize() {
  loading.value = true
  error.value = ''
  try {
    const [
      events,
      assistants,
      datasources,
      lensnodes,
      channelRows,
      users,
      groups
    ] = await Promise.all([
      alertsAdminApi.getCatalog(),
      listAssistants(),
      availableScopes(
        collectPaginatedResults((page) =>
          listDataSources({ page, page_size: 100 })
        )
      ),
      availableScopes(listLensNodes()),
      collectPaginatedResults((page) =>
        alertsAdminApi.getChannels({ page, page_size: 100 })
      ),
      managementApi.getAllUsers(),
      managementApi.getAllGroups()
    ])
    catalog.value = events
    channels.value = channelRows
    options.value = {
      assistants: choices(assistants),
      datasources: choices(datasources),
      lensnodes: choices(lensnodes),
      channels: channelRows.filter(
        (row) =>
          row.is_active &&
          ['email', 'webhook', 'feishu', 'wecom', 'dingtalk'].includes(
            row.channel_type
          )
      ),
      users: choices(users, 'id'),
      groups: choices(groups, 'id')
    }
    ready.value = true
    await loadRules()
  } catch (err) {
    error.value = alertApiError(err)
  } finally {
    loading.value = false
  }
}
async function loadRules() {
  const data = await alertsAdminApi.getRules({
    page: page.value,
    page_size: pageSize.value
  })
  rules.value = data.results || []
  total.value = data.count || 0
}
async function changePage(value) {
  page.value = Math.max(1, value)
  try {
    await loadRules()
  } catch (err) {
    error.value = alertApiError(err)
  }
}
function ruleChannels(rule) {
  return [
    ...new Set((rule.subscriptions || []).map((item) => item.channel_uuid))
  ].map(
    (uuid) =>
      channels.value.find((channel) => channel.uuid === uuid) || {
        uuid,
        name: t('alertManagement.rules.noChannel')
      }
  )
}
function edit(rule) {
  selectedRule.value = null
  editing.value = rule
  showEditor.value = true
}
async function saved() {
  showEditor.value = false
  await changePage(page.value)
}
async function remove() {
  deleting.value = true
  try {
    await alertsAdminApi.deleteRule(deleteTarget.value.uuid)
    deleteTarget.value = null
    await changePage(1)
  } catch (err) {
    error.value = alertApiError(err)
  } finally {
    deleting.value = false
  }
}
onMounted(initialize)
</script>
