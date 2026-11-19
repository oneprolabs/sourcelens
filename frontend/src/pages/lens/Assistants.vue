<template>
  <component :is="embedded ? 'div' : AdminLayout" :class="{ hidden: embedded }">
    <div class="assistants-page">
      <header class="admin-page-heading">
        <div class="admin-heading-copy">
          <h1>
            {{ t('lensAdmin.pages.assistants.title')
            }}<span class="admin-heading-count">{{ activeCount }}</span>
          </h1>
          <p>{{ t('lensAdmin.pages.assistants.description') }}</p>
        </div>
        <BaseButton
          v-if="!showArchived"
          :disabled="readonly"
          @click="startCreate"
        >
          <Plus :size="14" aria-hidden="true" />{{
            t('lensAdmin.pages.assistants.action')
          }}
        </BaseButton>
      </header>
      <section class="assistant-list-panel">
        <div
          v-if="attentionCount && !showArchived"
          class="assistant-risk-banner"
        >
          <span>{{
            t('lensAdmin.managementChecks.affected', { count: attentionCount })
          }}</span>
          <BaseButton
            v-for="field in affectedModelFields"
            :key="field"
            variant="outline"
            size="sm"
            :disabled="loading || readonly"
            @click="replaceAffectedModels(field)"
            >{{
              t('lensAdmin.managementChecks.replaceSlot', {
                slot: t(`lensAdmin.managementChecks.${field}`)
              })
            }}</BaseButton
          >
        </div>

        <div class="assistant-view-toolbar">
          <div
            class="assistant-status-tabs"
            :aria-label="t('lensAdmin.pages.assistants.viewSelector')"
          >
            <button
              type="button"
              :aria-pressed="
                !showArchived && !attentionOnly && !enablementFilter
              "
              @click="switchArchiveView(false)"
            >
              {{ t('lensAdmin.enablement.all') }}
              <small>{{ activeCount }}</small>
            </button>
            <button
              v-for="value in ['enabled', 'disabled']"
              :key="value"
              type="button"
              :aria-pressed="enablementFilter === value"
              @click="showEnablementView(value)"
            >
              {{ t(`lensAdmin.enablement.${value}`) }}
              <small>{{
                value === 'enabled' ? enabledCount : disabledCount
              }}</small>
            </button>
            <button
              type="button"
              :aria-pressed="attentionOnly"
              @click="showAttention"
            >
              {{ t('lensAdmin.managementChecks.attention') }}
              <small>{{ attentionCount }}</small>
            </button>
            <button
              type="button"
              :aria-pressed="showArchived"
              @click="switchArchiveView(true)"
            >
              {{ t('lensAdmin.pages.assistants.archived') }}
              <small>{{ archivedCount }}</small>
            </button>
          </div>
          <label class="admin-toolbar-search assistant-list-search">
            <Search :size="14" aria-hidden="true" />
            <input
              v-model="searchQuery"
              type="search"
              :aria-label="t('lensAdmin.pages.assistants.searchLabel')"
              :placeholder="t('lensAdmin.pages.assistants.searchPlaceholder')"
              autocomplete="off"
              @input="resetPage"
            />
          </label>
          <BaseButton variant="outline" size="sm" @click="showBatchTable">{{
            t('lensAdmin.assistantList.batchTable')
          }}</BaseButton>
          <BaseButton
            variant="ghost"
            size="sm"
            :aria-expanded="showFilters"
            @click="showFilters = !showFilters"
            ><SlidersHorizontal :size="14" aria-hidden="true" />{{
              t('lensAdmin.assistantList.filters')
            }}</BaseButton
          >
          <BaseButton
            variant="ghost"
            size="sm"
            :aria-label="t('common.refresh')"
            :loading="loading"
            @click="load"
            ><RefreshCw :size="14" aria-hidden="true"
          /></BaseButton>
          <div class="assistant-view-toggle">
            <button
              type="button"
              :aria-label="t('lensAdmin.assistantList.cardView')"
              :aria-pressed="listView === 'cards'"
              @click="listView = 'cards'"
            >
              <Layers :size="15" aria-hidden="true" />
            </button>
            <button
              type="button"
              :aria-label="t('lensAdmin.assistantList.tableView')"
              :aria-pressed="listView === 'table'"
              @click="listView = 'table'"
            >
              <List :size="15" aria-hidden="true" />
            </button>
          </div>
        </div>
        <div
          v-if="showFilters"
          class="assistant-toolbar assistant-extra-filters"
        >
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
              <option value="tenant">
                {{ t('lensAdmin.visibility.tenant') }}
              </option>
              <option value="public">
                {{ t('lensAdmin.visibility.public') }}
              </option>
            </BaseSelect>
          </div>
          <div class="filter-control">
            <span>{{ t('lensAdmin.managementChecks.modelFilter') }}</span>
            <BaseSelect
              v-model="modelFilter"
              :aria-label="t('lensAdmin.managementChecks.modelFilter')"
              @change="resetPage"
            >
              <option value="">
                {{ t('lensAdmin.managementChecks.allModels') }}
              </option>
              <option
                v-for="model in listModels"
                :key="model.uuid"
                :value="model.uuid"
              >
                {{ model.name }}
              </option>
            </BaseSelect>
          </div>
          <div class="filter-control">
            <span>{{ t('lensAdmin.managementChecks.nodeFilter') }}</span>
            <BaseSelect
              v-model="nodeFilter"
              :aria-label="t('lensAdmin.managementChecks.nodeFilter')"
              @change="resetPage"
            >
              <option value="">
                {{ t('lensAdmin.managementChecks.allNodes') }}
              </option>
              <option value="automatic">
                {{ t('lensAdmin.assistantPresentation.automaticNode') }}
              </option>
              <option
                v-for="node in listNodes"
                :key="node.uuid"
                :value="node.uuid"
              >
                {{ node.name }}
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

        <div
          v-if="selectedAssistantUuids.length"
          class="flex flex-wrap items-center gap-3 border-b border-line px-5 py-3"
        >
          <span class="text-sm">{{
            t('lensAdmin.bulkVisibility.selection', {
              count: selectedAssistantUuids.length
            })
          }}</span>
          <BaseButton
            v-for="enabled in showArchived ? [] : [true, false]"
            :key="String(enabled)"
            variant="outline"
            size="sm"
            :disabled="loading || readonly"
            @click="openEnablement(enabled)"
            >{{
              t(`lensAdmin.enablement.${enabled ? 'enable' : 'disable'}`)
            }}</BaseButton
          >
          <BaseButton
            variant="outline"
            size="sm"
            :disabled="loading || readonly"
            v-if="!showArchived"
            @click="openVisibilityBatch"
            >{{ t('lensAdmin.bulkVisibility.title') }}</BaseButton
          >
          <BaseButton
            v-if="!showArchived"
            variant="outline"
            size="sm"
            :disabled="loading || readonly"
            @click="openModelReplacement"
            >{{ t('lensAdmin.modelReplacement.title') }}</BaseButton
          >
          <BaseButton
            variant="danger-outline"
            size="sm"
            :disabled="loading || readonly"
            @click="openDeletion()"
          >
            {{ t('lensAdmin.deletion.title') }}
          </BaseButton>
        </div>
        <p v-if="listError" role="alert" class="px-5 pt-4 text-sm text-red-700">
          {{ listError }}
        </p>

        <div class="assistant-list-body">
          <BaseLoading v-if="loading && assistants.length === 0" />
          <div
            v-else-if="listError && assistants.length === 0"
            class="empty-state"
          >
            <BaseButton variant="outline" @click="load">{{
              t('lensAdmin.access.retry')
            }}</BaseButton>
          </div>

          <div
            v-else-if="!listError && viewRows.length === 0"
            class="empty-state"
          >
            <Bot :size="28" aria-hidden="true" />
            <h2>
              {{
                t(
                  attentionOnly
                    ? 'lensAdmin.managementChecks.noIssues'
                    : showArchived
                      ? 'lensAdmin.pages.assistants.emptyArchived'
                      : enablementFilter
                        ? `lensAdmin.enablement.empty${enablementFilter === 'enabled' ? 'Enabled' : 'Disabled'}`
                        : 'lensAdmin.pages.assistants.emptyActive'
                )
              }}
            </h2>
            <p v-if="!attentionOnly">
              {{ t('lensAdmin.pages.assistants.emptyHint') }}
            </p>
          </div>

          <div v-else-if="filteredAssistants.length === 0" class="empty-state">
            <Search :size="28" aria-hidden="true" />
            <h2>{{ t('lensAdmin.pages.assistants.noFilterResults') }}</h2>
            <p>{{ t('lensAdmin.pages.assistants.noFilterHint') }}</p>
            <BaseButton variant="outline" size="sm" @click="clearFilters">{{
              t('lensAdmin.pages.assistants.resetFilters')
            }}</BaseButton>
          </div>

          <div v-else-if="listView === 'cards'" class="assistant-summary-grid">
            <AssistantSummaryCard
              v-for="row in pagedAssistants"
              :key="row.uuid"
              :assistant="row"
              :state="assistantState(row)"
              :model-name="assistantModelName(row)"
              :node-name="lensNodeName(row)"
              :issue-label="
                row.management_checks?.issues?.[0]
                  ? checkIssueLabel(row.management_checks.issues[0])
                  : ''
              "
              @open="openDetails"
            />
          </div>
          <div v-else class="assistants-table-wrap admin-list-panel">
            <table
              class="assistants-table w-full table-fixed divide-y divide-line"
            >
              <colgroup>
                <col style="width: 44px" />
                <col />
                <col style="width: 130px" />
                <col style="width: 115px" />
                <col style="width: 140px" />
                <col style="width: 130px" />
                <col style="width: 110px" />
                <col style="width: 100px" />
                <col style="width: 44px" />
              </colgroup>
              <thead>
                <tr>
                  <th scope="col" class="table-head">
                    <input
                      type="checkbox"
                      :checked="allPageSelected"
                      :indeterminate="somePageSelected && !allPageSelected"
                      :aria-label="t('lensAdmin.modelReplacement.selectPage')"
                      @change="togglePageSelection($event.target.checked)"
                    />
                  </th>
                  <th
                    scope="col"
                    v-for="column in activeColumns"
                    :key="column"
                    class="table-head"
                  >
                    {{ column }}
                  </th>
                  <th scope="col" class="table-head">
                    <span class="sr-only">{{ t('common.actions') }}</span>
                  </th>
                </tr>
              </thead>
              <tbody class="divide-y divide-line bg-surface">
                <tr
                  v-for="row in pagedAssistants"
                  :key="row.uuid"
                  class="transition-colors hover:bg-line-soft"
                >
                  <td class="table-cell">
                    <input
                      v-model="selectedAssistantUuids"
                      type="checkbox"
                      :value="row.uuid"
                      :aria-label="
                        t('lensAdmin.modelReplacement.selectAssistant', {
                          name: row.name
                        })
                      "
                    />
                  </td>
                  <td class="table-cell assistant-name-cell">
                    <div class="assistant-name-row">
                      <span
                        class="assistant-glyph"
                        :class="{
                          'assistant-glyph-smart':
                            (row.mode || row.routing_mode) === 'smart'
                        }"
                      >
                        <component
                          :is="assistantIcon(row)"
                          :size="18"
                          aria-hidden="true"
                        />
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
                    class="table-cell"
                    :data-label="t('lensAdmin.columns.status')"
                  >
                    <span
                      class="assistant-state"
                      :data-tone="assistantState(row).tone"
                      >{{ assistantState(row).label }}</span
                    >
                    <p
                      v-for="issue in row.management_checks?.issues || []"
                      :key="`${issue.code}-${issue.field}`"
                      class="assistant-table-issue"
                    >
                      {{ checkIssueLabel(issue) }}
                    </p>
                  </td>
                  <td
                    class="table-cell"
                    :data-label="t('lensAdmin.columns.visibility')"
                  >
                    <span
                      class="assistant-visibility"
                      :data-visibility="row.visibility || 'private'"
                      >{{
                        t(
                          `lensAdmin.assistantList.visibility.${row.visibility || 'private'}`
                        )
                      }}</span
                    >
                  </td>
                  <td
                    class="table-cell"
                    :data-label="t('lensAdmin.assistantList.model')"
                  >
                    <span
                      class="assistant-table-model"
                      :title="assistantModelName(row)"
                      >{{ assistantModelName(row) }}</span
                    >
                  </td>
                  <td
                    class="table-cell text-ink-600"
                    :data-label="t('lensAdmin.columns.dataAndTools')"
                  >
                    <div
                      data-testid="assistant-tool-counts"
                      class="flex items-center gap-2"
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
                        :title="skillCountLabel(row)"
                        :aria-label="skillCountLabel(row)"
                      >
                        <BookOpen :size="16" aria-hidden="true" />
                        <span>{{ row.skill_summary?.enabled || 0 }}</span>
                      </span>
                      <span
                        class="data-tool-count"
                        :title="mcpCountLabel(row)"
                        :aria-label="mcpCountLabel(row)"
                      >
                        <Server :size="16" aria-hidden="true" />
                        <span>{{ row.mcp_summary?.enabled || 0 }}</span>
                      </span>
                    </div>
                  </td>
                  <td
                    class="table-cell assistant-table-number"
                    :data-label="t('lensAdmin.assistantList.questions')"
                  >
                    {{ formatNumber(row.management_checks?.questions_30d) }}
                  </td>
                  <td
                    class="table-cell assistant-table-updated"
                    :data-label="t('lensAdmin.columns.updatedAt')"
                  >
                    {{ formatDate(row.updated_at) }}
                  </td>
                  <td class="table-cell assistant-actions-cell">
                    <RowActionMenu
                      :label="`${t('common.moreActions')} · ${row.name}`"
                      :actions="assistantActions(row)"
                      @select="handleAssistantAction($event, row)"
                    />
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
          <PaginationBar
            prototype
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
      :key="assistantIdentity()"
      :show="Boolean(detailAssistant)"
      :assistant="detailAssistant"
      :busy="Boolean(actionUuid)"
      :lensnode-name="lensNodeName(detailAssistant)"
      :model-names="
        assistants.find((row) => row.uuid === detailAssistant?.uuid)
          ?.management_checks?.models || {}
      "
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

    <AssistantDeletionModal
      v-if="deletionBatch.length"
      :assistants="deletionBatch"
      @close="deletionBatch = []"
      @updated="deletionFinished"
    />

    <AssistantEnablementModal
      v-if="enablementBatch.length"
      :assistants="enablementBatch"
      :enabled="enablementTarget"
      @close="enablementBatch = []"
      @updated="enablementFinished"
    />

    <AssistantVisibilityModal
      v-if="visibilityBatch.length"
      :assistants="visibilityBatch"
      @close="visibilityBatch = []"
      @updated="visibilityBatchFinished"
    />

    <!-- Assistant Drawer (create wizard + edit) -->
    <AssistantModelReplacementModal
      v-if="showModelReplacement"
      :assistants="assistants"
      :selected-uuids="selectedAssistantUuids"
      :initial-field="replacementInitialField"
      @close="showModelReplacement = false"
      @updated="modelReplacementFinished"
      @refresh="load"
    />
    <AssistantFormDrawer
      :show="showDrawer"
      :mode="mode"
      :form="form"
      :assistants="collaborationAssistants"
      :skills="skills"
      :environment-variable-sets="environmentVariableSets"
      :mcps="mcps"
      :plugin-connections="pluginConnections"
      :plugin-manifests="pluginManifests"
      :plugin-icon-urls="pluginIconUrls"
      :llm-config-options="llmConfigOptions"
      :decision-catalog="decisionCatalog"
      :datasource-options="datasourceOptions"
      :form-resources-loading="formResourcesLoading"
      :saving="saving"
      :continue-label="continueLabel"
      :form-error="formError"
      @close="!saving && closeDrawer()"
      @save="save"
    />

    <ConfirmDeleteModal
      :show="discardConfirmOpen"
      :title="t('lensAdmin.messages.unsavedChanges')"
      icon-type="warning"
      variant="primary"
      :confirm-text="t('lensAdmin.messages.discardChanges')"
      :cancel-text="t('common.cancel')"
      @confirm="discardDrawer"
      @cancel="discardConfirmOpen = false"
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
  </component>
