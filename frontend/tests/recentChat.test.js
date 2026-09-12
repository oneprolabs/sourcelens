import assert from 'node:assert/strict'
import test from 'node:test'
import {
  readRecentChat,
  saveRecentChat,
  pickRecentAssistant,
  pickRecentSession
} from '../src/utils/recentChat.js'

function memoryStorage() {
  const values = new Map()
  return {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, value),
    removeItem: (key) => values.delete(key)
  }
}

test('recent chat isolates accounts and persists only navigation IDs', () => {
  const storage = memoryStorage()
  saveRecentChat({ pk: 1 }, 'docs', 'session-a', storage)
  saveRecentChat({ pk: 2 }, 'code', 'session-b', storage)
  assert.deepEqual(readRecentChat({ pk: 1 }, storage), {
    assistantSlug: 'docs',
    sessionUuid: 'session-a'
  })
  assert.equal(readRecentChat({ pk: 2 }, storage).sessionUuid, 'session-b')
  assert.equal(readRecentChat(null, storage), null)
  saveRecentChat({ pk: 1 }, 'new-assistant', '', storage)
  assert.equal(readRecentChat({ pk: 1 }, storage).sessionUuid, '')
})

test('invalid or unavailable storage cannot interrupt chat', () => {
  const storage = memoryStorage()
  for (const value of ['broken json', '42', '[]', '{"assistantSlug":"docs"}']) {
    storage.setItem('sourcelens:last-chat:1', value)
    assert.equal(readRecentChat({ id: 1 }, storage), null)
  }
  const blocked = {
    getItem() {
      throw new Error('blocked')
    },
    setItem() {
      throw new Error('quota')
    }
  }
  assert.equal(readRecentChat({ id: 1 }, blocked), null)
  assert.doesNotThrow(() => saveRecentChat({ id: 1 }, 'docs', '', blocked))
})

test('restore only an active assistant returned by the authorized API', () => {
  const assistants = [
    { slug: 'first', status: 'active' },
    { slug: 'last', status: 'active' },
    { slug: 'archived', status: 'archived' }
  ]
  assert.equal(
    pickRecentAssistant(assistants, { assistantSlug: 'last' }).slug,
    'last'
  )
  for (const assistantSlug of ['deleted', 'archived']) {
    assert.equal(
      pickRecentAssistant(assistants, { assistantSlug }).slug,
      'first'
    )
  }
  assert.equal(pickRecentAssistant([], null), undefined)
})

test('restore sessions only within the selected assistant accessible list', () => {
  const sessions = [{ uuid: 'latest' }, { uuid: 'older' }]
  const recent = { assistantSlug: 'docs', sessionUuid: 'older' }
  assert.equal(pickRecentSession(sessions, recent, 'docs'), 'older')
  assert.equal(pickRecentSession(sessions, recent, 'code'), '')
  assert.equal(pickRecentSession([], recent, 'docs'), '')
})
