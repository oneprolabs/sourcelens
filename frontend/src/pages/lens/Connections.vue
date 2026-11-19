<template>
  <component :is="embedded ? 'div' : AdminLayout" :class="{ hidden: embedded }">
    <div class="connections-page">
      <header class="admin-page-heading">
        <div class="admin-heading-copy">
          <h1>
            {{ t('lensAdmin.pages.connections.title')
            }}<span class="admin-heading-count">{{ connections.length }}</span>
          </h1>
          <p>{{ t('lensAdmin.pages.connections.description') }}</p>
        </div>
        <div class="catalog-header-actions">
          <BaseButton
            size="sm"
            variant="ghost"
            :loading="loading"
            :title="t('common.refresh')"
            @click="load"
            ><RefreshCw :size="15" aria-hidden="true" /><span class="sr-only">{{
              t('common.refresh')
            }}</span></BaseButton
          >
        </div>
      </header>
      <router-link
        class="connections-market-link"
        :to="{
          path: '/management/lens/resources/marketplace',
          query: { kind: 'connector' }
        }"
        >{{ t('lensAdmin.connections.addFromMarketplace') }} ›</router-link
      >
      <div class="connections-view-toolbar">
        <div
          class="catalog-segments"
          role="group"
          :aria-label="t('lensAdmin.connections.usage')"
        >
          <button
            v-for="tab in connectionUsageTabs"
            :key="tab.key"
            type="button"
            :aria-pressed="connectionUsageFilter === tab.key"
            @click="connectionUsageFilter = tab.key"
          >
            {{ tab.label }}<small>{{ tab.count }}</small>
          </button>
        </div>
        <button
          type="button"
          class="connections-filter-toggle"
          :aria-expanded="showConnectionFilters"
          aria-controls="connection-filters"
          @click="showConnectionFilters = !showConnectionFilters"
        >
          <ListFilter :size="14" aria-hidden="true" />{{ t('common.filter') }}
        </button>
      </div>
      <div
        v-show="showConnectionFilters"
        id="connection-filters"
        class="connections-toolbar"
      >
        <input
          v-model="connectionSearch"
          class="connection-toolbar-input"
          type="search"
          :aria-label="t('lensAdmin.connections.searchPlaceholder')"
          :placeholder="t('lensAdmin.connections.searchPlaceholder')"
        />
        <BaseSelect
          v-model="connectionPluginFilter"
          :aria-label="t('lensAdmin.connections.pluginLabel')"
        >
          <option value="all">
            {{ t('lensAdmin.connections.allPlugins') }}
          </option>
          <option
            v-for="plugin in connectionPlugins"
            :key="plugin.key"
            :value="plugin.key"
          >
            {{ pluginDisplayName(plugin.key) }}
          </option>
        </BaseSelect>
        <BaseSelect
          v-model="connectionStatusFilter"
          :aria-label="t('lensAdmin.connections.allStatuses')"
        >
          <option value="all">
            {{ t('lensAdmin.connections.allStatuses') }}
          </option>
          <option value="active">{{ t('common.status.active') }}</option>
          <option value="disabled">{{ t('common.status.disabled') }}</option>
        </BaseSelect>
      </div>
      <BaseLoading v-if="loading && !connections.length" />
      <div
        v-else-if="!filteredConnections.length"
        class="rounded-lg border border-dashed border-line px-6 py-16 text-center"
      >
        <p class="text-sm text-ink-700">{{ t('common.noData') }}</p>
        <p class="mt-1 text-sm text-ink-500">
          {{ t('lensAdmin.connections.emptyHint') }}
        </p>
      </div>
      <ul v-else class="catalog-grid" :aria-busy="loading">
        <li v-for="row in filteredConnections" :key="row.uuid">
          <button
            type="button"
            class="catalog-row connection-catalog-row"
            :aria-label="`${t('common.viewDetails')}: ${row.name}`"
            @click="openConnectionDetail(row)"
          >
            <CatalogTile :name="row.name" />
            <span class="catalog-row-copy">
              <strong>{{ row.name }}</strong>
              <span class="catalog-row-description"
                >{{ pluginDisplayName(row.plugin_key) }} ·
                {{
                  row.plugin_key === 'github' && !row.secret_version_uuid
                    ? t('lensAdmin.connections.anonymous')
                    : row.has_secret
                      ? t('lensAdmin.connections.secretConfigured')
                      : t('lensAdmin.connections.secretMissing')
                }}</span
              >
            </span>
            <span class="connection-row-side">
              <span
                v-for="label in connectionUsageLabels(row)"
                :key="label.key"
                class="connection-use-tag"
                >{{ label.text
                }}<template
                  v-if="label.key === 'datasource' && row.datasource_count"
                >
                  · {{ row.datasource_count }}</template
                ></span
              >
              <span class="connection-row-status" :data-status="row.status">{{
                row.status === 'active'
                  ? t('common.status.active')
                  : t('common.status.disabled')
              }}</span>
            </span>
          </button>
        </li>
      </ul>
    </div>

    <BaseDrawer
      :show="connectionDetailOpen"
      width="2xl"
      :title="detailConnection?.name || ''"
      :subtitle="
        detailConnection ? pluginDisplayName(detailConnection.plugin_key) : ''
      "
      @close="closeConnectionDetail"
    >
      <div v-if="detailConnection" class="space-y-4">
        <section
          class="overflow-hidden rounded-xl border border-line bg-surface"
        >
          <div
            class="flex items-center gap-3 border-b border-line bg-surface-sunken p-4"
          >
            <div
              class="flex h-12 w-12 shrink-0 items-center justify-center overflow-hidden rounded-xl border border-line bg-surface"
            >
              <img
                v-if="pluginIconUrl(detailConnection.plugin_key)"
                :src="pluginIconUrl(detailConnection.plugin_key)"
                :alt="pluginDisplayName(detailConnection.plugin_key)"
                class="h-full w-full object-cover"
              />
              <span
                v-else
                class="text-xs font-semibold uppercase text-brand-700"
                >{{ detailConnection.plugin_key.slice(0, 2) }}</span
              >
            </div>
            <div class="min-w-0 flex-1">
              <p class="font-medium text-ink-900">
                {{ pluginDisplayName(detailConnection.plugin_key) }}
              </p>
              <p class="mt-1 text-xs text-ink-500">
                {{ t('lensAdmin.connections.connectionOverview') }}
              </p>
            </div>
            <span
              class="rounded-full border px-2 py-1 text-xs font-medium"
              :class="
                detailConnection.status === 'active'
                  ? 'border-success-200 bg-success-50 text-success-700'
                  : 'border-line bg-surface text-ink-500'
              "
            >
              {{
                detailConnection.status === 'active'
                  ? t('common.status.active')
                  : t('common.status.disabled')
              }}
            </span>
          </div>
          <dl class="grid gap-px bg-line sm:grid-cols-2">
            <div class="bg-surface px-4 py-3">
              <dt class="text-xs text-ink-500">
                {{ t('lensAdmin.connections.endpoint') }}
              </dt>
              <dd class="mt-1 truncate font-mono text-sm text-ink-800">
                {{ detailConnection.endpoint || emptyValue }}
              </dd>
            </div>
            <div class="bg-surface px-4 py-3">
              <dt class="text-xs text-ink-500">
                {{ t('lensAdmin.connections.token') }}
              </dt>
              <dd class="mt-1 font-mono text-sm text-ink-800">
                {{
                  detailConnection.secret_hint ||
                  t(
                    detailConnection.plugin_key === 'github' &&
                      !detailConnection.secret_version_uuid
                      ? 'lensAdmin.connections.anonymous'
                      : 'lensAdmin.connections.secretMissing'
                  )
                }}
              </dd>
            </div>
          </dl>
        </section>

        <section>
          <h3 class="text-sm font-semibold text-ink-900">
            {{ t('lensAdmin.connections.usage') }}
          </h3>
          <dl class="mt-2 grid gap-3 sm:grid-cols-2">
            <div
              class="rounded-xl border border-line bg-surface-sunken px-4 py-3"
            >
              <dt class="text-xs text-ink-500">
                {{ t('lensAdmin.connections.datasources') }}
              </dt>
              <dd class="mt-1 text-xl font-semibold text-ink-900">
                {{ detailConnection.datasource_count || 0 }}
              </dd>
            </div>
            <div
              class="rounded-xl border border-line bg-surface-sunken px-4 py-3"
            >
              <dt class="text-xs text-ink-500">
                {{ t('lensAdmin.connections.assistants') }}
              </dt>
              <dd class="mt-1 text-xl font-semibold text-ink-900">
                {{ detailConnection.assistant_count || 0 }}
              </dd>
            </div>
          </dl>
        </section>

        <section>
          <ManifestSchemaForm
            :schema="detailScopeSchema"
            :model-value="detailScopeModel"
            :resources="detailScopeResourceOptions"
            :read-only="true"
            :empty-resource-text="emptyValue"
            :tree-search-placeholder="
              t('lensAdmin.connections.resourceSearchPlaceholder')
            "
            :resource-search-empty-text="
              t('lensAdmin.connections.resourceSearchEmpty')
            "
            :resource-count-label="
              t('lensAdmin.connections.resourceCountLabel')
            "
            :selected-count-label="
              t('lensAdmin.connections.selectedCountLabel')
            "
          />
        </section>
        <p
          v-if="
            !detailConnection.has_secret &&
            detailConnection.plugin_key !== 'github'
          "
          class="rounded-lg border border-warning-200 bg-warning-50 px-3 py-2 text-sm text-warning-800"
        >
          {{ t('lensAdmin.connections.secretMissing') }}
        </p>
        <p
          v-if="validationResults[detailConnection.uuid]"
          class="rounded-lg border px-3 py-2 text-sm"
          :class="
            validationResults[detailConnection.uuid].ok
              ? 'border-success-200 bg-success-50 text-success-700'
              : 'border-danger-200 bg-danger-50 text-danger-700'
          "
        >
          {{ validationResults[detailConnection.uuid].message }}
        </p>
      </div>
      <template #footer>
        <div class="flex flex-wrap justify-between gap-2">
          <BaseButton
            variant="danger"
            :disabled="
              !detailConnection ||
              detailConnection.datasource_count > 0 ||
              detailConnection.assistant_count > 0
            "
            @click="removeDetailConnection"
            >{{ t('common.delete') }}</BaseButton
          >
          <div class="flex gap-2">
            <BaseButton
              variant="outline"
              :loading="validatingUuid === detailConnection?.uuid"
              :disabled="detailConnection?.status !== 'active'"
              @click="validateRow(detailConnection)"
              >{{ t('lensAdmin.connections.validate') }}</BaseButton
            >
            <BaseButton
              variant="outline"
              :class="
                detailConnection?.status === 'active'
                  ? 'text-danger-700'
                  : 'text-success-700'
              "
              :loading="saving"
              @click="toggleDetailConnectionStatus"
            >
              {{
                detailConnection?.status === 'active'
                  ? t('lensAdmin.connections.pause')
                  : t('lensAdmin.connections.resume')
              }}
            </BaseButton>
            <BaseButton variant="primary" @click="editDetailConnection">{{
              t('common.edit')
            }}</BaseButton>
          </div>
        </div>
      </template>
    </BaseDrawer>

    <BaseDrawer
      :show="drawerOpen"
      width="6xl"
      :title="
        mode === 'create'
          ? t('lensAdmin.connections.createTitle')
          : t('lensAdmin.connections.editTitle')
      "
      @close="!saving && closeDrawer()"
    >
      <fieldset class="min-w-0 space-y-5" :disabled="saving">
        <div
          v-if="manifest"
          class="flex items-start gap-4 rounded-xl border border-brand-200 bg-brand-50/70 p-4"
        >
          <span
            class="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-brand-600 text-sm font-semibold uppercase text-white shadow-sm"
            aria-hidden="true"
          >
            {{ form.plugin_key.slice(0, 2) }}
          </span>
          <div class="min-w-0">
            <p
              class="text-xs font-medium uppercase tracking-wide text-brand-700"
            >
              {{ t('lensAdmin.connections.pluginLabel') }}
            </p>
            <h3 class="mt-0.5 truncate text-base font-semibold text-ink-900">
              {{ localizedManifest.display_name }}
            </h3>
            <p
              class="mt-1 truncate text-sm leading-5 text-ink-600"
              :title="localizedManifest.description"
            >
              {{ localizedManifest.description }}
            </p>
          </div>
        </div>

        <div
          class="connection-form-layout grid gap-5 md:grid-cols-[minmax(0,1fr)_20rem] md:items-start xl:grid-cols-[minmax(0,1fr)_22rem]"
        >
          <div class="space-y-5">
            <section class="rounded-xl border border-line bg-surface p-4">
              <div class="mb-4">
                <h3 class="text-sm font-semibold text-ink-900">
                  {{ t('lensAdmin.connections.basicSection') }}
                </h3>
                <p class="mt-1 text-xs text-ink-500">
                  {{ t('lensAdmin.connections.basicSectionHint') }}
                </p>
              </div>
              <div
                v-if="mode === 'create'"
                class="mb-4 grid gap-2 sm:grid-cols-2 lg:grid-cols-3"
              >
                <button
                  v-for="plugin in connectionPlugins"
                  :key="plugin.key"
                  type="button"
                  :disabled="saving"
                  class="flex items-center gap-3 rounded-lg border p-3 text-left transition focus:outline-none focus:ring-2 focus:ring-brand-500/20"
                  :class="
                    form.plugin_key === plugin.key
                      ? 'border-brand-300 bg-brand-50'
                      : 'border-line hover:border-brand-200 hover:bg-surface-sunken'
                  "
                  @click="handlePluginChange(plugin.key)"
                >
                  <span
                    class="flex h-9 w-9 shrink-0 items-center justify-center overflow-hidden rounded-lg border border-line bg-surface"
                  >
                    <img
                      v-if="pluginIconUrl(plugin.key)"
                      :src="pluginIconUrl(plugin.key)"
                      :alt="pluginDisplayName(plugin.key)"
                      class="h-full w-full object-cover"
                    />
                    <span
                      v-else
                      class="text-xs font-semibold uppercase text-brand-700"
                      >{{ plugin.key.slice(0, 2) }}</span
                    >
                  </span>
                  <span class="min-w-0">
                    <span
                      class="block truncate text-sm font-medium text-ink-900"
                      >{{ pluginDisplayName(plugin.key) }}</span
                    >
                    <span class="mt-0.5 block truncate text-xs text-ink-500">{{
                      plugin.version
                    }}</span>
                  </span>
                </button>
              </div>
              <div class="grid gap-4 md:grid-cols-2">
                <label class="block">
                  <span class="mb-1.5 block text-sm font-medium text-ink-700">
                    {{ t('lensAdmin.connections.name') }}
                  </span>
                  <input
                    v-model="form.name"
                    class="connection-form-input"
                    required
                  />
                </label>
                <div v-if="mode === 'edit'" class="block">
                  <span class="mb-1.5 block text-sm font-medium text-ink-700">
                    {{ t('lensAdmin.connections.pluginLabel') }}
                  </span>
                  <div
                    class="flex h-10 items-center gap-2 rounded-lg border border-line bg-surface-sunken px-3 text-sm text-ink-700"
                  >
                    <img
                      v-if="pluginIconUrl(form.plugin_key)"
                      :src="pluginIconUrl(form.plugin_key)"
                      :alt="pluginDisplayName(form.plugin_key)"
                      class="h-6 w-6 rounded-md"
                    />
                    <span>{{ pluginDisplayName(form.plugin_key) }}</span>
                  </div>
                </div>
              </div>
            </section>

            <section class="rounded-xl border border-line bg-surface p-4">
              <div class="mb-4">
                <h3 class="text-sm font-semibold text-ink-900">
                  {{ t('lensAdmin.connections.accessSection') }}
                </h3>
                <p class="mt-1 text-xs text-ink-500">
                  {{ t('lensAdmin.connections.accessSectionHint') }}
                </p>
              </div>
              <ManifestSchemaForm
                v-if="manifest?.connection_schema"
                :model-value="form"
                :normalize-custom-resource="
                  form.plugin_key === 'github'
                    ? normalizeGitHubRepositoryAddress
                    : undefined
                "
                :custom-resource-label="
                  t('lensAdmin.connections.repositoryAddress')
                "
                :add-array-item-label="t('common.add')"
                :schema="
                  localizedManifest.connection_schema ||
                  manifest.connection_schema
                "
                :resources="connectionResourceOptions"
                control-class="connection-form-input"
                :password-placeholder="storedSecretPlaceholder"
                :empty-resource-text="
                  t('lensAdmin.connections.resourceTreeEmpty')
                "
                :tree-search-placeholder="
                  t('lensAdmin.connections.resourceSearchPlaceholder')
                "
                :resource-search-empty-text="
                  t('lensAdmin.connections.resourceSearchEmpty')
                "
                :resource-count-label="
                  t('lensAdmin.connections.resourceCountLabel')
                "
                :selected-count-label="
                  t('lensAdmin.connections.selectedCountLabel')
                "
                :private-resource-label="
                  t('lensAdmin.connections.privateResource')
                "
                :allow-all-label="allowAllLabel"
                :allow-all-hint="allowAllHint"
                :select-option-label="t('lensAdmin.pluginForm.selectOption')"
                :loading-options-label="
                  t('lensAdmin.pluginForm.loadingOptions')
                "
                @update:model-value="updateConnectionForm"
              >
                <template #field-actions="{ field }">
                  <BaseButton
                    v-if="field.key === connectionResourceField?.[0]"
                    size="sm"
                    variant="outline"
                    :loading="discoveringResources"
                    :disabled="!canDiscoverConnectionResources"
                    @click.prevent="discoverConnectionResources"
                  >
                    {{ t('lensAdmin.connections.discoverResourcesAction') }}
                  </BaseButton>
                </template>
              </ManifestSchemaForm>
              <p
                v-if="mode === 'edit'"
                class="mt-3 rounded-lg bg-surface-sunken px-3 py-2 text-xs leading-5 text-ink-600"
              >
                {{ t('lensAdmin.connections.tokenEditHint') }}
              </p>
            </section>
          </div>

          <FeishuConnectionGuide v-if="form.plugin_key === 'feishu'" />
          <GitHubConnectionGuide v-if="form.plugin_key === 'github'" />
          <GitLabConnectionGuide v-if="form.plugin_key === 'gitlab'" />
          <JiraConnectionGuide v-if="form.plugin_key === 'jira'" />
        </div>

        <p v-if="anonymousAllScope" class="text-sm text-ink-600">
          {{ t('lensAdmin.connections.publicAccessHint') }}
        </p>
        <p v-if="formError" role="alert" class="text-sm text-danger-700">
          {{ formError }}
        </p>
      </fieldset>
      <template #footer>
        <div class="flex flex-wrap justify-end gap-2">
          <BaseButton variant="outline" :disabled="saving" @click="closeDrawer">
            {{ t('common.cancel') }}
          </BaseButton>
          <BaseButton
            :variant="continueLabel ? 'outline' : 'primary'"
            :loading="saving"
            :disabled="!canSave"
            @click="save()"
          >
            {{
              embedded && form.status === 'active'
                ? t('lensAdmin.connections.saveAndValidate')
                : t('common.save')
            }}
          </BaseButton>
          <BaseButton
            v-if="continueLabel"
            :loading="saving"
            :disabled="!canSave"
            @click="save(true)"
          >
            {{ continueLabel }}
          </BaseButton>
        </div>
      </template>
    </BaseDrawer>

    <ConfirmDeleteModal
      :show="Boolean(deleteTarget)"
      :title="t('lensAdmin.connections.deleteConfirm')"
      :name="deleteTarget?.name"
      :message="t('lensAdmin.connections.deleteConfirmMessage')"
      :loading="deleting"
      @confirm="confirmRemoveRow"
      @cancel="deleteTarget = null"
    />
  </component>
