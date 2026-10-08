<template>
  <div class="overflow-hidden rounded-md border border-line bg-surface">
    <div
      class="flex items-center justify-between gap-2 border-b border-line px-3 py-2"
    >
      <span class="text-[11px] font-semibold text-ink-700">
        {{ t('lensAdmin.wizard.environmentSetValues') }}
      </span>
      <BaseButton
        size="sm"
        variant="outline"
        :disabled="!scopedValues.some((item) => !item.secret)"
        @click="toggleReveal"
      >
        {{
          revealed
            ? t('lensAdmin.environmentVariables.hideValues')
            : t('lensAdmin.environmentVariables.revealValues')
        }}
      </BaseButton>
    </div>
    <div v-if="loading" class="px-3 py-3 text-xs text-ink-500">
      {{ t('common.loading') }}
    </div>
    <div v-else>
      <div v-if="!scopedValues.length" class="px-3 py-3 text-xs text-ink-400">
        {{ t('lensAdmin.environmentVariables.noValues') }}
      </div>
      <div v-else class="divide-y divide-line">
        <div
          v-for="item in scopedValues"
          :key="item.key"
          class="flex items-center justify-between gap-3 px-3 py-2"
        >
          <span
            class="min-w-0 truncate font-mono text-[11px] font-medium text-ink-700"
          >
            {{ item.key }}
          </span>
          <span
            class="min-w-0 truncate font-mono text-[11px] text-ink-500"
            :class="revealed ? '' : 'select-none tracking-widest'"
          >
            {{ item.secret || !revealed ? '••••••••' : item.value }}
          </span>
        </div>
      </div>
      <p
        v-if="unusedCount"
        class="border-t border-line bg-surface-sunken px-3 py-2 text-[11px] leading-4 text-ink-500"
        role="status"
      >
        {{
          t('lensAdmin.wizard.environmentSetUnusedValues', {
            count: unusedCount
          })
        }}
      </p>
    </div>
  </div>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'

import { revealEnvironmentVariableSet } from '@/api/lens'
import BaseButton from '@/components/ui/BaseButton.vue'
import { useToast } from '@/composables/useToast'
import { scopeEnvironmentSetValues } from '@/pages/lens/assistantEnvironment'
import { extractErrorMessage } from '@/utils/api'

const props = defineProps({
  variableSet: {
    type: Object,
    default: null
  },
  allowedKeys: {
    type: Array,
    default: null
  }
})

const { t } = useI18n()
const { showError } = useToast()

const items = ref([])
const loading = ref(false)
const revealed = ref(false)
const scoped = computed(() =>
  scopeEnvironmentSetValues(items.value, props.allowedKeys)
)
const scopedValues = computed(() => scoped.value.values)
const unusedCount = computed(() => scoped.value.unusedCount)

watch(
  () => props.variableSet?.uuid,
  (uuid) => {
    revealed.value = false
    if (!uuid) {
      items.value = []
      return
    }
    loadValues(uuid)
  },
  { immediate: true }
)

async function loadValues(uuid) {
  loading.value = true
  try {
    const payload = await revealEnvironmentVariableSet(uuid)
    items.value = (payload.values || []).map((item) => ({ ...item }))
  } catch (error) {
    showError(extractErrorMessage(error, t('lensAdmin.messages.loadFailed')))
  } finally {
    loading.value = false
  }
}

function toggleReveal() {
  revealed.value = !revealed.value
}
</script>
