<template>
  <AdminLayout>
    <div class="flex max-w-full flex-col gap-4 py-4">
      <section class="overflow-hidden rounded-lg border border-line bg-surface shadow-sm">
        <header
          class="flex flex-col gap-4 border-b border-line px-5 py-4 md:flex-row md:items-start md:justify-between"
        >
          <div>
            <div class="flex flex-wrap items-center gap-2">
              <h1 class="text-xl font-semibold text-ink-900">
                {{ t('lensAdmin.pages.connections.title') }}
              </h1>
            </div>
            <p class="mt-1 text-sm text-ink-500">
              {{ t('lensAdmin.pages.connections.description') }}
            </p>
          </div>
          <div class="flex flex-wrap items-center gap-2">
            <BaseButton
              size="sm"
              variant="outline"
              :loading="loading"
              @click="load"
            >
              {{ t('common.refresh') }}
            </BaseButton>
            <BaseButton size="sm" variant="primary" @click="startCreate">
              {{ t('lensAdmin.pages.connections.action') }}
            </BaseButton>
          </div>
        </header>

        <div class="grid gap-3 border-b border-line bg-surface-sunken px-5 py-3 sm:grid-cols-3">
          <div class="flex items-center justify-between rounded-lg border border-line bg-surface px-4 py-3">
            <span class="text-sm text-ink-500">{{ t('lensAdmin.connections.total') }}</span>
            <strong class="text-xl font-semibold text-ink-900">{{ connections.length }}</strong>
          </div>
          <div class="flex items-center justify-between rounded-lg border border-line bg-surface px-4 py-3">
            <span class="text-sm text-ink-500">{{ t('lensAdmin.connections.activeCount') }}</span>
            <strong class="text-xl font-semibold text-success-700">{{ activeConnectionCount }}</strong>
          </div>
          <div class="flex items-center justify-between rounded-lg border border-line bg-surface px-4 py-3">
            <span class="text-sm text-ink-500">{{ t('lensAdmin.connections.pluginCount') }}</span>
            <strong class="text-xl font-semibold text-brand-700">{{ pluginCount }}</strong>
          </div>
        </div>

        <div class="px-5 py-4">
          <div class="connections-toolbar mb-4 flex flex-col gap-3 sm:flex-row sm:items-center">
            <input
              v-model="connectionSearch"
              class="connection-toolbar-input min-w-0 flex-1"
              type="search"
              :placeholder="t('lensAdmin.connections.searchPlaceholder')"
            />
            <BaseSelect v-model="connectionPluginFilter" class="sm:w-48">
              <option value="all">{{ t('lensAdmin.connections.allPlugins') }}</option>
              <option v-for="plugin in plugins" :key="plugin.key" :value="plugin.key">
                {{ pluginDisplayName(plugin.key) }}
              </option>
            </BaseSelect>
            <BaseSelect v-model="connectionStatusFilter" class="sm:w-36">
              <option value="all">{{ t('lensAdmin.connections.allStatuses') }}</option>
              <option value="active">{{ t('common.status.active') }}</option>
              <option value="disabled">{{ t('common.status.disabled') }}</option>
            </BaseSelect>
          </div>
          <BaseLoading v-if="loading && !connections.length" />
          <div
            v-else-if="!filteredConnections.length"
            class="rounded-xl border border-dashed border-line bg-surface-sunken px-6 py-16 text-center"
          >
            <p class="text-sm font-medium text-ink-700">
              {{ t('common.noData') }}
            </p>
            <p class="mt-1 text-sm text-ink-500">
              {{ t('lensAdmin.connections.emptyHint') }}
            </p>
          </div>
          <div v-else class="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
            <article
              v-for="row in filteredConnections"
              :key="row.uuid"
              class="connection-card group relative flex flex-col rounded-xl border border-line bg-surface p-4 shadow-sm transition hover:border-brand-200 hover:shadow-md"
            >
              <button
                type="button"
                class="absolute inset-0 rounded-xl focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500/30"
                :aria-label="`${t('common.viewDetails')}: ${row.name}`"
                @click="openConnectionDetail(row)"
              />
              <div class="pointer-events-none relative z-10 flex items-start gap-3">
                <div class="flex h-11 w-11 shrink-0 items-center justify-center overflow-hidden rounded-xl border border-line bg-surface-sunken">
                  <img v-if="pluginIconUrl(row.plugin_key)" :src="pluginIconUrl(row.plugin_key)" :alt="pluginDisplayName(row.plugin_key)" class="h-full w-full object-cover" />
                  <span v-else class="text-xs font-semibold uppercase text-brand-700">{{ row.plugin_key.slice(0, 2) }}</span>
                </div>
                <div class="min-w-0 flex-1">
                  <div class="flex items-start justify-between gap-2">
                    <div class="min-w-0">
                      <h2 class="truncate text-sm font-semibold text-ink-900">{{ row.name }}</h2>
                      <p class="mt-0.5 truncate text-xs text-ink-500">{{ pluginDisplayName(row.plugin_key) }}</p>
                    </div>
                    <span class="shrink-0 rounded-full border px-2 py-0.5 text-xs font-medium" :class="row.status === 'active' ? 'border-success-200 bg-success-50 text-success-700' : 'border-line bg-surface-sunken text-ink-500'">
                      {{ row.status === 'active' ? t('common.status.active') : t('common.status.disabled') }}
                    </span>
                  </div>
                  <div class="mt-2 flex flex-wrap gap-1.5">
                    <span
                      v-for="label in connectionUsageLabels(row)"
                      :key="label.key"
                      class="inline-flex items-center rounded-full border px-2 py-0.5 text-[11px] font-medium"
                      :class="label.className"
                    >
                      {{ label.text }}
                    </span>
                  </div>
                </div>
              </div>

              <div
                v-if="!row.has_secret"
                class="pointer-events-none relative z-10 mt-3"
              >
                <p class="inline-flex items-center gap-2 rounded-lg border border-warning-200 bg-warning-50 px-3 py-2 text-xs text-warning-800">
                  <span class="h-2 w-2 rounded-full bg-warning-500" />
                  {{ t('lensAdmin.connections.secretMissing') }}
                </p>
              </div>

              <div class="connection-usage-summary pointer-events-none relative z-10 mt-4 flex items-center justify-between border-t border-line pt-3">
                <p class="text-xs text-ink-500">
                  {{ row.datasource_count || 0 }} {{ t('lensAdmin.connections.datasources') }} ·
                  {{ row.assistant_count || 0 }} {{ t('lensAdmin.connections.assistants') }}
                </p>
                <div class="pointer-events-auto flex gap-2">
                  <BaseButton
                    size="sm"
                    variant="danger"
                    :disabled="row.datasource_count > 0 || row.assistant_count > 0"
                    @click.stop="removeRow(row)"
                  >
                    {{ t('common.delete') }}
                  </BaseButton>
                  <BaseButton
                    size="sm"
                    variant="outline"
                    @click.stop="startEdit(row)"
                  >
                    {{ t('common.edit') }}
                  </BaseButton>
                </div>
              </div>
            </article>
          </div>
        </div>
      </section>
    </div>

    <BaseDrawer
      :show="connectionDetailOpen"
      width="2xl"
      :title="detailConnection?.name || ''"
      :subtitle="detailConnection ? pluginDisplayName(detailConnection.plugin_key) : ''"
      @close="closeConnectionDetail"
    >
      <div v-if="detailConnection" class="space-y-4">
        <section class="overflow-hidden rounded-xl border border-line bg-surface">
          <div class="flex items-center gap-3 border-b border-line bg-surface-sunken p-4">
            <div class="flex h-12 w-12 shrink-0 items-center justify-center overflow-hidden rounded-xl border border-line bg-surface">
              <img v-if="pluginIconUrl(detailConnection.plugin_key)" :src="pluginIconUrl(detailConnection.plugin_key)" :alt="pluginDisplayName(detailConnection.plugin_key)" class="h-full w-full object-cover" />
              <span v-else class="text-xs font-semibold uppercase text-brand-700">{{ detailConnection.plugin_key.slice(0, 2) }}</span>
            </div>
            <div class="min-w-0 flex-1">
              <p class="font-medium text-ink-900">{{ pluginDisplayName(detailConnection.plugin_key) }}</p>
              <p class="mt-1 text-xs text-ink-500">{{ t('lensAdmin.connections.connectionOverview') }}</p>
            </div>
            <span class="rounded-full border px-2 py-1 text-xs font-medium" :class="detailConnection.status === 'active' ? 'border-success-200 bg-success-50 text-success-700' : 'border-line bg-surface text-ink-500'">
              {{ detailConnection.status === 'active' ? t('common.status.active') : t('common.status.disabled') }}
            </span>
          </div>
          <dl class="grid gap-px bg-line sm:grid-cols-2">
            <div class="bg-surface px-4 py-3">
              <dt class="text-xs text-ink-500">{{ t('lensAdmin.connections.endpoint') }}</dt>
              <dd class="mt-1 truncate font-mono text-sm text-ink-800">{{ detailConnection.endpoint || emptyValue }}</dd>
            </div>
            <div class="bg-surface px-4 py-3">
              <dt class="text-xs text-ink-500">{{ t('lensAdmin.connections.token') }}</dt>
              <dd class="mt-1 font-mono text-sm text-ink-800">
                {{ detailConnection.secret_hint || t('lensAdmin.connections.secretMissing') }}
              </dd>
            </div>
          </dl>
        </section>

        <section>
          <h3 class="text-sm font-semibold text-ink-900">{{ t('lensAdmin.connections.usage') }}</h3>
          <dl class="mt-2 grid gap-3 sm:grid-cols-2">
            <div class="rounded-xl border border-line bg-surface-sunken px-4 py-3">
              <dt class="text-xs text-ink-500">{{ t('lensAdmin.connections.datasources') }}</dt>
              <dd class="mt-1 text-xl font-semibold text-ink-900">{{ detailConnection.datasource_count || 0 }}</dd>
            </div>
            <div class="rounded-xl border border-line bg-surface-sunken px-4 py-3">
              <dt class="text-xs text-ink-500">{{ t('lensAdmin.connections.assistants') }}</dt>
              <dd class="mt-1 text-xl font-semibold text-ink-900">{{ detailConnection.assistant_count || 0 }}</dd>
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
            :tree-search-placeholder="t('lensAdmin.connections.resourceSearchPlaceholder')"
            :resource-search-empty-text="t('lensAdmin.connections.resourceSearchEmpty')"
            :resource-count-label="t('lensAdmin.connections.resourceCountLabel')"
            :selected-count-label="t('lensAdmin.connections.selectedCountLabel')"
          />
        </section>
        <p v-if="!detailConnection.has_secret" class="rounded-lg border border-warning-200 bg-warning-50 px-3 py-2 text-sm text-warning-800">
          {{ t('lensAdmin.connections.secretMissing') }}
        </p>
        <p v-if="validationResults[detailConnection.uuid]" class="rounded-lg border px-3 py-2 text-sm" :class="validationResults[detailConnection.uuid].ok ? 'border-success-200 bg-success-50 text-success-700' : 'border-danger-200 bg-danger-50 text-danger-700'">
          {{ validationResults[detailConnection.uuid].message }}
        </p>
      </div>
      <template #footer>
        <div class="flex flex-wrap justify-between gap-2">
          <BaseButton variant="danger" :disabled="!detailConnection || detailConnection.datasource_count > 0 || detailConnection.assistant_count > 0" @click="removeDetailConnection">{{ t('common.delete') }}</BaseButton>
          <div class="flex gap-2">
            <BaseButton variant="outline" :loading="validatingUuid === detailConnection?.uuid" :disabled="detailConnection?.status !== 'active'" @click="validateRow(detailConnection)">{{ t('lensAdmin.connections.validate') }}</BaseButton>
            <BaseButton variant="outline" :class="detailConnection?.status === 'active' ? 'text-danger-700' : 'text-success-700'" :loading="saving" @click="toggleDetailConnectionStatus">
              {{ detailConnection?.status === 'active' ? t('lensAdmin.connections.pause') : t('lensAdmin.connections.resume') }}
            </BaseButton>
            <BaseButton variant="primary" @click="editDetailConnection">{{ t('common.edit') }}</BaseButton>
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
      @close="closeDrawer"
    >
      <div class="space-y-5">
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
          <div v-if="mode === 'create'" class="mb-4 grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
            <button
              v-for="plugin in plugins"
              :key="plugin.key"
              type="button"
              class="flex items-center gap-3 rounded-lg border p-3 text-left transition focus:outline-none focus:ring-2 focus:ring-brand-500/20"
              :class="form.plugin_key === plugin.key ? 'border-brand-300 bg-brand-50' : 'border-line hover:border-brand-200 hover:bg-surface-sunken'"
              @click="handlePluginChange(plugin.key)"
            >
              <span class="flex h-9 w-9 shrink-0 items-center justify-center overflow-hidden rounded-lg border border-line bg-surface">
                <img v-if="pluginIconUrl(plugin.key)" :src="pluginIconUrl(plugin.key)" :alt="pluginDisplayName(plugin.key)" class="h-full w-full object-cover" />
                <span v-else class="text-xs font-semibold uppercase text-brand-700">{{ plugin.key.slice(0, 2) }}</span>
              </span>
              <span class="min-w-0">
                <span class="block truncate text-sm font-medium text-ink-900">{{ pluginDisplayName(plugin.key) }}</span>
                <span class="mt-0.5 block truncate text-xs text-ink-500">{{ plugin.version }}</span>
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
              <div class="flex h-10 items-center gap-2 rounded-lg border border-line bg-surface-sunken px-3 text-sm text-ink-700">
                <img v-if="pluginIconUrl(form.plugin_key)" :src="pluginIconUrl(form.plugin_key)" :alt="pluginDisplayName(form.plugin_key)" class="h-6 w-6 rounded-md" />
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
            :schema="
              localizedManifest.connection_schema || manifest.connection_schema
            "
            :resources="connectionResourceOptions"
            control-class="connection-form-input"
            :password-placeholder="storedSecretPlaceholder"
            :empty-resource-text="t('lensAdmin.connections.resourceTreeEmpty')"
            :tree-search-placeholder="t('lensAdmin.connections.resourceSearchPlaceholder')"
            :resource-search-empty-text="t('lensAdmin.connections.resourceSearchEmpty')"
            :resource-count-label="t('lensAdmin.connections.resourceCountLabel')"
            :selected-count-label="t('lensAdmin.connections.selectedCountLabel')"
            :private-resource-label="t('lensAdmin.connections.privateResource')"
            :select-option-label="t('lensAdmin.pluginForm.selectOption')"
            :loading-options-label="t('lensAdmin.pluginForm.loadingOptions')"
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

          <FeishuConnectionGuide
            v-if="form.plugin_key === 'feishu'"
          />
          <section
            v-if="form.plugin_key === 'feishu'"
            class="rounded-xl border border-brand-200 bg-brand-50 p-4"
          >
            <h3 class="text-sm font-semibold text-ink-900">
              {{ t('lensAdmin.connections.feishuSelfRegisterTitle') }}
            </h3>
            <p class="mt-1 text-xs text-ink-600">
              {{ t('lensAdmin.connections.feishuSelfRegisterHint') }}
            </p>
            <BaseButton
              class="mt-3"
              size="sm"
              variant="outline"
              :loading="feishuRegistering"
              :disabled="feishuRegistering"
              @click="startFeishuScan"
            >
              {{ t('lensAdmin.connections.feishuSelfRegisterAction') }}
            </BaseButton>
            <img
              v-if="feishuQr"
              :src="feishuQr"
              :alt="t('lensAdmin.connections.feishuSelfRegisterQrAlt')"
              class="mt-3 h-48 w-48 rounded border bg-white p-2"
            />
            <p
              v-if="feishuRegisterStatus"
              class="mt-2 text-xs text-ink-600"
            >
              {{ feishuRegisterStatus }}
            </p>
          </section>
          <GitHubConnectionGuide
            v-if="form.plugin_key === 'github'"
          />
          <GitLabConnectionGuide v-if="form.plugin_key === 'gitlab'" />
          <JiraConnectionGuide v-if="form.plugin_key === 'jira'" />
        </div>

        <p v-if="formError" class="text-sm text-danger-700">{{ formError }}</p>
      </div>
      <template #footer>
        <div class="flex justify-end gap-2">
          <BaseButton variant="outline" @click="closeDrawer">
            {{ t('common.cancel') }}
          </BaseButton>
          <BaseButton
            variant="primary"
            :loading="saving"
            :disabled="!canSave"
            @click="save"
          >
            {{ t('common.save') }}
          </BaseButton>
        </div>
      </template>
    </BaseDrawer>
  </AdminLayout>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'

