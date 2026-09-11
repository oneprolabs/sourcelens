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
                {{ t('lensAdmin.pages.assistants.title') }}
              </h1>
              <span
                class="rounded-md border border-line bg-surface-sunken px-2 py-1 text-xs text-ink-500"
              >
                {{
                  t('lensAdmin.total', {
                    label: t('lensAdmin.pages.assistants.label'),
                    count: assistants.length
                  })
                }}
              </span>
            </div>
          </div>
          <div class="flex flex-wrap items-center gap-2">
            <div
              class="flex items-center gap-1 rounded-lg border border-line bg-surface-sunken p-1"
              role="group"
              :aria-label="t('lensAdmin.pages.assistants.viewSelector')"
            >
              <BaseButton
                :variant="showArchived ? 'ghost' : 'secondary'"
                size="sm"
                :aria-pressed="!showArchived"
                @click="switchArchiveView(false)"
              >
                {{ t('lensAdmin.pages.assistants.active') }}
              </BaseButton>
              <BaseButton
                :variant="showArchived ? 'secondary' : 'ghost'"
                size="sm"
                :aria-pressed="showArchived"
                @click="switchArchiveView(true)"
              >
                {{ t('lensAdmin.pages.assistants.archived') }}
              </BaseButton>
            </div>
            <BaseButton
              variant="outline"
              size="sm"
              :loading="loading"
              @click="load"
            >
              {{ t('common.refresh') }}
            </BaseButton>
            <BaseButton
              v-if="!showArchived"
              variant="primary"
              size="sm"
              @click="startCreate"
            >
              {{ t('lensAdmin.pages.assistants.action') }}
            </BaseButton>
          </div>
        </div>

        <div class="px-5 py-4">
          <BaseLoading v-if="loading && assistants.length === 0" />

          <div
            v-else-if="assistants.length === 0"
            class="rounded-lg border border-line bg-surface-sunken py-16 text-center"
          >
            <p class="text-sm font-medium text-ink-500">
              {{
                t(
                  showArchived
                    ? 'lensAdmin.pages.assistants.emptyArchived'
                    : 'lensAdmin.pages.assistants.emptyActive'
                )
              }}
            </p>
          </div>

          <div
            v-else
            class="assistants-table-wrap overflow-x-auto rounded-lg border border-line bg-surface"
          >
            <table
              class="min-w-[72rem] w-full table-fixed divide-y divide-line md:min-w-0"
            >
              <colgroup>
                <col style="width: 26%" />
                <col style="width: 12%" />
                <col style="width: 10%" />
                <col style="width: 8%" />
                <col style="width: 10%" />
                <col style="width: 8%" />
                <col style="width: 12%" />
                <col style="width: 11.5rem" />
              </colgroup>
              <thead class="bg-surface-sunken">
                <tr>
                  <th
                    v-for="(column, index) in activeColumns"
                    :key="column"
                    class="table-head"
                    :class="{ 'assistant-type-column': index === 2 }"
                  >
                    {{ column }}
                  </th>
                </tr>
              </thead>
              <tbody class="divide-y divide-line bg-surface">
                <tr
                  v-for="row in pagedAssistants"
                  :key="row.uuid"
                  class="transition-colors hover:bg-line-soft"
                >
                  <td class="table-cell assistant-name-cell">
                    <button
                      type="button"
                      class="assistant-name-action block max-w-full truncate text-left"
                      @click="openDetails(row)"
                    >
                      {{ row.name }}
                    </button>
                    <div
                      class="assistant-slug mt-1 font-mono text-xs text-ink-400"
                      :title="row.slug"
                    >
                      {{ row.slug }}
                    </div>
                  </td>
                  <td class="table-cell text-ink-600">
                    {{ lensNodeName(row) }}
                  </td>
                  <td class="assistant-type-column table-cell text-ink-600">
                    <div>
                      {{
                        (row.mode || row.routing_mode) === 'smart'
                          ? t('lensAdmin.routingModes.smart')
                          : assistantTypeLabel(row.capability)
                      }}
                    </div>
                  </td>
                  <td class="table-cell text-ink-600">
                    <div
                      data-testid="assistant-tool-counts"
                      class="flex items-center gap-3"
                    >
                      <span
                        class="tool-count"
                        :class="{
                          'tool-count-empty': !row.skill_summary?.enabled
                        }"
                        :title="skillCountLabel(row)"
                        :aria-label="skillCountLabel(row)"
                      >
                        <BookOpen :size="16" aria-hidden="true" />
                        {{ row.skill_summary?.enabled || 0 }}
                      </span>
                      <span
                        class="tool-count"
                        :class="{
                          'tool-count-empty': !row.mcp_summary?.enabled
                        }"
                        :title="mcpCountLabel(row)"
                        :aria-label="mcpCountLabel(row)"
                      >
                        <Server :size="16" aria-hidden="true" />
                        {{ row.mcp_summary?.enabled || 0 }}
                      </span>
                      <span
                        class="tool-count"
                        :class="{
                          'tool-count-empty': !row.datasource_bindings?.length
                        }"
                        :title="`Datasources: ${row.datasource_bindings?.length || 0}`"
                        :aria-label="`Datasources: ${row.datasource_bindings?.length || 0}`"
                      >
                        <Database :size="16" aria-hidden="true" />
                        {{ row.datasource_bindings?.length || 0 }}
                      </span>
                    </div>
                  </td>
                  <td class="table-cell">
                    <span
                      class="inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs font-semibold"
                      :class="
                        row.visibility === 'private'
                          ? 'border-amber-300 bg-amber-100 text-amber-800'
                          : 'border-emerald-300 bg-emerald-100 text-emerald-800'
                      "
                      :title="
                        t(
                          `lensAdmin.visibility.${row.visibility === 'private' ? 'private' : 'public'}Desc`
                        )
                      "
                    >
                      <component
                        :is="
                          row.visibility === 'private' ? LockIcon : GlobeIcon
                        "
                        class="h-3.5 w-3.5"
                      />
                      {{
                        t(
                          `lensAdmin.visibility.${row.visibility === 'private' ? 'private' : 'public'}`
                        )
                      }}
                    </span>
                  </td>
                  <td class="table-cell">
                    <StatusBadge :status="row.status" />
                  </td>
                  <td class="table-cell">
                    <BaseButton
                      v-if="row.status === 'active'"
                      size="sm"
                      variant="outline"
                      @click="copyShareUrl(row)"
                    >
                      <Copy :size="15" aria-hidden="true" />
                      {{ t('lens.share.copyLink') }}
                    </BaseButton>
                    <span v-else class="text-ink-400">{{ emptyValue }}</span>
                  </td>
                  <td class="table-cell assistant-actions-cell">
                    <div class="flex flex-nowrap items-center gap-2">
                      <BaseButton
                        v-if="row.status === 'active'"
                        size="sm"
                        variant="outline"
                        @click="startEdit(row)"
                      >
                        {{ t('common.edit') }}
                      </BaseButton>
                      <BaseButton
                        v-if="row.status === 'active'"
                        size="sm"
                        variant="danger-outline"
                        @click="requestArchive(row)"
                      >
                        {{ t('lensAdmin.pages.assistants.archive') }}
                      </BaseButton>
                      <BaseButton
                        v-else
                        size="sm"
                        variant="outline"
                        :loading="actionUuid === row.uuid"
                        @click="restore(row)"
                      >
                        {{ t('lensAdmin.pages.assistants.restore') }}
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
            :total="assistants.length"
            @page-size-change="handlePageSizeChange"
            @prev="goPrevPage"
            @next="goNextPage"
          />
        </div>
      </section>

      <AssistantDetailDrawer
        :show="Boolean(detailAssistant)"
        :assistant="detailAssistant"
        :lensnode-name="lensNodeName(detailAssistant?.lensnode)"
        :assistant-type="
          (detailAssistant?.mode || detailAssistant?.routing_mode) === 'smart'
            ? t('lensAdmin.routingModes.smart')
            : assistantTypeLabel(detailAssistant?.capability)
        "
        @close="closeDetails"
        @copy-share="copyShareUrl"
        @edit="startEditFromDetail"
      />

      <!-- Assistant Drawer (create wizard + edit) -->
      <AssistantFormDrawer
        :show="showDrawer"
        :mode="mode"
        :form="form"
        :lensnodes="lensnodes"
        :assistants="assistants"
        :skills="skills"
        :environment-variable-sets="environmentVariableSets"
        :mcps="mcps"
        :plugin-connections="pluginConnections"
        :plugin-manifests="pluginManifests"
        :plugin-icon-urls="pluginIconUrls"
        :llm-config-options="llmConfigOptions"
        :datasource-options="datasourceOptions"
        :saving="saving"
        :form-error="formError"
        :refreshing-dirs="refreshingDirs"
        @close="closeDrawer"
        @save="save"
        @refresh-dirs="refreshDirs"
      />

      <BaseModal
        :show="Boolean(archiveConfirmRow)"
        :title="t('lensAdmin.assistantDetail.archiveTitle')"
        icon-type="warning"
        @close="closeArchiveConfirmation"
      >
        <p class="text-sm text-ink-600">
          {{
            t('lensAdmin.assistantDetail.archiveMessage', {
              name: archiveConfirmRow?.name || ''
            })
          }}
        </p>
        <template #footer>
          <BaseButton
            variant="danger"
            :loading="actionUuid === archiveConfirmRow?.uuid"
            @click="archive(archiveConfirmRow)"
          >
            {{ t('common.confirm') }}
          </BaseButton>
          <BaseButton
            variant="outline"
            class="mr-3"
            :disabled="actionUuid === archiveConfirmRow?.uuid"
            @click="closeArchiveConfirmation"
          >
            {{ t('common.cancel') }}
          </BaseButton>
        </template>
      </BaseModal>
    </div>
  </AdminLayout>
