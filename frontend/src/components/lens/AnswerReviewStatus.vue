<template>
  <span
    v-if="status"
    class="text-xs text-theme-muted"
    data-testid="answer-review-status"
  >
    {{ t('lens.chat.answerReview.label') }}:
    {{ t(`lens.chat.answerReview.${status}`) }}
  </span>
</template>

<script setup>
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { answerReviewStatus } from '@/pages/lens/runtimeEvents'

const props = defineProps({ detail: { type: Object, default: null } })
const { t } = useI18n()
const status = computed(() => answerReviewStatus(props.detail))
</script>
