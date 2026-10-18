<template>
  <AdminLayout>
    <div class="w-full max-w-full space-y-4 p-4 md:p-6">
      <div class="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 class="text-lg font-semibold text-theme">
            {{ t('alertManagement.rules.title') }}
          </h1>
          <p class="mt-1 text-sm text-theme-muted">
            {{ t('alertManagement.rules.subtitle') }}
          </p>
        </div>
      </div>
      <BaseCard class="[&>.card-body]:p-4 md:[&>.card-body]:p-6">
        <div class="mb-6 flex justify-end">
          <BaseButton :disabled="!ready" @click="edit(null)">{{
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
                class="min-w-[48rem] w-full divide-y divide-line text-left text-sm"
              >
                <thead class="bg-surface-sunken text-theme-muted">
                  <tr>
                    <th
                      v-for="key in [
                        'name',
                        'events',
                        'scope',
                        'channel',
                        'enabled',
                        'actions'
                      ]"
                      :key="key"
                      class="px-4 py-3 text-xs font-semibold uppercase tracking-wider text-theme-secondary"
                    >
                      {{
                        key === 'scope'
                          ? t('alertManagement.unified.resourceScope')
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
                    <td class="px-4 py-3">
                      <div class="font-medium text-theme">{{ rule.name }}</div>
                      <span class="text-xs text-theme-muted">{{
                        t(`alertManagement.unified.subjects.${rule.subject}`)
                      }}</span>
                    </td>
                    <td class="max-w-xs px-4 py-3">
                      {{
                        rule.events
                          .map((event) =>
                            t(`alertManagement.unified.events.${event}`)
                          )
                          .join(' · ')
                      }}
                    </td>
                    <td class="max-w-xs break-words px-4 py-3">
                      {{ scopeLabel(rule) }}
                    </td>
                    <td class="px-4 py-3">
                      {{ rule.subscriptions.length
                      }}<span v-if="rule.personal_subscription_enabled">
                        · {{ t('alertManagement.unified.allowPersonal') }}</span
                      >
                    </td>
                    <td class="px-4 py-3">
                      <StatusBadge
                        :status="rule.enabled ? 'enabled' : 'disabled'"
                      />
                    </td>
                    <td class="px-4 py-3">
                      <div class="flex gap-2">
                        <BaseButton
                          variant="ghost"
                          size="sm"
                          @click="edit(rule)"
                          >{{ t('common.edit') }}</BaseButton
                        >
                        <BaseButton
                          variant="danger-outline"
                          size="sm"
                          @click="deleteTarget = rule"
                          >{{ t('common.delete') }}</BaseButton
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
import { useI18n } from 'vue-i18n'
import {
  alertsAdminApi,
  managementApi,
  notificationsAdminApi
} from '@/admin/api'
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
import { alertApiError } from './alertOptions'
const { t } = useI18n()
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
      channels,
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
        notificationsAdminApi.getChannels({ page, page_size: 100 })
      ),
      managementApi.getAllUsers(),
      managementApi.getAllGroups()
    ])
    catalog.value = events
    options.value = {
      assistants: choices(assistants),
      datasources: choices(datasources),
      lensnodes: choices(lensnodes),
      channels: channels.filter(
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
function scopeLabel(rule) {
  if (rule.subject === 'message') return rule.topic
  const resources = rule.subject === 'run' ? rule.assistants : rule.datasources
  const available =
    rule.subject === 'run'
      ? options.value.assistants
      : options.value.datasources
  return resources.length
    ? resources
        .map(
          (uuid) => available.find((row) => row.value === uuid)?.label || uuid
        )
        .join(', ')
    : t('alertManagement.unified.allResources')
}
function edit(rule) {
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