</template>

<script setup>
import {
  Bot,
  BookOpen,
  Code2,
  MessageCircle,
  Database,
  Plus,
  RefreshCw,
  Layers,
  List,
  SlidersHorizontal,
  Search,
  Server,
  UsersRound
} from '@lucide/vue'
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'

import apiClient from '@/api/index'
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
  listPublicConfig,
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
import ConfirmDeleteModal from '@/components/ui/ConfirmDeleteModal.vue'
import PaginationBar from '@/components/ui/PaginationBar.vue'
import RowActionMenu from '@/components/ui/RowActionMenu.vue'
import AssistantSummaryCard from './AssistantSummaryCard.vue'

import { useUserStore } from '@/store/user'
import { createLatestRequestRunner } from './assistantAccessSelectors'
import AssistantVisibilityModal from './AssistantVisibilityModal.vue'
import AssistantEnablementModal from './AssistantEnablementModal.vue'
import AssistantDeletionModal from './AssistantDeletionModal.vue'
import AssistantDetailDrawer from './AssistantDetailDrawer.vue'
import AssistantModelReplacementModal from './AssistantModelReplacementModal.vue'
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
  splitList
} from './adminHelpers'

const props = defineProps({
  embedded: Boolean,
  continueLabel: { type: String, default: '' }
})
const emit = defineEmits(['saved', 'close'])

