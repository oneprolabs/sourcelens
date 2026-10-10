<template>
  <AdminLayout>
    <div class="w-full max-w-full p-6">
      <div class="mb-4">
        <h1 class="text-lg font-semibold text-gray-900">
          {{ t('alertManagement.rules.title') }}
        </h1>
        <p class="mt-1 text-sm text-gray-500">
          {{ t('alertManagement.rules.subtitle') }}
        </p>
      </div>

      <div class="bg-white rounded-lg border border-gray-200 shadow-sm">
        <div class="p-6">
          <div class="flex flex-wrap items-center justify-end gap-3 mb-6">
            <BaseButton
              variant="outline"
              size="sm"
              :loading="loading"
              @click="loadRules"
            >
              {{ t('common.refresh') }}
            </BaseButton>
            <BaseButton variant="primary" size="sm" @click="openAddModal">
              {{ t('alertManagement.rules.addRule') }}
            </BaseButton>
          </div>

          <BaseLoading v-if="loading" />
          <template v-else>
            <div
              v-if="rules.length === 0"
              class="py-16 text-center rounded-lg border border-gray-200 bg-gray-50"
            >
              <p class="text-sm font-medium text-gray-600">
                {{ t('alertManagement.rules.noRules') }}
              </p>
            </div>
            <div
              v-else
              class="overflow-x-auto rounded-lg border border-gray-200"
            >
              <table class="min-w-full divide-y divide-gray-200">
                <thead class="bg-gray-50">
                  <tr>
                    <th
                      class="px-4 py-3 text-left text-xs font-semibold text-gray-700 uppercase tracking-wider"
                    >
                      {{ t('alertManagement.rules.name') }}
                    </th>
                    <th
                      class="px-4 py-3 text-left text-xs font-semibold text-gray-700 uppercase tracking-wider"
                    >
                      {{ t('alertManagement.rules.events') }}
                    </th>
                    <th
                      class="px-4 py-3 text-left text-xs font-semibold text-gray-700 uppercase tracking-wider"
                    >
                      {{ t('alertManagement.rules.scope') }}
                    </th>
                    <th
                      class="px-4 py-3 text-left text-xs font-semibold text-gray-700 uppercase tracking-wider"
                    >
                      {{ t('alertManagement.rules.channel') }}
                    </th>
                    <th
                      class="px-4 py-3 text-left text-xs font-semibold text-gray-700 uppercase tracking-wider"
                    >
                      {{ t('alertManagement.rules.enabled') }}
                    </th>
                    <th
                      class="px-4 py-3 text-right text-xs font-semibold text-gray-700 uppercase tracking-wider"
                    >
                      {{ t('alertManagement.rules.actions') }}
                    </th>
                  </tr>
                </thead>
                <tbody class="bg-white divide-y divide-gray-100">
                  <tr
                    v-for="row in rules"
                    :key="row.uuid"
                    class="hover:bg-gray-50"
                  >
                    <td class="px-4 py-3 text-sm text-gray-900">
                      {{ row.name }}
                    </td>
                    <td class="px-4 py-3 text-sm text-gray-700">
                      <span
                        v-for="event in row.events"
                        :key="event"
                        class="inline-flex items-center px-2 py-0.5 mr-1 rounded text-xs font-medium bg-gray-100 text-gray-700"
                      >
                        {{ eventLabel(event) }}
                      </span>
                    </td>
                    <td class="px-4 py-3 text-sm text-gray-700">
                      <span
                        v-if="!row.assistants || row.assistants.length === 0"
                      >
                        {{ t('alertManagement.rules.allAssistants') }}
                      </span>
                      <span v-else>
                        {{ assistantNames(row.assistants) }}
                      </span>
                    </td>
                    <td class="px-4 py-3 text-sm text-gray-700">
                      {{ channelName(row.channel_uuid) }}
                    </td>
                    <td class="px-4 py-3 text-sm">
                      <span
                        :class="
                          row.enabled ? 'text-green-600' : 'text-gray-400'
                        "
                      >
                        {{ row.enabled ? t('common.yes') : t('common.no') }}
                      </span>
                    </td>
                    <td class="px-4 py-3 whitespace-nowrap text-right">
                      <RowActionMenu
                        :actions="rowActions"
                        @select="handleRowAction($event, row)"
                      />
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
          </template>
        </div>
      </div>

      <BaseModal
        :show="showModal"
        :title="
          editingUuid
            ? t('alertManagement.rules.editRule')
            : t('alertManagement.rules.addRule')
        "
        @close="closeModal"
      >
        <form @submit.prevent="submitForm" class="space-y-4">
          <BaseInput
            v-model="form.name"
            :label="t('alertManagement.rules.name')"
            required
          />

          <div>
            <label class="block text-sm font-medium text-gray-700 mb-1">
              {{ t('alertManagement.rules.events') }}
            </label>
            <label
              v-for="option in eventOptions"
              :key="option.value"
              class="flex cursor-pointer items-center gap-2 py-1"
            >
              <input
                v-model="form.events"
                type="checkbox"
                :value="option.value"
                class="h-4 w-4 rounded border-gray-300 text-primary-600 focus:ring-primary-500"
              />
              <span class="text-sm text-gray-700">{{ option.label }}</span>
            </label>
          </div>

          <BaseInput
            v-if="form.events.includes('token_exceeded')"
            v-model="form.token_threshold"
            type="number"
            :label="t('alertManagement.rules.tokenThreshold')"
            required
          />
          <BaseInput
            v-if="form.events.includes('rounds_exceeded')"
            v-model="form.rounds_threshold"
            type="number"
            :label="t('alertManagement.rules.roundsThreshold')"
            required
          />

          <div>
            <label class="block text-sm font-medium text-gray-700 mb-1">
              {{ t('alertManagement.rules.channel') }}
            </label>
            <BaseSelect v-model="form.channel_uuid">
              <option value="">
                {{ t('alertManagement.rules.channelPlaceholder') }}
              </option>
              <option
                v-for="channel in channels"
                :key="channel.uuid"
                :value="channel.uuid"
              >
                {{ channelLabel(channel) }}
              </option>
            </BaseSelect>
          </div>

          <div v-if="isEmailChannel">
            <label class="block text-sm font-medium text-gray-700 mb-1">
              {{ t('alertManagement.rules.emailRecipients') }}
            </label>
            <textarea
              v-model="form.email_recipients"
              rows="2"
              class="block w-full px-3 py-2 text-sm border border-gray-300 rounded-md shadow-sm focus:ring-primary-500 focus:border-primary-500"
              :placeholder="
                t('alertManagement.rules.emailRecipientsPlaceholder')
              "
            ></textarea>
            <p class="mt-1 text-xs text-gray-500">
              {{ t('alertManagement.rules.emailRecipientsDesc') }}
            </p>
          </div>

          <div>
            <label class="block text-sm font-medium text-gray-700 mb-1">
              {{ t('alertManagement.rules.scope') }}
            </label>
            <p class="mb-2 text-xs text-gray-500">
              {{ t('alertManagement.rules.selectAssistantsDesc') }}
            </p>
            <div
              class="max-h-48 overflow-y-auto rounded-md border border-gray-200 p-2 space-y-1"
            >
              <label
                v-for="assistant in assistants"
                :key="assistant.uuid"
                class="flex cursor-pointer items-center gap-2"
              >
                <input
                  v-model="form.assistants"
                  type="checkbox"
                  :value="assistant.uuid"
                  class="h-4 w-4 rounded border-gray-300 text-primary-600 focus:ring-primary-500"
                />
                <span class="text-sm text-gray-700">{{ assistant.name }}</span>
              </label>
            </div>
          </div>

          <label class="flex cursor-pointer items-center gap-2">
            <input
              v-model="form.enabled"
              type="checkbox"
              class="h-4 w-4 rounded border-gray-300 text-primary-600 focus:ring-primary-500"
            />
            <span class="text-sm font-medium text-gray-700">
              {{ t('alertManagement.rules.enabled') }}
            </span>
          </label>

          <div v-if="formError" class="text-sm text-red-600">
            {{ formError }}
          </div>

          <div
            class="flex flex-wrap items-center justify-end gap-3 pt-2 border-t border-gray-200"
          >
            <BaseButton type="button" variant="outline" @click="closeModal">
              {{ t('common.cancel') }}
            </BaseButton>
            <BaseButton type="submit" variant="primary" :loading="saving">
              {{ t('common.save') }}
            </BaseButton>
          </div>
        </form>
      </BaseModal>

      <BaseModal
        :show="showDeleteConfirm"
        :title="t('alertManagement.rules.delete')"
        @close="showDeleteConfirm = false"
      >
        <p class="text-sm text-gray-700 mb-4">
          {{ t('alertManagement.rules.confirmDelete') }}
        </p>
        <div class="flex flex-wrap items-center justify-end gap-3">
          <BaseButton variant="outline" @click="showDeleteConfirm = false">
            {{ t('common.cancel') }}
          </BaseButton>
          <BaseButton
            variant="primary"
            :loading="deleting"
            class="bg-red-600 hover:bg-red-700"
            @click="doDelete"
          >
            {{ t('common.delete') }}
          </BaseButton>
        </div>
      </BaseModal>
    </div>
  </AdminLayout>
