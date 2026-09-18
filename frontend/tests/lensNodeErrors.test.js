import assert from 'node:assert/strict'
import test from 'node:test'

import { lensNodeErrorMessage } from '../src/utils/lensNodeErrors.js'

test('maps an unavailable LensNode to the offline retry message', () => {
  const seen = []
  const message = lensNodeErrorMessage('LENSNODE_UNAVAILABLE', (key) => {
    seen.push(key)
    return key
  })

  assert.equal(message, 'lensNodeErrors.offline')
  assert.deepEqual(seen, ['lensNodeErrors.offline'])
})

test('names the empty datasource behind a target failure', () => {
  const seen = []
  const message = lensNodeErrorMessage(
    'DATASOURCE_UNAVAILABLE:ray.sun',
    (key, params) => {
      seen.push([key, params])
      return key
    }
  )

  assert.equal(message, 'lensNodeErrors.datasourceUnavailable')
  assert.deepEqual(seen, [
    ['lensNodeErrors.datasourceUnavailable', { name: 'ray.sun' }]
  ])
})

test('falls back for a raw LensNode datasource target failure', () => {
  const message = lensNodeErrorMessage(
    'DATASOURCE_TARGET_UNAVAILABLE:64fd100d-968f-47e9-9695-2ef65bc1f683',
    (key) => key
  )

  assert.equal(message, 'lensNodeErrors.datasourceUploadRequired')
})

test('falls back when the empty datasource has no resolvable name', () => {
  const message = lensNodeErrorMessage('DATASOURCE_UNAVAILABLE:', (key) => key)

  assert.equal(message, 'lensNodeErrors.datasourceUploadRequired')
})

test('maps a conversion guard to a translated message', () => {
  const message = lensNodeErrorMessage(
    'DATASOURCE_CONVERSION_TYPE_REQUIRED',
    (key) => key
  )

  assert.equal(message, 'lensNodeErrors.datasourceConversionTypeRequired')
})
