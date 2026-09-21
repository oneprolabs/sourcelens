<template>
  <BaseDrawer
    :show="show"
    :title="t('lens.chat.citations.viewerTitle')"
    :subtitle="citation?.path || ''"
    width="2xl"
    @close="$emit('close')"
  >
    <BaseLoading v-if="loading" :message="t('lens.chat.citations.loading')" />

    <div v-else-if="error" class="citation-error" role="alert">
      <AlertCircle :size="24" aria-hidden="true" />
      <p>{{ error }}</p>
      <BaseButton variant="outline" size="sm" @click="$emit('retry')">
        {{ t('lens.chat.citations.retry') }}
      </BaseButton>
    </div>

    <div v-else-if="citation" class="citation-viewer">
      <dl class="citation-meta">
        <div>
          <dt>{{ t('lens.chat.citations.revision') }}</dt>
          <dd>{{ citation.revision }}</dd>
        </div>
        <div v-if="citation.symbol">
          <dt>{{ t('lens.chat.citations.symbol') }}</dt>
          <dd>{{ citation.symbol }}</dd>
        </div>
        <div>
          <dt>{{ t('lens.chat.citations.lines') }}</dt>
          <dd>
            {{ citation.highlight_start_line }}–{{
              citation.highlight_end_line
            }}
          </dd>
        </div>
      </dl>

      <p v-if="citation.supports" class="citation-supports">
        {{ citation.supports }}
      </p>

      <div v-if="isDocumentSource" class="citation-view-switch" role="tablist">
        <button
          type="button"
          role="tab"
          class="citation-view-tab"
          :class="{ 'citation-view-tab-active': view === 'preview' }"
          :aria-selected="view === 'preview'"
          @click="view = 'preview'"
        >
          {{ t('lens.chat.citations.preview') }}
        </button>
        <button
          type="button"
          role="tab"
          class="citation-view-tab"
          :class="{ 'citation-view-tab-active': view === 'source' }"
          :aria-selected="view === 'source'"
          @click="view = 'source'"
        >
          {{ t('lens.chat.citations.source') }}
        </button>
      </div>

      <div
        v-if="isDocumentSource && view === 'preview'"
        class="citation-preview"
      >
        <MarkdownRenderer :content="previewContent" />
      </div>

      <div v-else class="citation-code" tabindex="0">
        <div
          v-for="line in citation.lines"
          :key="line.number"
          class="citation-code-line"
          :class="{
            'citation-code-line-highlighted': isHighlighted(line.number)
          }"
        >
          <span class="citation-line-number" aria-hidden="true">
            {{ line.number }}
          </span>
          <code>{{ line.content || ' ' }}</code>
        </div>
      </div>
    </div>
  </BaseDrawer>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { AlertCircle } from '@lucide/vue'
import { useI18n } from 'vue-i18n'

import BaseButton from '@/components/ui/BaseButton.vue'
import BaseDrawer from '@/components/ui/BaseDrawer.vue'
import BaseLoading from '@/components/ui/BaseLoading.vue'
import MarkdownRenderer from '@/components/ui/MarkdownRenderer.vue'
import { isDocumentCitationPath } from '@/pages/lens/codeCitations'
import { normalizeMarkdownTables } from '@/utils/documentPreview'

const props = defineProps({
  show: { type: Boolean, default: false },
  citation: { type: Object, default: null },
  loading: { type: Boolean, default: false },
  error: { type: String, default: '' }
})

defineEmits(['close', 'retry'])

const { t } = useI18n()

const view = ref('preview')

watch(
  () => props.citation,
  () => {
    view.value = 'preview'
  }
)

const isDocumentSource = computed(() =>
  isDocumentCitationPath(props.citation?.path)
)

const previewContent = computed(() =>
  normalizeMarkdownTables(
    (props.citation?.lines || []).map((line) => line.content).join('\n')
  )
)

function isHighlighted(lineNumber) {
  if (!props.citation) return false
  return (
    lineNumber >= props.citation.highlight_start_line &&
    lineNumber <= props.citation.highlight_end_line
  )
}
</script>

<style scoped>
.citation-error {
  @apply flex min-h-48 flex-col items-center justify-center gap-3 rounded-lg border border-line bg-surface-sunken p-6 text-center text-sm text-theme-muted;
}

.citation-viewer {
  @apply space-y-4;
}

.citation-meta {
  @apply flex flex-wrap gap-x-6 gap-y-3 rounded-lg border border-line bg-surface-sunken px-4 py-3;
}

.citation-meta dt {
  @apply text-xs text-theme-subtle;
}

.citation-meta dd {
  @apply mt-0.5 break-all font-mono text-xs font-medium text-theme;
}

.citation-supports {
  @apply text-sm leading-6 text-theme-secondary;
}

.citation-view-switch {
  @apply inline-flex rounded-lg border border-line bg-surface-sunken p-0.5;
}

.citation-view-tab {
  @apply rounded-md px-3 py-1 text-sm font-medium text-theme-muted transition-colors;
}

.citation-view-tab-active {
  @apply bg-surface text-theme shadow-sm;
}

.citation-preview {
  @apply max-h-[70vh] overflow-auto rounded-lg border border-line px-4 py-3;
}

.citation-preview :deep(table) {
  width: max-content;
  min-width: 100%;
}

.citation-preview :deep(th),
.citation-preview :deep(td) {
  @apply whitespace-nowrap px-3 py-1.5 align-top;
}

.citation-code {
  @apply max-h-[70vh] overflow-auto rounded-lg border border-line py-2 font-mono text-xs outline-none focus-visible:ring-2 focus-visible:ring-primary-500;
  color: var(--sl-code-text);
  background: var(--sl-code-bg);
}

.citation-code-line {
  @apply flex min-w-max border-l-2 border-transparent pr-4 leading-6;
}

.citation-code-line-highlighted {
  @apply border-primary-400;
  background: var(--sl-code-highlight);
}

.citation-line-number {
  @apply mr-4 w-14 flex-shrink-0 select-none text-right;
  color: var(--sl-code-line-number);
}

.citation-code code {
  @apply whitespace-pre;
}
</style>
