import assert from 'node:assert/strict'
import test from 'node:test'

import {
  eligibleReplacementModel,
  selectableReplacementAssistants,
  toggleReplacementSelection,
  replacementPickerPosition
} from '../src/pages/lens/assistantModelReplacement.js'

const assistants = [
  { uuid: 'a', agent_model_ref: 'old', status: 'active' },
  { uuid: 'b', agent_model_ref: 'other', status: 'active' },
  { uuid: 'c', agent_model_ref: null, status: 'active' },
  { uuid: 'd', agent_model_ref: 'old', status: 'archived' },
  { uuid: 'e', routing_mode: 'smart', status: 'active' }
]

test('assistant multi-select searches names and slugs across the full active list', () => {
  const rows = [
    { ...assistants[0], name: 'Code Advisor', slug: 'code-advisor' },
    { ...assistants[1], name: 'Research', slug: 'docs-reader' },
    { ...assistants[3], name: 'Archived Code', slug: 'old-code' },
    { ...assistants[4], name: 'Smart', slug: 'smart' }
  ]
  assert.deepEqual(
    selectableReplacementAssistants(rows, 'agent_model_ref', '').map(
      (r) => r.uuid
    ),
    ['a', 'b', 'e']
  )
  assert.deepEqual(
    selectableReplacementAssistants(rows, 'agent_model_ref', ' CODE ').map(
      (r) => r.uuid
    ),
    ['a']
  )
  assert.deepEqual(
    selectableReplacementAssistants(rows, 'agent_model_ref', 'docs').map(
      (r) => r.uuid
    ),
    ['b']
  )
  assert.deepEqual(
    selectableReplacementAssistants(rows, 'multimodal_model_ref', '').map(
      (r) => r.uuid
    ),
    ['a', 'b']
  )
})

test('select-all adds search matches while keeping selections outside the search', () => {
  assert.deepEqual(
    toggleReplacementSelection(['a'], [assistants[1], assistants[2]], true),
    ['a', 'b', 'c']
  )
  assert.deepEqual(
    toggleReplacementSelection(['a', 'b', 'c'], [assistants[1]], false),
    ['a', 'c']
  )
  assert.deepEqual(toggleReplacementSelection(['a'], [assistants[0]], true), [
    'a'
  ])
})

test('dropdown stays within the viewport and flips above when there is more room', () => {
  const below = replacementPickerPosition(
    { left: 100, top: 80, bottom: 120, width: 600 },
    { width: 1000, height: 800 },
    360
  )
  assert.deepEqual(below, { left: 100, top: 124, width: 600, maxHeight: 360 })
  const above = replacementPickerPosition(
    { left: 100, top: 650, bottom: 690, width: 600 },
    { width: 1000, height: 800 },
    360
  )
  assert.deepEqual(above, { left: 100, top: 286, width: 600, maxHeight: 360 })
  const mobile = replacementPickerPosition(
    { left: 16, top: 160, bottom: 200, width: 600 },
    { width: 390, height: 500 },
    360
  )
  assert.equal(mobile.width, 374)
  assert.equal(mobile.left, 8)
  assert.equal(mobile.top + mobile.maxHeight, 492)
})

test('assistant selection includes existing target assignments and default models', () => {
  assert.deepEqual(
    selectableReplacementAssistants(assistants, 'agent_model_ref').map(
      (row) => row.uuid
    ),
    ['a', 'b', 'c', 'e']
  )
})

test('target models must be active global LLMs with vision support for multimodal replacement', () => {
  const model = {
    scope: 'global',
    model_type: 'llm',
    is_active: true,
    config: {}
  }
  assert.equal(eligibleReplacementModel(model, 'agent_model_ref'), true)
  assert.equal(
    eligibleReplacementModel({ ...model, is_active: false }, 'agent_model_ref'),
    false
  )
  assert.equal(
    eligibleReplacementModel(
      { ...model, model_type: 'embedding' },
      'agent_model_ref'
    ),
    false
  )
  assert.equal(
    eligibleReplacementModel({ ...model, scope: 'user' }, 'agent_model_ref'),
    false
  )
  assert.equal(eligibleReplacementModel(model, 'multimodal_model_ref'), false)
  assert.equal(
    eligibleReplacementModel(
      { ...model, config: { supports_vision: true } },
      'multimodal_model_ref'
    ),
    true
  )
  assert.equal(
    eligibleReplacementModel(
      { ...model, vision_capability: 'supported' },
      'multimodal_model_ref'
    ),
    true
  )
})
