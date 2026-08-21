import assert from 'node:assert/strict'
import test from 'node:test'

import { lensNodeErrorMessage } from '../src/utils/lensNodeErrors.js'

test('maps LensNode dispatch error codes to localized messages', () => {
  const t = (key) => key

  const cases = {
    LENSNODE_RESULT_TIMEOUT: 'resultTimeout',
    LENSNODE_OFFLINE: 'offline',
    GENERAL_CHAT_SKILL_REQUIRED: 'skillRequired',
    MCP_ENVIRONMENT_REQUIRED: 'mcpEnvironmentRequired',
    ATTACHMENT_UNREADABLE: 'attachmentUnreadable'
  }

  Object.entries(cases).forEach(([code, key]) => {
    assert.equal(lensNodeErrorMessage(code, t), `lensNodeErrors.${key}`)
  })
})

test('does not translate unknown errors', () => {
  assert.equal(
    lensNodeErrorMessage('NEW_LENSNODE_ERROR', () => 'message'),
    ''
  )
})
