<template>
  <div
    v-if="summary && (summary.expiring_soon || summary.unused)"
    class="rounded-md border border-warning-200 bg-warning-50 px-4 py-3 text-sm text-warning-800"
    role="status"
  >
    <p v-if="summary.expiring_soon">
      {{
        t('management.apiKeyInsights.expiringSummary', {
          count: summary.expiring_soon
        })
      }}
    </p>
    <p v-if="summary.unused">
      {{
        t('management.apiKeyInsights.unusedSummary', { count: summary.unused })
      }}
    </p>
    <p class="mt-1">{{ t('management.apiKeyInsights.riskAdvice') }}</p>
  </div>
</template>

<script setup>
import { useI18n } from 'vue-i18n'

defineProps({ summary: { type: Object, default: null } })
const { t } = useI18n()
</script>
