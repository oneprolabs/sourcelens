<script setup>
import { nextTick, onUnmounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { Download, Maximize2, Minimize2, X } from '@lucide/vue'

import MarkdownRenderer from '@/components/ui/MarkdownRenderer.vue'
import { fetchDeliverableBlob, previewKind } from '@/utils/filePreview'
import { escapeHtml } from '@/utils/sanitize'

const props = defineProps({
  // The delivered file to preview, or null when the modal is closed.
  file: { type: Object, default: null }
})

const emit = defineEmits(['close', 'download'])

const { t } = useI18n()

const kind = ref('')
const objectUrl = ref('')
const textContent = ref('')
const showSource = ref(false)
const htmlPreviewDocument = ref('')
const docxHost = ref(null)
const workbook = ref(null)
const sheetNames = ref([])
const selectedSheet = ref('')
const pptxHost = ref(null)
const previewPanel = ref(null)
const isFullscreen = ref(false)
const loading = ref(false)
const failed = ref(false)
let currentUrl = ''
let loadSeq = 0
let pptxPreviewer = null
let pptxBuffer = null

function getPptxPreviewSize() {
  if (!isFullscreen.value || !previewPanel.value) {
    return { width: 900, height: 620 }
  }

  const header = previewPanel.value.querySelector('.preview-header')
  return {
    width: previewPanel.value.clientWidth,
    height: Math.max(
      previewPanel.value.clientHeight - (header?.offsetHeight || 0),
      1
    )
  }
}

function waitForPptxLayout() {
  return new Promise((resolve) => {
    requestAnimationFrame(() => requestAnimationFrame(resolve))
  })
}

async function renderPptx(buffer, seq) {
  await nextTick()
  await waitForPptxLayout()
  if (seq !== loadSeq || !pptxHost.value) {
    return
  }
  if (pptxPreviewer) {
    pptxPreviewer.destroy()
  }
  pptxHost.value.innerHTML = ''
  const { init: initPptxPreview } = await import('pptx-preview')
  pptxPreviewer = initPptxPreview(pptxHost.value, {
    ...getPptxPreviewSize(),
    mode: 'slide'
  })
  await pptxPreviewer.preview(buffer)
}

function renderSheet(name) {
  if (!workbook.value || !name) return ''
  const XLSX = workbook.value.XLSX
  const rows = XLSX.utils.sheet_to_json(workbook.value.sheetMap[name], {
    header: 1,
    defval: '',
    raw: false
  })
  return `<table class="preview-spreadsheet-table"><tbody>${rows
    .map(
      (row) =>
        `<tr>${row
          .map((cell) => `<td>${escapeHtml(String(cell))}</td>`)
          .join('')}</tr>`
    )
    .join('')}</tbody></table>`
}

function updateSheet(name) {
  selectedSheet.value = name
}

function cleanup() {
  if (currentUrl) {
    URL.revokeObjectURL(currentUrl)
    currentUrl = ''
  }
  objectUrl.value = ''
  textContent.value = ''
  showSource.value = false
  htmlPreviewDocument.value = ''
  pptxBuffer = null
  workbook.value = null
  sheetNames.value = []
  selectedSheet.value = ''
  if (pptxPreviewer) {
    pptxPreviewer.destroy()
    pptxPreviewer = null
  }
  if (pptxHost.value) {
    pptxHost.value.innerHTML = ''
  }
  if (docxHost.value) {
    docxHost.value.innerHTML = ''
  }
}

async function load(file) {
  // Bump the sequence so any in-flight load resolving later is discarded;
  // this prevents leaked object URLs and cross-file renders on rapid
  // preview switches.
  const seq = ++loadSeq
  cleanup()
  failed.value = false
  if (!file) {
    kind.value = ''
    return
  }
  kind.value = previewKind(file)
  if (!kind.value) {
    failed.value = true
    return
  }
  loading.value = true
  try {
    const blob = await fetchDeliverableBlob(file)
    if (seq !== loadSeq) {
      return
    }
    if (kind.value === 'pptx') {
      pptxBuffer = await blob.arrayBuffer()
      await renderPptx(pptxBuffer, seq)
    } else if (kind.value === 'docx') {
      const { renderAsync } = await import('docx-preview')
      await nextTick()
      if (seq !== loadSeq || !docxHost.value) {
        return
      }
      await renderAsync(blob, docxHost.value, docxHost.value, {
        breakPages: true,
        ignoreHeight: false,
        ignoreWidth: false,
        inWrapper: true,
        renderComments: false,
        renderEndnotes: true,
        renderFooters: true,
        renderFootnotes: true,
        renderHeaders: true
      })
    } else if (kind.value === 'xlsx') {
      const XLSX = await import('xlsx')
      const parsed = XLSX.read(await blob.arrayBuffer(), {
        type: 'array',
        cellText: true,
        cellDates: true
      })
      if (seq !== loadSeq) return
      const names = parsed.SheetNames || []
      workbook.value = {
        XLSX,
        sheetMap: parsed.Sheets
      }
      sheetNames.value = names
      selectedSheet.value = names[0] || ''
    } else if (kind.value === 'text' || kind.value === 'markdown') {
      const text = await blob.text()
      if (seq !== loadSeq) {
        return
      }
      textContent.value = text
    } else if (kind.value === 'html') {
      textContent.value = await blob.text()
      if (seq !== loadSeq) {
        return
      }
      htmlPreviewDocument.value = buildHtmlPreviewDocument(textContent.value)
    } else {
      const url = URL.createObjectURL(blob)
      if (seq !== loadSeq) {
        URL.revokeObjectURL(url)
        return
      }
      currentUrl = url
      objectUrl.value = url
    }
  } catch {
    if (seq === loadSeq) {
      failed.value = true
    }
  } finally {
    if (seq === loadSeq) {
      loading.value = false
    }
  }
}

function buildHtmlPreviewDocument(source) {
  const parser = new DOMParser()
  const document = parser.parseFromString(source, 'text/html')
  document.querySelectorAll('script, base, meta[http-equiv="refresh"]').forEach(
    (element) => element.remove()
  )
  document.querySelectorAll('a[href], area[href]').forEach((element) => {
    element.removeAttribute('href')
  })
  const policy =
    "default-src 'none'; img-src data: blob:; style-src 'unsafe-inline' data:; " +
    "font-src data:; form-action 'none'; base-uri 'none'; navigate-to 'none'"
  const policyTag = `<meta http-equiv="Content-Security-Policy" content="${policy}">`
  document.head.insertAdjacentHTML('afterbegin', policyTag)
  return `<!doctype html>${document.documentElement.outerHTML}`
}

function close() {
  exitFullscreen()
  emit('close')
}

async function enterFullscreen() {
  isFullscreen.value = true
  if (previewPanel.value?.requestFullscreen) {
    try {
      await previewPanel.value.requestFullscreen()
    } catch {
      // Keep the CSS fallback when the browser denies fullscreen access.
    }
  }
  if (kind.value === 'pptx' && pptxBuffer) {
    await renderPptx(pptxBuffer, loadSeq)
  }
}

async function exitFullscreen() {
  if (document.fullscreenElement && document.exitFullscreen) {
    await document.exitFullscreen().catch(() => {})
  }
  isFullscreen.value = false
  if (kind.value === 'pptx' && pptxBuffer) {
    await renderPptx(pptxBuffer, loadSeq)
  }
}

function toggleFullscreen() {
  if (isFullscreen.value) {
    exitFullscreen()
  } else {
    enterFullscreen()
  }
}

function onFullscreenChange() {
  isFullscreen.value = Boolean(document.fullscreenElement)
  if (kind.value === 'pptx' && pptxBuffer) {
    renderPptx(pptxBuffer, loadSeq)
  }
}

function onDownload() {
  if (props.file) {
    emit('download', props.file)
  }
}

function turnPptxPage(direction) {
  if (!pptxPreviewer || kind.value !== 'pptx') {
    return
  }
  if (direction > 0 && pptxPreviewer.renderNextSlide) {
    pptxPreviewer.renderNextSlide()
  } else if (direction < 0 && pptxPreviewer.renderPreSlide) {
    pptxPreviewer.renderPreSlide()
  }
}

function onKeydown(event) {
  if (event.key === 'Escape') {
    if (isFullscreen.value) {
      exitFullscreen()
      return
    }
    close()
    return
  }
  if (
    kind.value === 'pptx' &&
    [' ', 'ArrowRight', 'ArrowLeft'].includes(event.key)
  ) {
    event.preventDefault()
    event.stopPropagation()
    turnPptxPage(event.key === 'ArrowLeft' ? -1 : 1)
  }
}

let locked = false
let prevOverflow = ''

function lockScroll() {
  if (!locked) {
    prevOverflow = document.body.style.overflow
    locked = true
  }
  document.body.style.overflow = 'hidden'
}

function unlockScroll() {
  if (locked) {
    document.body.style.overflow = prevOverflow
    locked = false
  }
}

watch(
  () => props.file,
  (file) => {
    load(file)
    if (file) {
      document.addEventListener('keydown', onKeydown)
      document.addEventListener('fullscreenchange', onFullscreenChange)
      lockScroll()
    } else {
      document.removeEventListener('keydown', onKeydown)
      document.removeEventListener('fullscreenchange', onFullscreenChange)
      exitFullscreen()
      unlockScroll()
    }
  },
  { immediate: true }
)

onUnmounted(() => {
  cleanup()
  document.removeEventListener('keydown', onKeydown)
  document.removeEventListener('fullscreenchange', onFullscreenChange)
  exitFullscreen()
  unlockScroll()
})
</script>

<template>
  <Teleport to="body">
    <div v-if="file" class="preview-backdrop" @click="close">
      <div
        ref="previewPanel"
        class="preview-panel"
        :class="{ 'preview-panel--fullscreen': isFullscreen }"
        @click.stop
      >
        <header class="preview-header">
          <span class="preview-title" :title="file.filename">
            {{ file.filename }}
          </span>
          <div class="preview-tools">
            <button
              v-if="kind === 'html'"
              type="button"
              class="preview-tool"
              :title="
                showSource ? t('lens.chat.preview') : t('lens.chat.viewSource')
              "
              :aria-label="
                showSource ? t('lens.chat.preview') : t('lens.chat.viewSource')
              "
              @click="showSource = !showSource"
            >
              <span aria-hidden="true">&lt;/&gt;</span>
            </button>
            <button
              type="button"
              class="preview-tool"
              :title="
                t(
                  isFullscreen
                    ? 'lens.chat.exitFullscreen'
                    : 'lens.chat.enterFullscreen'
                )
              "
              :aria-label="
                t(
                  isFullscreen
                    ? 'lens.chat.exitFullscreen'
                    : 'lens.chat.enterFullscreen'
                )
              "
              @click="toggleFullscreen"
            >
              <Minimize2 v-if="isFullscreen" :size="18" />
              <Maximize2 v-else :size="18" />
            </button>
            <button
              type="button"
              class="preview-tool"
              :title="t('lens.chat.download')"
              @click="onDownload"
            >
              <Download :size="18" />
            </button>
            <button
              type="button"
              class="preview-tool"
              :aria-label="t('common.close')"
              @click="close"
            >
              <X :size="18" />
            </button>
          </div>
        </header>

        <div class="preview-body">
          <div v-if="kind === 'html' && !showSource" class="preview-security-note">
            {{ t('lens.chat.htmlPreviewRestricted') }}
          </div>
          <div v-if="kind === 'pptx'" ref="pptxHost" class="preview-pptx"></div>
          <div v-if="kind === 'docx'" ref="docxHost" class="preview-docx"></div>

          <div v-if="loading" class="preview-status">
            {{ t('lens.chat.previewLoading') }}
          </div>
          <div v-else-if="failed" class="preview-status">
            {{ t('lens.chat.previewFailed') }}
          </div>

          <img
            v-else-if="kind === 'image'"
            :src="objectUrl"
            :alt="file.filename"
            class="preview-image"
          />

          <iframe
            v-else-if="kind === 'pdf'"
            :src="objectUrl"
            :title="file.filename"
            class="preview-frame"
          ></iframe>

          <pre
            v-else-if="kind === 'html' && showSource"
            class="preview-source"
          >{{ textContent }}</pre>

          <iframe
            v-else-if="kind === 'html'"
            :srcdoc="htmlPreviewDocument"
            :title="file.filename"
            class="preview-frame"
            sandbox=""
          ></iframe>

          <div v-else-if="kind === 'xlsx'" class="preview-xlsx">
            <div class="preview-sheet-tabs" role="tablist">
              <button
                v-for="name in sheetNames"
                :key="name"
                type="button"
                class="preview-sheet-tab"
                :class="{ 'preview-sheet-tab--active': name === selectedSheet }"
                role="tab"
                :aria-selected="name === selectedSheet"
                @click="updateSheet(name)"
              >
                {{ name }}
              </button>
            </div>
            <div
              class="preview-sheet-content"
              v-html="renderSheet(selectedSheet)"
            ></div>
          </div>

          <MarkdownRenderer
            v-else-if="kind === 'markdown'"
            :content="textContent"
            class="preview-markdown"
          />

          <pre v-else-if="kind === 'text'" class="preview-text">{{
            textContent
          }}</pre>
        </div>
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
.preview-backdrop {
  position: fixed;
  inset: 0;
  z-index: 9999;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 4vh 4vw;
  background: rgba(0, 0, 0, 0.7);
}
.preview-panel {
  display: flex;
  flex-direction: column;
  width: 100%;
  max-width: 960px;
  height: 100%;
  max-height: 90vh;
  overflow: hidden;
  background: var(--sl-bg-surface);
  border-radius: 12px;
  box-shadow: 0 12px 48px rgba(0, 0, 0, 0.4);
}
.preview-panel--fullscreen,
.preview-panel:fullscreen {
  width: 100vw;
  max-width: none;
  height: 100vh;
  max-height: none;
  border-radius: 0;
}
.preview-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 12px 16px;
  border-bottom: 1px solid var(--sl-border-default);
}
.preview-title {
  min-width: 0;
  overflow: hidden;
  font-size: 14px;
  font-weight: 600;
  color: var(--sl-text-primary);
  text-overflow: ellipsis;
  white-space: nowrap;
}
.preview-tools {
  display: flex;
  flex-shrink: 0;
  gap: 4px;
}
.preview-tool {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  color: var(--sl-text-muted);
  border-radius: 8px;
  transition: all 0.15s;
}
.preview-tool:hover {
  color: var(--sl-accent);
  background: var(--sl-bg-hover);
}
.preview-body {
  flex: 1;
  min-height: 0;
  overflow: auto;
  background: var(--sl-bg-canvas);
}
.preview-status {
  padding: 48px 16px;
  font-size: 14px;
  color: var(--sl-text-muted);
  text-align: center;
}
.preview-security-note {
  padding: 8px 16px;
  color: var(--sl-text-muted);
  background: var(--sl-bg-surface);
  border-bottom: 1px solid var(--sl-border-default);
  font-size: 12px;
}
.preview-image {
  display: block;
  max-width: 100%;
  margin: 0 auto;
}
.preview-frame {
  width: 100%;
  height: 100%;
  min-height: 70vh;
  background: var(--sl-bg-surface);
  border: 0;
}
.preview-pptx {
  min-height: 620px;
  overflow: auto;
  background: #202124;
}
.preview-markdown {
  padding: 20px 24px;
}
.preview-text {
  padding: 20px 24px;
  margin: 0;
  overflow-x: auto;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 13px;
  line-height: 1.6;
  color: var(--sl-text-primary);
  white-space: pre-wrap;
  word-break: break-word;
}
.preview-source {
  min-height: 100%;
  padding: 20px 24px;
  margin: 0;
  overflow: auto;
  color: var(--sl-text-primary);
  background: var(--sl-bg-canvas);
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 13px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-word;
}
.preview-docx {
  max-width: 860px;
  min-height: 100%;
  padding: 32px 40px;
  margin: 0 auto;
  color: var(--sl-text-primary);
  background: var(--sl-bg-surface);
}
.preview-docx :deep(img) {
  max-width: 100%;
}
.preview-docx :deep(h1),
.preview-docx :deep(h2),
.preview-docx :deep(h3),
.preview-docx :deep(h4),
.preview-docx :deep(h5),
.preview-docx :deep(h6) {
  margin: 1.4em 0 0.6em;
  font-weight: 700;
  line-height: 1.25;
  color: var(--sl-text-primary);
}
.preview-docx :deep(h1) {
  margin-top: 0;
  font-size: 1.8rem;
}
.preview-docx :deep(h2) {
  font-size: 1.45rem;
}
.preview-docx :deep(h3) {
  font-size: 1.2rem;
}
.preview-docx :deep(p) {
  margin: 0 0 0.9em;
  line-height: 1.75;
}
.preview-docx :deep(ul),
.preview-docx :deep(ol) {
  padding-left: 1.6em;
  margin: 0 0 1em;
  line-height: 1.7;
}
.preview-docx :deep(li) {
  padding-left: 0.25em;
  margin: 0.2em 0;
}
.preview-docx :deep(blockquote) {
  padding: 0.7em 1em;
  margin: 1em 0;
  color: var(--sl-text-secondary);
  background: var(--sl-bg-canvas);
  border-left: 3px solid var(--sl-border-strong);
}
.preview-docx :deep(table) {
  width: 100%;
  margin: 1.2em 0;
  border-collapse: collapse;
  font-size: 0.92em;
}
.preview-docx :deep(th),
.preview-docx :deep(td) {
  padding: 0.55em 0.7em;
  text-align: left;
  vertical-align: top;
  border: 1px solid var(--sl-border-default);
}
.preview-docx :deep(th) {
  font-weight: 600;
  background: var(--sl-bg-canvas);
}
.preview-docx :deep(a) {
  color: var(--sl-accent);
  text-decoration: underline;
  text-underline-offset: 2px;
}
.preview-docx :deep(hr) {
  margin: 1.5em 0;
  border: 0;
  border-top: 1px solid var(--sl-border-default);
}
.preview-docx :deep(pre) {
  padding: 12px 14px;
  margin: 1em 0;
  overflow-x: auto;
  color: var(--sl-code-text);
  background: var(--sl-code-bg);
  border-radius: 6px;
}
.preview-docx :deep(code) {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
}
.preview-xlsx {
  min-width: max-content;
  min-height: 100%;
  padding: 12px;
}
.preview-sheet-tabs {
  display: flex;
  gap: 4px;
  margin-bottom: 12px;
}
.preview-sheet-tab {
  padding: 7px 12px;
  color: var(--sl-text-muted);
  border-radius: 6px;
}
.preview-sheet-tab--active,
.preview-sheet-tab:hover {
  color: var(--sl-text-primary);
  background: var(--sl-bg-surface);
}
.preview-sheet-content {
  overflow: auto;
  background: var(--sl-bg-surface);
}
.preview-sheet-content :deep(table) {
  border-collapse: collapse;
}
.preview-sheet-content :deep(td) {
  min-width: 96px;
  max-width: 360px;
  padding: 7px 10px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  border: 1px solid var(--sl-border-default);
}
</style>