const { t, locale } = useI18n()
const { showSuccess, showError } = useToast()

const userStore = useUserStore()
const readonly = computed(() => userStore.delegation?.mode === 'readonly')
const attentionOnly = ref(false)
const enablementFilter = ref('')
const enablementBatch = ref([])
const enablementTarget = ref(false)
const visibilityBatch = ref([])
const modelFilter = ref('')
const nodeFilter = ref('')
const listError = ref('')
const listRequests = createLatestRequestRunner()
const detailRequests = createLatestRequestRunner()
const loading = ref(false)
const saving = ref(false)
const showDrawer = ref(false)
const mode = ref('create')
const form = ref({})
const formError = ref('')
const formBaseline = ref('')
const discardConfirmOpen = ref(false)
const showArchived = ref(false)
const archiveConfirmRow = ref(null)
const actionUuid = ref('')
const detailAssistant = ref(null)

const assistants = ref([])
const selectedAssistantUuids = ref([])
const deletionBatch = ref([])
const showModelReplacement = ref(false)
const replacementInitialField = ref('agent_model_ref')
const currentPage = ref(1)
const pageSize = ref(20)
const searchQuery = ref('')
const listView = ref('cards')
const showFilters = ref(false)
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
const decisionCatalog = ref({ decisions: [] })
const datasourceOptions = ref([])
const collaborationAssistants = ref([])
const formResourcesLoading = ref(false)
let formResourcesPromise = null
let globalSettingsPromise = null

