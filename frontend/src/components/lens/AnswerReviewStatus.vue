<template>
  <div v-if="status" class="answer-review" data-testid="answer-review-status">
    <button
      type="button"
      class="answer-review-chip"
      :class="`is-${status}`"
      :aria-expanded="details.length ? expanded : undefined"
      @click="details.length && (expanded = !expanded)"
    >
      <span class="answer-review-dot" aria-hidden="true" />
      <span>
        {{ t('lens.chat.answerReview.label') }}:
        {{ t(`lens.chat.answerReview.${status}`) }}
      </span>
      <svg
        v-if="details.length"
        class="answer-review-chevron"
        :class="{ 'is-open': expanded }"
        viewBox="0 0 12 12"
        fill="none"
        stroke="currentColor"
        stroke-width="1.6"
        aria-hidden="true"
      >
        <path
          d="M3 4.5 6 7.5 9 4.5"
          stroke-linecap="round"
          stroke-linejoin="round"
        />
      </svg>
    </button>
    <div v-if="expanded && details.length" class="answer-review-detail">
      <p v-for="item in details" :key="item">
        {{ item }}
      </p>
    </div>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { answerReviewStatus } from '@/pages/lens/runtimeEvents'

const props = defineProps({
  detail: { type: Object, default: null },
  showNotRequested: { type: Boolean, default: true }
})
const { t } = useI18n()

const expanded = ref(false)
const status = computed(() => {
  const value = answerReviewStatus(props.detail)
  return value === 'not_requested' && !props.showNotRequested ? null : value
})

const WARNING_KEYS = {
  evidence_incomplete: 'lens.chat.answerReview.warning.evidenceIncomplete',
  review_material_incomplete:
    'lens.chat.answerReview.warning.reviewMaterialIncomplete',
  review_unavailable: 'lens.chat.answerReview.warning.reviewUnavailable',
  review_inconclusive: 'lens.chat.answerReview.warning.reviewInconclusive'
}

const COVERAGE_KEYS = {
  complete: 'lens.chat.answerReview.coverage.complete',
  incomplete: 'lens.chat.answerReview.coverage.incomplete'
}

const details = computed(() => {
  const items = []
  const coverage = props.detail?.assessment?.coverage
  if (coverage && COVERAGE_KEYS[coverage]) {
    items.push(t(COVERAGE_KEYS[coverage]))
  }
  const warnings = Array.isArray(props.detail?.warnings)
    ? props.detail.warnings
    : []
  for (const warning of warnings) {
    if (WARNING_KEYS[warning]) items.push(t(WARNING_KEYS[warning]))
  }
  return items
})
</script>

<style scoped>
.answer-review {
  @apply flex flex-col items-start gap-1.5;
}

.answer-review-chip {
  @apply flex max-w-full items-center gap-2 rounded-full border px-3 py-1
    text-xs font-medium transition-colors;
}

.answer-review-chip.is-supported {
  border-color: #cfe7da;
  background: #f2faf5;
  color: #2e7d55;
}

.answer-review-chip.is-unsupported,
.answer-review-chip.is-inconclusive {
  border-color: #f3dfb4;
  background: #fff8e8;
  color: #8a5a00;
}

.answer-review-chip.is-not_requested {
  border-color: var(--sl-border-default);
  background: var(--sl-bg-canvas);
  color: var(--sl-text-muted);
}

.answer-review-chip:hover {
  filter: brightness(0.98);
}

.answer-review-dot {
  @apply h-1.5 w-1.5 shrink-0 rounded-full;
  background: currentColor;
}

.answer-review-chevron {
  @apply h-3 w-3 shrink-0 transition-transform;
}

.answer-review-chevron.is-open {
  transform: rotate(180deg);
}

.answer-review-detail {
  @apply max-w-2xl rounded-lg border px-3 py-2 text-xs leading-relaxed;
  border-color: #f6ebcf;
  background: #fffbf2;
  color: #6b5420;
}
</style>
