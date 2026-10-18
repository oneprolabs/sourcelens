import assert from 'node:assert/strict'
import test from 'node:test'
import {
  alertRulePayload,
  alertApiError,
  eventsForSubject
} from '../src/admin/pages/Alerts/alertOptions.js'

test('running reminders exclude terminal failures and budget termination', () => {
  const catalog = {
    run: ['run_failed', 'token_exceeded', 'run_token_limit', 'run_slow']
  }
  assert.deepEqual(eventsForSubject(catalog, 'run', 'running'), [
    'token_exceeded',
    'run_slow'
  ])
})

test('changing a subject never submits incompatible scopes or thresholds', () => {
  const body = alertRulePayload({
    subject: 'sync',
    events: ['sync_failed'],
    assistants: ['private'],
    datasources: ['source'],
    lensnodes: [],
    subscriptions: [],
    token_threshold: 200,
    rounds_threshold: 5
  })
  assert.deepEqual(body.assistants, [])
  assert.deepEqual(body.datasources, ['source'])
  assert.equal(body.token_threshold, null)
})

test('destination identities survive edits and recipients are normalized', () => {
  const body = alertRulePayload({
    subject: 'run',
    events: ['run_failed'],
    subscriptions: [
      {
        uuid: 'destination',
        channel_uuid: 'email',
        email_recipients: 'a@example.com, b@example.com\na@example.com',
        users: [1],
        groups: [2]
      }
    ]
  })
  assert.equal(body.subscriptions[0].uuid, 'destination')
  assert.deepEqual(body.subscriptions[0].email_recipients, [
    'a@example.com',
    'b@example.com'
  ])
})

test('message topic subscriptions clear execution scopes and retain personal opt-in', () => {
  const body = alertRulePayload({
    subject: 'message',
    topic: 'release.ready',
    events: ['message_published'],
    assistants: ['assistant'],
    datasources: ['source'],
    lensnodes: ['node'],
    personal_subscription_enabled: true,
    personal_channel_uuid: 'email'
  })
  assert.equal(body.topic, 'release.ready')
  assert.deepEqual(
    [body.assistants, body.datasources, body.lensnodes],
    [[], [], []]
  )
  assert.equal(body.personal_subscription_enabled, true)
})

test('publication conflicts show the actionable server message', () => {
  const error = {
    response: {
      data: {
        code: 409,
        message: 'failed',
        data: { detail: 'Use a new business key for changed content.' }
      }
    }
  }
  assert.equal(
    alertApiError(error),
    'Use a new business key for changed content.'
  )
})

test('diagnosis budgets and managed destination languages survive an edit', () => {
  const body = alertRulePayload({
    subject: 'processing',
    events: ['processing_failed'],
    diagnosis_mode: 'diagnose_first',
    diagnosis_timeout_seconds: '30',
    diagnosis_max_tokens: '1024',
    subscriptions: [
      {
        language: 'es',
        channel_uuid: 'email',
        email_recipients: ['a@example.test']
      }
    ]
  })
  assert.equal(body.diagnosis_mode, 'diagnose_first')
  assert.equal(body.diagnosis_timeout_seconds, 30)
  assert.equal(body.diagnosis_max_tokens, 1024)
  assert.equal(body.subscriptions[0].language, 'es')
})

test('message and running rules always use direct notification', () => {
  for (const form of [
    { subject: 'message' },
    { subject: 'run', evaluation_mode: 'running' }
  ]) {
    assert.equal(
      alertRulePayload({
        ...form,
        events: [],
        diagnosis_mode: 'diagnose_first'
      }).diagnosis_mode,
      'direct'
    )
  }
})