const activeColumns = computed(() => [
  t('lensAdmin.pages.assistants.title'),
  t('lensAdmin.columns.status'),
  t('lensAdmin.columns.visibility'),
  t('lensAdmin.assistantList.model'),
  t('lensAdmin.columns.dataAndTools'),
  t('lensAdmin.assistantList.questions'),
  t('lensAdmin.columns.updatedAt')
])

function showBatchTable() {
  listView.value = 'table'
}

function assistantState(row) {
  if (row.status === 'archived')
    return { label: t('lensAdmin.pages.assistants.archived'), tone: 'muted' }
  if (row.is_enabled === false)
    return { label: t('lensAdmin.enablement.disabled'), tone: 'muted' }
  const issues = row.management_checks?.issues || []
  if (issues.some((issue) => issue.code === 'model_unavailable'))
    return {
      label: t('lensAdmin.assistantList.modelUnavailable'),
      tone: 'danger'
    }
  if (issues.length)
    return { label: t('lensAdmin.assistantList.incomplete'), tone: 'warning' }
  return { label: t('lensAdmin.enablement.enabled'), tone: 'success' }
}

function assistantModelName(row) {
  return (
    row.management_checks?.models?.[row.agent_model_ref] ||
    (row.agent_model_ref
      ? t('lensAdmin.managementChecks.unavailableModel', {
          ref: row.agent_model_ref.slice(0, 8)
        })
      : '—')
  )
}

