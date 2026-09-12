<template>
  <AdminLayout>
    <div class="flex max-w-full flex-col gap-4 py-4">
      <section
        class="overflow-hidden rounded-lg border border-line bg-surface shadow-sm"
      >
        <div
          class="flex flex-col gap-4 border-b border-line px-5 py-4 lg:flex-row lg:items-start lg:justify-between"
        >
          <div class="min-w-0">
            <div class="flex flex-wrap items-center gap-2">
              <h1 class="text-xl font-semibold text-ink-900">
                {{ t('lensAdmin.pages.mcp.title') }}
              </h1>
              <span
                class="rounded-md border border-line bg-surface-sunken px-2 py-1 text-xs text-ink-500"
              >
                {{
                  t('lensAdmin.total', {
                    label: t('lensAdmin.pages.mcp.label'),
                    count: mcps.length
                  })
                }}
              </span>
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
              {{ t('lensAdmin.pages.mcp.action') }}
            </BaseButton>
          </div>
        </div>

        <div class="px-5 py-4">
          <BaseLoading v-if="loading && mcps.length === 0" />

          <div
            v-else-if="mcps.length === 0"
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
                  v-for="row in pagedMcps"
                  :key="row.uuid"
                  class="transition-colors hover:bg-line-soft"
                >
                  <td class="table-cell font-medium text-ink-900">
                    {{ row.name }}
                  </td>
                  <td class="table-cell font-mono text-ink-500">
                    {{ row.transport }}
                  </td>
                  <td class="table-cell font-mono text-ink-500">
                    {{ mcpTarget(row) }}
                  </td>
                  <td class="table-cell">
                    <StatusBadge
                      :status="row.enabled ? 'enabled' : 'disabled'"
                    />
                  </td>
                  <td class="table-cell">
                    <RowActions :row="row" @edit="startEdit" @delete="remove" />
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
          <PaginationBar
            v-if="!loading"
            v-model:page-size="pageSize"
            :current-page="currentPage"
            :total="mcps.length"
            @page-size-change="handlePageSizeChange"
            @prev="goPrevPage"
            @next="goNextPage"
          />
        </div>
      </section>

      <BaseModal :show="showModal" :title="modalTitle" @close="closeModal">
        <form class="space-y-4" @submit.prevent="save">
          <FormRow :label="t('lensAdmin.fields.name')">
            <input v-model="form.name" class="form-input" required />
          </FormRow>
          <div class="grid gap-4 md:grid-cols-2">
            <FormRow :label="t('lensAdmin.fields.transport')">
              <BaseSelect v-model="form.transport">
                <option value="url">url</option>
                <option value="stdio">stdio</option>
                <option value="plugin">plugin</option>
              </BaseSelect>
            </FormRow>
            <FormRow
              v-if="form.transport !== 'plugin'"
              :label="t('lensAdmin.fields.endpoint')"
            >
              <input v-model="form.endpoint" class="form-input" />
            </FormRow>
          </div>
          <template v-if="form.transport === 'plugin'">
            <FormRow :label="t('lensAdmin.mcp.pluginConnection')">
              <BaseSelect
                :model-value="form.connection_uuid"
                @update:model-value="handleConnectionChange"
              >
                <option value="">
                  {{ t('lensAdmin.mcp.selectConnection') }}
                </option>
                <option
                  v-for="connection in pluginConnections"
                  :key="connection.uuid"
                  :value="connection.uuid"
                >
                  {{ connection.name }} · {{ connection.plugin_key }}
                </option>
              </BaseSelect>
            </FormRow>
            <FormRow :label="t('lensAdmin.mcp.pluginTools')">
              <div class="space-y-2 rounded-lg border border-line p-3">
                <label
                  v-for="tool in pluginTools"
                  :key="tool.key"
                  class="flex items-start gap-2 text-sm text-ink-700"
                >
                  <input
                    type="checkbox"
                    class="mt-1"
                    :checked="form.tools.includes(tool.key)"
                    @change="togglePluginTool(tool.key)"
                  />
                  <span>
                    <span class="font-medium text-ink-900">{{ tool.key }}</span>
                    <span class="mt-0.5 block text-xs text-ink-500">
                      {{ tool.description }}
                    </span>
                  </span>
                </label>
                <p v-if="!pluginTools.length" class="text-xs text-ink-500">
                  {{ t('lensAdmin.mcp.noPluginTools') }}
                </p>
              </div>
              <p class="mt-2 text-xs leading-5 text-ink-500">
                {{ t('lensAdmin.mcp.pluginAdapterHint') }}
              </p>
            </FormRow>
          </template>
          <template v-else>
            <FormRow :label="t('lensAdmin.fields.config')">
              <KeyValueEditor
                v-model="form.config_rows"
                :key-label="t('lensAdmin.fields.configKey')"
                :value-label="t('lensAdmin.fields.configValue')"
                :mask-sensitive-values="true"
                :configured-label="t('lensAdmin.mcp.secretConfigured')"
              />
              <p class="mt-2 text-xs leading-5 text-ink-500">
                {{ t('lensAdmin.mcp.sensitiveConfigHint') }}
              </p>
            </FormRow>
            <SkillEnvironmentEditor
              v-model="form.environment"
              :help-text="t('lensAdmin.mcp.environmentVariablesHelp')"
            />
            <p class="text-xs leading-5 text-ink-500">
              {{
                t('lensAdmin.mcp.environmentPlaceholderHint', {
                  placeholder: '${VARIABLE_NAME}'
                })
              }}
            </p>
          </template>
          <BooleanRow v-model="form.enabled" />

          <p v-if="formError" class="text-sm text-danger-700">
            {{ formError }}
          </p>
        </form>
        <template #footer>
          <div class="flex flex-row-reverse gap-2">
            <BaseButton :loading="saving" variant="primary" @click="save">
              {{ t('common.save') }}
            </BaseButton>
            <BaseButton variant="outline" @click="closeModal">
              {{ t('common.cancel') }}
            </BaseButton>
          </div>
        </template>
      </BaseModal>
    </div>
  </AdminLayout>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'

