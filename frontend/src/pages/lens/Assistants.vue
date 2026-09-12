<template>
  <AdminLayout>
    <div class="assistants-page flex max-w-full flex-col gap-4 py-4">
      <section class="assistant-list-panel admin-data-panel">
        <header
          class="flex flex-col gap-4 border-b border-line px-5 py-4 lg:flex-row lg:items-start lg:justify-between"
        >
          <div>
            <div class="flex flex-wrap items-center gap-2">
              <h1 class="admin-page-title">
                {{ t('lensAdmin.pages.assistants.managementTitle') }}
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
              <RefreshCw :size="16" aria-hidden="true" />
              {{ t('common.refresh') }}
            </BaseButton>
            <BaseButton
              v-if="!showArchived"
              variant="primary"
              size="sm"
              @click="startCreate"
            >
              <Plus :size="16" aria-hidden="true" />
              {{ t('lensAdmin.pages.assistants.action') }}
            </BaseButton>
          </div>
        </header>

        <div class="flex items-center gap-6 border-b border-line px-5">
          <button
            type="button"
            class="segment-tab"
            :class="{ 'segment-tab-active': !showArchived }"
            :aria-pressed="!showArchived"
            @click="switchArchiveView(false)"
          >
            {{ t('lensAdmin.pages.assistants.active') }}
            <small>{{ activeCount }}</small>
          </button>
          <button
            type="button"
            class="segment-tab"
            :class="{ 'segment-tab-active': showArchived }"
            :aria-pressed="showArchived"
            @click="switchArchiveView(true)"
          >
            {{ t('lensAdmin.pages.assistants.archived') }}
            <small>{{ archivedCount }}</small>
          </button>
        </div>

        <div
          class="assistant-toolbar flex flex-wrap items-end gap-3 border-b border-line px-5 py-4"
        >
          <label class="filter-control filter-search">
            <span>{{ t('lensAdmin.pages.assistants.searchLabel') }}</span>
            <span class="filter-input-wrap">
              <Search :size="16" aria-hidden="true" />
              <input
                v-model="searchQuery"
                type="search"
                :placeholder="t('lensAdmin.pages.assistants.searchPlaceholder')"
                autocomplete="off"
                @input="resetPage"
              />
            </span>
          </label>
          <div class="filter-control">
            <span>{{ t('lensAdmin.pages.assistants.typeLabel') }}</span>
            <BaseSelect
              v-model="typeFilter"
              size="md"
              :aria-label="t('lensAdmin.pages.assistants.typeLabel')"
              @change="resetPage"
            >
              <option value="">
                {{ t('lensAdmin.pages.assistants.allTypes') }}
              </option>
              <option value="general_chat">
                {{ assistantTypeLabel('general_chat') }}
              </option>
              <option value="knowledge_qa">
                {{ assistantTypeLabel('knowledge_qa') }}
              </option>
              <option value="code_analysis">
                {{ assistantTypeLabel('code_analysis') }}
              </option>
              <option value="smart">
                {{ t('lensAdmin.routingModes.smart') }}
              </option>
            </BaseSelect>
          </div>
          <div class="filter-control">
            <span>{{ t('lensAdmin.pages.assistants.visibilityLabel') }}</span>
            <BaseSelect
              v-model="visibilityFilter"
              size="md"
              :aria-label="t('lensAdmin.pages.assistants.visibilityLabel')"
              @change="resetPage"
            >
              <option value="">
                {{ t('lensAdmin.pages.assistants.allVisibility') }}
              </option>
              <option value="private">
                {{ t('lensAdmin.visibility.private') }}
              </option>
              <option value="public">
                {{ t('lensAdmin.visibility.public') }}
              </option>
            </BaseSelect>
          </div>
          <BaseButton
            v-if="hasFilters"
            variant="ghost"
            size="sm"
            @click="clearFilters"
          >
            {{ t('lensAdmin.pages.assistants.resetFilters') }}
          </BaseButton>
        </div>

        <div class="px-5 py-4">
          <BaseLoading v-if="loading && assistants.length === 0" />

          <div v-else-if="assistants.length === 0" class="empty-state">
            <Bot :size="28" aria-hidden="true" />
            <h2>
              {{
                t(
                  showArchived
                    ? 'lensAdmin.pages.assistants.emptyArchived'
                    : 'lensAdmin.pages.assistants.emptyActive'
                )
              }}
            </h2>
            <p>{{ t('lensAdmin.pages.assistants.emptyHint') }}</p>
          </div>

          <div v-else-if="filteredAssistants.length === 0" class="empty-state">
            <Search :size="28" aria-hidden="true" />
            <h2>{{ t('lensAdmin.pages.assistants.noFilterResults') }}</h2>
            <p>{{ t('lensAdmin.pages.assistants.noFilterHint') }}</p>
            <BaseButton variant="outline" size="sm" @click="clearFilters">{{
              t('lensAdmin.pages.assistants.resetFilters')
            }}</BaseButton>
          </div>

          <div
            v-else
            class="assistants-table-wrap overflow-x-auto rounded-lg border border-line bg-surface"
          >
            <table
              class="assistants-table w-full table-fixed divide-y divide-line"
            >
              <colgroup>
                <col style="width: 24%" />
                <col style="width: 13%" />
                <col style="width: 23%" />
                <col style="width: 12%" />
                <col style="width: 10%" />
                <col style="width: 18%" />
              </colgroup>
              <thead class="bg-surface-sunken">
                <tr>
                  <th
                    scope="col"
                    v-for="column in activeColumns"
                    :key="column"
                    class="table-head"
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
                    <div class="assistant-name-row">
                      <span
                        class="assistant-glyph"
                        :class="{
                          'assistant-glyph-smart':
                            (row.mode || row.routing_mode) === 'smart'
                        }"
                      >
                        <UsersRound
                          v-if="(row.mode || row.routing_mode) === 'smart'"
                          :size="18"
                          aria-hidden="true"
                        />
                        <Bot v-else :size="18" aria-hidden="true" />
                      </span>
                      <div class="min-w-0">
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
                      </div>
                    </div>
                  </td>
                  <td
                    class="table-cell text-ink-600"
                    :data-label="t('lensAdmin.columns.type')"
                  >
                    <div class="font-medium text-ink-800">
                      {{
                        (row.mode || row.routing_mode) === 'smart'
                          ? t('lensAdmin.routingModes.smart')
                          : assistantTypeLabel(row.capability)
                      }}
                    </div>
                  </td>
                  <td
                    class="table-cell text-ink-600"
                    :data-label="t('lensAdmin.columns.dataAndTools')"
                  >
                    <div
                      data-testid="assistant-tool-counts"
                      class="flex flex-wrap items-center gap-2"
                    >
                      <span
                        class="data-tool-count"
                        :aria-label="`${t('lensAdmin.columns.datasource')} ${(row.datasource_bindings || []).length}`"
                      >
                        <Database :size="16" aria-hidden="true" />
                        <span>{{
                          (row.datasource_bindings || []).length
                        }}</span>
                      </span>
                      <span
                        class="data-tool-count"
                        :class="{
                          'tool-count-empty': !row.skill_summary?.enabled
                        }"
                        :title="skillCountLabel(row)"
                        :aria-label="skillCountLabel(row)"
                      >
                        <BookOpen :size="16" aria-hidden="true" />
                        <span>{{ row.skill_summary?.enabled || 0 }}</span>
                      </span>
                      <span
                        class="data-tool-count"
                        :class="{
                          'tool-count-empty': !row.mcp_summary?.enabled
                        }"
                        :title="mcpCountLabel(row)"
                        :aria-label="mcpCountLabel(row)"
                      >
                        <Server :size="16" aria-hidden="true" />
                        <span>{{ row.mcp_summary?.enabled || 0 }}</span>
                      </span>
                      <span
                        v-if="row.plugin_summary?.enabled"
                        class="data-tool-count"
                        :title="`${t('lensAdmin.assistantPresentation.plugins')} ${row.plugin_summary.enabled}`"
                      >
                        <Plug :size="16" aria-hidden="true" />
                        <span>{{ row.plugin_summary.enabled }}</span>
                      </span>
                    </div>
                  </td>
                  <td
                    class="table-cell"
                    :data-label="t('lensAdmin.columns.visibility')"
                  >
                    <span
                      class="visibility-chip"
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
                  <td
                    class="table-cell"
                    :data-label="t('lensAdmin.columns.status')"
                  >
                    <StatusBadge :status="row.status" />
                  </td>
                  <td class="table-cell assistant-actions-cell">
                    <div class="flex flex-wrap items-center gap-2">
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
            :total="filteredAssistants.length"
            @page-size-change="handlePageSizeChange"
            @prev="goPrevPage"
            @next="goNextPage"
          />
        </div>
      </section>
    </div>

    <AssistantDetailDrawer
      :show="Boolean(detailAssistant)"
      :assistant="detailAssistant"
      :lensnode-name="lensNodeName(detailAssistant)"
      :assistant-type="
        (detailAssistant?.mode || detailAssistant?.routing_mode) === 'smart'
          ? t('lensAdmin.routingModes.smart')
          : assistantTypeLabel(detailAssistant?.capability)
      "
      @close="closeDetails"
      @copy-share="copyShareUrl"
      @edit="startEditFromDetail"
      @restore="restore"
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
  </AdminLayout>