import AdminLayout from '@/admin/layout/AdminLayout.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseDrawer from '@/components/ui/BaseDrawer.vue'
import BaseLoading from '@/components/ui/BaseLoading.vue'
import BaseSelect from '@/components/ui/BaseSelect.vue'
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
import {
  pollFeishuSelfRegister,
  startFeishuSelfRegister
} from '@/api/plugins'
import { useToast } from '@/composables/useToast'
import { extractErrorMessage } from '@/utils/api'
import {
  localizePluginManifest,
  pluginDisplayName as translatedPluginDisplayName
} from '@/utils/pluginI18n'
import { EMPTY_VALUE as emptyValue } from './adminHelpers'

const { t, te } = useI18n()
const { showError, showSuccess } = useToast()
const connections = ref([])
const plugins = ref([])
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
const pluginIconUrls = ref({})
const feishuRegistering = ref(false)
const feishuQr = ref('')
const feishuRegisterStatus = ref('')
let feishuPollTimer = null
let feishuExpiryTimer = null
const connectionDetailOpen = ref(false)
const detailConnection = ref(null)
const connectionResourceOptions = computed(() => ({
  [connectionResourceField.value?.[1]?.resource || '']: {
    items: connectionResourceCandidates.value
  }
}))

const activeConnectionCount = computed(
  () => connections.value.filter((row) => row.status === 'active').length
)
const pluginCount = computed(
  () => new Set(connections.value.map((row) => row.plugin_key)).size
)

