import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

import { resolveComposerEnterAction } from '../src/pages/lens/chatComposerKeyboard.js'

test('submits with Enter and preserves multiline input with Shift+Enter', async () => {
  const source = await readFile(
    new URL('../src/pages/lens/Chat.vue', import.meta.url),
    'utf8'
  )

  assert.match(source, /@keydown="handleComposerKeydown"/)
  assert.doesNotMatch(
    source,
    /@keydown\.enter\.exact\.prevent="handlePrimaryAction"/
  )
  assert.doesNotMatch(
    source,
    /@keydown\.shift\.enter\.exact\.prevent="insertNewline"/
  )
  assert.doesNotMatch(source, /@keydown\.ctrl\.enter/)
})

test('keeps long composer input scrollable within a bounded height', async () => {
  const source = await readFile(
    new URL('../src/pages/lens/Chat.vue', import.meta.url),
    'utf8'
  )

  assert.match(source, /const COMPOSER_MAX_HEIGHT = 200/)
  assert.match(source, /Math\.min\(target\.scrollHeight, COMPOSER_MAX_HEIGHT\)/)
  assert.match(source, /max-height: var\(--composer-max-height\)/)
  assert.match(source, /overflow-y: auto/)
  assert.match(source, /overflow-x: hidden/)
})

test('resolves only explicit non-composing Enter presses as actions', () => {
  assert.equal(resolveComposerEnterAction({ key: 'Enter' }), 'primary')
  assert.equal(
    resolveComposerEnterAction({ key: 'Enter', shiftKey: true }),
    'newline'
  )
  assert.equal(
    resolveComposerEnterAction({ key: 'Enter', isComposing: true }),
    null
  )
  assert.equal(resolveComposerEnterAction({ key: 'Enter', keyCode: 229 }), null)
  assert.equal(
    resolveComposerEnterAction({ key: 'Enter', ctrlKey: true }),
    null
  )
  assert.equal(
    resolveComposerEnterAction({ key: 'Enter', metaKey: true }),
    null
  )
  assert.equal(resolveComposerEnterAction({ key: 'Enter', altKey: true }), null)
  assert.equal(resolveComposerEnterAction({ key: 'a' }), null)
})
