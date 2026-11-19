import assert from 'node:assert/strict'
import test from 'node:test'

import {
  assistantCapability,
  promptSuggestionKeys
} from '../src/pages/lens/chatPromptSuggestions.js'

test('uses code-analysis suggestions for code assistants', () => {
  assert.deepEqual(promptSuggestionKeys('code_analysis'), [
    'lens.chat.suggestions.codeArchitecture',
    'lens.chat.suggestions.codeFlow',
    'lens.chat.suggestions.codeRisks'
  ])
})

test('uses knowledge suggestions for knowledge assistants', () => {
  assert.deepEqual(promptSuggestionKeys('knowledge_qa'), [
    'lens.chat.suggestions.knowledgeSummary',
    'lens.chat.suggestions.knowledgeCompare',
    'lens.chat.suggestions.knowledgeActions'
  ])
})

test('falls back to general task suggestions for unknown assistants', () => {
  assert.deepEqual(promptSuggestionKeys('custom_task'), [
    'lens.chat.suggestions.generalCapabilities',
    'lens.chat.suggestions.generalAnalysis',
    'lens.chat.suggestions.generalPlan'
  ])
})

test('usage capabilities drive the correct suggestions without management task fields', () => {
  const assistant = { capability: 'code_analysis', name: 'Code helper' }
  assert.equal(assistantCapability(assistant), 'code_analysis')
  assert.deepEqual(
    promptSuggestionKeys(assistantCapability(assistant)),
    promptSuggestionKeys('code_analysis')
  )
  assert.equal(
    assistantCapability({ selected_task: 'general_chat' }),
    'general_chat'
  )
  assert.equal(
    assistantCapability({
      capability: 'knowledge_qa',
      selected_task: 'general_chat'
    }),
    'knowledge_qa'
  )
  assert.equal(assistantCapability(null), undefined)
})
