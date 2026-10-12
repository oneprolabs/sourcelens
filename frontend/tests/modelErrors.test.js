import assert from 'node:assert/strict'
import test from 'node:test'
import { modelErrorMessage } from '../src/utils/modelErrors.js'

test('model failures explain the concrete cause', () => {
  const reasons = {
    MODEL_TIMEOUT: 'errorModelTimeout',
    MODEL_QUOTA_EXCEEDED: 'errorModelQuotaExceeded',
    MODEL_AUTHENTICATION_ERROR: 'errorModelAuthentication',
    MODEL_PERMISSION_DENIED: 'errorModelPermissionDenied',
    MODEL_RATE_LIMITED: 'errorModelRateLimited',
    MODEL_INVALID_REQUEST: 'errorModelInvalidRequest',
    MODEL_NOT_FOUND: 'errorModelNotFound',
    MODEL_UNAVAILABLE: 'errorModelUnavailable',
    MODEL_STREAM_ERROR: 'errorModelStream'
  }
  for (const [code, key] of Object.entries(reasons)) {
    assert.equal(
      modelErrorMessage(code, (key) => key),
      `lens.chat.${key}`
    )
  }
})

test('unrelated errors keep their existing handling', () => {
  assert.equal(
    modelErrorMessage('RUN_TIMEOUT', (key) => key),
    ''
  )
  assert.equal(
    modelErrorMessage('VISION_PROVIDER_QUOTA_EXCEEDED', (key) => key),
    ''
  )
  assert.equal(
    modelErrorMessage('', (key) => key),
    ''
  )
})
