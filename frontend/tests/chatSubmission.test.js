import assert from 'node:assert/strict'
import test from 'node:test'

import {
  prepareRunSubmission,
  updateSessionSubmissionSet
} from '../src/pages/lens/chatSubmission.js'

test('isolates submission locks by session', () => {
  const first = updateSessionSubmissionSet(new Set(), 'session-1', true)
  const both = updateSessionSubmissionSet(first, 'session-2', true)
  const secondOnly = updateSessionSubmissionSet(both, 'session-1', false)

  assert.deepEqual([...both], ['session-1', 'session-2'])
  assert.deepEqual([...secondOnly], ['session-2'])
  assert.equal(secondOnly.has('session-1'), false)
  assert.equal(secondOnly.has('session-2'), true)
})

test('reuses the idempotency key for the same unknown-result replay', () => {
  const first = prepareRunSubmission({
    sessionUuid: 'session-1',
    question: 'Check status',
    attachmentUuids: ['attachment-1'],
    randomUUID: () => 'submission-1'
  })

  const replay = prepareRunSubmission({
    sessionUuid: 'session-1',
    question: 'Check status',
    attachmentUuids: ['attachment-1'],
    pendingSubmission: first.submission,
    randomUUID: () => 'must-not-be-used'
  })

  assert.equal(replay.payload.idempotency_key, 'submission-1')
  assert.deepEqual(replay.submission, first.submission)
})

test('explicit Retry creates a new key and carries the source Run', () => {
  const prepared = prepareRunSubmission({
    sessionUuid: 'session-1',
    question: 'Check status',
    retryDraft: {
      question: 'Check status',
      runUuid: 'run-1'
    },
    randomUUID: () => 'retry-submission'
  })

  assert.equal(prepared.payload.idempotency_key, 'retry-submission')
  assert.equal(prepared.payload.retry_of_run_uuid, 'run-1')
})

test('manual identical submission remains a distinct ordinary turn', () => {
  const first = prepareRunSubmission({
    sessionUuid: 'session-1',
    question: 'Check status',
    randomUUID: () => 'submission-1'
  })
  const second = prepareRunSubmission({
    sessionUuid: 'session-1',
    question: 'Check status',
    randomUUID: () => 'submission-2'
  })

  const firstKey = first.payload.idempotency_key
  const secondKey = second.payload.idempotency_key
  assert.notEqual(firstKey, secondKey)
  assert.equal('retry_of_run_uuid' in second.payload, false)
})

test('editing a Retry draft turns it into an ordinary new turn', () => {
  const prepared = prepareRunSubmission({
    sessionUuid: 'session-1',
    question: 'Check a different status',
    retryDraft: {
      question: 'Check status',
      runUuid: 'run-1'
    },
    randomUUID: () => 'submission-2'
  })

  assert.equal('retry_of_run_uuid' in prepared.payload, false)
})

test('sends every explicitly mentioned assistant in one submission', () => {
  const prepared = prepareRunSubmission({
    sessionUuid: 'session-1',
    question: '@First @Second Compare the findings',
    routingAssistantUuids: ['assistant-1', 'assistant-2'],
    randomUUID: () => 'multi-assistant-submission'
  })

  assert.deepEqual(prepared.payload.routing_assistant_uuids, [
    'assistant-1',
    'assistant-2'
  ])
})

test('only sends a reasoning override when explicitly selected', () => {
  const defaults = { sessionUuid: 'session-1', question: 'Check status' }
  assert.equal('agent_rounds' in prepareRunSubmission(defaults).payload, false)
  for (const agentRounds of ['flash', 'fast', 'balanced', 'deep', 'max']) {
    const prepared = prepareRunSubmission({ ...defaults, agentRounds })
    assert.equal(prepared.payload.agent_rounds, agentRounds)
  }
})

test('replays the same depth but gives a changed depth a new key', () => {
  const defaults = {
    sessionUuid: 'session-1',
    question: 'Check status',
    agentRounds: 'flash'
  }
  const first = prepareRunSubmission({
    ...defaults,
    randomUUID: () => 'first'
  })
  const replay = prepareRunSubmission({
    ...defaults,
    pendingSubmission: first.submission,
    randomUUID: () => 'replay'
  })
  assert.equal(replay.payload.idempotency_key, 'first')
  for (const agentRounds of ['', 'max']) {
    const changed = prepareRunSubmission({
      ...defaults,
      agentRounds,
      pendingSubmission: first.submission,
      randomUUID: () => 'changed'
    })
    assert.equal(changed.payload.idempotency_key, 'changed')
  }
})

test('a Retry accepts the newly selected reasoning depth', () => {
  const prepared = prepareRunSubmission({
    sessionUuid: 'session-1',
    question: 'Check status',
    agentRounds: 'max',
    retryDraft: { question: 'Check status', runUuid: 'run-1' }
  })
  assert.equal(prepared.payload.agent_rounds, 'max')
  assert.equal(prepared.payload.retry_of_run_uuid, 'run-1')
})