</template>

<script setup>
import { ListFilter, RefreshCw } from '@lucide/vue'
import CatalogTile from './components/CatalogTile.vue'
import { normalizeGitHubRepositoryAddress } from '@/utils/githubRepository'
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'

import AdminLayout from '@/admin/layout/AdminLayout.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseDrawer from '@/components/ui/BaseDrawer.vue'
import BaseLoading from '@/components/ui/BaseLoading.vue'
import BaseSelect from '@/components/ui/BaseSelect.vue'
import ConfirmDeleteModal from '@/components/ui/ConfirmDeleteModal.vue'
import FeishuConnectionGuide from '@/components/lens/FeishuConnectionGuide.vue'
import GitHubConnectionGuide from '@/components/lens/GitHubConnectionGuide.vue'
import GitLabConnectionGuide from '@/components/lens/GitLabConnectionGuide.vue'
import JiraConnectionGuide from '@/components/lens/JiraConnectionGuide.vue'
import ManifestSchemaForm from '@/components/lens/ManifestSchemaForm.vue'
import {
  createConnection,
  deleteConnection,
  getConnectionResourceCandidates,
  getConnectionResources,
  getPluginIcon,
  getPluginManifest,
  listConnections,
  listPlugins,
  previewConnectionResources,
  updateConnection,
  validateConnection
} from '@/api/lens'
import { useToast } from '@/composables/useToast'
import { extractErrorMessage } from '@/utils/api'
import { lensNodeErrorMessage } from '@/utils/lensNodeErrors'
import {
  localizePluginManifest,
  pluginDisplayName as translatedPluginDisplayName
} from '@/utils/pluginI18n'
import { EMPTY_VALUE as emptyValue } from './adminHelpers'