</template>

<script setup>
import { Pencil, Trash2 } from '@lucide/vue'
import { computed, onMounted, reactive, ref } from 'vue'
import { useI18n } from 'vue-i18n'

import { alertsAdminApi, notificationsAdminApi } from '@/admin/api'
import AdminLayout from '@/admin/layout/AdminLayout.vue'
import { listAssistants } from '@/api/lens'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseInput from '@/components/ui/BaseInput.vue'
import BaseLoading from '@/components/ui/BaseLoading.vue'
import BaseModal from '@/components/ui/BaseModal.vue'
import BaseSelect from '@/components/ui/BaseSelect.vue'
import RowActionMenu from '@/components/ui/RowActionMenu.vue'
import { useToast } from '@/composables/useToast'

const { t } = useI18n()
const { showSuccess, showError } = useToast()

const loading = ref(false)
const saving = ref(false)
const deleting = ref(false)
const rules = ref([])
const channels = ref([])
const assistants = ref([])
const showModal = ref(false)
const showDeleteConfirm = ref(false)
const editingUuid = ref(null)
const deleteTarget = ref(null)
const formError = ref('')

const form = reactive({
  name: '',
  enabled: true,
  events: [],
  token_threshold: '',
  rounds_threshold: '',
  channel_uuid: '',
  email_recipients: '',
  assistants: []
})

