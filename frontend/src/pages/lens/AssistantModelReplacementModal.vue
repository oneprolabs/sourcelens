<template>
  <BaseModal
    :show="true"
    :title="t(`${prefix}.title`)"
    max-width="4xl"
    @close="close"
  >
    <BaseLoading v-if="loading" />
    <fieldset
      v-else
      class="grid min-w-0 grid-cols-1 gap-4 md:grid-cols-2"
      :disabled="busy"
    >
      <section
        class="flex min-w-0 flex-col gap-4 rounded-lg border border-line p-4"
        aria-labelledby="replacement-assistants-title"
      >
        <h4
          id="replacement-assistants-title"
          class="text-sm font-semibold text-theme"
        >
          {{ t(`${prefix}.affectedAssistants`) }}
        </h4>
        <div ref="assistantPicker" class="relative flex flex-col gap-1">
          <label
            for="replacement-assistant-picker"
            class="text-sm font-medium text-theme"
            >{{ t(`${prefix}.assistants`) }}</label
          >
          <button
            id="replacement-assistant-picker"
            ref="assistantPickerTrigger"
            type="button"
            class="flex w-full items-center justify-between gap-2 rounded-lg border border-line bg-surface px-3 py-2 text-left text-sm text-theme shadow-sm focus:outline-none focus:ring-2 focus:ring-primary-500 disabled:opacity-50"
            :aria-expanded="assistantPickerOpen"
            :aria-label="t(`${prefix}.assistants`)"
            aria-controls="replacement-assistant-options"
            :disabled="busy"
            @click="assistantPickerOpen = !assistantPickerOpen"
            @keydown.esc.stop="closeAssistantPicker"
          >
            <span aria-live="polite">{{
              t(`${prefix}.selectedCount`, { count: scopeRows.length })
            }}</span>
            <ChevronDown
              :size="16"
              aria-hidden="true"
              :class="{ 'rotate-180': assistantPickerOpen }"
            />
          </button>
          <Teleport to="body">
            <div
              v-if="assistantPickerOpen"
              id="replacement-assistant-options"
              ref="assistantPickerMenu"
              class="fixed z-[120] flex flex-col overflow-hidden rounded-lg border border-line bg-surface shadow-lg"
              :style="assistantPickerStyle"
              @keydown.esc.stop="closeAssistantPicker"
              @keydown.tab.stop="handleAssistantPickerTab"
            >
              <div class="shrink-0 border-b border-line p-2">
                <label
                  class="flex items-center gap-2 rounded-md bg-surface-sunken px-3 py-2 text-ink-500"
                >
                  <Search :size="16" aria-hidden="true" />
                  <input
                    v-model="assistantSearch"
                    type="search"
                    name="model-replacement-assistant-search"
                    :aria-label="t(`${prefix}.searchAssistants`)"
                    :placeholder="t(`${prefix}.searchAssistants`)"
                    :disabled="busy"
                    class="assistant-picker-search min-w-0 flex-1 bg-transparent text-sm text-theme outline-none placeholder:text-ink-400"
                  />
                </label>
              </div>
              <label
                class="flex shrink-0 cursor-pointer items-center gap-3 border-b border-line px-3 py-2.5 text-sm text-ink-600 hover:bg-surface-sunken"
              >
                <input
                  type="checkbox"
                  class="peer sr-only"
                  :checked="allSearchResultsSelected"
                  :indeterminate="
                    someSearchResultsSelected && !allSearchResultsSelected
                  "
                  :disabled="busy || !visibleAssistants.length"
                  @change="toggleAllAssistants($event.target.checked)"
                />
                <span
                  class="flex h-4 w-4 shrink-0 items-center justify-center rounded border border-line peer-focus-visible:ring-2 peer-focus-visible:ring-primary-500"
                  :class="{
                    'border-primary-600 bg-primary-600 text-white':
                      someSearchResultsSelected
                  }"
                  aria-hidden="true"
                >
                  <Check
                    v-if="allSearchResultsSelected"
                    :size="12"
                    :stroke-width="3"
                  />
                  <Minus
                    v-else-if="someSearchResultsSelected"
                    :size="12"
                    :stroke-width="3"
                  />
                </span>
                <span class="flex-1">{{
                  t(
                    `${prefix}.${assistantSearch.trim() ? 'selectAllResults' : 'selectAllAssistants'}`,
                    { count: visibleAssistants.length }
                  )
                }}</span>
              </label>
              <div class="min-h-0 max-h-52 flex-1 overflow-y-auto p-1">
                <label
                  v-for="row in visibleAssistants"
                  :key="row.uuid"
                  class="flex cursor-pointer items-center gap-3 rounded-md px-2 py-2 text-sm transition-colors hover:bg-surface-sunken"
                  :class="{
                    'bg-surface-selected': assistantSelection.includes(row.uuid)
                  }"
                >
                  <input
                    v-model="assistantSelection"
                    type="checkbox"
                    :value="row.uuid"
                    :disabled="busy"
                    class="peer sr-only"
                    :aria-label="
                      t(`${prefix}.selectAssistant`, { name: row.name })
                    "
                  />
                  <span
                    class="flex h-4 w-4 shrink-0 items-center justify-center rounded border border-line peer-focus-visible:ring-2 peer-focus-visible:ring-primary-500"
                    :class="{
                      'border-primary-600 bg-primary-600 text-white':
                        assistantSelection.includes(row.uuid)
                    }"
                    aria-hidden="true"
                  >
                    <Check
                      v-if="assistantSelection.includes(row.uuid)"
                      :size="12"
                      :stroke-width="3"
                    />
                  </span>
                  <span
                    class="flex h-8 w-8 shrink-0 items-center justify-center rounded-md border border-line bg-surface-sunken text-ink-500"
                    aria-hidden="true"
                  >
                    <Bot :size="16" />
                  </span>
                  <span class="min-w-0 flex-1">
                    <span class="block truncate font-medium text-theme">{{
                      row.name
                    }}</span>
                    <span class="block truncate text-xs text-ink-400">{{
                      row.slug
                    }}</span>
                  </span>
                </label>
                <p
                  v-if="!visibleAssistants.length"
                  class="px-3 py-6 text-center text-sm text-ink-500"
                >
                  {{ t(`${prefix}.noAssistants`) }}
                </p>
              </div>
              <div
                class="flex shrink-0 items-center justify-between gap-2 border-t border-line px-3 py-2"
              >
                <span class="text-xs text-ink-500" aria-live="polite">{{
                  t(`${prefix}.selectedCount`, { count: scopeRows.length })
                }}</span>
                <div class="flex items-center gap-2">
                  <BaseButton
                    variant="ghost"
                    size="sm"
                    :disabled="busy || !scopeRows.length"
                    @click="assistantSelection = []"
                    >{{ t(`${prefix}.clearSelection`) }}</BaseButton
                  >
                  <BaseButton
                    variant="outline"
                    size="sm"
                    @click="closeAssistantPicker"
                    >{{ t(`${prefix}.done`) }}</BaseButton
                  >
                </div>
              </div>
            </div>
          </Teleport>
        </div>
        <p v-if="scopeRows.length > 1000" class="text-sm text-danger-700">
          {{ t(`${prefix}.limit`) }}
        </p>
        <div class="overflow-hidden rounded-lg border border-line">
          <ul class="max-h-72 overflow-y-auto divide-y divide-line text-sm">
            <li
              v-for="row in scopeRows"
              :key="row.uuid"
              class="flex items-center gap-3 px-3 py-2"
            >
              <Bot
                :size="16"
                class="shrink-0 text-ink-500"
                aria-hidden="true"
              />
              <span class="min-w-0 break-words font-medium text-theme">{{
                row.name
              }}</span>
            </li>
          </ul>
          <p
            v-if="!scopeRows.length"
            class="px-3 py-6 text-center text-sm text-ink-500"
          >
            {{ t(`${prefix}.noAssistants`) }}
          </p>
        </div>
      </section>
      <section
        class="flex min-w-0 flex-col gap-4 rounded-lg border border-line p-4"
        aria-labelledby="replacement-models-title"
      >
        <h4
          id="replacement-models-title"
          class="text-sm font-semibold text-theme"
        >
          {{ t(`${prefix}.modelSettings`) }}
        </h4>
        <BaseSelect
          v-model="field"
          :label="t(`${prefix}.field`)"
          :aria-label="t(`${prefix}.field`)"
          :disabled="busy"
        >
          <option value="agent_model_ref">
            {{ t('lensAdmin.fields.agentModel') }}
          </option>
          <option value="multimodal_model_ref">
            {{ t('lensAdmin.fields.multimodalModel') }}
          </option>
        </BaseSelect>
        <BaseSelect
          v-model="target"
          :label="t(`${prefix}.target`)"
          :aria-label="t(`${prefix}.target`)"
          :disabled="busy"
        >
          <option value="">
            {{ t('lensAdmin.placeholders.selectModel') }}
          </option>
          <option
            v-for="model in targetModels"
            :key="model.uuid"
            :value="model.uuid"
          >
            {{ formatLLMConfigLabel(model) }}
          </option>
        </BaseSelect>
        <p v-if="!targetModels.length" class="text-sm text-amber-700">
          {{ t(`${prefix}.noModels`) }}
        </p>
      </section>
    </fieldset>
    <p v-if="error" role="alert" class="mt-3 text-sm text-danger-700">
      {{ error }}
    </p>
    <template #footer>
      <BaseButton :loading="busy" :disabled="!canSubmit" @click="submit">
        {{ t(`${prefix}.replace`) }}
      </BaseButton>
      <BaseButton
        class="mr-3"
        variant="outline"
        :disabled="busy"
        @click="close"
        >{{ t('common.cancel') }}</BaseButton
      >
    </template>
  </BaseModal>