function formatNumber(value) {
  return Number.isFinite(value)
    ? new Intl.NumberFormat(locale.value).format(value)
    : '—'
}

function formatDate(value) {
  if (!value) return '—'
  const date = new Date(value)
  return Number.isNaN(date.getTime())
    ? '—'
    : date.toLocaleDateString(locale.value)
}

function assistantActions(row) {
  const blocked = loading.value || readonly.value
  return [
    ...(row.status === 'active'
      ? [
          { key: 'edit', label: t('common.edit'), disabled: blocked },
          {
            key: 'replace',
            label: t('lensAdmin.modelReplacement.title'),
            disabled: blocked
          },
          {
            key: 'enablement',
            label: t(
              `lensAdmin.enablement.${row.is_enabled === false ? 'enable' : 'disable'}`
            ),
            disabled: blocked
          },
          {
            key: 'archive',
            label: t('lensAdmin.pages.assistants.archive'),
            disabled: blocked
          }
        ]
      : [
          {
            key: 'restore',
            label: t('lensAdmin.pages.assistants.restore'),
            disabled: blocked || actionUuid.value === row.uuid
          }
        ]),
    {
      key: 'delete',
      label: t('lensAdmin.deletion.title'),
      variant: 'danger',
      disabled: blocked
    }
  ]
}

function handleAssistantAction(action, row) {
  if (loading.value || readonly.value) return
  if (action === 'edit') startEdit(row)
  else if (action === 'replace') {
    selectedAssistantUuids.value = [row.uuid]
    openModelReplacement()
  } else if (action === 'enablement')
    openEnablement(row.is_enabled === false, row)
  else if (action === 'archive') requestArchive(row)
  else if (action === 'restore') restore(row)
  else if (action === 'delete') openDeletion(row)
}

