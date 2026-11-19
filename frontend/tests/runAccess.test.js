import assert from 'node:assert/strict'
import test from 'node:test'

import { runStreamHeaders } from '../src/utils/runAccess.js'

test('native streams carry the same web credentials and delegation as REST', () => {
  const headers = runStreamHeaders(
    { getItem: () => 'access-token' },
    { getItem: () => JSON.stringify({ delegation_id: 'delegation-a' }) }
  )
  assert.deepEqual(headers, {
    Accept: 'text/event-stream',
    Authorization: 'Bearer access-token',
    'X-Delegation-ID': 'delegation-a'
  })
})

test('corrupt or absent delegation never becomes a request header', () => {
  for (const value of ['{', 'null', '{}']) {
    assert.deepEqual(
      runStreamHeaders({ getItem: () => null }, { getItem: () => value }),
      { Accept: 'text/event-stream' }
    )
  }
})
