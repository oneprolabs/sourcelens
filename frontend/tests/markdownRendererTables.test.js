import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const rendererSource = readFileSync(
  new URL('../src/components/ui/MarkdownRenderer.vue', import.meta.url),
  'utf8'
)

test('Markdown tables render inside a bounded horizontal scroll container', () => {
  assert.match(
    rendererSource,
    /<div class="markdown-table-scroll">[\s\S]*?renderTable\(token\)/
  )
  assert.match(
    rendererSource,
    /\.markdown-content :deep\(\.markdown-table-scroll\) \{[\s\S]*?max-w-full[\s\S]*?overflow-x-auto/
  )
})

test('Markdown tables keep readable intrinsic columns on mobile only', () => {
  assert.match(
    rendererSource,
    /@media \(max-width: 639px\) \{[\s\S]*?\.markdown-content :deep\(table\) \{[\s\S]*?width: max-content;[\s\S]*?min-width: 100%;/
  )
  assert.match(
    rendererSource,
    /@media \(max-width: 639px\) \{[\s\S]*?\.markdown-content :deep\(th\),[\s\S]*?\.markdown-content :deep\(td\) \{[\s\S]*?word-break: keep-all;[\s\S]*?overflow-wrap: normal;/
  )
})

test('Markdown tables use the compact v2 card surfaces', () => {
  assert.match(
    rendererSource,
    /\.markdown-content :deep\(\.markdown-table-scroll\) \{[\s\S]*?rounded-\[12px\] border border-line/
  )
  assert.match(
    rendererSource,
    /\.markdown-content :deep\(table\) \{[\s\S]*?font-size: 13\.5px;/
  )
  assert.match(
    rendererSource,
    /\.markdown-content :deep\(th\) \{[\s\S]*?text-\[12\.5px\]/
  )
})