const eventOptions = computed(() => [
  { value: 'run_failed', label: t('alertManagement.rules.eventRunFailed') },
  {
    value: 'token_exceeded',
    label: t('alertManagement.rules.eventTokenExceeded')
  },
  {
    value: 'rounds_exceeded',
    label: t('alertManagement.rules.eventRoundsExceeded')
  }
])

const rowActions = computed(() => [
  { key: 'edit', label: t('common.edit'), icon: Pencil },
  {
    key: 'delete',
    label: t('common.delete'),
    icon: Trash2,
    variant: 'danger',
    divider: true
  }
])

const isEmailChannel = computed(() => {
  const channel = channels.value.find((item) => item.uuid === form.channel_uuid)
  return channel?.channel_type === 'email'
})

function eventLabel(value) {
  const option = eventOptions.value.find((item) => item.value === value)
  return option ? option.label : value
}

function channelLabel(channel) {
  const type =
    channel.channel_type === 'email'
      ? t('alertManagement.rules.typeEmail')
      : t('alertManagement.rules.typeWebhook')
  return `${channel.name || channel.uuid} (${type})`
}

function channelName(uuid) {
  if (!uuid) return t('alertManagement.rules.noChannel')
  const channel = channels.value.find((item) => item.uuid === uuid)
  return channel ? channel.name || uuid : uuid
}

function assistantNames(uuids) {
  return uuids
    .map(
      (uuid) =>
        assistants.value.find((item) => item.uuid === uuid)?.name || uuid
    )
    .join(', ')
}

