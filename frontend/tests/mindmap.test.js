import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
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
  assert.doesNotMatch(html, /data-mindmap-action="(?:expand|collapse)"/)
  assert.match(html, /data-mindmap-action="zoom-out"/)
  assert.match(html, /data-mindmap-action="zoom-in"/)
  assert.match(html, /data-mindmap-action="fit"/)
  assert.match(html, /data-mindmap-action="fullscreen"/)
  assert.match(html, /data-mindmap-action="code"/)
  assert.match(html, /data-mindmap-width="[0-9.]+"/)
  assert.match(html, /data-mindmap-height="[0-9.]+"/)
  assert.match(
    html,
    /class="mindmap-canvas"[\s\S]*class="mindmap-code-panel" aria-hidden="true"/
  )
  assert.match(html, /<circle cx="284" cy="[0-9.]+" r="6"/)
  assert.match(html, /<text x="216" y="[0-9.]+" fill="currentColor"/)
})

test('draws a dot only on branch nodes, just after the label', () => {
  const html = renderMindmap(source)

  // "一、定位" is a branch: label spans x=216..272 with its dot at x=284.
  assert.match(html, /<tspan x="216" dy="0">一、定位<\/tspan>/)
  assert.match(html, /<circle cx="284" cy="[0-9.]+" r="6"/)
  // "评估对象" is a leaf at x=324 and must not get a dot.
  assert.match(html, /<tspan x="324" dy="0">评估对象<\/tspan>/)
  assert.doesNotMatch(html, /<circle cx="324"/)
  // The branch leaves the parent dot (284) and ends at the child label (324).
  assert.match(
    html,
    /data-mindmap-link="0\.1\.1" d="M284,[0-9.]+ C[^"]* 314,[0-9.]+"/
  )
})

test('does not emit unescaped SVG text', () => {
  const html = renderMindmap('## A <script>alert(1)</script>\n- x & y')

  assert.doesNotMatch(html, /<script>/)
  assert.match(html, /&lt;script&gt;/)
  assert.match(html, /x &amp; y/)
})

test('renders node positions with sanitizer-allowed SVG attributes', () => {
  const sanitizer = readFileSync(
    new URL('../src/utils/sanitize.js', import.meta.url),
    'utf8'
  )

  assert.doesNotMatch(renderMindmap(source), /\stransform=/)
  assert.match(sanitizer, /['"]cx['"]/)
  assert.match(sanitizer, /['"]cy['"]/)
  assert.match(sanitizer, /['"]x['"]/)
  assert.match(sanitizer, /['"]y['"]/)
  assert.match(sanitizer, /['"]data-mindmap-width['"]/)
  assert.match(sanitizer, /['"]data-mindmap-height['"]/)
})

test('includes escaped source code for the code view', () => {
  const html = renderMindmap('## A <script>alert(1)</script>\n- x & y')

  assert.match(html, /class="mindmap-code-panel" aria-hidden="true"/)
  assert.match(html, /&lt;script&gt;alert\(1\)&lt;\/script&gt;/)
  assert.match(html, /x &amp; y/)
})