const activeRows = computed(() =>
  assistants.value.filter((row) => !row.status || row.status === 'active')
)
const archivedRows = computed(() =>
  assistants.value.filter((row) => row.status === 'archived')
)
const activeCount = computed(() => activeRows.value.length)
const enabledCount = computed(
  () => activeRows.value.filter((row) => row.is_enabled !== false).length
)
const disabledCount = computed(() => activeCount.value - enabledCount.value)
const archivedCount = computed(() => archivedRows.value.length)
const attentionRows = computed(() =>
  activeRows.value.filter((row) => row.management_checks?.issues?.length)
)
const attentionCount = computed(() => attentionRows.value.length)
const modelAttentionRows = computed(() =>
  attentionRows.value.filter((row) =>
    row.management_checks.issues.some((issue) => issue.field !== 'lensnode')
  )
)
const viewRows = computed(() =>
  showArchived.value
    ? archivedRows.value
    : attentionOnly.value
      ? attentionRows.value
      : activeRows.value.filter(
          (row) =>
            !enablementFilter.value ||
            (row.is_enabled === false ? 'disabled' : 'enabled') ===
              enablementFilter.value
        )
)
const modelFields = [
  'agent_model_ref',
  'decision_model_ref',
  'multimodal_model_ref'
]
const affectedModelFields = computed(() =>
  modelFields.filter((field) =>
    modelAttentionRows.value.some((row) =>
      row.management_checks.issues.some((issue) => issue.field === field)
    )
  )
)
const listModels = computed(() => [
  ...new Map(
    assistants.value.flatMap((row) =>
      modelFields
        .filter((field) => row[field])
        .map((field) => [
          row[field],
          {
            uuid: row[field],
            name:
              row.management_checks?.models?.[row[field]] ||
              t('lensAdmin.managementChecks.unavailableModel', {
                ref: row[field].slice(0, 8)
              })
          }
        ])
    )
  ).values()
])
const listNodes = computed(() => [
  ...new Map(
    assistants.value
      .filter((row) => row.lensnode)
      .map((row) => [
        row.lensnode,
        { uuid: row.lensnode, name: row.lensnode_name || row.lensnode }
      ])
  ).values()
])
const hasFilters = computed(() =>
  Boolean(
    searchQuery.value ||
      typeFilter.value ||
      visibilityFilter.value ||
      modelFilter.value ||
      nodeFilter.value
  )
)
const filteredAssistants = computed(() => {
  const query = searchQuery.value.trim().toLowerCase()
  return viewRows.value.filter((row) => {
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
    const matchesModel =
      !modelFilter.value ||
      modelFields.some((field) => row[field] === modelFilter.value)
    const matchesNode =
      !nodeFilter.value || (row.lensnode || 'automatic') === nodeFilter.value
    return (
      matchesQuery &&
      matchesType &&
      matchesVisibility &&
      matchesModel &&
      matchesNode
    )
  })
})
const totalPages = computed(() =>
  Math.max(1, Math.ceil(filteredAssistants.value.length / pageSize.value))
)
const pagedAssistants = computed(() => {
  const start = (currentPage.value - 1) * pageSize.value
  return filteredAssistants.value.slice(start, start + pageSize.value)
})
const allPageSelected = computed(
  () =>
    pagedAssistants.value.length > 0 &&
    pagedAssistants.value.every((row) =>
      selectedAssistantUuids.value.includes(row.uuid)
    )
)
const somePageSelected = computed(() =>
  pagedAssistants.value.some((row) =>
    selectedAssistantUuids.value.includes(row.uuid)
  )
)

function togglePageSelection(checked) {
  const pageUuids = pagedAssistants.value.map((row) => row.uuid)
  selectedAssistantUuids.value = checked
    ? [...new Set([...selectedAssistantUuids.value, ...pageUuids])]
    : selectedAssistantUuids.value.filter((uuid) => !pageUuids.includes(uuid))
}

async function modelReplacementFinished() {
  showModelReplacement.value = false
  selectedAssistantUuids.value = []
  await load()
}

function resetPage() {
  currentPage.value = 1
  selectedAssistantUuids.value = []
}

