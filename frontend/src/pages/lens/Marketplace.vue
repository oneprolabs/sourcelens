<template>
  <AdminLayout>
    <div class="marketplace-page">
      <header class="admin-page-heading">
        <div class="admin-heading-copy">
          <h1>
            {{
              t(
                connectorOnly
                  ? 'catalog.connectors'
                  : 'catalog.marketplaceTitle'
              )
            }}
          </h1>
          <p>
            {{
              t(
                connectorOnly
                  ? 'catalog.connectorHint'
                  : 'catalog.marketplaceHint'
              )
            }}
          </p>
        </div>
        <div class="catalog-header-actions">
          <BaseButton
            variant="ghost"
            size="sm"
            :loading="loading"
            :disabled="!!saving"
            :title="t('common.refresh')"
            @click="load"
            ><RefreshCw :size="15" aria-hidden="true" /><span class="sr-only">{{
              t('common.refresh')
            }}</span></BaseButton
          >
        </div>
      </header>
      <section v-if="!connectorOnly" class="catalog-capability-bar">
        <span class="catalog-capability-icon"
          ><Sparkles :size="16" aria-hidden="true"
        /></span>
        <div class="catalog-capability-copy">
          <h2>
            {{ t('catalog.capabilityTitle') }}
          </h2>
          <p>
            {{ t('catalog.capabilityHint') }}
          </p>
        </div>
        <p class="catalog-capability-counts">
          {{ t('catalog.skillCount', { count: enabledCount('skill') }) }} ·
          {{ t('catalog.mcpCount', { count: enabledCount('mcp') }) }}
        </p>
        <router-link
          to="/management/lens/resources/connections"
          class="text-sm text-brand-700 hover:underline"
          >{{
            t('catalog.connectionCount', { count: connectionCount })
          }}
          →</router-link
        >
      </section>
      <div v-if="!connectorOnly" class="catalog-tab-row">
        <div
          class="catalog-type-tabs"
          role="tablist"
          :aria-label="t('catalog.resourceType')"
        >
          <button
            v-for="tab in tabs"
            :key="tab.key"
            type="button"
            role="tab"
            :aria-selected="kind === tab.key"
            :tabindex="kind === tab.key ? 0 : -1"
            @keydown="navigateTabs($event, tab.key)"
            @click="kind = tab.key"
          >
            {{ tab.label }}
            <span class="catalog-count">{{ tab.count }}</span>
          </button>
        </div>
        <div
          v-if="kind !== 'connector'"
          class="catalog-segments"
          role="group"
          :aria-label="t('catalog.availability')"
        >
          <button
            v-for="option in ['enabled', 'discover']"
            :key="option"
            type="button"
            :aria-pressed="view === option"
            @click="view = option"
          >
            {{ t(`catalog.${option}`) }}
          </button>
        </div>
      </div>
      <div
        v-if="error"
        role="alert"
        class="flex flex-wrap items-center justify-between gap-3 rounded-lg bg-warning-50 p-4 text-sm text-warning-700"
      >
        <span>{{ error }}</span>
        <BaseButton
          size="sm"
          variant="outline"
          :loading="loading"
          @click="load"
          >{{ t('common.retry') }}</BaseButton
        >
      </div>
      <BaseLoading v-if="loading && !rows.length" />
      <div v-else class="space-y-6" :aria-busy="loading">
        <section
          v-for="section in sections"
          :key="section.key"
          class="catalog-section"
          :aria-labelledby="`catalog-section-${section.key}`"
        >
          <header class="catalog-section-heading">
            <h2 :id="`catalog-section-${section.key}`">
              {{ section.label }}
              <span class="catalog-count">{{ section.count }}</span>
            </h2>
            <div class="flex flex-wrap gap-3">
              <router-link
                v-if="
                  !connectorOnly &&
                  section.key !== 'connector' &&
                  view === 'enabled' &&
                  rows.some(
                    (row) =>
                      row.kind === section.key &&
                      !row.is_official &&
                      !row.tenant_enabled
                  )
                "
                :to="`/management/lens/resources/${section.key === 'skill' ? 'skills' : 'mcp'}`"
                class="self-center text-xs text-brand-700 hover:underline"
                >{{
                  t(
                    section.key === 'skill'
                      ? 'catalog.privateSkills'
                      : 'catalog.privateMcp'
                  )
                }}</router-link
              >
              <button
                v-if="kind === 'all' && section.hasMore"
                type="button"
                class="min-h-9 text-sm text-brand-700 hover:underline"
                @click="kind = section.key"
              >
                {{ t('catalog.viewAll') }}
              </button>
              <BaseButton
                v-if="
                  !connectorOnly &&
                  view === 'enabled' &&
                  section.key !== 'connector'
                "
                size="sm"
                variant="outline"
                :disabled="readonly || loading || !!saving"
                @click="openEditor(section.key)"
                >{{
                  t(
                    section.key === 'skill'
                      ? 'catalog.uploadSkill'
                      : 'catalog.addServer'
                  )
                }}</BaseButton
              >
            </div>
          </header>
          <p
            v-if="section.key === 'connector' && !connectorOnly"
            class="text-xs text-ink-500"
          >
            {{ t('catalog.connectionsManagedSeparately') }}
          </p>
          <ul v-if="section.rows.length" class="catalog-grid">
            <li
              v-for="row in section.rows"
              :key="row.kind + row.uuid"
              class="min-w-0"
            >
              <CatalogCard
                v-if="!connectorOnly"
                :row="row"
                :compact="view === 'enabled'"
                :loading="saving === row.kind + row.uuid"
                :disabled="readonly || loading || !!saving"
                @detail="openDetail"
                @toggle="toggle"
                @connect="openEditor('connector', $event)"
                @manage="openEditor($event.kind, $event)"
              />
              <button
                v-else
                type="button"
                class="h-full w-full rounded-lg border border-line bg-surface p-4 text-left hover:border-brand-300"
                @click="openDetail(row)"
              >
                <span class="font-semibold text-ink-900">{{ row.name }}</span>
                <span class="mt-2 block text-sm text-ink-500">{{
                  row.description
                }}</span>
                <span class="mt-3 block text-sm text-brand-700">{{
                  t('catalog.viewTools')
                }}</span>
              </button>
            </li>
          </ul>
          <div
            v-else
            class="rounded-lg border border-dashed border-line bg-surface px-4 py-8 text-center"
          >
            <p class="text-sm text-ink-500">
              {{
                t(
                  failedKinds.has(section.key)
                    ? 'catalog.sectionUnavailable'
                    : view === 'enabled' && section.key !== 'connector'
                      ? 'catalog.noEnabled'
                      : 'catalog.noPublished'
                )
              }}
            </p>
            <button
              v-if="
                view === 'enabled' &&
                section.key !== 'connector' &&
                !failedKinds.has(section.key)
              "
              type="button"
              class="mt-3 min-h-9 text-sm text-brand-700 hover:underline"
              @click="view = 'discover'"
            >
              {{ t('catalog.discover') }} →
            </button>
          </div>
        </section>
      </div>
    </div>
    <BaseDrawer
      :show="!!detail"
      :title="detail?.name || ''"
      @close="closeDetail"
    >
      <div v-if="detail" class="space-y-5">
        <BaseLoading v-if="detailLoading" />
        <div v-if="detailError" role="alert" class="text-sm text-warning-700">
          {{ detailError }}
          <BaseButton size="sm" variant="outline" @click="openDetail(detail)">{{
            t('common.retry')
          }}</BaseButton>
        </div>
        <p v-if="detail.kind !== 'connector'" class="text-sm text-ink-500">
          {{ t('catalog.bindingHint') }}
        </p>
        <MarkdownRenderer
          v-if="detail.kind === 'skill' && !detailLoading"
          :content="
            detail.definition?.skill_md ||
            detail.definition?.content ||
            detail.description ||
            ''
          "
        />
        <div v-else-if="!detailLoading" class="space-y-3 text-sm text-ink-700">
          <p class="break-words">{{ detail.description }}</p>
          <p>{{ detail.transport }}</p>
          <ul v-if="detail.tools?.length" class="divide-y divide-line">
            <li
              v-for="tool in detail.tools"
              :key="typeof tool === 'string' ? tool : tool.key"
              class="py-3"
            >
              <p class="break-all font-semibold">
                {{ typeof tool === 'string' ? tool : tool.key }}
              </p>
              <p v-if="tool.description" class="mt-1 break-words text-ink-500">
                {{ tool.description }}
              </p>
            </li>
          </ul>
          <p class="break-all">{{ detail.endpoint }}</p>
        </div>
        <div v-if="environment(detail).length">
          <h2 class="mb-2 font-semibold text-ink-900">
            {{ t('catalog.environment') }}
          </h2>
          <ul class="space-y-2 text-sm text-ink-600">
            <li
              v-for="item in environment(detail)"
              :key="item.name"
              class="break-words"
            >
              <code>{{ item.name }}</code> {{ item.description }}
            </li>
          </ul>
        </div>
      </div>
      <template #footer>
        <BaseButton
          v-if="detail && !connectorOnly && !detailLoading && !detailError"
          :disabled="readonly || loading || !!saving"
          :loading="saving === detail.kind + detail.uuid"
          @click="
            detail.kind === 'connector'
              ? openEditor('connector', detail)
              : detail.is_official
                ? toggle(detail)
                : openEditor(detail.kind, detail)
          "
          >{{
            t(
              detail.kind === 'connector'
                ? 'catalog.configureConnection'
                : !detail.is_official
                  ? 'common.edit'
                  : detail.tenant_enabled
                    ? 'catalog.disable'
                    : 'catalog.enable'
            )
          }}</BaseButton
        >
      </template>
    </BaseDrawer>
    <component
      :is="editors[editor]"
      v-if="editor"
      :key="editor"
      embedded
      v-bind="editorProps"
      @saved="editorSaved"
      @close="editor = ''"
    />
  </AdminLayout>
