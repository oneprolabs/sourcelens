import assert from 'node:assert/strict'
import test from 'node:test'

import {
  isSelectableChatModel,
  selectableChatModels
} from '../src/components/llm/providerModels.js'
import {
  modelProviderOptions,
  modelParamSchema,
  selectableDecisionModels
} from '../src/components/llm/providerModels.js'

test('decision providers and models use their dedicated schema on mixed providers', () => {
  const schemas = {
    openai: {
      editable_params: ['temperature'],
      decision: {
        models: ['gpt-6-luna'],
        editable_params: ['model', 'api_key']
      }
    },
    typesafe: {
      model_types: ['decision'],
      decision: { models: ['jev-latest'] }
    },
    anthropic: { editable_params: ['temperature'] }
  }
  const providers = [{ id: 'openai' }, { id: 'typesafe' }, { id: 'anthropic' }]
  assert.deepEqual(
    modelProviderOptions(providers, schemas, 'decision').map((item) => item.id),
    ['openai', 'typesafe']
  )
  assert.deepEqual(
    modelProviderOptions(providers, schemas, 'llm').map((item) => item.id),
    ['openai', 'anthropic']
  )
  assert.deepEqual(
    modelParamSchema(schemas, 'openai', 'decision').editable_params,
    ['model', 'api_key']
  )
  assert.deepEqual(
    selectableDecisionModels(schemas.openai.decision).map((item) => item.id),
    ['gpt-6-luna']
  )
})

test('chat providers respect catalog model types when schemas lack that metadata', () => {
  const providers = [
    { id: 'openai', model_types: ['llm', 'decision'] },
    { id: 'litellm_proxy', model_types: ['decision'] }
  ]
  const schemas = {
    openai: { decision: { models: ['gpt-6-luna'] } },
    litellm_proxy: { decision: { models: [] } }
  }

  assert.deepEqual(
    modelProviderOptions(providers, schemas, 'llm').map((item) => item.id),
    ['openai']
  )
  assert.deepEqual(
    modelProviderOptions(providers, schemas, 'decision').map((item) => item.id),
    ['openai', 'litellm_proxy']
  )
})

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