const props = defineProps({
  embedded: Boolean,
  initialPluginKey: { type: String, default: '' },
  initialConnectionUuid: { type: String, default: '' },
  continueLabel: { type: String, default: '' }
})
const emit = defineEmits(['saved', 'close'])

const { t, te } = useI18n()
const { showError, showSuccess } = useToast()
const connections = ref([])
const plugins = ref([])
const connectionPlugins = computed(() =>
  plugins.value.filter((plugin) => plugin.key !== 'file_upload')
)
const pluginManifests = ref({})
const loading = ref(false)
const saving = ref(false)
const validatingUuid = ref('')
const validationResults = ref({})
const drawerOpen = ref(false)
const mode = ref('create')
const formError = ref('')
const form = ref(defaultForm())
const discoveringResources = ref(false)
const connectionResourceCandidates = ref([])
const connectionSearch = ref('')
const connectionPluginFilter = ref('all')
const connectionStatusFilter = ref('all')
const connectionUsageFilter = ref('all')
const showConnectionFilters = ref(false)
const pluginIconUrls = ref({})
const connectionDetailOpen = ref(false)
const detailConnection = ref(null)
const deleteTarget = ref(null)
const deleting = ref(false)
const connectionResourceOptions = computed(() => ({
  [connectionResourceField.value?.[1]?.resource || '']: {
    items: connectionResourceCandidates.value
  }
}))

