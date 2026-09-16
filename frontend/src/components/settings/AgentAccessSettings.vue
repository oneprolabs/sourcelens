<template>
  <div class="space-y-4">
    <p class="text-sm text-theme-muted">
      {{ t('settings.modal.agentAccessDesc') }}
    </p>

    <div class="space-y-3 rounded-xl border border-line px-4 py-4">
      <div class="flex items-center justify-between gap-4">
        <div class="min-w-0">
          <div class="text-sm font-medium text-theme">
            {{ t('settings.modal.agentAccessValidity') }}
          </div>
          <p class="mt-1 text-xs leading-5 text-theme-muted">
            {{ t('settings.modal.agentAccessValidityDesc') }}
          </p>
        </div>
        <BaseSelect
          id="agent-access-validity"
          v-model="lifetimeMonths"
          class="w-32 shrink-0"
          :full-width="false"
          size="sm"
          :aria-label="t('settings.modal.agentAccessValidity')"
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
            {{ t('settings.modal.agentAccessPromptTitle') }}
          </div>
          <p class="mt-1 text-xs leading-5 text-theme-muted">
            {{ t('settings.modal.agentAccessPromptDesc') }}
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
        <pre
          class="max-h-56 overflow-auto whitespace-pre-wrap break-words rounded-lg bg-surface-sunken px-3 py-2 font-mono text-xs text-theme-secondary"
          >{{ prompt }}</pre
        >
        <div
          class="flex flex-wrap items-center justify-between gap-2 text-xs text-theme-muted"
        >
          <span>{{ t('settings.modal.agentAccessExpires', { date }) }}</span>
          <button
            type="button"
            class="font-medium text-primary-600 transition-colors hover:text-primary-700"
            @click="copyPrompt"
          >
            {{
              copied
                ? t('settings.modal.agentAccessCopied')
                : t('settings.modal.agentAccessCopy')
            }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'

import { generateMcpToken } from '@/api/auth'
import BaseSelect from '@/components/ui/BaseSelect.vue'
import { useToast } from '@/composables/useToast'
import { extractErrorMessage, extractResponseData } from '@/utils/api'
import { copyToClipboard } from '@/utils/clipboard'
import { publicBaseUrl } from '@/utils/lens'

const { t, locale } = useI18n()
const { showError } = useToast()

const prompt = ref('')
const expiresAt = ref(0)
const generating = ref(false)
const copied = ref(false)
const lifetimeMonths = ref(1)

const validityOptions = computed(() =>
  [1, 3, 6].map((months) => ({
    value: months,
    label: t('settings.modal.agentAccessValidityMonths', { months })
  }))
)

const actionLabel = computed(() => {
  if (generating.value) return t('settings.modal.agentAccessGenerating')
  if (prompt.value) return t('settings.modal.agentAccessRegenerate')
  return t('settings.modal.agentAccessGenerate')
})

const date = computed(() =>
  expiresAt.value
    ? new Date(expiresAt.value * 1000).toLocaleDateString(locale.value)
    : ''
)

async function generatePrompt() {
  generating.value = true
  try {
    const payload = extractResponseData(
      await generateMcpToken(lifetimeMonths.value)
    )
    expiresAt.value = payload.expires_at
    copied.value = false
    prompt.value = t('settings.modal.agentAccessPrompt', {
      url: `${publicBaseUrl()}/api/lens/mcp/`,
      token: payload.access,
      date: date.value
    })
  } catch (error) {
    showError(extractErrorMessage(error, t('settings.modal.agentAccessFailed')))
  } finally {
    generating.value = false
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
  showError(t('settings.modal.agentAccessCopyFailed'))
}
</script>
