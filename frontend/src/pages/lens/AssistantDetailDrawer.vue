<template>
  <BaseDrawer
    :show="show"
    :title="t('lensAdmin.assistantDetail.title')"
    :subtitle="assistant?.name || ''"
    width="xl"
    @close="$emit('close')"
  >
    <template #actions>
      <BaseButton
        v-if="assistant?.status === 'active'"
        size="sm"
        variant="outline"
        @click="$emit('edit', assistant)"
      >
        <Pencil :size="15" aria-hidden="true" />
        {{ t('common.edit') }}
      </BaseButton>
    </template>

    <div v-if="assistant" class="space-y-6">
      <div class="rounded-lg bg-surface-sunken p-4">
        <div class="flex flex-wrap items-center gap-2">
          <span class="text-sm font-medium text-ink-900">{{
            assistantType
          }}</span>
          <StatusBadge :status="assistant.status" />
          <span class="text-xs text-ink-500">{{
            t(`lensAdmin.visibility.${visibility}`)
          }}</span>
        </div>
        <p
          v-if="assistant.description"
          class="mt-3 whitespace-pre-wrap break-words text-sm text-ink-600"
        >
          {{ assistant.description }}
        </p>
      </div>
      <section class="space-y-3">
        <h3 class="detail-heading">
          {{ t('lensAdmin.assistantDetail.overview') }}
        </h3>
        <dl class="detail-overview">
          <div>
            <dt class="detail-label">{{ t('lensAdmin.fields.slug') }}</dt>
            <dd class="detail-value font-mono">
              {{ assistant.slug || emptyValue }}
            </dd>
          </div>
          <div>
            <dt class="detail-label">{{ t('lensAdmin.fields.lensnode') }}</dt>
            <dd class="detail-value">{{ lensnodeName }}</dd>
          </div>
          <div>
            <dt class="detail-label">
              {{ t('lensAdmin.fields.routingMode') }}
            </dt>
            <dd class="detail-value">
              {{
                t(
                  `lensAdmin.routingModes.${(assistant.mode || assistant.routing_mode) === 'smart' ? 'smart' : 'direct'}`
                )
              }}
            </dd>
          </div>
        </dl>
      </section>

      <section class="space-y-3" data-testid="assistant-detail-datasources">
        <div class="flex flex-wrap items-center justify-between gap-2">
          <h3 class="detail-heading">
            {{ t('lensAdmin.datasourceSelection.title') }}
          </h3>
          <span
            class="rounded bg-surface-sunken px-2 py-1 text-xs text-ink-600"
          >
            {{ t(`lensAdmin.datasourceSelection.${dataMode}`) }}
          </span>
        </div>
        <ul v-if="assistant.datasource_bindings?.length" class="detail-list">
          <li
            v-for="binding in assistant.datasource_bindings"
            :key="binding.uuid"
            class="flex items-start gap-3 px-4 py-3"
          >
            <Database class="mt-0.5 h-4 w-4 shrink-0 text-ink-400" />
            <div class="min-w-0">
              <p class="break-words text-sm font-medium text-ink-700">
                {{ binding.datasource_name }}
              </p>
              <p class="mt-1 break-words text-xs text-ink-500">
                {{
                  binding.item_name ||
                  t('lensAdmin.assistantPresentation.allItems')
                }}
              </p>
            </div>
          </li>
        </ul>
        <p v-else class="text-sm text-ink-500">
          {{
            t(
              `lensAdmin.assistantPresentation.${dataMode === 'selected' ? 'noSources' : 'allSources'}`
            )
          }}
        </p>
      </section>

      <section
        v-if="(assistant.mode || assistant.routing_mode) === 'smart'"
        data-testid="assistant-detail-collaboration-members"
        class="space-y-3"
      >
        <h3 class="detail-heading">
          {{ t('lensAdmin.assistantDetail.collaborationMembers') }}
        </h3>
        <ul v-if="assistant.collaboration_members?.length" class="detail-list">
          <li
            v-for="member in assistant.collaboration_members"
            :key="member.uuid"
            class="flex items-center justify-between gap-3 px-4 py-3"
          >
            <span class="min-w-0 truncate text-sm text-ink-700">
              {{ member.name }}
            </span>
            <span class="text-xs text-ink-400">{{ member.capability }}</span>
          </li>
        </ul>
        <p v-else class="detail-empty">
          {{ t('lensAdmin.assistantDetail.noCollaborationMembers') }}
        </p>
      </section>

      <section
        v-if="detail.workspaceDirectories.length"
        data-testid="assistant-detail-directories"
        class="space-y-3"
      >
        <h3 class="detail-heading">
          {{ t('lensAdmin.assistantDetail.workspaceDirectories') }}
        </h3>
        <ul v-if="detail.workspaceDirectories.length" class="detail-list">
          <li
            v-for="directory in detail.workspaceDirectories"
            :key="directory"
            class="flex items-start gap-2 px-4 py-3"
          >
            <Folder class="mt-0.5 h-4 w-4 shrink-0 text-ink-400" />
            <span class="break-all font-mono text-sm text-ink-700">
              {{ directory }}
            </span>
          </li>
        </ul>
        <p v-else class="detail-empty">
          {{ t('lensAdmin.assistantDetail.noDirectories') }}
        </p>
      </section>

      <section class="space-y-3">
        <h3 class="detail-heading">
          {{ t('lensAdmin.assistantDetail.capabilities') }}
        </h3>
        <p
          v-if="
            !detail.skills.length &&
            !detail.mcps.length &&
            !detail.plugins.length
          "
          class="text-sm text-ink-500"
        >
          {{ t('lensAdmin.assistantPresentation.noTools') }}
        </p>
        <div class="grid gap-4 sm:grid-cols-2">
          <div
            v-if="detail.skills.length"
            data-testid="assistant-detail-skills"
            class="overflow-hidden rounded-lg border border-line"
          >
            <div class="detail-card-heading">
              <BookOpen class="h-4 w-4 text-ink-400" />
              {{ t('lensAdmin.columns.skill') }}
            </div>
            <ul v-if="detail.skills.length" class="divide-y divide-line">
              <li
                v-for="skill in detail.skills"
                :key="skill.name"
                class="flex items-center justify-between gap-3 px-4 py-3"
              >
                <span class="min-w-0 truncate text-sm text-ink-700">
                  {{ skill.name }}
                </span>
                <StatusBadge :status="bindingStatus(skill)" />
              </li>
            </ul>
            <p v-else class="detail-empty border-0">
              {{ t('lensAdmin.assistantDetail.noSkills') }}
            </p>
          </div>

          <div
            v-if="detail.mcps.length"
            data-testid="assistant-detail-mcps"
            class="overflow-hidden rounded-lg border border-line"
          >
            <div class="detail-card-heading">
              <Server class="h-4 w-4 text-ink-400" />
              {{ t('lensAdmin.columns.mcpServer') }}
            </div>
            <ul v-if="detail.mcps.length" class="divide-y divide-line">
              <li
                v-for="mcp in detail.mcps"
                :key="mcp.name"
                class="flex items-center justify-between gap-3 px-4 py-3"
              >
                <span class="min-w-0 truncate text-sm text-ink-700">
                  {{ mcp.name }}
                </span>
                <StatusBadge :status="bindingStatus(mcp)" />
              </li>
            </ul>
            <p v-else class="detail-empty border-0">
              {{ t('lensAdmin.assistantDetail.noMcps') }}
            </p>
          </div>
        </div>
      </section>

      <section v-if="detail.plugins.length" class="space-y-3">
        <h3 class="detail-heading">
          {{ t('lensAdmin.assistantPresentation.plugins') }}
        </h3>
        <ul class="detail-list">
          <li
            v-for="plugin in detail.plugins"
            :key="plugin.name"
            class="flex items-center justify-between gap-3 px-4 py-3"
          >
            <span class="min-w-0 break-words text-sm text-ink-700">{{
              plugin.name
            }}</span>
            <StatusBadge :status="bindingStatus(plugin)" />
          </li>
        </ul>
      </section>

      <section
        v-if="visibility === 'private'"
        data-testid="assistant-detail-access"
        class="space-y-3"
      >
        <h3 class="detail-heading">
          {{ t('lensAdmin.assistantDetail.access') }}
        </h3>
        <p
          v-if="
            !detail.authorizedUsers.length && !detail.authorizedGroups.length
          "
          class="text-sm text-ink-500"
        >
          {{ t('lensAdmin.assistantPresentation.adminOnly') }}
        </p>
        <div class="grid gap-4 sm:grid-cols-2">
          <div
            v-if="detail.authorizedUsers.length"
            class="overflow-hidden rounded-lg border border-line"
          >
            <div class="detail-card-heading">
              <User class="h-4 w-4 text-ink-400" />
              {{ t('lensAdmin.access.users') }}
            </div>
            <ul
              v-if="detail.authorizedUsers.length"
              class="divide-y divide-line"
            >
              <li
                v-for="user in detail.authorizedUsers"
                :key="user.id"
                class="px-4 py-3"
              >
                <div class="text-sm font-medium text-ink-800">
                  {{ user.username || emptyValue }}
                </div>
                <div class="mt-0.5 break-all text-xs text-ink-500">
                  {{ user.email || emptyValue }}
                </div>
              </li>
            </ul>
            <p v-else class="detail-empty border-0">
              {{ t('lensAdmin.assistantDetail.noAuthorizedUsers') }}
            </p>
          </div>

          <div
            v-if="detail.authorizedGroups.length"
            class="overflow-hidden rounded-lg border border-line"
          >
            <div class="detail-card-heading">
              <Users class="h-4 w-4 text-ink-400" />
              {{ t('lensAdmin.access.groups') }}
            </div>
            <ul
              v-if="detail.authorizedGroups.length"
              class="divide-y divide-line"
            >
              <li
                v-for="group in detail.authorizedGroups"
                :key="group.id"
                class="px-4 py-3 text-sm text-ink-700"
              >
                {{ group.name || emptyValue }}
              </li>
            </ul>
            <p v-else class="detail-empty border-0">
              {{ t('lensAdmin.assistantDetail.noAuthorizedGroups') }}
            </p>
          </div>
        </div>
      </section>

      <section class="space-y-3">
        <h3 class="detail-heading">
          {{ t('lensAdmin.assistantDetail.share') }}
        </h3>
        <BaseButton
          variant="outline"
          :disabled="assistant.status !== 'active'"
          @click="$emit('copy-share', assistant)"
        >
          <Copy :size="16" aria-hidden="true" />
          {{ t('lens.share.copyLink') }}
        </BaseButton>
      </section>
    </div>
  </BaseDrawer>