const connectionUsageTabs = computed(() => [
  { key: 'all', label: t('catalog.all'), count: connections.value.length },
  {
    key: 'datasource',
    label: t('lensAdmin.connections.forKnowledge'),
    count: connections.value.filter((row) =>
      connectionHasDatasource(row.plugin_key)
    ).length
  },
  {
    key: 'assistant',
    label: t('lensAdmin.connections.forAssistant'),
    count: connections.value.filter((row) =>
      connectionHasAssistantTools(row.plugin_key)
    ).length
  }
])

const filteredConnections = computed(() => {
  const keyword = connectionSearch.value.trim().toLowerCase()
  return connections.value.filter((row) => {
    const matchesKeyword =
      !keyword ||
      [row.name, row.plugin_key, row.endpoint]
        .filter(Boolean)
        .some((value) => String(value).toLowerCase().includes(keyword))
    const matchesPlugin =
      connectionPluginFilter.value === 'all' ||
      row.plugin_key === connectionPluginFilter.value
    const matchesStatus =
      connectionStatusFilter.value === 'all' ||
      row.status === connectionStatusFilter.value
    const matchesUsage =
      connectionUsageFilter.value === 'all' ||
      (connectionUsageFilter.value === 'datasource'
        ? connectionHasDatasource(row.plugin_key)
        : connectionHasAssistantTools(row.plugin_key))
    return matchesKeyword && matchesPlugin && matchesStatus && matchesUsage
  })
})

