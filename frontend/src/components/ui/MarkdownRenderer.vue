<template>
  <div
    class="markdown-content prose max-w-none"
    v-html="renderedContent"
    @click="handleMarkdownClick"
    @keydown="handleMarkdownKeydown"
  ></div>
</template>

<script setup>
import { computed } from 'vue'
import { marked } from 'marked'
import hljs from 'highlight.js/lib/core'
import { useI18n } from 'vue-i18n'
import { copyToClipboard } from '@/utils/clipboard'
import { sanitizeHtml, escapeHtml } from '@/utils/sanitize'
import { renderMindmap } from '@/utils/mindmap'

// Import common languages for syntax highlighting
import javascript from 'highlight.js/lib/languages/javascript'
import python from 'highlight.js/lib/languages/python'
import bash from 'highlight.js/lib/languages/bash'
import json from 'highlight.js/lib/languages/json'
import xml from 'highlight.js/lib/languages/xml'

// Register languages
hljs.registerLanguage('javascript', javascript)
hljs.registerLanguage('python', python)
hljs.registerLanguage('bash', bash)
hljs.registerLanguage('json', json)
hljs.registerLanguage('xml', xml)

const props = defineProps({
  content: {
    type: String,
    default: ''
  },
  enableHighlight: {
    type: Boolean,
    default: true
  }
})

const { t } = useI18n()
const copyResetTimers = new WeakMap()

const languageLabels = {
  bash: 'Bash',
  javascript: 'JavaScript',
  js: 'JavaScript',
  json: 'JSON',
  python: 'Python',
  py: 'Python',
  sh: 'Bash',
  shell: 'Bash',
  xml: 'XML'
}

// Configure marked. marked v16 removed the `highlight` option, so syntax
// highlighting runs in a custom code renderer instead. Emitting the
// `hljs` class lets the global highlight.js theme style the block (dark
// background + token colors) even when the language is unknown.
const renderer = new marked.Renderer()
renderer.code = ({ text, lang }) => {
  const declaredLanguage = typeof lang === 'string' ? lang.trim() : ''
  if (declaredLanguage.toLowerCase() === 'mindmap') {
    return renderMindmap(text)
  }
  const language =
    props.enableHighlight &&
    declaredLanguage &&
    hljs.getLanguage(declaredLanguage)
      ? declaredLanguage
      : ''
  const label = declaredLanguage
    ? languageLabels[declaredLanguage.toLowerCase()] || declaredLanguage
    : ''
  let body
  try {
    body = language
      ? hljs.highlight(text, { language }).value
      : escapeHtml(text)
  } catch (err) {
    body = escapeHtml(text)
  }
  const languageClass = language ? ` language-${escapeHtml(language)}` : ''
  const languageLabel = label
    ? `<span class="markdown-code-language">${escapeHtml(label)}</span>`
    : ''
  return (
    '<div class="markdown-code-block">' +
    '<div class="markdown-code-header">' +
    languageLabel +
    `<button type="button" class="markdown-code-copy" ` +
    `data-markdown-code-copy aria-label="${escapeHtml(t('common.copy'))}" ` +
    `title="${escapeHtml(t('common.copy'))}"></button>` +
    '</div>' +
    `<pre><code class="hljs${languageClass}">${body}</code></pre>` +
    '</div>'
  )
}

const renderTable = renderer.table.bind(renderer)
renderer.table = (token) => {
  return '<div class="markdown-table-scroll">' + renderTable(token) + '</div>'
}

// Open answer links in a new tab so clicking a doc link never replaces the
// chat page. rel="noopener noreferrer" avoids the opened page accessing
// window.opener. (sanitizeHtml already allows target/rel attributes.)
renderer.link = function link({ href, title, text, tokens }) {
  const inner = tokens && tokens.length ? this.parser.parseInline(tokens) : text
  const titleAttr = title ? ` title="${title}"` : ''
  return (
    `<a href="${href}"${titleAttr} target="_blank" ` +
    `rel="noopener noreferrer">${inner}</a>`
  )
}

marked.setOptions({
  renderer,
  breaks: true,
  gfm: true
})

