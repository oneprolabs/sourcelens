<template>
  <div class="space-y-4">
    <p class="text-sm text-theme-muted">
      {{ t('settings.modal.agentIntegrationDesc') }}
    </p>

    <div class="space-y-3 rounded-xl border border-line px-4 py-4">
      <div
        class="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between sm:gap-4"
      >
        <div class="min-w-0">
          <label
            for="agent-integration-validity"
            class="block text-sm font-medium text-theme"
          >
            {{ t('settings.modal.agentIntegrationValidity') }}
          </label>
          <p class="mt-1 text-xs leading-5 text-theme-muted">
            {{ t('settings.modal.agentIntegrationValidityDesc') }}
          </p>
        </div>
        <BaseSelect
          id="agent-integration-validity"
          v-model="lifetimeMonths"
          class="w-full shrink-0 sm:w-40"
          :full-width="false"
          :menu-min-width="160"
          :aria-label="t('settings.modal.agentIntegrationValidity')"
        >
          <option
            v-for="option in validityOptions"
            :key="option.value"
            :value="option.value"
          >
            {{ option.label }}
          </option>
        </BaseSelect>
      </div>

      <div class="flex items-start justify-between gap-4">
        <div class="min-w-0">
          <div class="text-sm font-medium text-theme">
            {{ t('settings.modal.agentIntegrationPromptTitle') }}
          </div>
          <p class="mt-1 text-xs leading-5 text-theme-muted">
            {{ t('settings.modal.agentIntegrationPromptDesc') }}
          </p>
        </div>
        <button
          type="button"
          :disabled="generating"
          class="shrink-0 rounded-lg border border-line px-3 py-1.5 text-xs font-medium text-theme-secondary transition-colors hover:bg-surface-hover hover:text-theme disabled:cursor-wait disabled:opacity-60"
          @click="generatePrompt"
        >
          {{ actionLabel }}
        </button>
      </div>

      <div v-if="prompt" class="space-y-2">
        <div
          class="flex flex-wrap items-center justify-between gap-2 text-xs text-theme-muted"
        >
          <span>{{ validityText }}</span>
          <button
            type="button"
            class="font-medium text-primary-600 transition-colors hover:text-primary-700"
            @click="copyPrompt"
          >
            {{
              copied
                ? t('settings.modal.agentIntegrationCopied')
                : t('settings.modal.agentIntegrationCopy')
            }}
          </button>
        </div>
        <pre
          class="max-h-56 overflow-auto whitespace-pre-wrap break-words rounded-lg bg-surface-sunken px-3 py-2 font-mono text-xs text-theme-secondary"
          >{{ prompt }}</pre
        >
      </div>
    </div>

    <AgentTokenList ref="tokenList" @revoked="handleRevoked" />
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'

import { generateAgentToken } from '@/api/auth'
import AgentTokenList from '@/components/settings/AgentTokenList.vue'
import BaseSelect from '@/components/ui/BaseSelect.vue'
import { useToast } from '@/composables/useToast'
import { extractErrorMessage, extractResponseData } from '@/utils/api'
import { copyToClipboard } from '@/utils/clipboard'
import { publicBaseUrl } from '@/utils/lens'

const { t, locale } = useI18n()
const { showError } = useToast()

const prompt = ref('')
const expiresAt = ref(null)
const generating = ref(false)
const copied = ref(false)
const lifetimeMonths = ref(1)
const tokenList = ref(null)
const currentTokenId = ref(null)

const validityOptions = computed(() => [
  ...[1, 3, 6].map((months) => ({
    value: months,
    label: t('settings.modal.agentIntegrationValidityMonths', { months })
  })),
  { value: 0, label: t('settings.modal.agentIntegrationNeverExpires') }
])

const actionLabel = computed(() => {
  if (generating.value) return t('settings.modal.agentIntegrationGenerating')
  if (prompt.value) return t('settings.modal.agentIntegrationRegenerate')
  return t('settings.modal.agentIntegrationGenerate')
})

const validityText = computed(() =>
  expiresAt.value === null
    ? t('settings.modal.agentIntegrationNeverExpires')
    : t('settings.modal.agentIntegrationExpires', {
        date: new Date(expiresAt.value * 1000).toLocaleDateString(locale.value)
      })
)

async function generatePrompt() {
  generating.value = true
  try {
    const payload = extractResponseData(
      await generateAgentToken(lifetimeMonths.value)
    )
    expiresAt.value = payload.expires_at
    currentTokenId.value = payload.id
    copied.value = false
    prompt.value = t('settings.modal.agentIntegrationPrompt', {
      url: publicBaseUrl(),
      token: payload.access,
      validity: validityText.value
    })
    await tokenList.value?.refresh()
  } catch (error) {
    showError(
      extractErrorMessage(error, t('settings.modal.agentIntegrationFailed'))
    )
  } finally {
    generating.value = false
  }
}

function handleRevoked(id) {
  if (currentTokenId.value === id) {
    prompt.value = ''
    copied.value = false
    currentTokenId.value = null
  }
}

async function copyPrompt() {
  if (await copyToClipboard(prompt.value)) {
    copied.value = true
    window.setTimeout(() => {
      copied.value = false
    }, 2400)
    return
  }
  showError(t('settings.modal.agentIntegrationCopyFailed'))
}
</script>
