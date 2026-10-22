<template>
  <div class="runtime-assistant-groups">
    <details
      v-for="group in groups"
      :key="group.key"
      :open="live || (groups.length === 1 && !group.assistantName)"
      class="runtime-assistant-card"
      :class="{ 'is-direct': groups.length === 1 && !group.assistantName }"
    >
      <summary
        v-if="groups.length > 1 || group.assistantName"
        class="runtime-assistant-summary"
      >
        <span
          class="runtime-assistant-status-indicator"
          :class="{ 'is-live': live }"
          aria-hidden="true"
        >
          {{ live ? '' : '✓' }}
        </span>
        <span class="runtime-assistant-summary-content">
          <span class="runtime-assistant-heading">
            <strong>{{ group.assistantName }}</strong>
            <span class="runtime-assistant-status-text">
              {{
                live
                  ? t('lens.chat.runtime.running')
                  : t('lens.chat.runtime.completed')
              }}
            </span>
          </span>
          <span v-if="group.tasks[0]" class="runtime-assistant-task-summary">
            {{ group.tasks[0] }}
          </span>
        </span>
        <span class="runtime-assistant-chevron" aria-hidden="true">⌄</span>
      </summary>

      <div class="runtime-assistant-details">
        <div
          v-if="group.summaryItems.length"
          class="runtime-assistant-activity-list"
        >
          <div
            v-for="(activity, index) in group.summaryItems"
            :key="activity.kind"
            class="runtime-assistant-activity"
          >
            <span
              v-if="live && index === group.summaryItems.length - 1"
              class="runtime-assistant-activity-live"
              aria-hidden="true"
            />
            <span v-else aria-hidden="true">✓</span>
            <span>{{ activityLabel(activity.kind) }}</span>
            <span
              v-if="activity.count > 1"
              class="runtime-assistant-activity-count"
            >
              ×{{ activity.count }}
            </span>
          </div>
        </div>

        <details v-if="group.tasks.length" class="runtime-assistant-full-task">
          <summary>{{ t('lens.chat.runtime.fullTask') }}</summary>
          <p v-for="task in group.tasks" :key="task">{{ task }}</p>
        </details>
      </div>
    </details>
  </div>
</template>

<script setup>
import { useI18n } from 'vue-i18n'

defineProps({
  groups: {
    type: Array,
    default: () => []
  },
  live: {
    type: Boolean,
    default: false
  }
})

const { t } = useI18n()

function activityLabel(kind) {
  const known = new Set([
    'analyzingResults',
    'findingCapability',
    'preparingOutput',
    'queryingData',
    'readingContext',
    'readingSources',
    'searchingSources',
    'usingCapability'
  ])
  const safeKind = known.has(kind) ? kind : 'usingCapability'
  return t(`lens.chat.runtime.activity.${safeKind}`)
}
</script>

<style scoped>
.runtime-assistant-groups {
  display: grid;
  gap: 0.5rem;
  margin-bottom: 0.5rem;
}

.runtime-assistant-card {
  overflow: hidden;
  border: 1px solid #ebebee;
  border-radius: 0.625rem;
  background: var(--sl-bg-raised);
}

.runtime-assistant-card.is-direct {
  overflow: visible;
  border: 0;
  border-radius: 0;
  background: transparent;
}

.runtime-assistant-card.is-direct > .runtime-assistant-details {
  padding: 0;
  border-top: 0;
}

.runtime-assistant-summary {
  display: flex;
  align-items: flex-start;
  gap: 0.625rem;
  padding: 0.625rem 0.875rem;
  background: #fcfcfc;
  cursor: pointer;
  list-style: none;
}

.runtime-assistant-summary::-webkit-details-marker {
  display: none;
}

.runtime-assistant-summary-content {
  min-width: 0;
  flex: 1;
}

.runtime-assistant-heading {
  display: flex;
  align-items: baseline;
  gap: 0.375rem;
  color: var(--sl-text-primary);
  font-size: 0.8125rem;
}

.runtime-assistant-status-text {
  color: #71717a;
  font-size: 0.75rem;
  font-weight: 400;
}

.runtime-assistant-status-indicator {
  display: inline-flex;
  width: 1.125rem;
  height: 1.125rem;
  flex: 0 0 auto;
  align-items: center;
  justify-content: center;
  border-radius: 999px;
  background: #e5f4ec;
  color: #2e9e6a;
  font-size: 0.625rem;
}

.runtime-assistant-status-indicator.is-live {
  border: 2px solid #d6e1fa;
  border-top-color: #2f62d6;
  background: #eef3fd;
  animation: runtime-assistant-spin 0.75s linear infinite;
}

.runtime-assistant-task-summary {
  display: -webkit-box;
  margin-top: 0.125rem;
  overflow: hidden;
  color: #71717a;
  font-size: 0.78125rem;
  line-height: 1.1rem;
  overflow-wrap: anywhere;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
}

.runtime-assistant-chevron {
  flex: 0 0 auto;
  color: #a1a1aa;
  font-size: 0.75rem;
  line-height: 1;
  transition: transform 0.15s ease;
}

.runtime-assistant-card[open]
  > .runtime-assistant-summary
  .runtime-assistant-chevron {
  transform: rotate(180deg);
}

.runtime-assistant-details {
  padding: 0.375rem 0.875rem 0.75rem;
  border-top: 1px solid #f2f2f4;
}

.runtime-assistant-activity-list {
  display: grid;
  gap: 0.125rem;
  padding-top: 0.125rem;
}

.runtime-assistant-activity {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.375rem 0;
  color: #3f3f46;
  font-size: 0.8125rem;
  line-height: 1.1875rem;
}

.runtime-assistant-activity > span:nth-child(2) {
  min-width: 0;
}

.runtime-assistant-activity > span:first-child {
  color: #2e9e6a;
  font-size: 0.6875rem;
}

.runtime-assistant-activity-live {
  width: 0.625rem;
  height: 0.625rem;
  flex: 0 0 auto;
  border: 2px solid #d6e1fa;
  border-top-color: #2f62d6;
  border-radius: 999px;
  animation: runtime-assistant-spin 0.75s linear infinite;
}

.runtime-assistant-activity-count {
  color: #a1a1aa;
  font-size: 0.71875rem;
  font-variant-numeric: tabular-nums;
}

.runtime-assistant-full-task {
  margin-top: 0.375rem;
  color: #71717a;
  font-size: 0.75rem;
}

.runtime-assistant-full-task > summary {
  width: fit-content;
  min-height: 1.5rem;
  cursor: pointer;
  color: #2f62d6;
}

.runtime-assistant-full-task p {
  margin: 0.25rem 0 0;
  padding: 0.5rem 0.625rem;
  border-radius: 0.5rem;
  background: #fafafa;
  color: #3f3f46;
  line-height: 1.45;
  overflow-wrap: anywhere;
  white-space: pre-wrap;
}

@keyframes runtime-assistant-spin {
  to {
    transform: rotate(360deg);
  }
}

@media (prefers-reduced-motion: reduce) {
  .runtime-assistant-status-indicator.is-live {
    animation: none;
  }

  .runtime-assistant-chevron {
    transition: none;
  }
}
</style>
