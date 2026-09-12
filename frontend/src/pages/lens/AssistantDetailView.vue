<template>
  <div
    class="assistant-detail-page mx-auto flex max-w-[1440px] flex-col gap-6 py-4"
    :class="{ 'assistant-detail-embedded': embedded }"
  >
    <div v-if="!embedded" class="flex items-center gap-2 text-sm text-ink-500">
      <button type="button" class="detail-back-link" @click="$emit('back')">
        {{ t('lensAdmin.pages.assistants.managementTitle') }}
      </button>
      <span aria-hidden="true">/</span>
      <span class="font-medium text-ink-700">{{
        t('lensAdmin.assistantDetail.title')
      }}</span>
    </div>

    <header
      class="flex flex-col gap-4 md:flex-row md:items-start md:justify-between"
    >
      <div class="flex min-w-0 items-start gap-3">
        <span
          class="assistant-detail-glyph"
          :class="{ 'assistant-detail-glyph-smart': isSmart }"
        >
          <UsersRound v-if="isSmart" :size="20" aria-hidden="true" />
          <Bot v-else :size="20" aria-hidden="true" />
        </span>
        <div class="min-w-0">
          <div class="flex flex-wrap items-center gap-2">
            <h1
              class="truncate text-2xl font-semibold tracking-tight text-ink-950"
            >
              {{ assistant.name || emptyValue }}
            </h1>
            <StatusBadge :status="assistant.status" />
          </div>
          <p class="mt-1 text-sm text-ink-500">
            <span class="font-mono">{{ assistant.slug || emptyValue }}</span>
            <span aria-hidden="true"> · </span>{{ assistantType }}
          </p>
        </div>
      </div>
      <div class="flex flex-wrap items-center gap-2">
        <BaseButton
          v-if="!embedded"
          variant="outline"
          size="sm"
          @click="$emit('back')"
        >
          <ArrowLeft :size="16" aria-hidden="true" />
          {{ t('lensAdmin.assistantDetail.back') }}
        </BaseButton>
        <BaseButton
          v-if="assistant.status === 'active'"
          variant="primary"
          size="sm"
          @click="$emit('edit', assistant)"
        >
          <Pencil :size="16" aria-hidden="true" />
          {{ t('common.edit') }}
        </BaseButton>
        <BaseButton
          v-else
          variant="primary"
          size="sm"
          :loading="restoring"
          @click="$emit('restore', assistant)"
        >
          {{ t('lensAdmin.pages.assistants.restore') }}
        </BaseButton>
      </div>
    </header>

    <nav
      class="detail-tabs flex gap-6 border-b border-line"
      :aria-label="t('lensAdmin.assistantDetail.title')"
    >
      <button
        v-for="tab in tabs"
        :key="tab.id"
        type="button"
        class="detail-tab"
        :class="{ 'detail-tab-active': activeTab === tab.id }"
        :aria-current="activeTab === tab.id ? 'page' : undefined"
        @click="activeTab = tab.id"
      >
        {{ tab.label }}
      </button>
    </nav>

    <div class="grid gap-6 lg:grid-cols-[minmax(0,1fr)_272px]">
      <main class="space-y-5">
        <template v-if="activeTab === 'overview'">
          <section class="detail-panel detail-overview-panel">
            <div class="detail-panel-heading">
              <h2>{{ t('lensAdmin.assistantDetail.overview') }}</h2>
            </div>
            <dl class="detail-definition-grid detail-overview-grid">
              <div>
                <dt>{{ t('lensAdmin.assistantDetail.identifier') }}</dt>
                <dd class="font-mono">{{ assistant.slug || emptyValue }}</dd>
              </div>
              <div>
                <dt>{{ t('lensAdmin.fields.type') }}</dt>
                <dd>{{ assistantType }}</dd>
              </div>
              <div>
                <dt>{{ t('lensAdmin.fields.agentModel') }}</dt>
                <dd>
                  {{
                    modelLabel(
                      assistant.agent_model_name,
                      assistant.agent_model_ref
                    )
                  }}
                </dd>
              </div>
              <div>
                <dt>{{ t('lensAdmin.fields.agentRounds') }}</dt>
                <dd>{{ agentRoundsLabel }}</dd>
              </div>
              <div>
                <dt>{{ t('lensAdmin.fields.multimodalModel') }}</dt>
                <dd>
                  {{
                    modelLabel(
                      assistant.multimodal_model_name,
                      assistant.multimodal_model_ref
                    )
                  }}
                </dd>
              </div>
              <div>
                <dt>{{ t('lensAdmin.fields.maxConcurrency') }}</dt>
                <dd>{{ assistant.max_concurrency ?? emptyValue }}</dd>
              </div>
              <div>
                <dt>{{ t('lensAdmin.fields.lensnode') }}</dt>
                <dd>
                  {{
                    lensnodeName ||
                    t('lensAdmin.assistantPresentation.automaticNode')
                  }}
                </dd>
              </div>
              <div>
                <dt>{{ t('lensAdmin.columns.updatedAt') }}</dt>
                <dd>
                  {{ assistant.updated_at || assistant.updated || emptyValue }}
                </dd>
              </div>
              <div class="detail-definition-wide">
                <dt>{{ t('lensAdmin.fields.description') }}</dt>
                <dd class="whitespace-pre-wrap">
                  {{
                    assistant.description ||
                    t('lensAdmin.wizard.noAssistantDescription')
                  }}
                </dd>
              </div>
            </dl>
          </section>
          <section class="detail-panel">
            <div class="detail-panel-heading">
              <h2>
                {{
                  isSmart
                    ? t('lensAdmin.assistantDetail.collaborationContext')
                    : t('lensAdmin.assistantDetail.workspaceContext')
                }}
              </h2>
            </div>
            <p class="detail-copy">
              {{
                assistant.workspace_guide?.content ||
                assistant.settings?.pre_prompt ||
                t('lensAdmin.assistantDetail.notConfigured')
              }}
            </p>
          </section>
        </template>

        <template v-else-if="activeTab === 'data'">
          <section class="detail-panel">
            <div class="detail-panel-heading">
              <h2>{{ t('lensAdmin.datasourceSelection.title') }}</h2>
              <span class="detail-chip">{{ dataModeLabel }}</span>
            </div>
            <ul
              v-if="assistant.datasource_bindings?.length"
              class="detail-record-list"
            >
              <li
                v-for="binding in assistant.datasource_bindings"
                :key="
                  binding.uuid ||
                  binding.datasource_uuid ||
                  binding.datasource_name
                "
              >
                <Database :size="17" aria-hidden="true" />
                <div>
                  <strong>
                    {{
                      binding.datasource_name ||
                      t('lensAdmin.assistantDetail.unnamedDatasource')
                    }}
                  </strong>
                  <small>{{
                    binding.item_name ||
                    t('lensAdmin.assistantPresentation.allItems')
                  }}</small>
                </div>
              </li>
            </ul>
            <p v-else class="detail-empty">
              {{ t('lensAdmin.assistantPresentation.noSources') }}
            </p>
          </section>
          <section v-if="isSmart" class="detail-panel">
            <div class="detail-panel-heading">
              <h2>{{ t('lensAdmin.assistantDetail.collaborationMembers') }}</h2>
            </div>
            <ul
              v-if="assistant.collaboration_members?.length"
              class="detail-record-list"
            >
              <li
                v-for="member in assistant.collaboration_members"
                :key="member.uuid"
              >
                <UsersRound :size="17" aria-hidden="true" />
                <div>
                  <strong>{{
                    member.name ||
                    t('lensAdmin.assistantDetail.unnamedAssistant')
                  }}</strong>
                  <small>{{ memberCapabilityLabel(member.capability) }}</small>
                </div>
              </li>
            </ul>
            <p v-else class="detail-empty">
              {{ t('lensAdmin.assistantDetail.noCollaborationMembers') }}
            </p>
          </section>
          <section
            v-if="detail.workspaceDirectories.length"
            class="detail-panel"
          >
            <div class="detail-panel-heading">
              <h2>{{ t('lensAdmin.assistantDetail.workspaceDirectories') }}</h2>
            </div>
            <ul class="detail-record-list">
              <li
                v-for="directory in detail.workspaceDirectories"
                :key="directory"
              >
                <Folder :size="17" aria-hidden="true" /><code>{{
                  directory
                }}</code>
              </li>
            </ul>
          </section>
        </template>

        <template v-else-if="activeTab === 'capabilities'">
          <section class="detail-panel">
            <div class="detail-panel-heading">
              <h2>{{ t('lensAdmin.assistantDetail.capabilities') }}</h2>
              <span class="detail-chip">{{ toolCount }}</span>
            </div>
            <div v-if="toolCount" class="capability-grid">
              <article
                v-for="group in capabilityGroups"
                :key="group.id"
                class="capability-card"
              >
                <h3>
                  <component :is="group.icon" :size="16" aria-hidden="true" />{{
                    group.label
                  }}<span>{{ group.items.length }}</span>
                </h3>
                <ul>
                  <li v-for="item in group.items" :key="item.name">
                    <span>{{ item.name || capabilityFallback(group.id) }}</span>
                    <StatusBadge
                      :status="item.enabled ? 'enabled' : 'disabled'"
                    />
                  </li>
                </ul>
              </article>
            </div>
            <p v-else class="detail-empty">
              {{ t('lensAdmin.assistantPresentation.noTools') }}
            </p>
          </section>
        </template>

        <template v-else>
          <section class="detail-panel">
            <div class="detail-panel-heading">
              <h2>{{ t('lensAdmin.assistantDetail.access') }}</h2>
              <span class="detail-chip">{{ visibilityLabel }}</span>
            </div>
            <div class="access-summary">
              <component
                :is="assistant.visibility === 'private' ? Lock : Globe"
                :size="18"
                aria-hidden="true"
              />
              <p>
                {{
                  assistant.visibility === 'private'
                    ? t('lensAdmin.visibility.privateDesc')
                    : t('lensAdmin.visibility.publicDesc')
                }}
              </p>
            </div>
          </section>
          <div
            v-if="assistant.visibility === 'private'"
            class="grid gap-5 md:grid-cols-2"
          >
            <section class="detail-panel">
              <div class="detail-panel-heading">
                <h2>{{ t('lensAdmin.access.groups') }}</h2>
              </div>
              <ul
                v-if="detail.authorizedGroups.length"
                class="detail-record-list"
              >
                <li v-for="group in detail.authorizedGroups" :key="group.id">
                  <UsersRound :size="17" aria-hidden="true" /><strong>{{
                    group.name || emptyValue
                  }}</strong>
                </li>
              </ul>
              <p v-else class="detail-empty">
                {{ t('lensAdmin.assistantDetail.noAuthorizedGroups') }}
              </p>
            </section>
            <section class="detail-panel">
              <div class="detail-panel-heading">
                <h2>{{ t('lensAdmin.access.users') }}</h2>
              </div>
              <ul
                v-if="detail.authorizedUsers.length"
                class="detail-record-list"
              >
                <li v-for="user in detail.authorizedUsers" :key="user.id">
                  <User :size="17" aria-hidden="true" />
                  <div>
                    <strong>{{ user.username || emptyValue }}</strong
                    ><small>{{ user.email || emptyValue }}</small>
                  </div>
                </li>
              </ul>
              <p v-else class="detail-empty">
                {{ t('lensAdmin.assistantDetail.noAuthorizedUsers') }}
              </p>
            </section>
          </div>
        </template>
      </main>

      <aside
        class="detail-summary-card h-fit rounded-xl border border-line bg-surface p-4"
      >
        <h2>{{ t('lensAdmin.assistantDetail.summary') }}</h2>
        <dl>
          <div>
            <dt>{{ t('lensAdmin.fields.routingMode') }}</dt>
            <dd>
              {{
                isSmart
                  ? t('lensAdmin.routingModes.smart')
                  : t('lensAdmin.routingModes.direct')
              }}
            </dd>
          </div>
          <div>
            <dt>
              {{
                isSmart
                  ? t('lensAdmin.assistantDetail.collaborationMembers')
                  : t('lensAdmin.datasourceSelection.title')
              }}
            </dt>
            <dd>
              {{
                isSmart
                  ? assistant.collaboration_members?.length || 0
                  : datasourceCount
              }}
            </dd>
          </div>
          <div>
            <dt>{{ t('lensAdmin.assistantDetail.skills') }}</dt>
            <dd>{{ detail.skills.length }}</dd>
          </div>
          <div>
            <dt>{{ t('lensAdmin.assistantDetail.mcpServers') }}</dt>
            <dd>{{ detail.mcps.length }}</dd>
          </div>
          <div>
            <dt>{{ t('lensAdmin.assistantDetail.access') }}</dt>
            <dd>{{ visibilityLabel }}</dd>
          </div>
        </dl>
        <BaseButton
          class="mt-4 w-full"
          variant="outline"
          :disabled="assistant.status !== 'active'"
          @click="$emit('copy-share', assistant)"
        >
          <Copy :size="16" aria-hidden="true" />{{ t('lens.share.copyLink') }}
        </BaseButton>
      </aside>
    </div>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import {
  ArrowLeft,
  BookOpen,
  Bot,
  Copy,
  Database,
  Folder,
  Globe,
  Lock,
  Pencil,
  Plug,
  Server,
  User,
  UsersRound
} from '@lucide/vue'