function clearFilters() {
  searchQuery.value = ''
  typeFilter.value = ''
  visibilityFilter.value = ''
  modelFilter.value = ''
  nodeFilter.value = ''
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

function assistantIcon(row) {
  if ((row.mode || row.routing_mode) === 'smart') return UsersRound
  return (
    {
      general_chat: MessageCircle,
      knowledge_qa: BookOpen,
      code_analysis: Code2
    }[row.capability] || Bot
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

async function load() {
  loading.value = true
  listError.value = ''
  const result = await listRequests.run(() =>
    listAssistants({ managed: true, archived: 'all' })
  )
  if (!result.current) return
  loading.value = false
  if (result.error) {
    listError.value = extractErrorMessage(
      result.error,
      t('lensAdmin.messages.loadFailed')
    )
    showError(listError.value)
    return
  }
  assistants.value = normalizeList(result.value)
  collaborationAssistants.value = activeRows.value.filter(
    (row) => row.is_enabled !== false
  )
  selectedAssistantUuids.value = []
}

function checkIssueLabel(issue) {
  const slot = t(`lensAdmin.managementChecks.${issue.field}`)
  return t(`lensAdmin.managementChecks.${issue.code}`, { slot })
}

function showAttention() {
  enablementFilter.value = ''
  showArchived.value = false
  attentionOnly.value = true
  clearFilters()
}

function replaceAffectedModels(field) {
  if (readonly.value) return
  replacementInitialField.value = field
  selectedAssistantUuids.value = modelAttentionRows.value
    .filter((row) =>
      row.management_checks.issues.some((issue) => issue.field === field)
    )
    .map((row) => row.uuid)
  showModelReplacement.value = true
}

function openModelReplacement() {
  replacementInitialField.value = 'agent_model_ref'
  showModelReplacement.value = true
}

function showEnablementView(value) {
  switchArchiveView(false)
  enablementFilter.value = value
}

function openDeletion(row = null) {
  if (readonly.value || loading.value) return
  deletionBatch.value = (
    row
      ? [row]
      : assistants.value.filter((item) =>
          selectedAssistantUuids.value.includes(item.uuid)
        )
  ).map((item) => ({ ...item }))
}

async function deletionFinished() {
  detailAssistant.value = null
  await load()
}

function openEnablement(enabled, row = null) {
  if (readonly.value || loading.value) return
  enablementTarget.value = enabled
  enablementBatch.value = (
    row
      ? [row]
      : activeRows.value.filter((item) =>
          selectedAssistantUuids.value.includes(item.uuid)
        )
  ).map((item) => ({ ...item }))
}

async function enablementFinished() {
  await load()
  if (detailAssistant.value) {
    await openDetails(detailAssistant.value)
  }
}

function openVisibilityBatch() {
  if (readonly.value || loading.value) return
  visibilityBatch.value = activeRows.value
    .filter((row) => selectedAssistantUuids.value.includes(row.uuid))
    .map((row) => ({ ...row }))
}

async function visibilityBatchFinished() {
  visibilityBatch.value = []
  selectedAssistantUuids.value = []
  await load()
}

async function loadGlobalSettings() {
  if (globalSettings.value.length) return
  if (!globalSettingsPromise) {
    globalSettingsPromise = listPublicConfig()
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

  formResourcesLoading.value = true
  formResourcesPromise = Promise.all([
    listLensNodes(),
    listAssistants({ managed: true }),
    listDataSources({ page_size: 1000 }),
    listSkills({ compact: true, usable: true }),
    listEnvironmentVariableSets(),
    listMcpServers({ usable: true }),
    listConnections({ status: 'active' }),
    listPlugins(),
    llmAdminApi
      .getLLMConfigAll({ scope: 'global', model_type: 'all' })
      .catch(() => []),
    apiClient
      .get('/lens/admin/decision-catalog/')
      .then((response) => response.data?.data || response.data)
  ])
    .then(
      ([
        lensnodeRows,
        collaborationRows,
        datasourceRows,
        skillRows,
        environmentVariableSetRows,
        mcpRows,
        connectionRows,
        installedPlugins,
        llmRows,
        decisionRows
      ]) => {
        lensnodes.value = normalizeList(lensnodeRows)
        collaborationAssistants.value = normalizeList(collaborationRows)
        skills.value = normalizeList(skillRows)
        environmentVariableSets.value = normalizeList(
          environmentVariableSetRows
        )
        mcps.value = normalizeList(mcpRows)
        pluginConnections.value = normalizeList(connectionRows)
        llmConfigOptions.value = normalizeList(llmRows)
        decisionCatalog.value = decisionRows
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
      formResourcesLoading.value = false
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

function switchArchiveView(archived) {
  enablementFilter.value = ''
  showArchived.value = archived
  attentionOnly.value = false
  archiveConfirmRow.value = null
  detailAssistant.value = null
  clearFilters()
}

async function startCreate() {
  detailAssistant.value = null
  mode.value = 'create'
  formError.value = ''
  form.value = defaultForm()
  formBaseline.value = serializeForm(form.value)
  showDrawer.value = true
  loadFormResources().catch((error) => {
    showError(extractErrorMessage(error, t('lensAdmin.messages.loadFailed')))
  })
}

async function startEdit(row) {
  const assistant = await getAssistant(row.uuid)
  mode.value = 'edit'
  formError.value = ''
  form.value = formFromRow(assistant)
  formBaseline.value = serializeForm(form.value)
  showDrawer.value = true
  loadFormResources().catch((error) => {
    showError(extractErrorMessage(error, t('lensAdmin.messages.loadFailed')))
  })
}

async function openDetails(row) {
  const result = await detailRequests.run(() => getAssistant(row.uuid))
  if (!result.current) return
  if (result.error) {
    showError(
      extractErrorMessage(result.error, t('lensAdmin.messages.loadFailed'))
    )
  } else {
    detailAssistant.value = result.value
  }
}

function closeDetails() {
  detailRequests.invalidate()
  detailAssistant.value = null
}

function assistantIdentity() {
  return JSON.stringify([
    userStore.userInfo?.id,
    userStore.userInfo?.tenant?.uuid,
    userStore.userInfo?.tenant_role,
    userStore.delegation || null
  ])
}

watch(assistantIdentity, closeDetails, { flush: 'sync' })

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
    serializeForm(form.value) !== formBaseline.value
  ) {
    discardConfirmOpen.value = true
    return
  }
  discardDrawer()
}

function discardDrawer() {
  discardConfirmOpen.value = false
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

function defaultForm() {
  return {
    name: '',
    description: '',
    capability: '',
    slug: '',
    lensnode_uuid: '',
    datasource_bindings: [],
    agent_model_ref: '',
    decision_model_ref: '',
    decision_auto: true,
    decision_gates: {},
    decision_analyses: {},
    execution_max_tokens: 0,
    execution_max_agent_turns: 0,
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
    public_share_policy: 'inherit',
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
    datasource_bindings: Array.isArray(row.datasource_bindings)
      ? row.datasource_bindings
      : [],
    agent_model_ref: row.agent_model_ref || '',
    decision_model_ref: row.decision_model_ref || '',
    decision_auto: row.decision_auto !== false,
    decision_gates: structuredClone(row.decision_gates || {}),
    decision_analyses: structuredClone(row.decision_analyses || {}),
    execution_max_tokens: row.settings?.execution_budget?.max_tokens || 0,
    execution_max_agent_turns:
      row.settings?.execution_budget?.max_agent_turns || 0,
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
      enabled: binding.enabled !== false,
      decision_gates: { ...(binding.decision_gates || {}) },
      decision_analyses: { ...(binding.decision_analyses || {}) },
      decision_auto: binding.decision_auto !== false
    })),
    visibility: row.visibility || 'private',
    access_group_ids: (row.access_grants || [])
      .filter((g) => g.type === 'group')
      .map((g) => g.id),
    access_user_ids: (row.access_grants || [])
      .filter((g) => g.type === 'user')
      .map((g) => g.id),
    access_grant_options: row.access_grants || [],
    settings: { ...(row.settings || {}), datasource_routing: 'selected' },
    public_share_policy: row.settings?.public_share?.policy || 'inherit',
    enable_codegraph: row.settings?.features?.codegraph !== false,
    status: row.status || 'active'
  }
}

async function save(advance = false) {
  if (saving.value) return
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
    emit('saved', { advance })
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
    agent_model_ref: form.value.agent_model_ref || null,
    decision_model_ref: form.value.decision_model_ref || null,
    decision_auto: form.value.decision_auto !== false,
    decision_gates: form.value.decision_gates || {},
    decision_analyses: form.value.decision_analyses || {},
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
            enabled: binding.enabled !== false,
            decision_gates: { ...(binding.decision_gates || {}) },
            decision_analyses: { ...(binding.decision_analyses || {}) },
            decision_auto: binding.decision_auto !== false
          }))
        : [],
    visibility: form.value.visibility || 'private',
    ...(!userStore.delegation ? { access_grants: buildAccessGrants() } : {}),
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
  settings.retrieval_policy = {
    exclude_extensions: splitList(form.value.exclude_extensions_text),
    exclude_dirs: splitList(form.value.exclude_dirs_text)
  }
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
  if (
    form.value.public_share_policy &&
    form.value.public_share_policy !== 'inherit'
  ) {
    settings.public_share = { policy: form.value.public_share_policy }
  } else {
    delete settings.public_share
  }
  settings.execution_budget = {
    max_tokens: executionBudgetValue(form.value.execution_max_tokens),
    max_agent_turns: executionBudgetValue(form.value.execution_max_agent_turns)
  }
  return settings
}

