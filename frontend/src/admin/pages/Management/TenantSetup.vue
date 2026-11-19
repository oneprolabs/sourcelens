<template>
  <AdminLayout>
    <div class="tenant-setup-page">
      <header class="admin-page-heading tenant-summary-heading">
        <div class="admin-heading-copy">
          <h1>
            {{ t('tenantSetup.title') }}
          </h1>
          <p>
            {{ t('tenantSetup.subtitle') }}
          </p>
        </div>
        <div class="tenant-summary-refresh">
          <BaseButton
            variant="ghost"
            size="sm"
            :loading="loading"
            :disabled="finishing"
            @click="load"
            ><RefreshCw :size="14" aria-hidden="true" />{{
              t('common.refresh')
            }}</BaseButton
          >
          <p v-if="overview" class="mt-2 text-xs text-ink-500">
            {{ t('platformOverview.generatedAt') }}
            {{ formatTime(overview.generated_at) }}
          </p>
        </div>
      </header>
      <div
        v-if="error"
        role="alert"
        class="rounded-lg border border-danger-200 bg-danger-50 p-4 text-sm text-danger-700"
      >
        {{ error }}
        <button
          class="ml-3 underline"
          :disabled="loading || finishing"
          @click="load"
        >
          {{ t('common.tryAgain') }}
        </button>
      </div>
      <BaseLoading v-if="loading && !overview" />
      <template v-if="overview">
        <section class="tenant-setup-summary">
          <div class="tenant-setup-summary-top">
            <div>
              <h2 class="font-semibold text-ink-900">
                {{
                  t(
                    overview.setup.complete
                      ? 'tenantSetup.configured'
                      : 'tenantSetup.getStarted'
                  )
                }}
              </h2>
              <p class="mt-1 text-sm text-ink-500">
                {{
                  t('tenantSetup.progress', {
                    done: completedRequired,
                    total: requiredCount
                  })
                }}
              </p>
            </div>
            <router-link
              v-if="overview.setup.complete && !userStore.delegation"
              to="/"
              class="text-sm font-medium text-brand-700 hover:underline"
              >{{ t('tenantSetup.tryChat') }} →</router-link
            >
            <BaseButton
              v-else-if="!overview.setup.complete && !readonly"
              size="sm"
              :disabled="finishing || loading"
              @click="openEditor(nextStage)"
              >{{ t('tenantSetup.continueSetup') }}</BaseButton
            >
          </div>
          <div
            class="tenant-setup-progress"
            role="progressbar"
            :aria-label="t('tenantSetup.title')"
            :aria-valuenow="completedRequired"
            :aria-valuemin="0"
            :aria-valuemax="requiredCount"
          >
            <i
              :style="{
                width: `${(completedRequired / requiredCount) * 100}%`
              }"
            />
          </div>
        </section>
        <p class="tenant-setup-intro">{{ t('tenantSetup.intro') }}</p>
        <p
          v-if="readonly"
          role="status"
          class="rounded-lg bg-warning-50 p-4 text-sm text-warning-700"
        >
          {{ t('tenantSetup.readonly') }}
        </p>
        <ol class="tenant-setup-stages">
          <li
            v-for="(stage, index) in stages"
            :key="stage.key"
            :id="`setup-stage-${stage.key}`"
            :aria-current="
              (activeStage || nextStage) === stage.key ? 'step' : undefined
            "
            class="tenant-setup-stage"
            :data-state="stage.state"
          >
            <div class="tenant-setup-rail">
              <span class="tenant-setup-dot"
                ><Check
                  v-if="stage.state === 'done'"
                  :size="16"
                  aria-hidden="true"
                /><template v-else>{{ index + 1 }}</template></span
              >
            </div>
            <section class="tenant-setup-stage-body">
              <div class="tenant-setup-stage-top">
                <div class="min-w-0 flex-1">
                  <div class="tenant-setup-stage-title">
                    <component
                      :is="
                        stage.key === 'model'
                          ? KeyRound
                          : stage.key === 'datasource'
                            ? Database
                            : stage.key === 'assistant'
                              ? Bot
                              : Users
                      "
                      :size="16"
                      aria-hidden="true"
                    />
                    <h2 tabindex="-1" class="font-semibold text-ink-900">
                      {{ t(stage.title) }}
                    </h2>
                    <span
                      class="tenant-setup-requirement"
                      :data-optional="stage.key === 'members'"
                      >{{ t(stage.requirement) }}</span
                    >
                    <span class="tenant-setup-state">{{
                      t(`tenantSetup.states.${stage.state}`)
                    }}</span>
                    <div class="tenant-setup-stage-actions">
                      <BaseButton
                        v-if="stage.key !== 'members' || !userStore.delegation"
                        size="sm"
                        :variant="
                          stage.state === 'done' || stage.state === 'optional'
                            ? 'outline'
                            : 'primary'
                        "
                        :disabled="readonly || finishing"
                        @click="openEditor(stage.key)"
                        >{{ t(stage.action) }}</BaseButton
                      >
                      <BaseButton
                        v-if="stage.key === 'datasource' && canSkipDatasource"
                        size="sm"
                        variant="outline"
                        :disabled="readonly || finishing"
                        @click="skipDatasource"
                        >{{ t('tenantSetup.skipDatasource') }}</BaseButton
                      >
                      <router-link
                        v-if="stage.key !== 'members' || !userStore.delegation"
                        :to="stage.to"
                        class="text-sm text-brand-700 hover:underline"
                        >{{
                          t(
                            stage.key === 'members'
                              ? 'tenantSetup.manageMembers'
                              : 'tenantSetup.advanced'
                          )
                        }}</router-link
                      >
                    </div>
                  </div>
                  <p class="tenant-setup-stage-description">
                    {{ t(stage.description) }}
                  </p>
                  <p class="tenant-setup-resource-count">
                    {{ t('tenantSetup.resourceCount', { count: stage.count }) }}
                  </p>
                  <ul v-if="stage.items?.length" class="tenant-setup-chips">
                    <li
                      v-for="item in stage.items"
                      :key="item.uuid"
                      class="tenant-setup-chip"
                    >
                      {{ item.name || item.provider }}
                    </li>
                  </ul>
                </div>
              </div>
              <div
                v-if="stage.key === 'datasource'"
                class="tenant-setup-connections"
              >
                <div class="flex flex-wrap items-center justify-between gap-3">
                  <h3 class="text-sm font-medium text-ink-700">
                    {{
                      t('tenantSetup.connectionCount', {
                        count: overview.setup.connection_count || 0
                      })
                    }}
                    <span class="ml-2 text-xs text-ink-500">{{
                      t('tenantSetup.optional')
                    }}</span>
                  </h3>
                  <div class="flex flex-wrap items-center gap-3">
                    <BaseButton
                      size="sm"
                      variant="outline"
                      :disabled="readonly || finishing"
                      @click="openConnection()"
                    >
                      {{ t('tenantSetup.addConnection') }}
                    </BaseButton>
                    <router-link
                      to="/management/lens/connections"
                      class="text-sm text-brand-700 hover:underline"
                    >
                      {{ t('tenantSetup.advanced') }}
                    </router-link>
                  </div>
                </div>
                <ul
                  v-if="overview.setup.connections?.length"
                  class="mt-3 flex flex-wrap gap-2"
                >
                  <li
                    v-for="connection in overview.setup.connections"
                    :key="connection.uuid"
                    class="min-w-0 max-w-full"
                  >
                    <button
                      type="button"
                      class="max-w-full break-words rounded-md bg-surface-sunken px-2 py-1 text-left text-xs text-ink-700 hover:text-brand-700 disabled:cursor-default"
                      :disabled="readonly || finishing"
                      @click="openConnection(connection.uuid)"
                    >
                      {{ connection.name }} · {{ connection.plugin_key }} ·
                      {{ t(`common.status.${connection.status}`) }}
                    </button>
                  </li>
                </ul>
              </div>
            </section>
          </li>
        </ol>
      </template>
    </div>
    <component
      :is="editors[editor]"
      v-if="editor && editor !== 'members'"
      :key="editor"
      embedded
      :continue-label="continueLabel"
      v-bind="
        editor === 'datasource'
          ? {
              skipLabel: canSkipDatasource
                ? t('tenantSetup.skipDatasource')
                : '',
              onSkip: skipDatasource,
              onConnectionSaved: load
            }
          : editor === 'connection'
            ? { initialConnectionUuid: selectedConnectionUuid }
            : {}
      "
      @saved="saved"
      @close="closeEditor"
    />
    <TenantInvitationDrawer
      :show="editor === 'members'"
      @close="closeEditor"
      @saved="saved"
    />
  </AdminLayout>
