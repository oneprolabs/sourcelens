<template>
  <BaseModal :show="open" :title="t('lens.qa.shareTitle')" @close="emitClose">
    <div class="space-y-4">
      <div
        class="flex items-start gap-3 rounded-lg border border-primary-200 bg-primary-50 px-3 py-3"
      >
        <Bot
          class="mt-0.5 shrink-0 text-primary-600"
          :size="18"
          aria-hidden="true"
        />
        <div>
          <p class="text-sm font-medium text-ink-800">
            {{ t('lens.qa.shareAgentTitle') }}
          </p>
          <p class="mt-1 text-xs leading-relaxed text-ink-500">
            {{
              t('lens.qa.shareAgentDescription', {
                name: assistantName || t('lens.qa.genericAgent')
              })
            }}
          </p>
        </div>
      </div>

      <p class="rounded-md bg-warning/10 px-3 py-2 text-xs text-warning">
        {{ t('lens.qa.shareWarning') }}
      </p>

      <div>
        <label class="mb-1 block text-xs font-medium text-ink-500">
          {{ t('lens.qa.shareTitleLabel') }}
        </label>
        <input
          v-model="title"
          type="text"
          class="w-full rounded-md border border-line bg-surface px-3 py-2 text-sm text-ink-800 focus:border-primary-500 focus:outline-none"
        />
      </div>

      <div
        v-if="!showsAccessChoice && accessPolicyHint"
        class="text-xs text-ink-500"
      >
        {{ accessPolicyHint }}
      </div>

      <div v-if="showsAccessChoice">
        <label class="mb-1 block text-xs font-medium text-ink-500">
          {{ t('lens.qa.shareAccessLabel') }}
        </label>
        <div class="space-y-2">
          <label
            v-for="option in accessOptions"
            :key="option.value"
            class="flex cursor-pointer items-start gap-2 rounded-md bg-surface-sunken p-3"
          >
            <input
              :checked="accessChoice === option.value"
              type="radio"
              :value="option.value"
              class="mt-0.5 h-4 w-4 border-line text-brand-600 focus:ring-brand-500"
              @change="onAccessChoiceChange(option.value)"
            />
            <span class="min-w-0 flex-1">
              <span class="block text-sm font-medium text-ink-800">
                {{ option.label }}
              </span>
              <span class="mt-0.5 block text-xs leading-5 text-ink-500">
                {{ option.description }}
              </span>
            </span>
          </label>
        </div>
      </div>

      <div v-if="answerPreview">
        <div class="mb-1 text-xs font-medium text-ink-500">
          {{ t('lens.qa.previewLabel') }}
        </div>
        <div class="qa-share-preview">
          <MarkdownRenderer
            :content="answerPreview"
            :enable-highlight="false"
          />
        </div>
      </div>

      <div v-if="share">
        <label class="mb-1 block text-xs font-medium text-ink-500">
          {{ t('lens.qa.linkLabel') }}
        </label>
        <a
          :href="shareLink"
          target="_blank"
          rel="noopener"
          class="block truncate font-mono text-xs text-ink-500 no-underline transition-colors hover:text-primary-600 hover:underline"
          :title="shareLink"
        >
          {{ shareLink }}
        </a>
        <p class="mt-2 text-xs text-ink-400">{{ t('lens.qa.listNote') }}</p>
      </div>

      <BaseButton
        v-if="share"
        class="qa-share-native"
        variant="primary"
        size="md"
        block
        @click="copy"
      >
        <Copy :size="16" :stroke-width="2" aria-hidden="true" />
        {{ t('lens.share.copyLink') }}
      </BaseButton>
    </div>

    <template #footer>
      <div class="flex justify-end gap-2">
        <BaseButton
          v-if="share"
          class="qa-share-action"
          variant="danger"
          size="sm"
          @click="unshare"
        >
          {{ t('lens.qa.unshare') }}
        </BaseButton>
        <BaseButton
          class="qa-share-action"
          variant="primary"
          size="sm"
          :loading="creating || saving"
          @click="primaryAction"
        >
          {{ primaryLabel }}
        </BaseButton>
      </div>
    </template>
  </BaseModal>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { Bot, Copy } from '@lucide/vue'

import BaseModal from '@/components/ui/BaseModal.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import MarkdownRenderer from '@/components/ui/MarkdownRenderer.vue'
import {
  shareRun,
  updateMyShare,
  deleteShare,
  getSharePolicy
} from '@/api/lens'
import { copyToClipboard } from '@/utils/clipboard'
import { qaShareUrl } from '@/utils/lens'
import { useToast } from '@/composables/useToast'

const props = defineProps({
  open: { type: Boolean, default: false },
  runUuid: { type: String, default: '' },
  existingShare: { type: Object, default: null },
  assistantName: { type: String, default: '' },
  assistantUuid: { type: String, default: '' },
  assistantSlug: { type: String, default: '' },
  question: { type: String, default: '' },
  answerPreview: { type: String, default: '' }
})
const emit = defineEmits(['close', 'shared', 'unshared'])

function defaultTitle(question) {
  return (question || '').replace(/\s+/g, ' ').trim().slice(0, 80)
}

const { t } = useI18n()
const { showSuccess, showError } = useToast()

