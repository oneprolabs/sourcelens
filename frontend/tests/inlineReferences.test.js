import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

import {
  buildReferenceIndex,
  linkifyReferences,
  stripSourceMarkers
} from '../src/utils/inlineReferences.js'

const REFERENCES = [
  {
    id: 'evidence-handler',
    path: 'hosted_demo/src/app.py',
    startLine: 10,
    endLine: 20
  }
]

function countButtons(html) {
  return (html.match(/data-inline-citation-id/g) || []).length
}

test('indexes the full path, the mount-relative path, and a unique basename', () => {
  const index = buildReferenceIndex(REFERENCES)

  assert.ok(index.has('hosted_demo/src/app.py'))
  assert.ok(index.has('src/app.py'))
  assert.ok(index.has('app.py'))
})

test('links a dot-slash prefixed path', () => {
  const output = linkifyReferences('<p>./src/app.py</p>', REFERENCES)

  assert.match(output, /data-inline-citation-id="evidence-handler"/)
  assert.match(output, />app\.py<\/button>/)
})

test('drops a basename shared by two citations', () => {
  const index = buildReferenceIndex([
    { id: 'one', path: 'one/app.py', startLine: 1, endLine: 1 },
    { id: 'two', path: 'two/app.py', startLine: 1, endLine: 1 }
  ])

  assert.ok(index.has('one/app.py'))
  assert.ok(index.has('two/app.py'))
  assert.ok(!index.has('app.py'))
})

test('links a path in prose and inside inline code', () => {
  const output = linkifyReferences(
    '<p>See <code>src/app.py</code> and src/app.py.</p>',
    REFERENCES
  )

  assert.equal(countButtons(output), 2)
  assert.match(output, /data-inline-citation-id="evidence-handler"/)
  assert.doesNotMatch(output, /&amp;/)
})

test('keeps a cited line range in the linked label', () => {
  const output = linkifyReferences('<p>src/app.py:15</p>', REFERENCES)

  assert.match(output, />app\.py:15<\/button>/)
  assert.match(output, /title="src\/app\.py"/)
})

test('links a unique bare basename but not a longer path suffix', () => {
  const output = linkifyReferences(
    '<p>app.py and my-app.py and src/app.py</p>',
    REFERENCES
  )

  assert.equal(countButtons(output), 2)
  assert.match(output, />app\.py<\/button>/)
  assert.doesNotMatch(output, />my-app\.py<\/button>/)
})

test('leaves block code and external links untouched', () => {
  const output = linkifyReferences(
    '<pre><code>src/app.py</code></pre>' +
      '<p><a href="https://x/app.py">src/app.py</a></p>' +
      '<p>src/app.py</p>',
    REFERENCES
  )

  assert.match(output, /<pre><code>src\/app\.py<\/code><\/pre>/)
  assert.match(output, /<a href="https:\/\/x\/app\.py">src\/app\.py<\/a>/)
  assert.equal(countButtons(output), 1)
})

test('resolves the citation window that contains the cited line', () => {
  const references = [
    { id: 'top', path: 'src/app.py', startLine: 1, endLine: 5 },
    { id: 'bottom', path: 'src/app.py', startLine: 40, endLine: 60 }
  ]

  assert.match(
    linkifyReferences('<p>src/app.py:45</p>', references),
    /data-inline-citation-id="bottom"/
  )
  assert.match(
    linkifyReferences('<p>src/app.py</p>', references),
    /data-inline-citation-id="top"/
  )
})

test('returns the html unchanged when there are no references', () => {
  const html = '<p>src/app.py</p>'

  assert.equal(linkifyReferences(html, []), html)
})

test('renders a generation-time source marker as a chip in place', () => {
  const output = linkifyReferences(
    '<p>The value is 3. [[source: hosted_demo/src/app.py]] Then stop.</p>',
    REFERENCES
  )

  assert.match(output, /class="inline-citation-icon"/)
  assert.match(output, /data-inline-citation-id="evidence-handler"/)
  assert.match(output, />app\.py:10-20<\/button>/)
  assert.match(output, /title="hosted_demo\/src\/app\.py"/)
  assert.doesNotMatch(output, /\[\[source/)
})

test('resolves a marker by basename and by an explicit line range', () => {
  const byBasename = linkifyReferences('<p>[source: app.py]</p>', REFERENCES)
  assert.match(byBasename, /data-inline-citation-id="evidence-handler"/)

  const byRange = linkifyReferences(
    '<p>[source: src/app.py:15-25]</p>',
    REFERENCES
  )
  assert.match(byRange, /data-inline-citation-id="evidence-handler"/)
})

test('drops a marker that matches no consulted source', () => {
  const output = linkifyReferences(
    '<p>Claim. [[source: secret/other.py]]</p>',
    REFERENCES
  )

  assert.doesNotMatch(output, /\[\[source/)
  assert.doesNotMatch(output, /data-inline-citation-id/)
})

test('still strips markers when citations are unavailable', () => {
  const output = linkifyReferences(
    '<p>Claim. [[source: hosted_demo/src/app.py]]</p>',
    []
  )

  assert.equal(output, '<p>Claim. </p>')
})

test('strips source markers from exported text', () => {
  assert.equal(
    stripSourceMarkers('Revenue grew. [[source: report.xlsx]] Then dipped.'),
    'Revenue grew. Then dipped.'
  )
  assert.equal(
    stripSourceMarkers('Total [[source: a/b.md:1-2]], ok.'),
    'Total, ok.'
  )
  assert.equal(stripSourceMarkers('No markers here.'), 'No markers here.')
  assert.equal(stripSourceMarkers(''), '')
})

test('does not relink the path inside a source marker', () => {
  const output = linkifyReferences(
    '<p>Claim. [[source: src/app.py]]</p>',
    REFERENCES
  )

  assert.equal(countButtons(output), 1)
})

test('leaves a marker inside code untouched', () => {
  const output = linkifyReferences(
    '<pre><code>[[source: src/app.py]]</code></pre>',
    REFERENCES
  )

  assert.match(output, /\[\[source: src\/app\.py\]\]/)
  assert.doesNotMatch(output, /data-inline-citation-id/)
})

test('chat and the markdown renderer wire inline references together', async () => {
  const chat = await readFile(
    new URL('../src/pages/lens/Chat.vue', import.meta.url),
    'utf8'
  )
  const renderer = await readFile(
    new URL('../src/components/ui/MarkdownRenderer.vue', import.meta.url),
    'utf8'
  )
  const sanitizer = await readFile(
    new URL('../src/utils/sanitize.js', import.meta.url),
    'utf8'
  )

  assert.match(chat, /:references="messageReferences\(message\)"/)
  assert.match(
    chat,
    /@reference-click="openInlineCitation\(message, \$event\)"/
  )
  assert.match(chat, /function messageReferences\(message\)/)
  assert.match(chat, /function openInlineCitation\(message, citationId\)/)
  assert.match(renderer, /data-inline-citation-id/)
  assert.match(renderer, /emit\('reference-click'/)
  assert.match(renderer, /sanitizeHtml\(\s*linkifyReferences\(/)
  assert.match(sanitizer, /'data-inline-citation-id'/)
})
