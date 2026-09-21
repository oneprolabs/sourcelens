import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

import {
  assistantShowsCitations,
  citationLocation,
  citationSourceUrl,
  isDocumentCitationPath,
  isMarkdownCitationPath
} from '../src/pages/lens/codeCitations.js'

test('formats workspace-relative citation paths with exact line ranges', () => {
  assert.equal(
    citationLocation({
      path: 'backend/lens/services.py',
      start_line: 2033,
      end_line: 2064
    }),
    'backend/lens/services.py:2033-2064'
  )
  assert.equal(
    citationLocation({ path: 'app.py', start_line: 7, end_line: 7 }),
    'app.py:7'
  )
})

test('builds citation source URLs from IDs instead of source paths', () => {
  const url = citationSourceUrl('run-123', 'evidence:handler.one')

  assert.equal(url, '/lens/runs/run-123/citations/evidence%3Ahandler.one/')
  assert.doesNotMatch(url, /services\.py/)
})

test('detects markdown citation paths for the rendered preview', () => {
  assert.equal(isMarkdownCitationPath('hosted_demo/docs/guide.md'), true)
  assert.equal(isMarkdownCitationPath('notes.MARKDOWN'), true)
  assert.equal(isMarkdownCitationPath('src/app.py'), false)
  assert.equal(isMarkdownCitationPath(''), false)
  assert.equal(isMarkdownCitationPath(undefined), false)
})

test('detects converted document paths for the rendered preview', () => {
  assert.equal(isDocumentCitationPath('hosted_demo/report.pdf'), true)
  assert.equal(isDocumentCitationPath('hosted_demo/book.XLSX'), true)
  assert.equal(isDocumentCitationPath('hosted_demo/notes.md'), true)
  assert.equal(isDocumentCitationPath('hosted_demo/data.csv'), true)
  assert.equal(isDocumentCitationPath('src/app.py'), false)
  assert.equal(isDocumentCitationPath('assets/icon.png'), false)
  assert.equal(isDocumentCitationPath(''), false)
  assert.equal(isDocumentCitationPath(undefined), false)
})

test('assistant citation preference defaults on for both payload shapes', () => {
  assert.equal(assistantShowsCitations(null), true)
  assert.equal(assistantShowsCitations({}), true)
  assert.equal(assistantShowsCitations({ show_citations: false }), false)
  assert.equal(assistantShowsCitations({ show_citations: true }), true)
  assert.equal(
    assistantShowsCitations({ settings: { features: { citations: false } } }),
    false
  )
  assert.equal(
    assistantShowsCitations({ settings: { features: { citations: true } } }),
    true
  )
})

test('assistant form and chat honor the citation switch', async () => {
  const assistants = await readFile(
    new URL('../src/pages/lens/Assistants.vue', import.meta.url),
    'utf8'
  )
  const form = await readFile(
    new URL(
      '../src/pages/lens/AssistantFormDrawerDirectEnvironment.vue',
      import.meta.url
    ),
    'utf8'
  )
  const chat = await readFile(
    new URL('../src/pages/lens/Chat.vue', import.meta.url),
    'utf8'
  )

  assert.match(
    assistants,
    /features\.citations = !!form\.value\.enable_citations/
  )
  assert.match(
    assistants,
    /enable_citations: row\.settings\?\.features\?\.citations !== false/
  )
  assert.match(form, /v-model="form\.enable_citations"/)
  assert.match(chat, /assistantShowsCitations/)
  assert.match(chat, /citationsEnabled/)
})

test('connects message citations to the authenticated code drawer', async () => {
  const chat = await readFile(
    new URL('../src/pages/lens/Chat.vue', import.meta.url),
    'utf8'
  )
  const renderer = await readFile(
    new URL('../src/components/ui/MarkdownRenderer.vue', import.meta.url),
    'utf8'
  )
  const citationDrawer = await readFile(
    new URL(
      '../src/pages/lens/components/CodeCitationDrawer.vue',
      import.meta.url
    ),
    'utf8'
  )

  assert.doesNotMatch(chat, /MessageCitations/)
  assert.match(chat, /:references="messageReferences\(message\)"/)
  assert.match(chat, /<CodeCitationDrawer/)
  assert.match(chat, /getRunCitationSource/)
  assert.match(renderer, /inline-citation-icon/)
  assert.match(citationDrawer, /<BaseDrawer/)
  assert.match(citationDrawer, /highlight_start_line/)
  assert.match(citationDrawer, /role="alert"/)
  assert.match(citationDrawer, /isDocumentCitationPath/)
  assert.match(citationDrawer, /<MarkdownRenderer/)
  assert.match(citationDrawer, /lens\.chat\.citations\.preview/)
  assert.match(citationDrawer, /lens\.chat\.citations\.source/)
})