const title = ref('')
const share = ref(null)
const creating = ref(false)
const saving = ref(false)
const accessPolicy = ref(null)
const accessChoice = ref('public')
const accessChoiceDirty = ref(false)

const configuredAccessPolicy = computed(
  () => accessPolicy.value?.policy || 'inherit'
)

const effectiveAccessChoice = computed(() => {
  if (configuredAccessPolicy.value !== 'inherit') {
    return configuredAccessPolicy.value
  }
  if (share.value) {
    if (share.value.require_login === true) return 'login'
    if (share.value.require_login === false) return 'public'
  }
  return 'public'
})

const showsAccessChoice = computed(
  () => accessPolicy.value?.requires_choice === true
)

const accessPolicyHint = computed(() => {
  if (configuredAccessPolicy.value === 'inherit') {
    return ''
  }
  const mode =
    configuredAccessPolicy.value === 'login'
      ? t('lens.qa.shareAccessLogin')
      : t('lens.qa.shareAccessPublic')
  const source =
    accessPolicy.value?.source === 'assistant'
      ? t('lens.qa.shareAccessByAssistant')
      : t('lens.qa.shareAccessByGlobal')
  return t('lens.qa.shareAccessConfigured', { mode, source })
})

const accessOptions = computed(() => [
  {
    value: 'public',
    label: t('lens.qa.shareAccessPublic'),
    description: t('lens.qa.shareAccessPublicHint')
  },
  {
    value: 'login',
    label: t('lens.qa.shareAccessLogin'),
    description: t('lens.qa.shareAccessLoginHint')
  }
])

const shareLink = computed(() =>
  share.value ? qaShareUrl(share.value.token) : ''
)

const titleDirty = computed(
  () => !!share.value && title.value.trim() !== (share.value.title || '')
)

const accessDirty = computed(() => !!share.value && accessChoiceDirty.value)

const primaryLabel = computed(() => {
  if (!share.value) {
    return t('lens.qa.createLink')
  }
  return titleDirty.value || accessDirty.value
    ? t('lens.qa.save')
    : t('lens.qa.done')
})

watch(
  () => props.open,
  (open) => {
    if (open) {
      share.value = props.existingShare || null
      title.value = share.value?.title || defaultTitle(props.question)
      creating.value = false
      saving.value = false
      accessChoiceDirty.value = false
      loadAccessPolicy()
    }
  }
)

async function loadAccessPolicy() {
  accessPolicy.value = null
  accessChoice.value = 'public'
  try {
    accessPolicy.value = await getSharePolicy({
      assistant_uuid: props.assistantUuid || undefined,
      assistant_slug: props.assistantSlug || undefined
    })
  } catch {
    accessPolicy.value = null
  }
  accessChoice.value = effectiveAccessChoice.value
}

function onAccessChoiceChange(value) {
  accessChoice.value = value
  accessChoiceDirty.value = true
}

function emitClose() {
  emit('close')
}

function primaryAction() {
  if (!share.value) {
    return create()
  }
  if (titleDirty.value || accessDirty.value) {
    return saveChanges()
  }
  return emitClose()
}

async function create() {
  if (!props.runUuid) {
    return
  }
  creating.value = true
  try {
    const payload = { title: title.value.trim() }
    if (accessChoiceDirty.value) {
      payload.require_login = accessChoice.value === 'login'
    }
    share.value = await shareRun(props.runUuid, payload)
    title.value = share.value.title || ''
    emit('shared', share.value)
    showSuccess(t('lens.qa.shared'))
  } catch {
    showError(t('lens.qa.shareFailed'))
  } finally {
    creating.value = false
  }
}

async function saveChanges() {
  if (!share.value) {
    return
  }
  saving.value = true
  try {
    const payload = { title: title.value.trim() }
    if (accessChoiceDirty.value) {
      payload.require_login = accessChoice.value === 'login'
    }
    share.value = await updateMyShare(share.value.uuid, payload)
    title.value = share.value.title || ''
    accessChoiceDirty.value = false
    showSuccess(t('lens.qa.actionDone'))
  } catch {
    showError(t('lens.qa.shareFailed'))
  } finally {
    saving.value = false
  }
}

async function copy() {
  if (await copyToClipboard(shareLink.value)) {
    showSuccess(t('lens.qa.copied'))
  } else {
    showError(t('lens.qa.copyFailed'))
  }
}

async function unshare() {
  if (!share.value) {
    return
  }
  try {
    await deleteShare(share.value.uuid)
    showSuccess(t('lens.qa.unshared'))
    emit('unshared', share.value)
    share.value = null
    emitClose()
  } catch {
    showError(t('lens.qa.shareFailed'))
  }
}
</script>

<style scoped>
.qa-share-preview {
  @apply max-h-40 overflow-hidden rounded-md border border-line bg-surface-sunken px-3 py-2;
}

.qa-share-preview :deep(.markdown-content) {
  @apply text-xs;
}

.qa-share-preview :deep(.markdown-content > :first-child) {
  margin-top: 0;
}

.qa-share-preview :deep(.markdown-content > :last-child) {
  margin-bottom: 0;
}

@media (max-width: 767px), (hover: none), (pointer: coarse) {
  :deep(.qa-share-action) {
    min-width: 44px;
    min-height: 44px;
  }
}
</style>