const detailScopeValues = computed(() =>
  detailConnection.value ? scopeValues(detailConnection.value) : []
)
const detailScopeSchema = computed(() => ({
  type: 'object',
  properties: {
    scope: {
      type: 'array',
      format: 'provider-resource',
      resource: 'detail-scope',
      title: t('lensAdmin.connections.scope')
    }
  }
}))
const detailScopeModel = computed(() => ({ scope: detailScopeValues.value }))
const detailScopeResourceOptions = computed(() => ({
  'detail-scope': { items: detailScopeValues.value }
}))

const manifest = computed(
  () => pluginManifests.value[form.value.plugin_key] || null
)
const localizedManifest = computed(() =>
  localizePluginManifest(manifest.value, t, te)
)
const connectionResourceField = computed(() =>
  Object.entries(manifest.value?.connection_schema?.properties || {}).find(
    ([, field]) =>
      field.type === 'array' && field.format === 'provider-resource'
  )
)

const hasStoredSecret = computed(
  () => mode.value === 'edit' && Boolean(form.value.has_secret)
)
const allowAllLabel = computed(() =>
  form.value.plugin_key === 'github' && !canDiscoverConnectionResources.value
    ? t('lensAdmin.connections.allowAllPublicRepositories')
    : t(
        form.value.plugin_key === 'gitlab'
          ? 'lensAdmin.connections.allowAllProjects'
          : 'lensAdmin.connections.allowAllRepositories'
      )
)
const allowAllHint = computed(() =>
  form.value.plugin_key === 'github' && !canDiscoverConnectionResources.value
    ? t('lensAdmin.connections.allowAllPublicRepositoriesHint')
    : t(
        form.value.plugin_key === 'gitlab'
          ? 'lensAdmin.connections.allowAllProjectsHint'
          : 'lensAdmin.connections.allowAllRepositoriesHint'
      )
)
const canDiscoverConnectionResources = computed(
  () => hasFieldValue(form.value.secret_value) || hasStoredSecret.value
)
const storedSecretPlaceholder = computed(() => {
  if (!hasStoredSecret.value) return ''
  return form.value.secret_hint || t('lensAdmin.connections.secretConfigured')
})