</template>

<script setup>
import { Bot, Check, ChevronDown, Minus, Search } from '@lucide/vue'
import { onClickOutside, useEventListener } from '@vueuse/core'
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'

import { llmAdminApi } from '@/admin/api/llmAdmin'
import { replaceAssistantModels } from '@/api/lens'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseLoading from '@/components/ui/BaseLoading.vue'
import BaseModal from '@/components/ui/BaseModal.vue'
import BaseSelect from '@/components/ui/BaseSelect.vue'
import { useToast } from '@/composables/useToast'
import { extractErrorMessage } from '@/utils/api'

import { formatLLMConfigLabel, normalizeList } from './adminHelpers'
import {
  eligibleReplacementModel,
  replacementPickerPosition,
  selectableReplacementAssistants,
  toggleReplacementSelection
} from './assistantModelReplacement'

const props = defineProps({
  assistants: { type: Array, required: true },
  selectedUuids: { type: Array, required: true }
})
const emit = defineEmits(['close', 'updated', 'refresh'])
const { t } = useI18n()
const { showSuccess } = useToast()
const prefix = 'lensAdmin.modelReplacement'
const assistantSelection = ref([...props.selectedUuids])
const assistantSearch = ref('')
const assistantPicker = ref(null)
const assistantPickerTrigger = ref(null)
const assistantPickerMenu = ref(null)
const assistantPickerOpen = ref(false)
const assistantPickerStyle = ref({})
onClickOutside(
  assistantPickerMenu,
  () => {
    assistantPickerOpen.value = false
  },
  { ignore: [assistantPicker] }
)