</template>

<script setup>
import { RefreshCw, Sparkles } from '@lucide/vue'
import {
  computed,
  defineAsyncComponent,
  onBeforeUnmount,
  ref,
  watch
} from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute } from 'vue-router'
import AdminLayout from '@/admin/layout/AdminLayout.vue'
import {
  getSkill,
  getPluginManifest,
  listConnections,
  listMcpServers,
  listPlugins,
  listSkills,
  setCatalogEnablement
} from '@/api/lens'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseDrawer from '@/components/ui/BaseDrawer.vue'
import BaseLoading from '@/components/ui/BaseLoading.vue'
import MarkdownRenderer from '@/components/ui/MarkdownRenderer.vue'
import { useToast } from '@/composables/useToast'
import { useUserStore } from '@/store/user'
import { extractErrorMessage } from '@/utils/api'
import { localizePluginManifest } from '@/utils/pluginI18n'
import CatalogCard from './components/CatalogCard.vue'

const { t, te, locale } = useI18n()
const route = useRoute()
const userStore = useUserStore()
const { showError } = useToast()
const connectorOnly = computed(() => !!route.meta.connectorOnly)
const readonly = computed(() => userStore.delegation?.mode === 'readonly')
const kind = ref('all')
const view = ref('discover')
const rows = ref([])
const connectionCount = ref('—')
const failedKinds = ref(new Set())
const detail = ref(null)
const detailLoading = ref(false)
const detailError = ref('')
const loading = ref(false)
const saving = ref('')
const error = ref('')
const editor = ref('')
const editorProps = ref({})
const editors = {
  skill: defineAsyncComponent(() => import('./Skills.vue')),
  mcp: defineAsyncComponent(() => import('./Mcp.vue')),
  connector: defineAsyncComponent(() => import('./Connections.vue'))
}
const typeLabels = computed(() => ({
  skill: t('catalog.skills'),
  mcp: 'MCP Servers',
  connector: t('catalog.connectors')
}))
const visibleRows = computed(() =>
  rows.value.filter(
    (row) =>
      row.kind === 'connector' ||
      (view.value === 'enabled' ? row.tenant_enabled : row.is_official)
  )
)
const tabs = computed(() =>
  ['all', 'skill', 'mcp', 'connector'].map((key) => ({
    key,
    label: key === 'all' ? t('catalog.all') : typeLabels.value[key],
    count: (key === 'all' ? failedKinds.value.size : failedKinds.value.has(key))
      ? '—'
      : visibleRows.value.filter((row) => key === 'all' || row.kind === key)
          .length
  }))
)
const sections = computed(() =>
  (kind.value === 'all' ? ['skill', 'mcp', 'connector'] : [kind.value]).map(
    (key) => {
      const items = visibleRows.value.filter((row) => row.kind === key)
      return {
        key,
        label: typeLabels.value[key],
        count: failedKinds.value.has(key) ? '—' : items.length,
        hasMore: items.length > 6,
        rows: kind.value === 'all' ? items.slice(0, 6) : items
      }
    }
  )
)
const enabledCount = (key) =>
  failedKinds.value.has(key)
    ? '—'
    : rows.value.filter((row) => row.kind === key && row.tenant_enabled).length
