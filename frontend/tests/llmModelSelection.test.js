import assert from 'node:assert/strict'
import test from 'node:test'

import {
  isSelectableChatModel,
  selectableChatModels
} from '../src/components/llm/providerModels.js'

test('keeps plain chat models selectable', () => {
  assert.equal(
    isSelectableChatModel({
      id: 'openai/gpt-5.4-nano/d688d',
      mode: 'chat',
      capabilities: ['text-to-text', 'vision']
    }),
    true
  )
})

test('keeps a chat model that has no mode field', () => {
  assert.equal(isSelectableChatModel({ id: 'gpt-4o-mini' }), true)
})

test('excludes embedding models by capability', () => {
  assert.equal(
    isSelectableChatModel({
      id: 'text-embedding-3-small',
      mode: 'embedding',
      capabilities: ['embedding']
    }),
    false
  )
})

test('excludes text-to-image models by capability', () => {
  assert.equal(
    isSelectableChatModel({
      id: 'gpt-image-2',
      mode: 'image_generation',
      capabilities: ['text-to-image']
    }),
    false
  )
})

test('excludes a non-chat mode even without a known capability tag', () => {
  assert.equal(
    isSelectableChatModel({ id: 'some-image-model', mode: 'image_generation' }),
    false
  )
})

test('filters a provider model list down to chat models', () => {
  const models = [
    { id: 'chat-a', mode: 'chat', capabilities: ['text-to-text'] },
    { id: 'embed', mode: 'embedding', capabilities: ['embedding'] },
    { id: 'image', mode: 'image_generation', capabilities: ['text-to-image'] },
    { id: 'chat-b', mode: 'chat', capabilities: ['text-to-text'] }
  ]

  assert.deepEqual(
    selectableChatModels(models).map((m) => m.id),
    ['chat-a', 'chat-b']
  )
})

test('handles a missing model list', () => {
  assert.deepEqual(selectableChatModels(undefined), [])
})

test('sorts the remaining models by display name', () => {
  const models = [
    { id: 'z', label: 'Zeta', mode: 'chat' },
    { id: 'b', label: 'beta', mode: 'chat' },
    { id: 'a', label: 'Alpha', mode: 'chat' }
  ]

  assert.deepEqual(
    selectableChatModels(models).map((m) => m.label),
    ['Alpha', 'beta', 'Zeta']
  )
})