</template>

<script setup>
import {
  Bot,
  BookOpen,
  Database,
  Plug,
  Globe as GlobeIcon,
  Lock as LockIcon,
  Plus,
  RefreshCw,
  Search,
  Server,
  UsersRound
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
import BaseSelect from '@/components/ui/BaseSelect.vue'
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
const formBaseline = ref('')
const showArchived = ref(false)
const archiveConfirmRow = ref(null)
const actionUuid = ref('')
const detailAssistant = ref(null)

const assistants = ref([])
const currentPage = ref(1)
const pageSize = ref(20)
const searchQuery = ref('')
const typeFilter = ref('')
const visibilityFilter = ref('')
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
  ['assistant', 'type', 'dataAndTools', 'visibility', 'status', 'actions'].map(
    (column) =>
      column === 'dataAndTools'
        ? t('lensAdmin.columns.dataAndTools')
        : t(`lensAdmin.columns.${column}`)
  )
)

const activeRows = computed(() =>
  assistants.value.filter(
    (row) => row.status === 'active' || (!row.status && !showArchived.value)
  )
)
const archivedRows = computed(() =>
  assistants.value.filter(
    (row) => row.status !== 'active' && (row.status || showArchived.value)
  )
)
const activeCount = computed(() => activeRows.value.length)
const archivedCount = computed(() => archivedRows.value.length)
const hasFilters = computed(() =>
  Boolean(searchQuery.value || typeFilter.value || visibilityFilter.value)
)
const filteredAssistants = computed(() => {
  const query = searchQuery.value.trim().toLowerCase()
  return assistants.value.filter((row) => {
    const modeValue = row.mode || row.routing_mode || 'direct'
    const matchesQuery =
      !query ||
      [row.name, row.slug, row.description]
        .filter(Boolean)
        .join(' ')
        .toLowerCase()
        .includes(query)
    const matchesType =
      !typeFilter.value ||
      (typeFilter.value === 'smart'
        ? modeValue === 'smart'
        : modeValue !== 'smart' && row.capability === typeFilter.value)
    const matchesVisibility =
      !visibilityFilter.value || row.visibility === visibilityFilter.value
    return matchesQuery && matchesType && matchesVisibility
  })
})
const totalPages = computed(() =>
  Math.max(1, Math.ceil(filteredAssistants.value.length / pageSize.value))
)
const pagedAssistants = computed(() => {
  const start = (currentPage.value - 1) * pageSize.value
  return filteredAssistants.value.slice(start, start + pageSize.value)
})