function executionBudgetValue(value) {
  const parsed = Number(value)
  if (!Number.isFinite(parsed) || parsed < 0) return 0
  return Math.min(Math.floor(parsed), 2147483647)
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

watch(showDrawer, (visible, previous) => {
  if (props.embedded && previous && !visible) emit('close')
})
onMounted(async () => {
  await load()
  if (props.embedded) await startCreate()
})
onBeforeUnmount(() => {
  listRequests.invalidate()
  detailRequests.invalidate()
  revokePluginIconUrls()
})
</script>

<style scoped>
.assistants-page {
  color: var(--sl-text-primary);
}
.assistants-page > .admin-page-heading {
  margin-bottom: 12px;
}

.filter-control {
  display: grid;
  min-width: 9.5rem;
  gap: 0.375rem;
  color: var(--sl-text-muted);
  font-size: 0.75rem;
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
  border: 1px solid transparent;
  border-radius: 0.5rem;
  background: #eaf0ff;
  color: #6384f4;
}

.assistant-glyph-smart {
  border-color: transparent;
  background: var(--sl-brand-soft);
  color: var(--sl-brand-strong);
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

.assistant-name-action {
  @apply font-medium text-ink-900 transition-colors;
  @apply hover:text-primary-700 hover:underline;
}
.assistants-table {
  min-width: 64rem;
}

@media (max-width: 820px) {
  .assistant-toolbar {
    align-items: stretch;
  }
  .filter-control {
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
    grid-template-columns: minmax(0, 1fr) 44px;
    padding: 14px;
    gap: 10px 8px;
  }
  .assistants-table .table-cell {
    display: flex;
    align-items: center;
    justify-content: space-between;
    grid-column: 1 / -1;
    min-width: 0;
    height: auto;
    padding: 0;
    border: 0;
  }
  .assistants-table .table-cell::before {
    content: attr(data-label);
    flex: none;
    color: var(--sl-text-subtle);
    font-size: 12px;
  }
  .assistants-table .table-cell:first-child {
    grid-column: 2;
    grid-row: 1;
    justify-self: end;
    align-self: start;
  }
  .assistants-table .assistant-name-cell {
    grid-column: 1;
    grid-row: 1;
    max-width: none;
    justify-content: start;
    padding-bottom: 6px;
  }
  .assistants-table .assistant-name-action {
    white-space: normal;
    overflow-wrap: anywhere;
  }
  .assistants-table .assistant-actions-cell {
    grid-column: 2;
    justify-self: end;
  }
}
</style>