const anonymousAllScope = computed(() => {
  const scope = form.value[connectionResourceField.value?.[0]]
  return (
    props.embedded &&
    ['github', 'gitlab'].includes(form.value.plugin_key) &&
    !hasFieldValue(form.value.secret_value) &&
    !hasStoredSecret.value &&
    Array.isArray(scope) &&
    scope.length === 1 &&
    scope[0] === '*'
  )
})

const canSave = computed(() => {
  if (anonymousAllScope.value) return false
  if (!form.value.name.trim() || !manifest.value) return false
  const required = manifest.value.connection_schema?.required || []
  return required.every((key) => {
    const field = manifest.value.connection_schema.properties?.[key]
    if (mode.value === 'edit' && field?.format === 'password') return true
    return hasFieldValue(form.value[key])
  })
})

function defaultForm() {
  const pluginKey =
    connectionPlugins.value.find(
      (plugin) => plugin.key === props.initialPluginKey
    )?.key ||
    connectionPlugins.value[0]?.key ||
    ''
  return {
    uuid: '',
    name: '',
    plugin_key: pluginKey,
    status: 'active',
    has_secret: false,
    secret_hint: '',
    ...manifestDefaults(pluginKey)
  }
}

function hasFieldValue(value) {
  if (Array.isArray(value)) return value.length > 0
  return String(value ?? '').trim().length > 0
}

function scopeValues(row) {
  const scope = row.allowed_scope || {}
  const primary = ['repositories', 'projects']
    .map((key) => scope[key])
    .find((value) => Array.isArray(value))
  if (primary) {
    if (primary.length === 1 && primary[0] === '*') {
      return [
        t(
          row.plugin_key === 'gitlab'
            ? 'lensAdmin.connections.allProjects'
            : 'lensAdmin.connections.allRepositories'
        )
      ]
    }
    return primary
  }
  return Object.entries(scope).flatMap(([key, value]) => {
    if (Array.isArray(value)) return value
    if (value === undefined || value === null || value === '') return []
    return [`${key}: ${value}`]
  })
}

function pluginIconUrl(pluginKey) {
  return pluginIconUrls.value[pluginKey] || ''
}

function pluginDisplayName(pluginKey) {
  const plugin =
    pluginManifests.value[pluginKey] ||
    plugins.value.find((item) => item.key === pluginKey)
  return (
    translatedPluginDisplayName(plugin, t, te) ||
    pluginKey ||
    t('lensAdmin.connections.unknownPlugin')
  )
}

function connectionHasDatasource(pluginKey) {
  const manifest = pluginManifests.value[pluginKey] || {}
  return (
    Boolean(manifest.datasource) || Boolean(manifest.datasource_source_type)
  )
}

function connectionHasAssistantTools(pluginKey) {
  const manifest = pluginManifests.value[pluginKey] || {}
  return (
    manifest.plugin_type === 'decision' ||
    (Array.isArray(manifest.tools) &&
      manifest.tools.some((tool) => tool.exposure !== 'internal'))
  )
}

function connectionUsageLabels(row) {
  const manifest = pluginManifests.value[row.plugin_key] || {}
  const labels = []
  const hasDatasource = connectionHasDatasource(row.plugin_key)
  const isDecision = manifest.plugin_type === 'decision'
  const modelTools = Array.isArray(manifest.tools)
    ? manifest.tools.filter((tool) => tool.exposure !== 'internal')
    : []
  if (hasDatasource) {
    labels.push({
      key: 'datasource',
      text: t('lensAdmin.connections.datasourceLabel'),
      className: 'border-amber-200 bg-amber-50 text-amber-700'
    })
  }
  if (isDecision) {
    labels.push({
      key: 'decision',
      text: t('lensAdmin.connections.decisionLabel'),
      className: 'border-violet-200 bg-violet-50 text-violet-700'
    })
  } else if (modelTools.length > 0) {
    labels.push({
      key: 'tool',
      text: t('lensAdmin.connections.toolLabel'),
      className: 'border-brand-200 bg-brand-50 text-brand-700'
    })
  }
  return labels
}