import BaseButton from '@/components/ui/BaseButton.vue'
import StatusBadge from '@/components/ui/StatusBadge.vue'
import { EMPTY_VALUE, formatAssistantType } from './adminHelpers'
import { buildAssistantDetail } from './assistantDetails'

const props = defineProps({
  assistant: { type: Object, required: true },
  lensnodeName: { type: String, default: '' },
  assistantType: { type: String, default: '' },
  restoring: { type: Boolean, default: false },
  embedded: { type: Boolean, default: false }
})

defineEmits(['back', 'copy-share', 'edit', 'restore'])

const { t } = useI18n()
const emptyValue = EMPTY_VALUE
const activeTab = ref('overview')
const detail = computed(() => buildAssistantDetail(props.assistant))
const isSmart = computed(
  () => (props.assistant.mode || props.assistant.routing_mode) === 'smart'
)
const datasourceCount = computed(
  () => props.assistant.datasource_bindings?.length || 0
)
const agentRoundsLabel = computed(() => {
  const value = props.assistant.agent_rounds
  return value ? t(`lensAdmin.agentRounds.${value}`) : emptyValue
})
const toolCount = computed(
  () =>
    detail.value.skills.length +
    detail.value.mcps.length +
    detail.value.plugins.length
)
const visibilityLabel = computed(() =>
  t(
    `lensAdmin.visibility.${props.assistant.visibility === 'private' ? 'private' : 'public'}`
  )
)
const dataModeLabel = computed(() =>
  t('lensAdmin.datasourceSelection.selected')
)
const capabilityGroups = computed(() =>
  [
    {
      id: 'skills',
      label: t('lensAdmin.assistantDetail.skills'),
      icon: BookOpen,
      items: detail.value.skills
    },
    {
      id: 'mcps',
      label: t('lensAdmin.assistantDetail.mcpServers'),
      icon: Server,
      items: detail.value.mcps
    },
    {
      id: 'plugins',
      label: t('lensAdmin.assistantPresentation.plugins'),
      icon: Plug,
      items: detail.value.plugins
    }
  ].filter((group) => group.items.length)
)
const tabs = computed(() => [
  { id: 'overview', label: t('lensAdmin.assistantDetail.overview') },
  { id: 'data', label: t('lensAdmin.assistantDetail.dataAccess') },
  { id: 'capabilities', label: t('lensAdmin.assistantDetail.capabilities') },
  { id: 'access', label: t('lensAdmin.assistantDetail.access') }
])

