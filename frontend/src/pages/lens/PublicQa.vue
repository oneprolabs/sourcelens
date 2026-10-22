<template>
  <div class="qa-screen-view min-h-screen bg-surface-sunken">
    <PublicLensHeader
      :assistant-name="qa?.assistant_name"
      :assistant-slug="qa?.assistant_slug"
      :show-qa-link="true"
    />
    <main
      class="mx-auto max-w-3xl px-4 py-6 pb-[calc(1.5rem+env(safe-area-inset-bottom))] sm:px-6 sm:py-8 sm:pb-8"
    >
      <BaseLoading v-if="loading" />

      <div v-else-if="accessState">
        <PublicQaAccessState
          :type="accessState"
          @login="goLogin"
          @home="goHome"
        />
      </div>

      <article v-else-if="qa">
        <router-link
          v-if="qa.assistant_slug"
          :to="`/lens/assistants/${qa.assistant_slug}/qa`"
          class="text-xs font-medium text-ink-500 no-underline hover:text-primary-600"
        >
          {{ qa.assistant_name }}
        </router-link>

        <div class="mt-3 flex flex-wrap items-center gap-x-2 gap-y-1">
          <span
            class="inline-flex items-center gap-1.5 whitespace-nowrap rounded-full bg-primary-50 px-2.5 py-1 text-xs font-semibold text-primary-700"
          >
            <Bot :size="14" :stroke-width="2" aria-hidden="true" />
            {{ t('lens.qa.agentBadge') }}
          </span>
          <span class="text-xs text-ink-500">
            {{
              t('lens.qa.agentAnswerBy', {
                name: qa.assistant_name || t('lens.qa.genericAgent')
              })
            }}
          </span>
        </div>

        <h1 class="mt-3 text-xl font-semibold text-ink-900">
          {{ qa.title || qa.question }}
        </h1>
        <p v-if="languageMismatch" class="mt-2 text-xs text-ink-400">
          {{
            t('lens.qa.contentLanguageNotice', {
              language: contentLanguageLabel
            })
          }}
        </p>
        <p v-else-if="!contentLanguage" class="mt-2 text-xs text-ink-400">
          {{ t('lens.qa.originalLanguageNotice') }}
        </p>

        <section
          class="mt-5 rounded-lg border border-line bg-surface px-4 py-4"
          :aria-labelledby="`shared-question-${token}`"
        >
          <h2
            :id="`shared-question-${token}`"
            class="text-xs font-semibold uppercase tracking-wide text-ink-400"
          >
            {{ t('lens.qa.question') }}
          </h2>
          <SharedQaFileList
            v-if="qa.input_attachments?.length"
            class="mt-3"
            :files="qa.input_attachments"
            :label="t('lens.qa.inputAttachments')"
            @preview="openPreview"
            @download="downloadFile"
          />
          <p class="mt-3 whitespace-pre-wrap text-base text-ink-900">
            {{ qa.question }}
          </p>
        </section>

        <section class="mt-5" :aria-labelledby="`shared-answer-${token}`">
          <h2
            :id="`shared-answer-${token}`"
            class="mb-3 text-xs font-semibold uppercase tracking-wide text-ink-400"
          >
            {{ t('lens.qa.answer') }}
          </h2>
          <MarkdownRenderer :content="qa.answer || ''" />
          <div v-if="qa.output_files?.length" class="mt-5">
            <h3 class="mb-2 text-sm font-medium text-ink-600">
              {{ t('lens.qa.outputFiles') }}
            </h3>
            <SharedQaFileList
              :files="qa.output_files"
              :label="t('lens.qa.outputFiles')"
              @preview="openPreview"
              @download="downloadFile"
            />
          </div>
        </section>

        <div
          class="mt-6 flex flex-col gap-2 border-t border-line pt-4 text-xs text-ink-400 sm:flex-row sm:items-center sm:justify-between"
        >
          <div class="flex flex-wrap items-center gap-x-3 gap-y-1">
            <span>{{ formatDate(qa.published_at, 'yyyy-MM-dd HH:mm') }}</span>
            <span>{{ t('lens.qa.viewCount', { count: qa.view_count }) }}</span>
          </div>
          <div class="-ml-2.5 flex items-center gap-1 sm:ml-0">
            <button
              type="button"
              class="inline-flex min-h-10 items-center gap-1 whitespace-nowrap rounded-md px-2.5 py-2 text-ink-500 transition-colors hover:bg-surface-sunken hover:text-primary-600 sm:min-h-0 sm:px-2 sm:py-1"
              @click="exportPdf"
            >
              <Download :size="14" :stroke-width="2" aria-hidden="true" />
              {{ t('lens.qa.exportPdf') }}
            </button>
            <button
              type="button"
              class="inline-flex min-h-10 items-center gap-1 whitespace-nowrap rounded-md px-2.5 py-2 text-ink-500 transition-colors hover:bg-surface-sunken hover:text-primary-600 sm:min-h-0 sm:px-2 sm:py-1"
              @click="copyLink"
            >
              <Copy :size="14" :stroke-width="2" aria-hidden="true" />
              {{ t('lens.share.copyLink') }}
            </button>
          </div>
        </div>

        <div
          v-if="qa.assistant_slug"
          class="mt-8 rounded-xl border border-line bg-surface px-4 py-4 sm:px-5"
        >
          <p class="text-sm font-medium text-ink-700">
            {{ t('lens.qa.ctaTitle') }}
          </p>
          <router-link
            :to="`/lens/assistants/${qa.assistant_slug}/chat`"
            class="mt-2 inline-block text-sm font-medium text-primary-600 no-underline hover:text-primary-700"
          >
            {{ t('lens.qa.ctaAction', { name: qa.assistant_name }) }} →
          </router-link>
        </div>
      </article>
    </main>
    <FilePreviewModal
      :file="previewFile"
      @close="closePreview"
      @download="downloadFile"
    />
  </div>