function updateAssistantPickerPosition() {
  if (!assistantPickerOpen.value || !assistantPickerTrigger.value) return
  const rect = assistantPickerTrigger.value.getBoundingClientRect()
  const position = replacementPickerPosition(
    rect,
    { width: window.innerWidth, height: window.innerHeight },
    360
  )
  assistantPickerStyle.value = Object.fromEntries(
    Object.entries(position).map(([key, value]) => [key, `${value}px`])
  )
  if (position.top < rect.top) {
    assistantPickerStyle.value.top = 'auto'
    assistantPickerStyle.value.bottom = `${window.innerHeight - rect.top + 4}px`
  } else {
    assistantPickerStyle.value.bottom = 'auto'
  }
}

watch(assistantPickerOpen, async (open) => {
  if (open) {
    await nextTick()
    updateAssistantPickerPosition()
    assistantPickerMenu.value?.querySelector('input[type="search"]')?.focus()
  }
})
useEventListener(window, 'resize', updateAssistantPickerPosition)
useEventListener(window, 'scroll', updateAssistantPickerPosition, {
  capture: true
})

function closeAssistantPicker() {
  assistantPickerOpen.value = false
  assistantPickerTrigger.value?.focus()
}

function handleAssistantPickerTab(event) {
  const controls = [
    ...assistantPickerMenu.value.querySelectorAll(
      'input:not(:disabled), button:not(:disabled)'
    )
  ]
  if (
    (event.shiftKey && event.target === controls[0]) ||
    (!event.shiftKey && event.target === controls.at(-1))
  ) {
    event.preventDefault()
    closeAssistantPicker()
  }
}
const field = ref('agent_model_ref')
const target = ref('')
const models = ref([])
const loading = ref(true)
const busy = ref(false)
const error = ref('')