function resetPage() {
  currentPage.value = 1
}

function clearFilters() {
  searchQuery.value = ''
  typeFilter.value = ''
  visibilityFilter.value = ''
  resetPage()
}

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
  const uuid = typeof value === 'object' ? value?.lensnode : value
  const found = lensnodes.value.find((lensnode) => lensnode.uuid === uuid)
  return (
    found?.name ||
    (uuid
      ? t('lensAdmin.assistantPresentation.nodeUnavailable')
      : t('lensAdmin.assistantPresentation.automaticNode'))
  )
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
          Promise.all(
            normalizeList(datasourceRows).map(async (source) => ({
              ...source,
              items: await listDataSourceItems(source.uuid)
            }))
          ).then((sources) => {
            datasourceOptions.value = sources
          }),
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
  clearFilters()
  assistants.value = []
  await load()
}

async function startCreate() {
  await loadFormResources()
  detailAssistant.value = null
  mode.value = 'create'
  formError.value = ''
  form.value = defaultForm()
  formBaseline.value = serializeForm(form.value)
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
  formBaseline.value = serializeForm(form.value)
  showDrawer.value = true
}

async function openDetails(row) {
  try {
    detailAssistant.value = await getAssistant(row.uuid)
  } catch (error) {
    showError(extractErrorMessage(error, t('lensAdmin.messages.loadFailed')))
  }
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
  if (
    !saving.value &&
    showDrawer.value &&
    serializeForm(form.value) !== formBaseline.value &&
    !window.confirm(t('lensAdmin.messages.unsavedChanges'))
  ) {
    return
  }
  showDrawer.value = false
  form.value = {}
  formError.value = ''
  formBaseline.value = ''
}