</template>

<script setup>
import { Bot, Check, Database, KeyRound, RefreshCw, Users } from '@lucide/vue'
import { computed, defineAsyncComponent, nextTick, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { getTenantOverview } from '@/api/tenant'
import { useUserStore } from '@/store/user'
import AdminLayout from '@/admin/layout/AdminLayout.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseLoading from '@/components/ui/BaseLoading.vue'
import TenantInvitationDrawer from './TenantInvitationDrawer.vue'

const { t, locale } = useI18n()
const userStore = useUserStore()
const overview = ref(null)
const loading = ref(false)
const error = ref('')
const editor = ref('')
const finishing = ref(false)
const activeStage = ref('')
const selectedConnectionUuid = ref('')
const readonly = computed(() => userStore.delegation?.mode === 'readonly')
const canSkipDatasource = computed(
  () => overview.value?.setup?.knowledge_required === false
)
const editors = {
  model: defineAsyncComponent(() => import('@/admin/pages/LLM/Config.vue')),
  connection: defineAsyncComponent(
    () => import('@/pages/lens/Connections.vue')
  ),
  datasource: defineAsyncComponent(
    () => import('@/pages/lens/DataSources.vue')
  ),
  assistant: defineAsyncComponent(() => import('@/pages/lens/Assistants.vue'))
}
const continueLabel = computed(() => {
  const next = {
    model: 'datasource',
    connection: 'datasource',
    datasource: 'assistant',
    assistant: 'members'
  }[editor.value]
  if (!next || (next === 'members' && userStore.delegation)) return ''
  return t('tenantSetup.saveAndContinue', { stage: t(`tenantSetup.${next}`) })
})
const requiredCount = computed(() =>
  overview.value?.setup?.knowledge_required ? 3 : 2
)
const completedRequired = computed(() => {
  const setup = overview.value?.setup || {}
  return (
    Number(Boolean(setup.model_configured)) +
    Number(Boolean(setup.assistant_configured)) +
    Number(Boolean(setup.knowledge_required && setup.datasource_configured))
  )
})
const stages = computed(() => {
  const data = overview.value || {}
  const setup = data.setup || {}
  return [
    {
      key: 'model',
      title: 'tenantSetup.model',
      description: 'tenantSetup.modelHint',
      requirement: 'tenantSetup.required',
      count: setup.model_count || 0,
      items: setup.models,
      state: setup.model_configured ? 'done' : 'pending',
      action: 'tenantSetup.addModel',
      to: '/management/llm/config'
    },
    {
      key: 'datasource',
      title: 'tenantSetup.datasource',
      description: 'tenantSetup.datasourceHint',
      requirement: 'tenantSetup.knowledgeRequirement',
      count: data.datasource_count || 0,
      items: setup.datasources,
      state: setup.datasource_configured
        ? 'done'
        : setup.knowledge_required
          ? 'pending'
          : 'skipped',
      action: 'tenantSetup.addDatasource',
      to: '/management/lens/datasources'
    },
    {
      key: 'assistant',
      title: 'tenantSetup.assistant',
      description: 'tenantSetup.assistantHint',
      requirement: 'tenantSetup.required',
      count: data.assistant_count || 0,
      items: setup.assistants,
      state: setup.assistant_configured ? 'done' : 'pending',
      action: 'tenantSetup.addAssistant',
      to: '/management/lens/assistants'
    },
    {
      key: 'members',
      title: 'tenantSetup.members',
      description: 'tenantSetup.membersHint',
      requirement: 'tenantSetup.optional',
      count: data.member_count || 0,
      state: data.member_count > 1 ? 'done' : 'optional',
      action: 'invitations.title',
      to: '/management/users'
    }
  ]
})
const nextStage = computed(
  () => stages.value.find((stage) => stage.state === 'pending')?.key || ''
)
function formatTime(value) {
  return value ? new Date(value).toLocaleString(locale.value) : '—'
}
async function load() {
  loading.value = true
  try {
    overview.value = await getTenantOverview()
    error.value = ''
    return true
  } catch (e) {
    error.value =
      e?.response?.data?.detail || e?.message || t('tenantOverview.loadFailed')
    return false
  } finally {
    loading.value = false
  }
}
function openEditor(key) {
  if (key === 'members' && userStore.delegation) return
  if (!readonly.value && !finishing.value) {
    activeStage.value = key === 'connection' ? 'datasource' : key
    editor.value = key
  }
}
function closeEditor() {
  if (!finishing.value) {
    if (editor.value === 'connection') load()
    editor.value = ''
  }
}
function openConnection(uuid = '') {
  if (readonly.value || finishing.value) return
  selectedConnectionUuid.value = uuid
  openEditor('connection')
}
function skipDatasource() {
  if (canSkipDatasource.value) openEditor('assistant')
}
async function saved({ advance = false } = {}) {
  if (finishing.value) return
  const previous = editor.value
  finishing.value = true
  const refreshed = await load()
  editor.value = ''
  await nextTick()
  finishing.value = false
  if (!advance || !refreshed || readonly.value) return
  const next = {
    model: 'datasource',
    connection: 'datasource',
    datasource: 'assistant',
    assistant: 'members'
  }[previous]
  if (!next || (next === 'members' && userStore.delegation)) return
  activeStage.value = next
  await nextTick()
  const stage = document.getElementById(`setup-stage-${next}`)
  stage?.scrollIntoView({ block: 'nearest' })
  if (next === 'members')
    stage?.querySelector('h2')?.focus({ preventScroll: true })
  else openEditor(next)
}
onMounted(load)
</script>
