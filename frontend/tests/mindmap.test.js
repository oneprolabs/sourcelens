import assert from 'node:assert/strict'
import test from 'node:test'
import { parseMindmap, renderMindmap } from '../src/utils/mindmap.js'

const source = `## **一、定位**
- 评估对象
- 传统差异
  - 仅评估输出文本
## **二、方法**
- 客观评测`

test('parses headings and indented bullets into a hierarchical tree', () => {
  const tree = parseMindmap(source)

  assert.equal(tree.children[0].label, '一、定位')
  assert.equal(tree.children[0].children[1].children[0].label, '仅评估输出文本')
  assert.equal(tree.children[1].children[0].label, '客观评测')
})

test('renders clickable node metadata and branch links', () => {
  const html = renderMindmap(source)

  assert.match(html, /data-mindmap-root/)
  assert.match(html, /data-mindmap-toggle="true"/)
  assert.match(html, /data-mindmap-path="0\.1\.2\.1"/)
  assert.match(html, /class="mindmap-link"/)
  assert.match(html, /data-mindmap-action="collapse"/)
})

test('does not emit unescaped SVG text', () => {
  const html = renderMindmap('## A <script>alert(1)</script>\n- x & y')

  assert.doesNotMatch(html, /<script>/)
  assert.match(html, /&lt;script&gt;/)
  assert.match(html, /x &amp; y/)
})