</template>

<script setup>
import {
  BookOpen,
  Database,
  Copy,
  Globe as GlobeIcon,
  Lock as LockIcon,
  Server
} from '@lucide/vue'
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'

import { llmAdminApi } from '@/admin/api/llmAdmin'
import { copyToClipboard } from '@/utils/clipboard'
import { extractErrorMessage } from '@/utils/api'
import { assistantChatUrl } from '@/utils/lens'
import AdminLayout from '@/admin/layout/AdminLayout.vue'
import {
  archiveAssistant,
  createAssistant,
  getAssistant,
  getPluginIcon,
  getPluginManifest,
  listAssistants,
  listDataSources,
  listDataSourceItems,
  listConnections,
  listGlobalSettings,
  listLensNodes,
  listMcpServers,
  listEnvironmentVariableSets,
  listPlugins,
  listSkills,
  restoreAssistant,
  updateAssistant
} from '@/api/lens'
import { useToast } from '@/composables/useToast'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseLoading from '@/components/ui/BaseLoading.vue'
import BaseModal from '@/components/ui/BaseModal.vue'
import PaginationBar from '@/components/ui/PaginationBar.vue'
import StatusBadge from '@/components/ui/StatusBadge.vue'

import AssistantDetailDrawer from './AssistantDetailDrawer.vue'
import AssistantFormDrawer from './AssistantFormDrawerDirectEnvironment.vue'
import { buildWorkspaceGuidePayload } from './assistantWorkspaceGuide'
import {
  buildMcpEnvironmentBinding,
  buildSkillEnvironmentBinding
} from './assistantEnvironment'
import {
  EMPTY_VALUE as emptyValue,
  formatAssistantType,
  listToText,
  normalizeList,
  selectedDirsFromValue,
  splitList
} from './adminHelpers'