async function load() {
  loading.value = true
  try {
    const [connectionRows, installedPlugins] = await Promise.all([
      listConnections(),
      listPlugins()
    ])
    const manifests = await Promise.all(
      installedPlugins.map((plugin) => getPluginManifest(plugin.key))
    )
    connections.value = connectionRows
    plugins.value = installedPlugins
    pluginManifests.value = Object.fromEntries(
      manifests.map((item) => [item.key, item])
    )
    await loadPluginIcons(installedPlugins)
    return true
  } catch (error) {
    showError(extractErrorMessage(error, t('lensAdmin.connections.loadFailed')))
    return false
  } finally {
    loading.value = false
  }
}

async function loadPluginIcons(installedPlugins) {
  revokePluginIconUrls()
  const entries = await Promise.all(
    installedPlugins.map(async (plugin) => {
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

function openConnectionDetail(row) {
  detailConnection.value = row
  connectionDetailOpen.value = true
}

function closeConnectionDetail() {
  connectionDetailOpen.value = false
  detailConnection.value = null
}

function editDetailConnection() {
  const row = detailConnection.value
  closeConnectionDetail()
  if (row) startEdit(row)
}

function removeDetailConnection() {
  const row = detailConnection.value
  if (!row) return
  closeConnectionDetail()
  requestRemoveRow(row)
}

function startCreate() {
  mode.value = 'create'
  form.value = defaultForm()
  formError.value = ''
  drawerOpen.value = true
}

function startEdit(row) {
  mode.value = 'edit'
  const nextForm = {
    uuid: row.uuid,
    name: row.name,
    plugin_key: row.plugin_key,
    status: row.status,
    has_secret: row.has_secret,
    secret_hint: row.secret_hint
  }
  const schema = pluginManifests.value[row.plugin_key]?.connection_schema
  Object.entries(schema?.properties || {}).forEach(([key, field]) => {
    nextForm[key] =
      field.format === 'password'
        ? ''
        : readConnectionField(row, field.write_to)
  })
  form.value = nextForm
  formError.value = ''
  drawerOpen.value = true
}

function closeDrawer() {
  clearSecretFields()
  drawerOpen.value = false
  formError.value = ''
  connectionResourceCandidates.value = []
}

function updateConnectionForm(nextForm) {
  const secretChanged = nextForm.secret_value !== form.value.secret_value
  if (secretChanged) {
    connectionResourceCandidates.value = []
    const resourceKey = connectionResourceField.value?.[0]
    if (resourceKey) nextForm[resourceKey] = []
  }
  form.value = nextForm
}

async function discoverConnectionResources() {
  if (!connectionResourceField.value) return
  discoveringResources.value = true
  try {
    const [key, field] = connectionResourceField.value
    const result = hasFieldValue(form.value.secret_value)
      ? await previewConnectionResources({
          plugin_key: form.value.plugin_key,
          secret_value: form.value.secret_value,
          endpoint: form.value.endpoint,
          config: buildConnectionPayload().config,
          limit: 50
        })
      : await getConnectionResourceCandidates(form.value.uuid, { limit: 50 })
    connectionResourceCandidates.value =
      result.resources?.[field.resource]?.items || []
    if (!Array.isArray(form.value[key])) form.value[key] = []
  } catch (error) {
    showError(
      extractErrorMessage(error, t('lensAdmin.connections.discoveryFailed'))
    )
  } finally {
    discoveringResources.value = false
  }
}

async function save(advance = false) {
  if (saving.value || !canSave.value) return
  saving.value = true
  formError.value = ''
  let validating = false
  try {
    const payload = buildConnectionPayload()
    const saved =
      mode.value === 'create'
        ? await createConnection(payload)
        : await updateConnection(form.value.uuid, payload)
    Object.assign(form.value, {
      uuid: saved.uuid,
      has_secret: saved.has_secret,
      secret_hint: saved.secret_hint || ''
    })
    mode.value = 'edit'
    clearSecretFields()
    if (props.embedded && form.value.status === 'active') {
      validating = true
      await validateConnection(form.value.uuid)
    }
    showSuccess(t('lensAdmin.connections.saveSuccess'))
    if (props.embedded)
      emit('saved', {
        uuid: form.value.uuid,
        plugin_key: form.value.plugin_key,
        advance
      })
    closeDrawer()
    if (!props.embedded) await load()
  } catch (error) {
    clearSecretFields()
    formError.value = extractErrorMessage(
      error,
      t('lensAdmin.connections.saveFailed')
    )
    if (validating)
      formError.value = t('lensAdmin.connections.validationFailedAfterSave', {
        error: formError.value
      })
    showError(formError.value)
  } finally {
    saving.value = false
  }
}

async function toggleDetailConnectionStatus() {
  const row = detailConnection.value
  if (!row) return
  saving.value = true
  try {
    const nextStatus = row.status === 'active' ? 'disabled' : 'active'
    await updateConnection(row.uuid, {
      status: nextStatus
    })
    showSuccess(
      row.status === 'active'
        ? t('lensAdmin.connections.paused')
        : t('lensAdmin.connections.resumed')
    )
    row.status = nextStatus
    await load()
    detailConnection.value =
      connections.value.find((connection) => connection.uuid === row.uuid) ||
      null
  } catch (error) {
    showError(extractErrorMessage(error, t('lensAdmin.connections.saveFailed')))
  } finally {
    saving.value = false
  }
}

async function validateRow(row) {
  validatingUuid.value = row.uuid
  try {
    await validateConnection(row.uuid)
    const hasDatasource = connectionHasDatasource(row.plugin_key)
    const count = hasDatasource
      ? resourceItemCount((await getConnectionResources(row.uuid)).resources)
      : 0
    validationResults.value[row.uuid] = {
      ok: true,
      message: t(
        hasDatasource
          ? 'lensAdmin.connections.validationSuccess'
          : 'lensAdmin.connections.validationSuccessNoResources',
        { count }
      )
    }
  } catch (error) {
    validationResults.value[row.uuid] = {
      ok: false,
      message: extractErrorMessage(
        error,
        t('lensAdmin.connections.validationFailed')
      )
    }
  } finally {
    validatingUuid.value = ''
  }
}

function handlePluginChange(pluginKey) {
  form.value = {
    uuid: form.value.uuid,
    name: form.value.name,
    plugin_key: pluginKey,
    status: form.value.status,
    has_secret: false,
    secret_hint: '',
    ...manifestDefaults(pluginKey)
  }
}

function manifestDefaults(pluginKey) {
  const schema = pluginManifests.value[pluginKey]?.connection_schema
  return Object.fromEntries(
    Object.entries(schema?.properties || {}).map(([key, field]) => [
      key,
      field.default ?? (field.type === 'array' ? [] : '')
    ])
  )
}

function readConnectionField(row, writeTo) {
  if (!writeTo) return ''
  return writeTo.split('.').reduce((value, key) => value?.[key], row) ?? ''
}

function buildConnectionPayload() {
  const payload = {
    name: form.value.name.trim(),
    plugin_key: form.value.plugin_key,
    config: {},
    allowed_scope: {},
    status: form.value.status
  }
  const fields = manifest.value?.connection_schema?.properties || {}
  Object.entries(fields).forEach(([key, field]) => {
    const value = form.value[key]
    if (field.format === 'password' && !hasFieldValue(value)) return
    writeConnectionField(payload, field.write_to, value)
  })
  return payload
}

function clearSecretFields() {
  const fields = manifest.value?.connection_schema?.properties || {}
  Object.entries(fields).forEach(([key, field]) => {
    if (field.format === 'password') form.value[key] = ''
  })
}

function writeConnectionField(payload, writeTo, value) {
  if (!writeTo) return
  const [section, key] = writeTo.split('.')
  if (key) payload[section][key] = value
  else payload[section] = value
}

function resourceItemCount(resources) {
  return Object.values(resources || {}).reduce(
    (total, collection) =>
      total + (Array.isArray(collection?.items) ? collection.items.length : 0),
    0
  )
}

function requestRemoveRow(row) {
  deleteTarget.value = row
}

async function confirmRemoveRow() {
  const row = deleteTarget.value
  if (!row) return
  deleting.value = true
  try {
    await deleteConnection(row.uuid)
    showSuccess(t('lensAdmin.connections.deleteSuccess'))
    deleteTarget.value = null
    await load()
  } catch (error) {
    showError(
      lensNodeErrorMessage(error.response?.data?.detail, t) ||
        extractErrorMessage(error, t('lensAdmin.connections.deleteFailed'))
    )
  } finally {
    deleting.value = false
  }
}

watch(drawerOpen, (visible, previous) => {
  if (props.embedded && previous && !visible) emit('close')
})
onMounted(async () => {
  const loaded = await load()
  if (props.embedded) {
    if (!loaded) return emit('close')
    if (props.initialConnectionUuid) {
      const row = connections.value.find(
        (connection) => connection.uuid === props.initialConnectionUuid
      )
      if (row) startEdit(row)
      else {
        showError(t('lensAdmin.connections.loadFailed'))
        emit('close')
      }
    } else startCreate()
  }
})
onBeforeUnmount(revokePluginIconUrls)
</script>

<style>
.connection-form-input {
  @apply w-full rounded-lg border border-line bg-surface px-3 py-2.5 text-sm
    text-ink-900 shadow-sm transition-colors placeholder:text-ink-400
    hover:border-ink-300 focus:border-brand-500 focus:outline-none
    focus:ring-2 focus:ring-brand-500/20 disabled:cursor-not-allowed
    disabled:bg-surface-sunken disabled:text-ink-500;
}

.connection-toolbar-input {
  @apply w-full rounded-lg border border-line bg-surface px-3 py-2 text-sm
    text-ink-900 shadow-sm transition-colors placeholder:text-ink-400
    hover:border-ink-300 focus:border-brand-500 focus:outline-none
    focus:ring-2 focus:ring-brand-500/20;
}
</style>
