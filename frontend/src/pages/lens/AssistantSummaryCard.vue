<template>
  <button
    type="button"
    class="assistant-summary-card"
    :class="{
      'has-warning': state.tone === 'warning' || state.tone === 'danger'
    }"
    @click="$emit('open', props.assistant)"
  >
    <div class="assistant-card-top">
      <span class="assistant-card-icon"
        ><Bot :size="18" aria-hidden="true"
      /></span>
      <span class="assistant-card-name">
        <strong>{{ assistant.name }}</strong>
        <small :title="assistant.slug">{{ assistant.slug }}</small>
      </span>
      <span class="assistant-state" :data-tone="state.tone">{{
        state.label
      }}</span>
    </div>
    <div
      v-if="assistant.management_checks?.issues?.length"
      class="assistant-card-warning"
      :data-tone="state.tone"
    >
      <TriangleAlert :size="14" aria-hidden="true" />
      <span>{{ issueLabel }}</span>
    </div>
    <div class="assistant-card-meta">
      <span :title="modelName"
        ><Zap :size="13" aria-hidden="true" />{{ modelName }}</span
      >
      <span :title="nodeName"
        ><Server :size="13" aria-hidden="true" />{{ nodeName }}</span
      >
      <span
        class="assistant-visibility"
        :data-visibility="assistant.visibility || 'private'"
        >{{
          t(
            `lensAdmin.assistantList.visibility.${assistant.visibility || 'private'}`
          )
        }}</span
      >
    </div>
    <div class="assistant-card-stats" data-testid="assistant-tool-counts">
      <span
        ><b>{{ formatNumber((assistant.datasource_bindings || []).length) }}</b
        >{{ t('lensAdmin.assistantList.knowledge') }}</span
      >
      <span
        ><b>{{ formatNumber(assistant.skill_summary?.enabled || 0) }}</b
        >{{ t('lensAdmin.assistantList.skills') }}</span
      >
      <span
        ><b>{{ formatNumber(assistant.mcp_summary?.enabled || 0) }}</b
        >MCP</span
      >
      <span
        ><b>{{ formatNumber(assistant.management_checks?.questions_30d) }}</b
        >{{ t('lensAdmin.assistantList.questions') }}</span
      >
    </div>
  </button>
</template>

<script setup>
import { Bot, Server, TriangleAlert, Zap } from '@lucide/vue'
import { useI18n } from 'vue-i18n'

const props = defineProps({
  assistant: { type: Object, required: true },
  state: { type: Object, required: true },
  modelName: { type: String, required: true },
  nodeName: { type: String, required: true },
  issueLabel: { type: String, default: '' }
})
defineEmits(['open'])
const { t, locale } = useI18n()
function formatNumber(value) {
  return Number.isFinite(value)
    ? new Intl.NumberFormat(locale.value).format(value)
    : '—'
}
</script>

<style scoped>
.assistant-summary-card {
  display: flex;
  flex-direction: column;
  gap: 12px;
  min-width: 0;
  padding: 16px;
  border: 1px solid var(--sl-border-default);
  border-radius: 10px;
  background: var(--sl-bg-surface);
  text-align: left;
  cursor: pointer;
}
.assistant-summary-card:hover {
  border-color: var(--sl-border-strong);
  box-shadow: 0 2px 6px rgb(23 24 28 / 5%);
}
.assistant-summary-card:focus-visible {
  outline: 2px solid var(--sl-accent);
  outline-offset: 3px;
}
.assistant-summary-card.has-warning {
  border-color: #bd7b22;
}
.assistant-card-top {
  display: flex;
  align-items: flex-start;
  gap: 12px;
}
.assistant-card-icon {
  display: grid;
  flex: none;
  width: 46px;
  height: 46px;
  place-items: center;
  border-radius: 12px;
  background: #e8eeff;
  color: #6384f4;
}
.assistant-card-name {
  display: flex;
  flex: 1;
  flex-direction: column;
  gap: 3px;
  min-width: 0;
}
.assistant-card-name strong {
  display: -webkit-box;
  overflow: hidden;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  font-size: 15px;
  font-weight: 600;
  overflow-wrap: anywhere;
}
.assistant-card-name small {
  overflow: hidden;
  color: var(--sl-text-subtle);
  font:
    400 12px/1.4 ui-monospace,
    monospace;
  white-space: nowrap;
  text-overflow: ellipsis;
}
.assistant-state {
  flex: none;
}
.assistant-card-warning {
  display: flex;
  align-items: center;
  gap: 6px;
  border-radius: 8px;
  padding: 7px 10px;
  background: #fdf4df;
  color: #bd7b22;
  font-size: 12.5px;
  line-height: 1.4;
}
.assistant-card-warning[data-tone='danger'] {
  background: #fdeceb;
  color: #d4503f;
}
.assistant-card-warning svg {
  flex: none;
}
.assistant-card-warning span {
  overflow-wrap: anywhere;
}
.assistant-card-meta {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px 14px;
  min-width: 0;
  color: var(--sl-text-muted);
  font:
    400 12.5px/1 ui-monospace,
    monospace;
}
.assistant-card-meta > span:not(.assistant-visibility) {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  max-width: 100%;
  overflow-wrap: anywhere;
}
.assistant-card-meta svg {
  flex: none;
}
.assistant-card-stats {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 6px;
  padding-top: 12px;
  border-top: 1px solid var(--sl-border-soft);
}
.assistant-card-stats > span {
  display: flex;
  flex-direction: column;
  gap: 3px;
  color: var(--sl-text-subtle);
  font-size: 11.5px;
  line-height: 1.2;
}
.assistant-card-stats b {
  color: var(--sl-text-primary);
  font-size: 16px;
  font-weight: 600;
  line-height: 1.1;
}
</style>