function resetForm() {
  form.name = ''
  form.enabled = true
  form.events = []
  form.token_threshold = ''
  form.rounds_threshold = ''
  form.channel_uuid = ''
  form.email_recipients = ''
  form.assistants = []
  formError.value = ''
  editingUuid.value = null
}

function openAddModal() {
  resetForm()
  showModal.value = true
}

function openEditModal(row) {
  resetForm()
  editingUuid.value = row.uuid
  form.name = row.name || ''
  form.enabled = !!row.enabled
  form.events = [...(row.events || [])]
  form.token_threshold = row.token_threshold ?? ''
  form.rounds_threshold = row.rounds_threshold ?? ''
  form.channel_uuid = row.channel_uuid || ''
  form.email_recipients = (row.email_recipients || []).join('\n')
  form.assistants = [...(row.assistants || [])]
  showModal.value = true
}

function closeModal() {
  showModal.value = false
  resetForm()
}

function handleRowAction(action, row) {
  if (action === 'edit') {
    openEditModal(row)
    return
  }
  deleteTarget.value = row
  showDeleteConfirm.value = true
}

async function loadRules() {
  loading.value = true
  try {
    const data = await alertsAdminApi.getRules({ page_size: 100 })
    rules.value = Array.isArray(data) ? data : data?.results || []
  } catch {
    rules.value = []
  } finally {
    loading.value = false
  }
}

async function loadChannels() {
  try {
    const data = await notificationsAdminApi.getChannels()
    const rows = Array.isArray(data) ? data : data?.results || []
    channels.value = rows.filter((channel) =>
      ['webhook', 'email'].includes(channel.channel_type)
    )
  } catch {
    channels.value = []
  }
}

async function loadAssistants() {
  try {
    const data = await listAssistants()
    assistants.value = Array.isArray(data) ? data : []
  } catch {
    assistants.value = []
  }
}

async function submitForm() {
  formError.value = ''
  if (!form.name.trim()) {
    formError.value = t('alertManagement.rules.nameRequired')
    return
  }
  if (form.events.length === 0) {
    formError.value = t('alertManagement.rules.eventsRequired')
    return
  }
  if (form.events.includes('token_exceeded') && !Number(form.token_threshold)) {
    formError.value = t('alertManagement.rules.thresholdRequired')
    return
  }
  if (
    form.events.includes('rounds_exceeded') &&
    !Number(form.rounds_threshold)
  ) {
    formError.value = t('alertManagement.rules.thresholdRequired')
    return
  }
  const recipients = form.email_recipients
    .split(/[\n,]/)
    .map((item) => item.trim())
    .filter(Boolean)
  if (isEmailChannel.value && recipients.length === 0) {
    formError.value = t('alertManagement.rules.emailRecipientsRequired')
    return
  }
  const body = {
    name: form.name.trim(),
    enabled: form.enabled,
    events: form.events,
    token_threshold: form.events.includes('token_exceeded')
      ? Number(form.token_threshold)
      : null,
    rounds_threshold: form.events.includes('rounds_exceeded')
      ? Number(form.rounds_threshold)
      : null,
    channel_uuid: form.channel_uuid || null,
    email_recipients: recipients,
    assistants: form.assistants
  }
  saving.value = true
  try {
    if (editingUuid.value) {
      await alertsAdminApi.updateRule(editingUuid.value, body)
    } else {
      await alertsAdminApi.createRule(body)
    }
    showSuccess(t('alertManagement.rules.saveSuccess'))
    closeModal()
    loadRules()
  } catch (e) {
    showError(
      e?.response?.data?.detail ||
        e?.message ||
        t('alertManagement.rules.saveFailed')
    )
  } finally {
    saving.value = false
  }
}

async function doDelete() {
  if (!deleteTarget.value) return
  deleting.value = true
  try {
    await alertsAdminApi.deleteRule(deleteTarget.value.uuid)
    showSuccess(t('common.success'))
    showDeleteConfirm.value = false
    deleteTarget.value = null
    loadRules()
  } catch (e) {
    showError(e?.response?.data?.detail || e?.message || t('common.error'))
  } finally {
    deleting.value = false
  }
}

onMounted(() => {
  loadRules()
  loadChannels()
  loadAssistants()
})
</script>
