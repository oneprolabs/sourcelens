import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const rendererSource = readFileSync(
  new URL('../src/components/ui/MarkdownRenderer.vue', import.meta.url),
  'utf8'
)
const sanitizerSource = readFileSync(
  new URL('../src/utils/sanitize.js', import.meta.url),
  'utf8'
)

test('block code renderer emits an isolated copyable container', () => {
  assert.match(rendererSource, /markdown-code-block/)
  assert.match(rendererSource, /markdown-code-header/)
  assert.match(rendererSource, /data-markdown-code-copy/)
  assert.match(rendererSource, /<pre><code class="hljs/)
})

test('code copying uses plain code text and localized success feedback', () => {
  assert.match(rendererSource, /querySelector\('code'\)/)
  assert.match(rendererSource, /copyToClipboard\(code\.textContent \|\| ''\)/)
  assert.match(rendererSource, /t\('common\.copied'\)/)
  assert.match(rendererSource, /t\('common\.copy'\)/)
  assert.match(rendererSource, /data-markdown-code-copied/)
  assert.match(rendererSource, /if \(!copied\) return/)
})

test('copy control is icon-only while retaining accessible labels', () => {
  assert.match(
    rendererSource,
    /title="\$\{escapeHtml\(t\('common\.copy'\)\)\}"><\/button>/
  )
  assert.match(rendererSource, /markdown-code-copy::before/)
  assert.match(rendererSource, /aria-label=/)
})

test('code styles preserve indentation and wrap long lines', () => {
  assert.match(rendererSource, /white-space: pre-wrap;/)
  assert.match(rendererSource, /overflow-x-auto/)
  assert.match(rendererSource, /word-break: break-word;/)
  assert.match(rendererSource, /overflow-wrap: anywhere;/)
})

test('syntax highlighting uses the common bundle and resolves aliases', () => {
  assert.match(rendererSource, /highlight\.js\/lib\/common/)
  assert.doesNotMatch(rendererSource, /highlight\.js\/lib\/core/)
  assert.match(rendererSource, /hljs\.getLanguage\(/)
  assert.match(rendererSource, /ignoreIllegals: true/)
  assert.doesNotMatch(rendererSource, /hljs\.highlightAuto\(/)
})

test('light and dark themes define separate syntax palettes', () => {
  assert.match(rendererSource, /\.hljs-keyword[\s\S]*?color: #a626a4/)
  assert.match(
    rendererSource,
    /:root\[data-theme='dark'\][\s\S]*?\.hljs-keyword[\s\S]*?color: #f92672/
  )
  assert.match(rendererSource, /\.hljs-string[\s\S]*?color: #17813b/)
})

test('sanitizer allows only the code copy controls required by the renderer', () => {
  assert.match(sanitizerSource, /'button'/)
  assert.match(sanitizerSource, /'type'/)
  assert.match(sanitizerSource, /'aria-label'/)
  assert.match(sanitizerSource, /'data-markdown-code-copy'/)
  assert.doesNotMatch(sanitizerSource, /'onclick'/)
  assert.doesNotMatch(sanitizerSource, /'style'/)
})

test('inline code is not targeted by the block copy selector', () => {
  assert.match(rendererSource, /closest\('\[data-markdown-code-copy\]'\)/)
  assert.match(rendererSource, /closest\('\.markdown-code-block'\)/)
})