import AdminLayout from '@/admin/layout/AdminLayout.vue'
import {
  createMcpServer,
  deleteMcpServer,
  getPluginManifest,
  listConnections,
  listMcpServers,
  listPlugins,
  updateMcpServer
} from '@/api/lens'
import { useToast } from '@/composables/useToast'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseLoading from '@/components/ui/BaseLoading.vue'
import BaseModal from '@/components/ui/BaseModal.vue'
import BaseSelect from '@/components/ui/BaseSelect.vue'
import PaginationBar from '@/components/ui/PaginationBar.vue'
import StatusBadge from '@/components/ui/StatusBadge.vue'
import { extractErrorMessage } from '@/utils/api'

import BooleanRow from './components/BooleanRow.vue'
import FormRow from './components/FormRow.vue'
import KeyValueEditor from './components/KeyValueEditor.vue'
import RowActions from './components/RowActions.vue'
import SkillEnvironmentEditor from './components/SkillEnvironmentEditor.vue'
import { EMPTY_VALUE as emptyValue, normalizeList } from './adminHelpers'
import {
  buildMcpEnvironment,
  mcpConfigToRows,
  mcpEnvironmentForm,
  mcpRowsToConfig
} from './mcpConfig'

const { t } = useI18n()
const { showSuccess, showError } = useToast()

const mcps = ref([])
const connections = ref([])
const pluginManifests = ref({})
const currentPage = ref(1)
const pageSize = ref(20)
const loading = ref(false)
const saving = ref(false)
const showModal = ref(false)
const mode = ref('create')
const form = ref({})
const formError = ref('')

const pluginConnections = computed(() =>
  connections.value.filter((connection) => connection.status === 'active')
)
const selectedConnection = computed(() =>
  connections.value.find(
    (connection) => connection.uuid === form.value.connection_uuid
  )
)
const pluginTools = computed(() => {
  const pluginKey = selectedConnection.value?.plugin_key
  return pluginManifests.value[pluginKey]?.tools || []
})

const columns = computed(() =>
  ['mcpServer', 'transport', 'endpoint', 'status', 'actions'].map((column) =>
    t(`lensAdmin.columns.${column}`)
  )
)

const totalPages = computed(() =>
  Math.max(1, Math.ceil(mcps.value.length / pageSize.value))
)
const pagedMcps = computed(() => {
  const start = (currentPage.value - 1) * pageSize.value
  return mcps.value.slice(start, start + pageSize.value)
})

function handlePageSizeChange() {
  currentPage.value = 1
}

function goPrevPage() {
  if (currentPage.value <= 1) return
  currentPage.value -= 1
}

function goNextPage() {
  if (currentPage.value >= totalPages.value) return
  currentPage.value += 1
}