const renderedContent = computed(() => {
  if (!props.content) return ''

  try {
    let markdown = props.content.replace(/\r\n/g, '\n').replace(/\r/g, '\n')

    // Convert relative image paths to absolute URLs
    // Always use local HTTP service, never object storage URLs
    // Match markdown image syntax: ![alt](path)
    markdown = markdown.replace(
      /!\[([^\]]*)\]\(([^)]+)\)/g,
      (match, alt, imagePath) => {
        const trimmedPath = imagePath.trim()
        const baseUrl = window.location.origin

        // If it's an OSS/object storage URL (https:// or http://), convert to local path
        if (
          trimmedPath.startsWith('http://') ||
          trimmedPath.startsWith('https://')
        ) {
          // OSS URL format: https://.../admin/articles/{article_id}/images/{filename}
          // Or: https://.../media/articles/{article_id}/{filename}
          // Extract article ID and filename
          let articleId = null
          let filename = null

          // Try pattern: /admin/articles/{article_id}/images/{filename}
          const adminMatch = trimmedPath.match(
            /\/admin\/articles\/([^/]+)\/images\/([^/]+)$/
          )
          if (adminMatch) {
            ;[, articleId, filename] = adminMatch
          } else {
            // Try pattern: /media/articles/{article_id}/{filename}
            const mediaMatch = trimmedPath.match(
              /\/media\/articles\/([^/]+)\/([^/]+)$/
            )
            if (mediaMatch) {
              ;[, articleId, filename] = mediaMatch
            } else {
              // Try to extract from any path that contains article ID pattern (UUID)
              const uuidPattern =
                /([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/
              const uuidMatch = trimmedPath.match(uuidPattern)
              if (uuidMatch) {
                articleId = uuidMatch[1]
                // Extract filename from URL
                const pathParts = trimmedPath.split('/')
                filename = pathParts[pathParts.length - 1]
              }
            }
          }

          if (articleId && filename) {
            const localUrl = `${baseUrl}/media/articles/${articleId}/${filename}`
            return `![${alt}](${localUrl})`
          }

          return match
        }

        // If starts with /media/articles, convert to full URL (local HTTP service)
        if (trimmedPath.startsWith('/media/articles')) {
          const fullUrl = `${baseUrl}${trimmedPath}`
          return `![${alt}](${fullUrl})`
        }

        // If relative path starting with /, convert to full URL
        if (trimmedPath.startsWith('/')) {
          const fullUrl = `${baseUrl}${trimmedPath}`
          return `![${alt}](${fullUrl})`
        }

        // For relative paths (no leading /), try to construct URL
        // This handles cases like "images/photo.jpg"
        if (trimmedPath && !trimmedPath.includes('://')) {
          const fullUrl = `${baseUrl}/${trimmedPath}`
          return `![${alt}](${fullUrl})`
        }

        // Return original if no conversion needed
        return match
      }
    )

    const html = marked.parse(markdown)

    // Also process img tags in the HTML output (in case marked already converted them)
    // Always convert to local HTTP service URLs
    const processedHtml = html.replace(
      /<img([^>]*)\ssrc=["']([^"']+)["']([^>]*)>/gi,
      (match, before, src, after) => {
        const trimmedSrc = src.trim()
        const baseUrl = window.location.origin

        // If it's an OSS/object storage URL, convert to local path
        if (
          trimmedSrc.startsWith('http://') ||
          trimmedSrc.startsWith('https://')
        ) {
          // OSS URL format: https://.../admin/articles/{article_id}/images/{filename}
          // Or: https://.../media/articles/{article_id}/{filename}
          let articleId = null
          let filename = null

          // Try pattern: /admin/articles/{article_id}/images/{filename}
          const adminMatch = trimmedSrc.match(
            /\/admin\/articles\/([^/]+)\/images\/([^/]+)$/
          )
          if (adminMatch) {
            ;[, articleId, filename] = adminMatch
          } else {
            // Try pattern: /media/articles/{article_id}/{filename}
            const mediaMatch = trimmedSrc.match(
              /\/media\/articles\/([^/]+)\/([^/]+)$/
            )
            if (mediaMatch) {
              ;[, articleId, filename] = mediaMatch
            } else {
              // Try to extract from any path that contains article ID pattern (UUID)
              const uuidPattern =
                /([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/
              const uuidMatch = trimmedSrc.match(uuidPattern)
              if (uuidMatch) {
                articleId = uuidMatch[1]
                // Extract filename from URL
                const pathParts = trimmedSrc.split('/')
                filename = pathParts[pathParts.length - 1]
              }
            }
          }

          if (articleId && filename) {
            const localUrl = `${baseUrl}/media/articles/${articleId}/${filename}`
            return `<img${before} src="${localUrl}"${after}>`
          }

          return match
        }

        // Convert relative paths to full URLs (local HTTP service)
        if (trimmedSrc.startsWith('/')) {
          const fullUrl = `${baseUrl}${trimmedSrc}`
          return `<img${before} src="${fullUrl}"${after}>`
        }

        return match
      }
    )

    return sanitizeHtml(processedHtml)
  } catch (error) {
    // Surface the real failure instead of silently degrading to plain text;
    // a throw here means the answer renders unformatted, so it must be
    // visible in the console for diagnosis rather than swallowed.
    console.error('MarkdownRenderer failed to render content:', error)
    return `<pre class="text-theme-secondary">${escapeHtml(props.content || '')}</pre>`
  }
})

async function handleMarkdownClick(event) {
  const action = event.target.closest('[data-mindmap-action]')
  if (action) {
    const mindmap = action.closest('[data-mindmap-root]')
    if (!mindmap) return
    const shouldCollapse = action.dataset.mindmapAction === 'collapse'
    mindmap
      .querySelectorAll(
        '[data-mindmap-toggle][data-mindmap-has-children="true"]'
      )
      .forEach((node) => {
        node.dataset.mindmapCollapsed = shouldCollapse ? 'true' : 'false'
        node.setAttribute('aria-expanded', String(!shouldCollapse))
      })
    updateMindmapVisibility(mindmap)
    return
  }

  const mindmapNode = event.target.closest('[data-mindmap-toggle]')
  if (mindmapNode) {
    toggleMindmapNode(mindmapNode)
    return
  }

  const button = event.target.closest('[data-markdown-code-copy]')
  if (!button) return

  const code = button.closest('.markdown-code-block')?.querySelector('code')
  if (!code) return

  const copied = await copyToClipboard(code.textContent || '')
  if (!copied) return

  const copiedLabel = t('common.copied')
  button.title = copiedLabel
  button.setAttribute('aria-label', copiedLabel)
  button.setAttribute('data-markdown-code-copied', '')

  const existingTimer = copyResetTimers.get(button)
  if (existingTimer) clearTimeout(existingTimer)
  copyResetTimers.set(
    button,
    setTimeout(() => {
      const copyLabel = t('common.copy')
      button.title = copyLabel
      button.setAttribute('aria-label', copyLabel)
      button.removeAttribute('data-markdown-code-copied')
      copyResetTimers.delete(button)
    }, 1800)
  )
}

function handleMarkdownKeydown(event) {
  if (event.key !== 'Enter' && event.key !== ' ') return
  const mindmapNode = event.target.closest('[data-mindmap-toggle]')
  if (!mindmapNode) return
  event.preventDefault()
  toggleMindmapNode(mindmapNode)
}

function toggleMindmapNode(mindmapNode) {
  const mindmap = mindmapNode.closest('[data-mindmap-root]')
  if (!mindmap) return
  mindmapNode.dataset.mindmapCollapsed =
    mindmapNode.dataset.mindmapCollapsed === 'true' ? 'false' : 'true'
  mindmapNode.setAttribute(
    'aria-expanded',
    mindmapNode.dataset.mindmapCollapsed !== 'true'
  )
  updateMindmapVisibility(mindmap)
}

function updateMindmapVisibility(mindmap) {
  const nodes = [...mindmap.querySelectorAll('[data-mindmap-toggle]')]
  const collapsedPaths = new Set(
    nodes
      .filter((node) => node.dataset.mindmapCollapsed === 'true')
      .map((node) => node.dataset.mindmapPath)
  )
  const isHidden = (path) => {
    const segments = path.split('.')
    return segments.some((_, index) => {
      if (index === 0) return false
      return collapsedPaths.has(segments.slice(0, index).join('.'))
    })
  }

  nodes.forEach((node) => {
    node.classList.toggle(
      'mindmap-node-hidden',
      isHidden(node.dataset.mindmapPath)
    )
  })
  mindmap.querySelectorAll('[data-mindmap-link]').forEach((link) => {
    link.classList.toggle(
      'mindmap-node-hidden',
      isHidden(link.dataset.mindmapLink)
    )
  })
}
</script>

<style scoped>
.markdown-content {
  @apply text-theme-secondary;
}

/* Override prose styles for better readability */
.markdown-content :deep(h1) {
  @apply mb-4 mt-6 text-xl font-bold text-theme first:mt-0;
}

.markdown-content :deep(h2) {
  @apply mb-3 mt-5 text-lg font-semibold text-theme first:mt-0;
}

.markdown-content :deep(h3) {
  @apply mb-2 mt-4 text-base font-medium text-theme first:mt-0;
}

.markdown-content :deep(h4) {
  @apply mb-2 mt-3 text-sm font-medium text-theme first:mt-0;
}

.markdown-content :deep(p) {
  @apply mb-3 leading-relaxed;
  white-space: pre-wrap;
}

.markdown-content :deep(br) {
  display: block;
  content: '';
  margin-bottom: 0.25em;
}

.markdown-content :deep(ul) {
  @apply list-disc list-outside mb-3 space-y-1 ml-6;
}

.markdown-content :deep(ol) {
  @apply list-decimal list-outside mb-3 space-y-1 ml-6;
}

.markdown-content :deep(li) {
  @apply text-theme-secondary;
}

.markdown-content :deep(blockquote) {
  @apply my-4 border-l-4 border-line-strong pl-4 italic text-theme-secondary;
}

.markdown-content :deep(code) {
  @apply rounded bg-surface-hover px-1 py-0.5 font-mono text-sm text-theme;
}

.markdown-content :deep(pre) {
  @apply m-0 max-w-full overflow-x-auto border-0 p-4 text-sm;
  background: #f3f3f3;
  white-space: pre;
  tab-size: 4;
}

.markdown-content :deep(pre code) {
  @apply block min-w-full w-max bg-transparent p-0 font-mono;
  color: #18181b;
  white-space: pre;
  word-break: normal;
}

.markdown-content :deep(.markdown-code-block) {
  @apply my-4 w-full min-w-0 max-w-full overflow-hidden rounded-lg border;
  border-color: transparent;
  background: #f3f3f3;
}

.markdown-content :deep(.markdown-mindmap) {
  @apply my-4 w-full overflow-hidden rounded-lg border;
  border-color: var(--sl-border, #e4e4e7);
  background: var(--sl-bg-surface, #fff);
}

.markdown-content :deep(.mindmap-toolbar) {
  @apply flex min-h-10 items-center gap-2 border-b px-3 py-2;
  border-color: var(--sl-border, #e4e4e7);
  background: var(--sl-bg-hover, #f8fafc);
}

.markdown-content :deep(.mindmap-toolbar-title) {
  @apply mr-auto text-sm font-medium;
  color: var(--sl-text-primary, #18181b);
}

.markdown-content :deep(.mindmap-toolbar button) {
  @apply rounded-md border px-2 py-1 text-xs transition-colors;
  border-color: var(--sl-border, #d4d4d8);
  color: var(--sl-text-secondary, #52525b);
  background: transparent;
}

.markdown-content :deep(.mindmap-toolbar button:hover) {
  background: var(--sl-bg-surface, #fff);
  color: var(--sl-text-primary, #18181b);
}

.markdown-content :deep(.mindmap-canvas) {
  @apply max-w-full overflow-auto;
  min-height: 180px;
}

.markdown-content :deep(.mindmap-svg) {
  display: block;
  width: max(100%, 560px);
  min-width: 560px;
  min-height: 180px;
  color: var(--sl-text-primary, #18181b);
}

.markdown-content :deep(.mindmap-link) {
  fill: none;
  stroke-width: 1.5;
}

.markdown-content :deep(.mindmap-node) {
  cursor: pointer;
  outline: none;
}

.markdown-content :deep(.mindmap-node circle) {
  stroke-width: 1.5;
}

.markdown-content :deep(.mindmap-node text) {
  font: 400 14px/18px sans-serif;
}

.markdown-content :deep(.mindmap-node:hover text),
.markdown-content :deep(.mindmap-node:focus text) {
  font-weight: 600;
}

.markdown-content :deep(.mindmap-node:focus circle) {
  stroke-width: 2.5;
}

.markdown-content :deep(.mindmap-node-hidden) {
  display: none;
}

:global(:root[data-theme='dark'] .markdown-content .markdown-mindmap) {
  border-color: #3f3f46;
  background: #19191b;
}

:global(:root[data-theme='dark'] .markdown-content .mindmap-toolbar) {
  border-color: #3f3f46;
  background: #27272a;
}

:global(:root[data-theme='dark'] .markdown-content .mindmap-toolbar button) {
  border-color: #52525b;
  color: #d4d4d8;
}

:global(
  :root[data-theme='dark'] .markdown-content .mindmap-toolbar button:hover
) {
  background: #3f3f46;
  color: #fafafa;
}

.markdown-content :deep(.markdown-code-header) {
  @apply flex min-h-12 items-center border-b px-3.5 py-2;
  border-color: transparent;
  background: #f3f3f3;
}

.markdown-content :deep(.markdown-code-language) {
  @apply font-mono text-sm font-medium;
  color: #27272a;
}

.markdown-content :deep(.markdown-code-copy) {
  @apply ml-auto inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-md border transition-colors;
  --markdown-copy-bg: transparent;
  border-color: transparent;
  background: var(--markdown-copy-bg);
  color: #27272a;
}

.markdown-content :deep(.markdown-code-copy::before) {
  width: 0.8rem;
  height: 0.8rem;
  border: 1.5px solid currentColor;
  border-radius: 2px;
  box-shadow:
    -3px -3px 0 -1px var(--markdown-copy-bg),
    -3px -3px 0 0 currentColor;
  content: '';
}

.markdown-content
  :deep(.markdown-code-copy[data-markdown-code-copied]::before) {
  width: 0.8rem;
  height: 0.45rem;
  border-width: 0 0 1.75px 1.75px;
  border-radius: 0;
  box-shadow: none;
  transform: translateY(-1px) rotate(-45deg);
}

.markdown-content :deep(.markdown-code-copy:hover) {
  --markdown-copy-bg: #e4e4e7;
  border-color: #d4d4d8;
}

.markdown-content :deep(.markdown-code-copy:active) {
  --markdown-copy-bg: #d4d4d8;
  transform: translateY(1px);
}

.markdown-content :deep(.markdown-code-copy:focus-visible) {
  @apply outline-none ring-2 ring-primary-500 ring-offset-2;
  --tw-ring-offset-color: #f3f3f3;
}

/* Custom styles for code highlighting - terminal theme */
.markdown-content :deep(.hljs) {
  background: #f3f3f3;
}

.markdown-content :deep(.hljs-comment),
.markdown-content :deep(.hljs-quote) {
  color: #6e7781;
}

.markdown-content :deep(.hljs-keyword),
.markdown-content :deep(.hljs-selector-tag),
.markdown-content :deep(.hljs-subst) {
  color: #a626a4;
}

.markdown-content :deep(.hljs-title),
.markdown-content :deep(.hljs-section),
.markdown-content :deep(.hljs-built_in),
.markdown-content :deep(.hljs-type) {
  color: #6f42c1;
}

.markdown-content :deep(.hljs-string),
.markdown-content :deep(.hljs-doctag),
.markdown-content :deep(.hljs-attr),
.markdown-content :deep(.hljs-template-tag),
.markdown-content :deep(.hljs-template-variable) {
  color: #17813b;
}

.markdown-content :deep(.hljs-number),
.markdown-content :deep(.hljs-literal),
.markdown-content :deep(.hljs-symbol),
.markdown-content :deep(.hljs-bullet) {
  color: #b54708;
}

.markdown-content :deep(.hljs-variable),
.markdown-content :deep(.hljs-params),
.markdown-content :deep(.hljs-property) {
  color: #0550ae;
}

:global(:root[data-theme='dark'] .markdown-content pre),
:global(:root[data-theme='dark'] .markdown-content .hljs),
:global(:root[data-theme='dark'] .markdown-content .markdown-code-block) {
  background: #19191b;
}

:global(:root[data-theme='dark'] .markdown-content pre code) {
  color: #f4f4f5;
}

:global(:root[data-theme='dark'] .markdown-content .hljs-comment),
:global(:root[data-theme='dark'] .markdown-content .hljs-quote) {
  color: #88846f;
}

:global(:root[data-theme='dark'] .markdown-content .hljs-keyword),
:global(:root[data-theme='dark'] .markdown-content .hljs-selector-tag),
:global(:root[data-theme='dark'] .markdown-content .hljs-subst) {
  color: #f92672;
}

:global(:root[data-theme='dark'] .markdown-content .hljs-title),
:global(:root[data-theme='dark'] .markdown-content .hljs-section),
:global(:root[data-theme='dark'] .markdown-content .hljs-built_in),
:global(:root[data-theme='dark'] .markdown-content .hljs-type) {
  color: #a6e22e;
}

:global(:root[data-theme='dark'] .markdown-content .hljs-string),
:global(:root[data-theme='dark'] .markdown-content .hljs-doctag),
:global(:root[data-theme='dark'] .markdown-content .hljs-attr),
:global(:root[data-theme='dark'] .markdown-content .hljs-template-tag),
:global(:root[data-theme='dark'] .markdown-content .hljs-template-variable) {
  color: #e6db74;
}

:global(:root[data-theme='dark'] .markdown-content .hljs-number),
:global(:root[data-theme='dark'] .markdown-content .hljs-literal),
:global(:root[data-theme='dark'] .markdown-content .hljs-symbol),
:global(:root[data-theme='dark'] .markdown-content .hljs-bullet) {
  color: #ae81ff;
}

:global(:root[data-theme='dark'] .markdown-content .hljs-variable),
:global(:root[data-theme='dark'] .markdown-content .hljs-params),
:global(:root[data-theme='dark'] .markdown-content .hljs-property) {
  color: #f8f8f2;
}

:global(:root[data-theme='dark'] .markdown-content .markdown-code-block),
:global(:root[data-theme='dark'] .markdown-content .markdown-code-header) {
  border-color: #505054;
}

:global(:root[data-theme='dark'] .markdown-content .markdown-code-header) {
  min-height: 3.5rem;
  padding: 0.75rem 1rem;
  background: #363638;
}

:global(:root[data-theme='dark'] .markdown-content .markdown-code-language) {
  font-size: 1rem;
  color: #d4d4d8;
}

:global(:root[data-theme='dark'] .markdown-content .markdown-code-copy) {
  --markdown-copy-bg: #454548;
  border-color: #5c5c61;
  color: #f4f4f5;
}

:global(:root[data-theme='dark'] .markdown-content .markdown-code-copy:hover) {
  --markdown-copy-bg: #505055;
  border-color: #74747a;
}

:global(:root[data-theme='dark'] .markdown-content .markdown-code-copy:active) {
  --markdown-copy-bg: #303034;
}

:global(
  :root[data-theme='dark'] .markdown-content .markdown-code-copy:focus-visible
) {
  --tw-ring-offset-color: #363638;
}

:global(:root[data-theme='dark'] .markdown-content pre) {
  padding: 1.5rem 2rem;
}

:global(:root[data-theme='dark'] .markdown-content pre code) {
  line-height: 1.75;
}

.markdown-content :deep(.markdown-table-scroll) {
  @apply my-4 w-full max-w-full overflow-x-auto;
}

.markdown-content :deep(table) {
  @apply w-full border-collapse border border-line-strong;
}

.markdown-content :deep(th) {
  @apply border border-line-strong bg-surface-sunken px-3 py-2 text-left font-medium text-theme;
}

.markdown-content :deep(td) {
  @apply border border-line-strong px-3 py-2 text-theme-secondary;
}

@media (max-width: 639px) {
  .markdown-content :deep(table) {
    width: max-content;
    min-width: 100%;
  }

  .markdown-content :deep(th),
  .markdown-content :deep(td) {
    word-break: keep-all;
    overflow-wrap: normal;
  }
}

.markdown-content :deep(a) {
  @apply text-primary-600 hover:text-primary-700 underline;
}

.markdown-content :deep(img) {
  @apply max-w-full h-auto rounded-lg shadow-md my-4;
}

.markdown-content :deep(strong) {
  @apply font-semibold text-theme;
}

.markdown-content :deep(em) {
  @apply italic;
}

.markdown-content :deep(hr) {
  @apply my-6 border-t border-line-strong;
}
</style>