const filteredConnections = computed(() => {
  const keyword = connectionSearch.value.trim().toLowerCase()
  return connections.value.filter((row) => {
    const matchesKeyword = !keyword ||
      [row.name, row.plugin_key, row.endpoint]
        .filter(Boolean)
        .some((value) => String(value).toLowerCase().includes(keyword))
    const matchesPlugin =
      connectionPluginFilter.value === 'all' ||
      row.plugin_key === connectionPluginFilter.value
    const matchesStatus =
      connectionStatusFilter.value === 'all' ||
      row.status === connectionStatusFilter.value
    return matchesKeyword && matchesPlugin && matchesStatus
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
const canDiscoverConnectionResources = computed(
  () => hasFieldValue(form.value.secret_value) || hasStoredSecret.value
)
const storedSecretPlaceholder = computed(() => {
  if (!hasStoredSecret.value) return ''
  return form.value.secret_hint || t('lensAdmin.connections.secretConfigured')
})

const canSave = computed(() => {
  if (!form.value.name.trim() || !manifest.value) return false
  const required = manifest.value.connection_schema?.required || []
  return required.every((key) => {
    const field = manifest.value.connection_schema.properties?.[key]
    if (mode.value === 'edit' && field?.format === 'password') return true
    return hasFieldValue(form.value[key])
  })
})

function defaultForm() {
  const pluginKey = plugins.value[0]?.key || ''
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
  const repositories = row.allowed_scope?.repositories
  if (Array.isArray(repositories)) return repositories
  const scope = row.allowed_scope || {}
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

function connectionUsageLabels(row) {
  const manifest = pluginManifests.value[row.plugin_key] || {}
  const labels = []
  const hasDatasource =
    Number(row.datasource_count || 0) > 0 || Boolean(manifest.datasource)
  const hasTool =
    Number(row.assistant_count || 0) > 0 ||
    (Array.isArray(manifest.tools) && manifest.tools.length > 0)
  if (hasDatasource) {
    labels.push({
      key: 'datasource',
      text: t('lensAdmin.connections.datasourceLabel'),
      className: 'border-amber-200 bg-amber-50 text-amber-700'
    })
  }
  if (hasTool) {
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
  } catch (error) {
    showError(extractErrorMessage(error, t('lensAdmin.connections.loadFailed')))
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

async function removeDetailConnection() {
  const row = detailConnection.value
  if (!row) return
  await removeRow(row)
  closeConnectionDetail()
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
  drawerOpen.value = false
  formError.value = ''
  connectionResourceCandidates.value = []
  stopFeishuPolling()
  feishuQr.value = ''
  feishuRegisterStatus.value = ''
}

async function startFeishuScan() {
  stopFeishuPolling()
  feishuRegistering.value = true
  feishuQr.value = ''
  feishuRegisterStatus.value = t(
    'lensAdmin.connections.feishuSelfRegisterWaiting'
  )
  try {
    const result = await startFeishuSelfRegister()
    if (!result?.device_code || !result?.qr_code_base64) {
      throw new Error(t('lensAdmin.connections.feishuSelfRegisterInvalid'))
    }
    feishuQr.value = result.qr_code_base64
    const deviceCode = result.device_code
    const interval = Math.max(5, Number(result.interval || 5)) * 1000
    const expiresIn = Math.max(1, Number(result.expires_in || 600)) * 1000
    let pollInFlight = false
    const poll = async () => {
      if (pollInFlight) return
      pollInFlight = true
      try {
        const status = await pollFeishuSelfRegister(deviceCode)
        feishuRegisterStatus.value = status.status || 'pending'
        if (status.status === 'success') {
          form.value.app_id = status.app_id
          form.value.app_secret = status.app_secret
          stopFeishuPolling()
          showSuccess(t('lensAdmin.connections.feishuSelfRegisterSuccess'))
          return
        }
        if (['denied', 'expired', 'error'].includes(status.status)) {
          stopFeishuPolling()
        }
      } catch (error) {
        stopFeishuPolling()
        feishuRegisterStatus.value = extractErrorMessage(
          error,
          t('lensAdmin.connections.feishuSelfRegisterFailed')
        )
      } finally {
        pollInFlight = false
      }
    }
    feishuPollTimer = window.setInterval(poll, interval)
    feishuExpiryTimer = window.setTimeout(() => {
      feishuRegisterStatus.value = t(
        'lensAdmin.connections.feishuSelfRegisterExpired'
      )
      stopFeishuPolling()
    }, expiresIn)
  } catch (error) {
    stopFeishuPolling()
    feishuRegisterStatus.value = extractErrorMessage(
      error,
      t('lensAdmin.connections.feishuSelfRegisterFailed')
    )
  }
}

function stopFeishuPolling() {
  if (feishuPollTimer) window.clearInterval(feishuPollTimer)
  if (feishuExpiryTimer) window.clearTimeout(feishuExpiryTimer)
  feishuPollTimer = null
  feishuExpiryTimer = null
  feishuRegistering.value = false
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

async function save() {
  saving.value = true
  formError.value = ''
  const payload = buildConnectionPayload()
  try {
    if (mode.value === 'create') {
      await createConnection(payload)
    } else {
      await updateConnection(form.value.uuid, payload)
    }
    clearSecretFields()
    showSuccess(t('lensAdmin.connections.saveSuccess'))
    closeDrawer()
    await load()
  } catch (error) {
    clearSecretFields()
    formError.value = extractErrorMessage(
      error,
      t('lensAdmin.connections.saveFailed')
    )
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
    detailConnection.value = connections.value.find(
      (connection) => connection.uuid === row.uuid
    ) || null
  } catch (error) {
    showError(extractErrorMessage(
      error,
      t('lensAdmin.connections.saveFailed')
    ))
  } finally {
    saving.value = false
  }
}

async function validateRow(row) {
  validatingUuid.value = row.uuid
  try {
    await validateConnection(row.uuid)
    const resources = await getConnectionResources(row.uuid)
    validationResults.value[row.uuid] = {
      ok: true,
      message: t('lensAdmin.connections.validationSuccess', {
        count: resourceItemCount(resources.resources)
      })
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

async function removeRow(row) {
  if (!window.confirm(t('lensAdmin.connections.deleteConfirm'))) return
  try {
    await deleteConnection(row.uuid)
    showSuccess(t('lensAdmin.connections.deleteSuccess'))
    await load()
  } catch (error) {
    showError(
      extractErrorMessage(error, t('lensAdmin.connections.deleteFailed'))
    )
  }
}

onMounted(load)
onBeforeUnmount(() => {
  stopFeishuPolling()
  revokePluginIconUrls()
})
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