function modelLabel(name, ref) {
  if (name) return name
  return ref
    ? t('lensAdmin.assistantDetail.modelConfigured')
    : t('lensAdmin.assistantDetail.notConfigured')
}

function memberCapabilityLabel(value) {
  if (!value) return emptyValue
  return formatAssistantType(value, t)
}

function capabilityFallback(groupId) {
  const key = {
    skills: 'unnamedSkill',
    mcps: 'unnamedMcp',
    plugins: 'unnamedPlugin'
  }[groupId]
  return t(`lensAdmin.assistantDetail.${key || 'notConfigured'}`)
}
</script>

<style scoped>
.assistant-detail-page {
  color: var(--sl-text-primary);
}
.detail-back-link {
  color: var(--sl-text-muted);
}
.detail-back-link:hover {
  color: var(--sl-brand-strong);
}
.assistant-detail-glyph {
  display: grid;
  width: 2.75rem;
  height: 2.75rem;
  flex: none;
  place-items: center;
  border: 1px solid var(--sl-border-default);
  border-radius: 0.625rem;
  color: var(--sl-text-secondary);
}
.assistant-detail-glyph-smart {
  border-color: transparent;
  background: var(--sl-brand-soft);
  color: var(--sl-brand-strong);
}
.detail-tabs {
  overflow-x: auto;
  scrollbar-width: thin;
  white-space: nowrap;
}
.detail-tab {
  flex: 0 0 auto;
  min-height: 3rem;
  border: 0;
  border-bottom: 2px solid transparent;
  background: transparent;
  color: var(--sl-text-muted);
  font-size: 0.875rem;
  font-weight: 500;
}
.detail-tab:hover,
.detail-tab-active {
  color: var(--sl-brand-strong);
}
.detail-tab-active {
  border-bottom-color: var(--sl-brand-strong);
  font-weight: 650;
}
.detail-panel {
  overflow: hidden;
  border: 1px solid var(--sl-border-default);
  border-radius: 0.75rem;
  background: var(--sl-bg-surface);
}
.detail-panel-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.75rem;
  border-bottom: 1px solid var(--sl-border-soft);
  padding: 1rem 1.25rem;
}
.detail-panel-heading h2,
.detail-summary-card h2 {
  font-size: 1rem;
  font-weight: 650;
}
.detail-overview-panel .detail-panel-heading {
  padding: 0.75rem 1rem;
}
.detail-overview-grid {
  grid-template-columns: repeat(3, minmax(0, 1fr));
}
.detail-overview-grid > div {
  padding: 0.75rem 1rem;
}
.detail-overview-grid dd {
  margin-top: 0.2rem;
  font-size: 0.8125rem;
  line-height: 1.4;
}
.detail-chip {
  border: 1px solid var(--sl-border-default);
  border-radius: 999px;
  padding: 0.2rem 0.55rem;
  color: var(--sl-text-muted);
  font-size: 0.75rem;
  white-space: nowrap;
}
.detail-definition-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 1px;
  background: var(--sl-border-default);
}
.detail-definition-grid > div {
  min-width: 0;
  background: var(--sl-bg-surface);
  padding: 1rem 1.25rem;
}
.detail-definition-grid dt {
  color: var(--sl-text-muted);
  font-size: 0.75rem;
}
.detail-definition-grid dd {
  margin-top: 0.3rem;
  color: var(--sl-text-secondary);
  font-size: 0.875rem;
  overflow-wrap: anywhere;
}
.detail-definition-wide {
  grid-column: 1/-1;
}
.detail-copy {
  padding: 1.25rem;
  color: var(--sl-text-secondary);
  font-size: 0.875rem;
  line-height: 1.7;
  white-space: pre-wrap;
}
.detail-record-list {
  margin: 0;
  padding: 0;
  list-style: none;
}
.detail-record-list li {
  display: flex;
  align-items: flex-start;
  gap: 0.75rem;
  padding: 0.85rem 1.25rem;
  color: var(--sl-text-secondary);
}
.detail-record-list li + li {
  border-top: 1px solid var(--sl-border-soft);
}
.detail-record-list svg {
  margin-top: 0.1rem;
  flex: none;
  color: var(--sl-text-subtle);
}
.detail-record-list strong,
.detail-record-list code {
  min-width: 0;
  overflow-wrap: anywhere;
  font-size: 0.875rem;
  font-weight: 550;
}
.detail-record-list small {
  display: block;
  margin-top: 0.2rem;
  color: var(--sl-text-muted);
  font-size: 0.75rem;
}
.detail-empty {
  padding: 2rem 1.25rem;
  text-align: center;
  color: var(--sl-text-muted);
  font-size: 0.875rem;
}
.capability-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 1rem;
  padding: 1.25rem;
}
.capability-card {
  overflow: hidden;
  border: 1px solid var(--sl-border-default);
  border-radius: 0.625rem;
}
.capability-card h3 {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  border-bottom: 1px solid var(--sl-border-soft);
  padding: 0.75rem 1rem;
  color: var(--sl-text-secondary);
  font-size: 0.8125rem;
  font-weight: 600;
}
.capability-card h3 span {
  margin-left: auto;
  color: var(--sl-text-muted);
  font-family: ui-monospace, monospace;
  font-size: 0.75rem;
}
.capability-card li {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.75rem;
  padding: 0.7rem 1rem;
  font-size: 0.8125rem;
}
.capability-card li + li {
  border-top: 1px solid var(--sl-border-soft);
}
.capability-card li span {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.access-summary {
  display: flex;
  align-items: flex-start;
  gap: 0.75rem;
  padding: 1.25rem;
  color: var(--sl-text-secondary);
  font-size: 0.875rem;
}
.detail-summary-card dl {
  margin-top: 1rem;
}
.detail-summary-card dl > div {
  display: flex;
  justify-content: space-between;
  gap: 1rem;
  border-top: 1px solid var(--sl-border-soft);
  padding: 0.75rem 0;
  font-size: 0.8125rem;
}
.detail-summary-card dt {
  color: var(--sl-text-muted);
}
.detail-summary-card dd {
  color: var(--sl-text-secondary);
  font-weight: 600;
  text-align: right;
}
.detail-summary-card {
  position: sticky;
  top: 1rem;
}
@media (max-width: 767px) {
  .detail-overview-grid {
    grid-template-columns: 1fr;
  }
  .detail-overview-grid .detail-definition-wide {
    grid-column: auto;
  }
  .capability-grid {
    grid-template-columns: 1fr;
  }
  .detail-summary-card {
    position: static;
  }
}
@media (min-width: 768px) and (max-width: 1023px) {
  .detail-overview-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
</style>
