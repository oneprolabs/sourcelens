<template>
  <section
    class="space-y-3"
    :aria-label="t('settings.modal.agentIntegrationTokens')"
  >
    <div class="flex items-center justify-between gap-3">
      <h3 class="text-sm font-medium text-theme">
        {{ t('settings.modal.agentIntegrationTokens') }}
      </h3>
      <button
        type="button"
        class="text-xs text-theme-secondary hover:text-theme disabled:opacity-60"
        :disabled="loading"
        @click="refresh"
      >
        {{ t('common.refresh') }}
      </button>
    </div>
    <p v-if="loading" class="text-xs text-theme-muted" role="status">
      {{ t('common.loading') }}
    </p>
    <p v-else-if="loadError" class="text-xs text-danger-700" role="alert">
      {{ t('settings.modal.agentIntegrationTokensFailed') }}
    </p>
    <p v-else-if="!tokens.length" class="text-xs text-theme-muted">
      {{ t('settings.modal.agentIntegrationTokensEmpty') }}
    </p>
    <ul v-else class="divide-y divide-line rounded-xl border border-line px-3">
      <li
        v-for="token in tokens"
        :key="token.id"
        class="flex items-center justify-between gap-3 py-3"
      >
        <div class="min-w-0 space-y-1">
          <p class="font-mono text-xs text-theme">{{ token.token_hint }}</p>
          <p class="text-xs text-theme-muted">
            {{
              t('settings.modal.agentIntegrationTokenCreated', {
                date: formatDate(token.created_at)
              })
            }}
          </p>
          <p class="text-xs text-theme-muted">
            {{
              token.expires_at === null
                ? t('settings.modal.agentIntegrationNeverExpires')
                : t('settings.modal.agentIntegrationExpires', {
                    date: formatDate(token.expires_at)
                  })
            }}
          </p>
        </div>
        <button
          type="button"
          class="shrink-0 rounded-lg border border-line px-3 py-1.5 text-xs text-danger-700 hover:bg-surface-hover disabled:opacity-60"
          :disabled="revokingId !== null"
          :aria-label="
            t('settings.modal.agentIntegrationRevokeNamed', {
              token: token.token_hint
            })
          "
          @click="revoke(token.id)"
        >
          {{ t('settings.modal.agentIntegrationRevoke') }}
        </button>
      </li>
    </ul>
  </section>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'

import { listAgentTokens, revokeAgentToken } from '@/api/auth'
import { useToast } from '@/composables/useToast'
import { extractErrorMessage, extractResponseData } from '@/utils/api'

const emit = defineEmits(['revoked'])
const { t, locale } = useI18n()
const { showError } = useToast()
const tokens = ref([])
const loading = ref(true)
const loadError = ref(false)
const revokingId = ref(null)

function formatDate(value) {
  return new Date(value).toLocaleDateString(locale.value)
}

async function refresh() {
  loading.value = true
  loadError.value = false
  try {
    tokens.value = extractResponseData(await listAgentTokens()).tokens
  } catch {
    loadError.value = true
  } finally {
    loading.value = false
  }
}

async function revoke(id) {
  revokingId.value = id
  try {
    await revokeAgentToken(id)
    tokens.value = tokens.value.filter((token) => token.id !== id)
    emit('revoked', id)
  } catch (error) {
    showError(
      extractErrorMessage(
        error,
        t('settings.modal.agentIntegrationRevokeFailed')
      )
    )
  } finally {
    revokingId.value = null
  }
}

onMounted(refresh)
defineExpose({ refresh })
</script>