const { t } = useI18n()
const { showSuccess, showError } = useToast()

const loading = ref(false)
const saving = ref(false)
const refreshingDirs = ref(false)
const showDrawer = ref(false)
const mode = ref('create')
const form = ref({})
const formError = ref('')
const showArchived = ref(false)
const archiveConfirmRow = ref(null)
const actionUuid = ref('')
const detailAssistant = ref(null)

const assistants = ref([])
const currentPage = ref(1)
const pageSize = ref(20)
const lensnodes = ref([])
const skills = ref([])
const environmentVariableSets = ref([])
const mcps = ref([])
const pluginConnections = ref([])
const pluginManifests = ref({})
const pluginIconUrls = ref({})
const globalSettings = ref([])
const llmConfigOptions = ref([])
const datasourceOptions = ref([])
let formResourcesPromise = null
let globalSettingsPromise = null

const activeColumns = computed(() =>
  [
    'assistant',
    'lensnode',
    'type',
    'tools',
    'visibility',
    'status',
    'shareUrl',
    'actions'
  ].map((column) => t(`lensAdmin.columns.${column}`))
)

const totalPages = computed(() =>
  Math.max(1, Math.ceil(assistants.value.length / pageSize.value))
)
const pagedAssistants = computed(() => {
  const start = (currentPage.value - 1) * pageSize.value
  return assistants.value.slice(start, start + pageSize.value)
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

function lensNodeName(value) {
  if (value?.lensnode_name) return value.lensnode_name
  const uuid = typeof value === 'object' ? value?.uuid : value
  const found = lensnodes.value.find((lensnode) => lensnode.uuid === uuid)
  return found?.name || uuid || emptyValue
}

function assistantTypeLabel(value) {
  return formatAssistantType(value, t)
}

function shareUrl(row) {
  return assistantChatUrl(row.slug, globalSettings.value)
}

function skillCountLabel(row) {
  return t('lensAdmin.assistantDetail.skillCount', {
    count: row.skill_summary?.enabled || 0
  })
}

function mcpCountLabel(row) {
  return t('lensAdmin.assistantDetail.mcpCount', {
    count: row.mcp_summary?.enabled || 0
  })
}

async function copyShareUrl(row) {
  await loadGlobalSettings()
  if (await copyToClipboard(shareUrl(row))) {
    showSuccess(t('lens.share.copied'))
  } else {
    showError(t('lens.share.copyFailed'))
  }
}

function selectedDirs() {
  return Array.isArray(form.value.selected_dirs) ? form.value.selected_dirs : []
}

async function load() {
  loading.value = true
  formError.value = ''
  try {
    const assistantRows = await listAssistants(
      showArchived.value ? { archived: true } : {}
    )
    assistants.value = normalizeList(assistantRows)
  } catch (error) {
    showError(extractErrorMessage(error, t('lensAdmin.messages.loadFailed')))
  } finally {
    loading.value = false
  }
}

async function loadGlobalSettings() {
  if (globalSettings.value.length) return
  if (!globalSettingsPromise) {
    globalSettingsPromise = listGlobalSettings()
      .then((rows) => {
        globalSettings.value = normalizeList(rows)
      })
      .finally(() => {
        globalSettingsPromise = null
      })
  }
  await globalSettingsPromise
}

async function loadFormResources() {
  if (formResourcesPromise) {
    await formResourcesPromise
    return
  }
  if (
    lensnodes.value.length ||
    skills.value.length ||
    mcps.value.length ||
    pluginConnections.value.length
  ) {
    return
  }

  formResourcesPromise = Promise.all([
    listLensNodes(),
    listDataSources({ page_size: 1000 }),
    listSkills(),
    listEnvironmentVariableSets(),
    listMcpServers(),
    listConnections({ status: 'active' }),
    listPlugins(),
    llmAdminApi.getLLMConfigAll({ scope: 'global' }).catch(() => [])
  ])
    .then(
      ([
        lensnodeRows,
        datasourceRows,
        skillRows,
        environmentVariableSetRows,
        mcpRows,
        connectionRows,
        installedPlugins,
        llmRows
      ]) => {
        lensnodes.value = normalizeList(lensnodeRows)
        skills.value = normalizeList(skillRows)
        environmentVariableSets.value = normalizeList(
          environmentVariableSetRows
        )
        mcps.value = normalizeList(mcpRows)
        pluginConnections.value = normalizeList(connectionRows)
        llmConfigOptions.value = normalizeList(llmRows)
        const plugins = normalizeList(installedPlugins)
        return Promise.all([
          Promise.all(normalizeList(datasourceRows).map(async (source) => ({
            ...source, items: await listDataSourceItems(source.uuid)
          }))).then((sources) => { datasourceOptions.value = sources }),
          Promise.all(plugins.map((plugin) => getPluginManifest(plugin.key))),
          loadPluginIcons(plugins)
        ]).then(([, manifests]) => {
          pluginManifests.value = Object.fromEntries(
            manifests.map((manifest) => [manifest.key, manifest])
          )
        })
      }
    )
    .finally(() => {
      formResourcesPromise = null
    })
  await formResourcesPromise
}

async function loadPluginIcons(plugins) {
  revokePluginIconUrls()
  const entries = await Promise.all(
    plugins.map(async (plugin) => {
      if (!plugin.icon_url) return [plugin.key, '']
      try {
        const blob = await getPluginIcon(plugin.key)
        return [plugin.key, URL.createObjectURL(blob)]
      } catch {
        return [plugin.key, '']
      }
    })
  )
  pluginIconUrls.value = Object.fromEntries(entries)
}

function revokePluginIconUrls() {
  Object.values(pluginIconUrls.value).forEach((url) => {
    if (url) URL.revokeObjectURL(url)
  })
  pluginIconUrls.value = {}
}

async function switchArchiveView(archived) {
  if (showArchived.value === archived) return
  showArchived.value = archived
  archiveConfirmRow.value = null
  detailAssistant.value = null
  currentPage.value = 1
  assistants.value = []
  await load()
}

async function startCreate() {
  await loadFormResources()
  detailAssistant.value = null
  mode.value = 'create'
  formError.value = ''
  form.value = defaultForm()
  showDrawer.value = true
}

async function startEdit(row) {
  const [assistant] = await Promise.all([
    getAssistant(row.uuid),
    loadFormResources()
  ])
  mode.value = 'edit'
  formError.value = ''
  form.value = formFromRow(assistant)
  showDrawer.value = true
}

async function openDetails(row) {
  detailAssistant.value = await getAssistant(row.uuid)
}

function closeDetails() {
  detailAssistant.value = null
}

function startEditFromDetail(row) {
  closeDetails()
  startEdit(row)
}

function selectedVisionModelIsEligible(modelRef) {
  if (!modelRef) return true
  const config = llmConfigOptions.value.find((item) => item.uuid === modelRef)
  if (!config || config.is_active === false) return false
  const declared = config.config?.supports_vision ?? config.config?.vision
  return (
    config.vision_capability === 'supported' ||
    config.supports_vision === true ||
    declared === true ||
    config.capabilities?.includes?.('vision')
  )
}

function requestArchive(row) {
  archiveConfirmRow.value = row
}

function closeArchiveConfirmation() {
  if (actionUuid.value) return
  archiveConfirmRow.value = null
}

function closeDrawer() {
  showDrawer.value = false
  form.value = {}
  formError.value = ''
}

async function refreshDirs() {
  if (!form.value.lensnode_uuid) return
  refreshingDirs.value = true
  try {
    lensnodes.value = normalizeList(await listLensNodes())
  } catch {
    showError(t('lensAdmin.messages.loadFailed'))
  } finally {
    refreshingDirs.value = false
  }
}

function defaultForm() {
  return {
    name: '',
    description: '',
    capability: '',
    slug: '',
    lensnode_uuid: '',
    selected_dirs: [],
    datasource_bindings: [],
    agent_model_ref: '',
    agent_rounds: 'balanced',
    max_concurrency: 5,
    multimodal_model_ref: '',
    exclude_extensions_text: '.lock,.pyc,.sqlite3',
    exclude_dirs_text: '.git,.venv,__pycache__,node_modules,dist,build',
    workspace_guide_overview: '',
    pre_prompt: '',
    post_prompt: '',
    skill_uuids: [],
    skill_environment_set_uuids: {},
    skill_environment_drafts: {},
    mcp_uuids: [],
    mcp_environment_set_uuids: {},
    mcp_environment_drafts: {},
    plugin_bindings: [],
    visibility: 'private',
    access_group_ids: [],
    access_user_ids: [],
    access_grant_options: [],
    settings: {},
    enable_codegraph: true,
    status: 'active',
    mode: 'direct',
    collaboration_member_uuids: []
  }
}

function workspaceGuideSkillUuids() {
  return new Set(
    skills.value.filter((s) => s.kind === 'workspace_guide').map((s) => s.uuid)
  )
}

function formFromRow(row) {
  const wgUuids = workspaceGuideSkillUuids()
  return {
    uuid: row.uuid,
    name: row.name || '',
    description: row.description || '',
    capability: row.capability || 'general_chat',
    mode: row.mode || row.routing_mode || 'direct',
    collaboration_member_uuids: (row.collaboration_members || [])
      .map((member) => member.uuid)
      .filter(Boolean),
    slug: row.slug || '',
    lensnode_uuid: row.lensnode?.uuid || row.lensnode || '',
    selected_dirs: selectedDirsFromValue(row.selected_dirs || []),
    datasource_bindings: Array.isArray(row.datasource_bindings)
      ? row.datasource_bindings
      : [],
    agent_model_ref: row.agent_model_ref || '',
    agent_rounds: row.agent_rounds || 'balanced',
    max_concurrency: row.max_concurrency ?? 5,
    multimodal_model_ref: row.multimodal_model_ref || '',
    exclude_extensions_text: listToText(
      row.settings?.retrieval_policy?.exclude_extensions || [
        '.lock',
        '.pyc',
        '.sqlite3'
      ]
    ),
    exclude_dirs_text: listToText(
      row.settings?.retrieval_policy?.exclude_dirs || [
        '.git',
        '.venv',
        '__pycache__',
        'node_modules',
        'dist',
        'build'
      ]
    ),
    workspace_guide_overview: row.workspace_guide?.content || '',
    pre_prompt: row.settings?.pre_prompt || '',
    post_prompt: row.settings?.post_prompt || '',
    skill_uuids: (row.skill_bindings || [])
      .map((b) => b.skill?.uuid || b.skill_uuid)
      .filter((u) => u && !wgUuids.has(u)),
    skill_environment_set_uuids: Object.fromEntries(
      (row.skill_bindings || [])
        .filter((binding) => binding.environment_variable_set_uuid)
        .map((binding) => [
          binding.skill_uuid,
          binding.environment_variable_set_uuid
        ])
    ),
    skill_environment_drafts: {},
    mcp_uuids: (row.mcp_bindings || [])
      .map((b) => b.mcp_server?.uuid || b.mcp_uuid)
      .filter(Boolean),
    mcp_environment_set_uuids: Object.fromEntries(
      (row.mcp_bindings || [])
        .filter((binding) => binding.environment_variable_set_uuid)
        .map((binding) => [
          binding.mcp_uuid,
          binding.environment_variable_set_uuid
        ])
    ),
    mcp_environment_drafts: {},
    plugin_bindings: (row.plugin_bindings || []).map((binding) => ({
      connection_uuid: binding.connection_uuid,
      enabled: binding.enabled !== false
    })),
    visibility: row.visibility || 'public',
    access_group_ids: (row.access_grants || [])
      .filter((g) => g.type === 'group')
      .map((g) => g.id),
    access_user_ids: (row.access_grants || [])
      .filter((g) => g.type === 'user')
      .map((g) => g.id),
    access_grant_options: row.access_grants || [],
    settings: { ...(row.settings || {}) },
    enable_codegraph: row.settings?.features?.codegraph !== false,
    status: row.status || 'active'
  }
}

async function save() {
  saving.value = true
  formError.value = ''
  try {
    if (!selectedVisionModelIsEligible(form.value.multimodal_model_ref)) {
      formError.value = t('lensAdmin.messages.invalidVisionModel')
      showError(formError.value)
      return
    }
    const payload = buildPayload()
    const uuid = form.value.uuid
    await saveByMode(uuid, payload, createAssistant, updateAssistant)
    showSuccess(t('lensAdmin.messages.saveSuccess'))
    closeDrawer()
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

async function saveByMode(uuid, payload, createFn, updateFn) {
  if (mode.value === 'create') {
    return createFn(payload)
  } else {
    return updateFn(uuid, payload)
  }
}

function buildPayload() {
  const guideContent = (form.value.workspace_guide_overview || '').trim()
  return {
    name: form.value.name,
    description: form.value.description?.trim() || '',
    capability: form.value.capability || 'general_chat',
    mode: form.value.mode || 'direct',
    ...(form.value.mode === 'smart'
      ? {
          collaboration_member_uuids: [
            ...(form.value.collaboration_member_uuids || [])
          ]
        }
      : {}),
    slug: form.value.slug?.trim() || '',
    ...(form.value.lensnode_uuid
      ? { lensnode_uuid: form.value.lensnode_uuid }
      : {}),
    datasource_bindings: form.value.datasource_bindings || [],
    selected_dirs:
      form.value.capability === 'general_chat' ? [] : buildSelectedDirs(),
    agent_model_ref: form.value.agent_model_ref || null,
    agent_rounds: form.value.agent_rounds || 'balanced',
    ...(mode.value === 'edit'
      ? { max_concurrency: Number(form.value.max_concurrency) || 5 }
      : {}),
    multimodal_model_ref: form.value.multimodal_model_ref || null,
    settings: buildAssistantSettings(),
    workspace_guide: {
      ...buildWorkspaceGuidePayload({ content: guideContent })
    },
    skill_bindings: (form.value.skill_uuids || []).map((uuid) => {
      const skill = skills.value.find((item) => item.uuid === uuid) || {
        uuid,
        definition: {}
      }
      return buildSkillEnvironmentBinding(
        skill,
        form.value.skill_environment_set_uuids?.[uuid],
        form.value.skill_environment_drafts?.[uuid]
      )
    }),
    mcp_bindings: (form.value.mcp_uuids || []).map((uuid) => {
      const mcp = mcps.value.find((item) => item.uuid === uuid) || {
        uuid,
        environment: []
      }
      return buildMcpEnvironmentBinding(
        mcp,
        form.value.mcp_environment_set_uuids?.[uuid],
        form.value.mcp_environment_drafts?.[uuid]
      )
    }),
    plugin_bindings:
      form.value.mode === 'direct'
        ? (form.value.plugin_bindings || []).map((binding) => ({
            connection_uuid: binding.connection_uuid,
            enabled: binding.enabled !== false
          }))
        : [],
    visibility: form.value.visibility || 'public',
    access_grants: buildAccessGrants(),
    status: form.value.status || 'active'
  }
}

function buildAccessGrants() {
  if (form.value.visibility !== 'private') {
    return []
  }
  return [
    ...(form.value.access_group_ids || []).map((id) => ({
      type: 'group',
      id
    })),
    ...(form.value.access_user_ids || []).map((id) => ({
      type: 'user',
      id
    }))
  ]
}

function buildAssistantSettings() {
  const settings = { ...(form.value.settings || {}) }
  const retrievalPolicy = {}
  const excludeExtensions = splitList(form.value.exclude_extensions_text)
  const excludeDirs = splitList(form.value.exclude_dirs_text)
  if (excludeExtensions.length) {
    retrievalPolicy.exclude_extensions = excludeExtensions
  }
  if (excludeDirs.length) {
    retrievalPolicy.exclude_dirs = excludeDirs
  }
  settings.retrieval_policy = retrievalPolicy
  if (form.value.pre_prompt?.trim()) {
    settings.pre_prompt = form.value.pre_prompt.trim()
  } else {
    delete settings.pre_prompt
  }
  if (form.value.post_prompt?.trim()) {
    settings.post_prompt = form.value.post_prompt.trim()
  } else {
    delete settings.post_prompt
  }
  const features = { ...(settings.features || {}) }
  features.codegraph = !!form.value.enable_codegraph
  settings.features = features
  return settings
}

function buildSelectedDirs() {
  return selectedDirs().map((dir) => {
    const includePaths = String(dir.include_paths_text || '')
      .split('\n')
      .map((item) => item.trim())
      .filter(Boolean)
    if (!includePaths.length) {
      return { path: dir.path }
    }
    return {
      path: dir.path,
      retrieval_scope: { include_paths: includePaths }
    }
  })
}

async function archive(row) {
  if (!row) return
  actionUuid.value = row.uuid
  try {
    await archiveAssistant(row.uuid)
    showSuccess(t('lensAdmin.messages.archiveSuccess'))
    archiveConfirmRow.value = null
    await load()
  } catch (error) {
    showError(extractErrorMessage(error, t('lensAdmin.messages.archiveFailed')))
  } finally {
    actionUuid.value = ''
  }
}

async function restore(row) {
  actionUuid.value = row.uuid
  try {
    await restoreAssistant(row.uuid)
    showSuccess(t('lensAdmin.messages.restoreSuccess'))
    await load()
  } catch (error) {
    showError(extractErrorMessage(error, t('lensAdmin.messages.restoreFailed')))
  } finally {
    actionUuid.value = ''
  }
}

onMounted(load)
onBeforeUnmount(revokePluginIconUrls)
</script>

<style scoped>
.assistants-table-wrap {
  max-width: 100%;
}

.table-head {
  @apply border-b border-line px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-ink-500;
}

.table-cell {
  @apply px-4 py-4 text-sm text-ink-700;
}

.assistant-name-cell {
  max-width: 0;
  overflow: hidden;
}

.assistant-slug {
  display: block;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.assistant-actions-cell {
  overflow: hidden;
  white-space: nowrap;
}

.table-cell.assistant-type-column,
.table-head.assistant-type-column {
  white-space: nowrap;
  word-break: keep-all;
  padding-left: clamp(0.375rem, 0.9vw, 0.5rem);
  padding-right: clamp(2.25rem, 4vw, 3rem);
}

.tool-count {
  @apply inline-flex items-center gap-1 text-xs font-medium text-ink-600;
}

.tool-count-empty {
  @apply text-ink-300;
}

.assistant-name-action {
  @apply font-medium text-ink-900 transition-colors;
  @apply hover:text-primary-700 hover:underline;
}
</style>