</template>

<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute, useRouter } from 'vue-router'
import { Bot, Copy, Download } from '@lucide/vue'

import PublicLensHeader from '@/components/lens/PublicLensHeader.vue'
import PublicQaAccessState from '@/components/lens/PublicQaAccessState.vue'
import FilePreviewModal from '@/components/lens/FilePreviewModal.vue'
import SharedQaFileList from '@/components/lens/SharedQaFileList.vue'
import MarkdownRenderer from '@/components/ui/MarkdownRenderer.vue'
import BaseLoading from '@/components/ui/BaseLoading.vue'
import { getPublicQa, getPublicQaPdf } from '@/api/lens'
import { copyToClipboard } from '@/utils/clipboard'
import { formatDate } from '@/utils/formatting'
import { qaShareUrl } from '@/utils/lens'
import { fetchDeliverableBlob } from '@/utils/filePreview'
import { downloadQaPdf } from '@/utils/qaPdf'
import { useSessionActivity } from '@/composables/useSessionActivity'
import { useToast } from '@/composables/useToast'
import { useUserStore } from '@/store/user'

const props = defineProps({ token: { type: String, required: true } })

const { t, locale } = useI18n()
const route = useRoute()
const router = useRouter()
const { showSuccess, showError } = useToast()
const userStore = useUserStore()
const sessionActivity = useSessionActivity()

const qa = ref(null)
const loading = ref(true)
const accessState = ref(null)
const previewFile = ref(null)
const isAuthenticated = computed(() => userStore.isAuthenticated)
const contentLanguage = computed(() => qa.value?.content_language || '')
const contentLanguageKey = computed(() =>
  contentLanguage.value.startsWith('zh')
    ? 'zh-CN'
    : contentLanguage.value.startsWith('es')
      ? 'es'
      : 'en'
)
const contentLanguageLabel = computed(() =>
  contentLanguage.value
    ? t(`settings.preferences.languages.${contentLanguageKey.value}`)
    : ''
)
const languageMismatch = computed(
  () =>
    Boolean(contentLanguage.value) &&
    contentLanguage.value.split('-')[0] !== locale.value.split('-')[0]
)

function accessStateFromError(error) {
  const code = error?.response?.data?.code
  if (error?.response?.status === 403 && code === 'AUTHENTICATION_REQUIRED') {
    return 'login-required'
  }
  if (error?.response?.status === 403 && code === 'ASSISTANT_ACCESS_DENIED') {
    return 'forbidden'
  }
  return 'not-found'
}

function setAccessStateAfterError(error) {
  accessState.value = accessStateFromError(error)
  document.title =
    accessState.value === 'login-required'
      ? t('lens.qa.loginRequiredTitle')
      : accessState.value === 'forbidden'
        ? t('lens.qa.accessDeniedTitle')
        : t('lens.qa.notFound')
}

async function load() {
  loading.value = true
  accessState.value = null
  qa.value = null

  try {
    qa.value = await getPublicQa(props.token)
    const title = qa.value?.title || qa.value?.question
    if (title) {
      document.title = title
    }
  } catch (error) {
    setAccessStateAfterError(error)
  } finally {
    loading.value = false
  }
}

async function copyLink() {
  if (await copyToClipboard(qaShareUrl(props.token))) {
    showSuccess(t('lens.qa.copied'))
  } else {
    showError(t('lens.qa.copyFailed'))
  }
}

async function exportPdf() {
  try {
    const response = await getPublicQaPdf(props.token)
    downloadQaPdf(response, {
      summary: qa.value?.title,
      question: qa.value?.question
    })
    sessionActivity.notify({
      duration: 5000,
      message: t('lens.qa.pdfGenerated', {
        title: qa.value?.title || t('lens.qa.genericAgent')
      }),
      to: route.fullPath,
      type: 'success'
    })
  } catch {
    sessionActivity.notify({
      duration: 8000,
      message: t('lens.qa.pdfGenerationFailed'),
      to: route.fullPath,
      type: 'error'
    })
  }
}

function openPreview(file) {
  previewFile.value = file
}

function closePreview() {
  previewFile.value = null
}

async function downloadFile(file) {
  if (!file?.url) return
  try {
    const blob = await fetchDeliverableBlob(file)
    const objectUrl = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = objectUrl
    link.download = file.filename || 'download'
    document.body.appendChild(link)
    link.click()
    document.body.removeChild(link)
    URL.revokeObjectURL(objectUrl)
  } catch {
    showError(t('lens.chat.downloadFailed'))
  }
}

function goLogin() {
  router.push({ path: '/login', query: { next: route.fullPath } })
}

function goHome() {
  router.push('/')
}

onMounted(load)
watch(() => props.token, load)
watch(isAuthenticated, (authenticated) => {
  if (authenticated && accessState.value === 'login-required') {
    load()
  }
})
</script>
