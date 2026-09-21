import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

import {
  assistantShowsCitations,
  citationLocation,
  citationSourceUrl,
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

test('connects message citations to the authenticated code drawer', async () => {
  const chat = await readFile(
    new URL('../src/pages/lens/Chat.vue', import.meta.url),
    'utf8'
  )
  const citationList = await readFile(
    new URL(
      '../src/pages/lens/components/MessageCitations.vue',
      import.meta.url
    ),
    'utf8'
  )
  const citationDrawer = await readFile(
    new URL(
      '../src/pages/lens/components/CodeCitationDrawer.vue',
      import.meta.url
    ),
    'utf8'
  )

  assert.match(chat, /<MessageCitations/)
  assert.match(chat, /<CodeCitationDrawer/)
  assert.match(chat, /getRunCitationSource/)
  assert.match(citationList, /<details/)
  assert.match(citationList, /type="button"/)
  assert.match(citationDrawer, /<BaseDrawer/)
  assert.match(citationDrawer, /highlight_start_line/)
  assert.match(citationDrawer, /role="alert"/)
  assert.match(citationDrawer, /isMarkdownCitationPath/)
  assert.match(citationDrawer, /<MarkdownRenderer/)
  assert.match(citationDrawer, /lens\.chat\.citations\.preview/)
  assert.match(citationDrawer, /lens\.chat\.citations\.source/)
})