const availableAssistants = computed(() =>
  selectableReplacementAssistants(props.assistants, field.value)
)
const visibleAssistants = computed(() =>
  selectableReplacementAssistants(
    props.assistants,
    field.value,
    assistantSearch.value
  )
)
const scopeRows = computed(() =>
  availableAssistants.value.filter((row) =>
    assistantSelection.value.includes(row.uuid)
  )
)
const allSearchResultsSelected = computed(
  () =>
    visibleAssistants.value.length > 0 &&
    visibleAssistants.value.every((row) =>
      assistantSelection.value.includes(row.uuid)
    )
)
const someSearchResultsSelected = computed(() =>
  visibleAssistants.value.some((row) =>
    assistantSelection.value.includes(row.uuid)
  )
)

function toggleAllAssistants(checked) {
  assistantSelection.value = toggleReplacementSelection(
    assistantSelection.value,
    visibleAssistants.value,
    checked
  )
}
const targetModels = computed(() =>
  models.value.filter((model) => eligibleReplacementModel(model, field.value))
)
const canSubmit = computed(
  () =>
    !loading.value &&
    !busy.value &&
    scopeRows.value.length > 0 &&
    scopeRows.value.length <= 1000 &&
    targetModels.value.some((model) => model.uuid === target.value)
)

watch(
  [assistantSelection, field, target],
  () => {
    error.value = ''
  },
  { deep: true }
)

watch(field, () => {
  assistantPickerOpen.value = false
  target.value = ''
})

function close() {
  if (!busy.value) emit('close')
}

async function submit() {
  if (!canSubmit.value) return
  busy.value = true
  assistantPickerOpen.value = false
  error.value = ''
  try {
    await replaceAssistantModels({
      model_field: field.value,
      target_model_ref: target.value,
      assistant_models: scopeRows.value.map((row) => ({
        uuid: row.uuid,
        model_ref: row[field.value] || null
      })),
      preview: false
    })
    showSuccess(t(`${prefix}.success`))
    emit('updated')
  } catch (err) {
    if (err.response?.status === 409) {
      error.value = t(`${prefix}.conflict`)
      emit('refresh')
    } else {
      error.value = extractErrorMessage(err, t('lensAdmin.messages.saveFailed'))
    }
  } finally {
    busy.value = false
  }
}

onMounted(async () => {
  try {
    models.value = normalizeList(
      await llmAdminApi.getLLMConfigAll({ scope: 'global' })
    )
  } catch (err) {
    error.value = extractErrorMessage(err, t('lensAdmin.messages.loadFailed'))
  } finally {
    loading.value = false
  }
})
</script>

<style scoped>
.assistant-picker-search {
  background: transparent !important;
  border: 0 !important;
  box-shadow: none !important;
  padding: 0 !important;
}
</style>