</template>

<script setup>
import {
  BookOpen,
  Copy,
  Database,
  Folder,
  Pencil,
  Server,
  User,
  Users
} from '@lucide/vue'
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

import BaseButton from '@/components/ui/BaseButton.vue'
import BaseDrawer from '@/components/ui/BaseDrawer.vue'
import StatusBadge from '@/components/ui/StatusBadge.vue'

import { EMPTY_VALUE } from './adminHelpers'
import { assistantDataMode, buildAssistantDetail } from './assistantDetails'

const props = defineProps({
  show: Boolean,
  assistant: { type: Object, default: null },
  lensnodeName: { type: String, default: '' },
  assistantType: { type: String, default: '' }
})

defineEmits(['close', 'copy-share', 'edit'])

const { t } = useI18n()
const emptyValue = EMPTY_VALUE
const detail = computed(() => buildAssistantDetail(props.assistant || {}))
const dataMode = computed(() => assistantDataMode(props.assistant || {}))
const visibility = computed(() => props.assistant?.visibility || 'public')

function bindingStatus(binding) {
  return binding.enabled ? 'enabled' : 'disabled'
}
</script>

<style scoped>
.detail-overview {
  @apply grid gap-4 rounded-lg border border-line p-4;
  @apply sm:grid-cols-2;
}

.detail-list {
  @apply divide-y divide-line overflow-hidden rounded-lg;
  @apply border border-line;
}

.detail-heading {
  @apply text-sm font-semibold text-ink-900;
}

.detail-label {
  @apply text-xs font-medium uppercase tracking-wide text-ink-400;
}

.detail-value {
  @apply mt-1 break-words text-sm text-ink-700;
}

.detail-card-heading {
  @apply flex items-center gap-2 border-b border-line;
  @apply bg-surface-sunken px-4 py-3 text-sm font-medium text-ink-700;
}

.detail-empty {
  @apply rounded-lg border border-line bg-surface-sunken px-4 py-5;
  @apply text-center text-sm text-ink-400;
}
</style>