const environment = (row) =>
  (row.kind === 'skill' ? row.definition?.environment : row.environment) || []

function navigateTabs(event, key) {
  const keys = tabs.value.map((tab) => tab.key)
  const index = keys.indexOf(key)
  let next
  if (event.key === 'ArrowRight') next = (index + 1) % keys.length
  else if (event.key === 'ArrowLeft')
    next = (index + keys.length - 1) % keys.length
  else if (event.key === 'Home') next = 0
  else if (event.key === 'End') next = keys.length - 1
  else return
  event.preventDefault()
  kind.value = keys[next]
  event.currentTarget.parentElement
    .querySelectorAll('[role="tab"]')
    [next]?.focus()
}

let loadRequestId = 0
let detailRequestId = 0
let initialized = false
async function load() {
  if (saving.value) return
  const requestId = ++loadRequestId
  loading.value = true
  const resources = connectorOnly.value
    ? []
    : [
        {
          kind: 'skill',
          catalog: 'official',
          fetch: () => listSkills({ compact: true, catalog: 'official' })
        },
        {
          kind: 'skill',
          catalog: 'private',
          fetch: () => listSkills({ compact: true, catalog: 'private' })
        },
        {
          kind: 'mcp',
          catalog: 'official',
          fetch: () => listMcpServers({ catalog: 'official' })
        },
        {
          kind: 'mcp',
          catalog: 'private',
          fetch: () => listMcpServers({ catalog: 'private' })
        }
      ]
  resources.push({ kind: 'connector', catalog: 'official', fetch: listPlugins })
  if (!connectorOnly.value)
    resources.push({ kind: 'connection', fetch: listConnections })
  const results = await Promise.allSettled(
    resources.map((item) => item.fetch())
  )
  if (requestId !== loadRequestId) return
  const failures = []
  failedKinds.value = new Set()
  results.forEach((result, index) => {
    const resource = resources[index]
    if (result.status === 'rejected') {
      failures.push(result.reason)
      if (resource.kind === 'connection') connectionCount.value = '—'
      else failedKinds.value.add(resource.kind)
      return
    }
    if (resource.kind === 'connection') {
      connectionCount.value = result.value.length
      return
    }
    const incoming = result.value
      .filter(
        (row) => resource.kind !== 'connector' || row.key !== 'file_upload'
      )
      .map((row) => ({
        ...row,
        kind: resource.kind,
        catalog: resource.catalog,
        uuid: row.uuid || row.key,
        name:
          resource.kind === 'connector'
            ? localizePluginManifest(row, t, te).display_name
            : row.name,
        description:
          resource.kind === 'skill'
            ? (locale.value.startsWith('zh') &&
                row.definition?.description_zh) ||
              row.definition?.description ||
              ''
            : resource.kind === 'connector'
              ? localizePluginManifest(row, t, te).description
              : row.description || ''
      }))
    rows.value = rows.value
      .filter(
        (row) => row.kind !== resource.kind || row.catalog !== resource.catalog
      )
      .concat(incoming)
  })
  if (
    !initialized &&
    !failedKinds.value.has('skill') &&
    !failedKinds.value.has('mcp')
  ) {
    view.value = rows.value.some(
      (row) => row.kind !== 'connector' && row.tenant_enabled
    )
      ? 'enabled'
      : 'discover'
    initialized = true
  }
  error.value = failures.length ? t('catalog.partialLoadFailed') : ''
  loading.value = false
}
async function toggle(row) {
  if (
    readonly.value ||
    connectorOnly.value ||
    loading.value ||
    saving.value ||
    !row.is_official ||
    row.kind === 'connector'
  )
    return
  saving.value = row.kind + row.uuid
  try {
    const result = await setCatalogEnablement(
      row.kind,
      row.uuid,
      !row.tenant_enabled
    )
    row.tenant_enabled = result.tenant_enabled
    const listed = rows.value.find(
      (item) => item.kind === row.kind && item.uuid === row.uuid
    )
    if (listed) listed.tenant_enabled = result.tenant_enabled
    if (detail.value?.kind === row.kind && detail.value?.uuid === row.uuid)
      detail.value.tenant_enabled = result.tenant_enabled
  } catch (err) {
    showError(extractErrorMessage(err, t('lensAdmin.messages.saveFailed')))
  } finally {
    saving.value = ''
  }
}
function closeDetail() {
  detailRequestId++
  detail.value = null
  detailLoading.value = false
  detailError.value = ''
}
async function openDetail(row) {
  const requestId = ++detailRequestId
  detail.value = row
  detailLoading.value = row.kind !== 'mcp'
  detailError.value = ''
  try {
    const data =
      row.kind === 'connector'
        ? await getPluginManifest(row.uuid)
        : row.kind === 'skill'
          ? await getSkill(row.uuid)
          : row
    if (requestId === detailRequestId)
      detail.value = { ...row, ...data, kind: row.kind }
  } catch (err) {
    if (requestId === detailRequestId)
      detailError.value = extractErrorMessage(
        err,
        t('lensAdmin.messages.loadFailed')
      )
  } finally {
    if (requestId === detailRequestId) detailLoading.value = false
  }
}
function openEditor(key, row) {
  if (readonly.value || connectorOnly.value || loading.value || saving.value)
    return
  closeDetail()
  editorProps.value =
    key === 'connector'
      ? { initialPluginKey: row.uuid }
      : key === 'skill'
        ? {
            initialSkillUuid: row?.uuid || '',
            initialCreateMethod: row ? 'manual' : 'upload'
          }
        : { initialMcpUuid: row?.uuid || '' }
  editor.value = key
}
async function editorSaved() {
  editor.value = ''
  await load()
}
watch(
  connectorOnly,
  () => {
    kind.value =
      connectorOnly.value || route.query?.kind === 'connector'
        ? 'connector'
        : 'all'
    view.value = 'discover'
    initialized = false
    rows.value = []
    connectionCount.value = '—'
    failedKinds.value = new Set()
    closeDetail()
    editor.value = ''
    load()
  },
  { immediate: true }
)
onBeforeUnmount(() => {
  loadRequestId++
  detailRequestId++
})
</script>