function serializeForm(value) {
  return JSON.stringify(value, (_, item) => {
    if (item && typeof item === 'object' && !Array.isArray(item)) {
      return Object.keys(item)
        .sort()
        .reduce((result, key) => {
          result[key] = item[key]
          return result
        }, {})
    }
    return item
  })
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
    settings: { datasource_routing: 'selected' },
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
    settings: { ...(row.settings || {}), datasource_routing: 'selected' },
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
    datasource_bindings:
      form.value.mode === 'smart' ? [] : form.value.datasource_bindings || [],
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
  settings.datasource_routing = 'selected'
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
    if (detailAssistant.value?.uuid === row.uuid) {
      detailAssistant.value = await getAssistant(row.uuid)
    }
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
.assistants-page {
  color: var(--sl-text-primary);
}

.segment-tab {
  min-height: 3.5rem;
  border: 0;
  border-bottom: 2px solid transparent;
  background: transparent;
  color: var(--sl-text-muted);
  font-size: 0.875rem;
  font-weight: 500;
}

.segment-tab small {
  padding-left: 0.25rem;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 0.75rem;
}

.segment-tab:hover,
.segment-tab-active {
  color: var(--sl-brand-strong);
}

.segment-tab-active {
  border-bottom-color: var(--sl-brand-strong);
  font-weight: 650;
}

.filter-control {
  display: grid;
  min-width: 9.5rem;
  gap: 0.375rem;
  color: var(--sl-text-muted);
  font-size: 0.75rem;
}

.filter-search {
  min-width: 15rem;
  flex: 1 1 18rem;
  max-width: 27rem;
}

.filter-control select,
.filter-input-wrap {
  min-height: 2.75rem;
  border: 1px solid var(--sl-border-default);
  border-radius: 0.5rem;
  background: var(--sl-bg-surface);
}

.filter-control :deep(button[role='combobox']) {
  min-height: 2.75rem;
}

.filter-control select {
  width: 100%;
  padding: 0.625rem 0.75rem;
  color: var(--sl-text-secondary);
  font-size: 0.875rem;
}

.filter-input-wrap {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0 0.75rem;
}

.filter-input-wrap:focus-within {
  border-color: var(--sl-accent);
  box-shadow: 0 0 0 3px rgb(var(--sl-accent-rgb) / 0.15);
}

.filter-input-wrap svg {
  flex: none;
  color: var(--sl-text-subtle);
}

.filter-input-wrap input {
  min-width: 0;
  flex: 1;
  border: 0 !important;
  outline: 0 !important;
  box-shadow: none !important;
}

.empty-state {
  display: grid;
  justify-items: center;
  gap: 0.5rem;
  padding: 3.5rem 1.5rem;
  text-align: center;
}

.empty-state > svg {
  margin-bottom: 0.5rem;
  color: var(--sl-text-subtle);
}

.empty-state h2 {
  color: var(--sl-text-secondary);
  font-size: 1.125rem;
  font-weight: 600;
}

.empty-state p {
  color: var(--sl-text-muted);
  font-size: 0.875rem;
}

.assistant-name-row {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  min-width: 0;
}

.assistant-glyph {
  display: grid;
  width: 2.25rem;
  height: 2.25rem;
  flex: none;
  place-items: center;
  border: 1px solid var(--sl-border-default);
  border-radius: 0.5rem;
  background: var(--sl-bg-surface);
  color: var(--sl-text-secondary);
}

.assistant-glyph-smart {
  border-color: transparent;
  background: var(--sl-brand-soft);
  color: var(--sl-brand-strong);
}

.visibility-chip {
  display: inline-flex;
  align-items: center;
  gap: 0.25rem;
  border: 1px solid;
  border-radius: 9999px;
  padding: 0.125rem 0.5rem;
  font-size: 0.75rem;
  font-weight: 600;
  white-space: nowrap;
}

.data-tool-count {
  position: relative;
  display: inline-flex;
  align-items: center;
  gap: 0.25rem;
  border-radius: 0.375rem;
  padding: 0.25rem 0.4rem;
  font-size: 0.75rem;
  font-weight: 600;
  color: var(--sl-text-secondary);
  background: var(--sl-bg-canvas);
}

.assistants-table-wrap {
  max-width: 100%;
}

.table-head {
  @apply border-b border-line px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-ink-500;
}

.table-cell {
  @apply px-4 py-3 align-top text-sm text-ink-700;
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

.tool-count {
  @apply inline-flex items-center gap-1 whitespace-nowrap text-xs font-medium text-ink-600;
}

.tool-count-empty {
  @apply text-ink-300;
}

.assistant-name-action {
  @apply font-medium text-ink-900 transition-colors;
  @apply hover:text-primary-700 hover:underline;
}
.assistants-table {
  min-width: 64rem;
}

@media (max-width: 767px) {
  .assistant-toolbar {
    align-items: stretch;
  }

  .filter-control,
  .filter-search {
    min-width: 0;
    max-width: none;
    flex: 1 1 100%;
  }

  .assistants-table {
    display: block;
    min-width: 0;
  }
  .assistants-table colgroup,
  .assistants-table thead {
    display: none;
  }
  .assistants-table tbody {
    display: block;
  }
  .assistants-table tr {
    display: grid;
    grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
    padding: 12px;
    gap: 16px;
  }
  .table-cell {
    display: block;
    padding: 0;
    min-width: 0;
  }
  .table-cell::before {
    content: attr(data-label);
    display: block;
    margin-bottom: 6px;
    font-size: 12px;
    color: var(--sl-text-muted);
  }
  .assistant-name-cell,
  .assistant-actions-cell {
    grid-column: 1 / -1;
    max-width: none;
  }
  .assistant-actions-cell {
    border-top: 1px solid;
    border-color: inherit;
    padding-top: 12px;
  }
}
</style>