const modalTitle = computed(() => {
  const action =
    mode.value === 'create'
      ? t('lensAdmin.modal.create')
      : t('lensAdmin.modal.edit')
  return `${action} ${t('lensAdmin.pages.mcp.label')}`
})

function defaultForm() {
  return {
    name: '',
    transport: 'url',
    endpoint: '',
    config_rows: [],
    environment: [],
    connection_uuid: '',
    tools: [],
    enabled: true
  }
}

function formFromRow(row) {
  return {
    uuid: row.uuid,
    name: row.name || '',
    transport: row.transport || 'url',
    endpoint: row.endpoint || '',
    config_rows: mcpConfigToRows(row.config || {}),
    environment: mcpEnvironmentForm(row.environment || []),
    connection_uuid: row.connection_uuid || '',
    tools: Array.isArray(row.tools) ? [...row.tools] : [],
    enabled: row.enabled !== false
  }
}

function buildPayload() {
  if (form.value.transport === 'plugin') {
    return {
      name: form.value.name,
      transport: form.value.transport,
      endpoint: '',
      config: {},
      environment: [],
      connection_uuid: form.value.connection_uuid,
      tools: form.value.tools,
      enabled: !!form.value.enabled
    }
  }
  return {
    name: form.value.name,
    transport: form.value.transport,
    endpoint: form.value.endpoint,
    config: mcpRowsToConfig(form.value.config_rows),
    environment: buildMcpEnvironment(form.value.environment),
    connection_uuid: null,
    tools: [],
    enabled: !!form.value.enabled
  }
}

async function load() {
  loading.value = true
  formError.value = ''
  try {
    const [mcpRows, connectionRows, installedPlugins] = await Promise.all([
      listMcpServers(),
      listConnections(),
      listPlugins()
    ])
    const manifests = await Promise.all(
      installedPlugins.map((plugin) => getPluginManifest(plugin.key))
    )
    mcps.value = normalizeList(mcpRows)
    connections.value = connectionRows
    pluginManifests.value = Object.fromEntries(
      manifests.map((manifest) => [manifest.key, manifest])
    )
  } catch (error) {
    showError(extractErrorMessage(error, t('lensAdmin.messages.loadFailed')))
  } finally {
    loading.value = false
  }
}

function handleConnectionChange(connectionUuid) {
  form.value.connection_uuid = connectionUuid
  form.value.tools = []
}

function togglePluginTool(toolKey) {
  const tools = new Set(form.value.tools)
  if (tools.has(toolKey)) tools.delete(toolKey)
  else tools.add(toolKey)
  form.value.tools = [...tools]
}

function mcpTarget(row) {
  if (row.transport !== 'plugin') return row.endpoint || emptyValue
  const connection = connections.value.find(
    (item) => item.uuid === row.connection_uuid
  )
  return connection?.name || row.connection_uuid || emptyValue
}

function startCreate() {
  mode.value = 'create'
  formError.value = ''
  form.value = defaultForm()
  showModal.value = true
}

function startEdit(row) {
  mode.value = 'edit'
  formError.value = ''
  form.value = formFromRow(row)
  showModal.value = true
}

function closeModal() {
  showModal.value = false
  form.value = {}
  formError.value = ''
}

async function saveByMode(uuid, payload) {
  if (mode.value === 'create') {
    await createMcpServer(payload)
  } else {
    await updateMcpServer(uuid, payload)
  }
}

async function save() {
  saving.value = true
  formError.value = ''
  try {
    await saveByMode(form.value.uuid, buildPayload())
    showSuccess(t('lensAdmin.messages.saveSuccess'))
    closeModal()
    await load()
  } catch (error) {
    formError.value = extractErrorMessage(
      error,
      t('lensAdmin.messages.saveFailed')
    )
    showError(formError.value)
  } finally {
    saving.value = false
  }
}

async function remove(row) {
  try {
    await deleteMcpServer(row.uuid)
    showSuccess(t('lensAdmin.messages.deleteSuccess'))
    await load()
  } catch (error) {
    showError(extractErrorMessage(error, t('lensAdmin.messages.deleteFailed')))
  }
}

onMounted(load)
</script>

<style scoped>
.form-input {
  @apply w-full rounded-lg border border-line bg-surface px-3 py-2 text-sm text-ink-900 focus:border-brand-500 focus:outline-none focus:ring-2 focus:ring-brand-500/20;
}

.table-head {
  @apply border-b border-line px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-ink-500;
}

.table-cell {
  @apply px-4 py-3 align-top text-sm text-ink-700;
}
</style>
