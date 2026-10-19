import assert from 'node:assert/strict'
import test from 'node:test'
import {
  alertRulePayload,
  alertApiError,
  alertEventTitle,
  alertEventSource,
  eventsForSubject
} from '../src/admin/pages/Alerts/alertOptions.js'

test('notification titles identify business content and fall back to named resources for older records', () => {
  const t = (key) =>
    ({
      'alertManagement.unified.events.run_failed': '问答失败',
      'alertManagement.unified.events.sync_failed': '同步失败',
      'alertManagement.unified.events.message_published': '消息已发布'
    })[key]
  assert.equal(
    alertEventTitle(
      {
        subject: 'run',
        event_type: 'run_failed',
        detail: {
          question: '  请分析\n昨天的告警  ',
          assistant_name: '运维助手'
        }
      },
      t
    ),
    '请分析 昨天的告警'
  )
  assert.equal(
    alertEventTitle(
      {
        subject: 'message',
        event_type: 'message_published',
        detail: { title: '版本发布完成' }
      },
      t
    ),
    '版本发布完成'
  )
  assert.equal(
    alertEventTitle(
      {
        subject: 'sync',
        event_type: 'sync_failed',
        detail: { datasource_name: '工程知识库' }
      },
      t
    ),
    '工程知识库 · 同步失败'
  )
  assert.equal(
    alertEventTitle(
      {
        subject: 'run',
        event_type: 'run_failed',
        detail: { assistant_name: '运维助手' }
      },
      t
    ),
    '运维助手 · 问答失败'
  )
  assert.equal(alertEventTitle({ event_type: 'run_failed' }, t), '问答失败')
})

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

test('new exception subscriptions start with failure events and a visible channel selector', async () => {
  const { createAlertRuleForm } = await import(
    '../src/admin/pages/Alerts/alertOptions.js'
  )
  const form = createAlertRuleForm(null, 'zh-CN')
  assert.deepEqual(form.events, ['run_failed', 'run_partial', 'run_timeout'])
  assert.equal(form.personal_subscription_enabled, false)
  assert.equal(form.subscriptions.length, 1)
  assert.equal(form.subscriptions[0].channel_uuid, '')
  assert.equal(form.subscriptions[0].language, 'zh-CN')
})

test('simplified subscription edits preserve advanced settings, identities and languages', async () => {
  const { createAlertRuleForm } = await import(
    '../src/admin/pages/Alerts/alertOptions.js'
  )
  const existing = {
    uuid: 'existing-rule',
    subject: 'run',
    events: ['run_token_limit'],
    diagnosis_mode: 'notify_then_diagnose',
    personal_subscription_enabled: true,
    personal_channel_uuid: 'personal-mail',
    subscriptions: [
      {
        uuid: 'destination',
        channel_uuid: 'feishu',
        language: 'es',
        email_recipients: []
      }
    ]
  }
  const form = createAlertRuleForm(existing, 'zh-CN')
  form.events.push('run_failed')
  assert.deepEqual(existing.events, ['run_token_limit'])
  const payload = alertRulePayload(form)
  assert.equal(payload.diagnosis_mode, 'notify_then_diagnose')
  assert.equal(payload.personal_subscription_enabled, true)
  assert.equal(payload.subscriptions[0].uuid, 'destination')
  assert.equal(payload.subscriptions[0].language, 'es')
})

test('selecting search results keeps hidden selections and avoids duplicates', async () => {
  const { setAlertSelection } = await import(
    '../src/admin/pages/Alerts/alertOptions.js'
  )
  assert.deepEqual(
    setAlertSelection(['hidden', 'visible'], ['visible', 'second'], true),
    ['hidden', 'visible', 'second']
  )
  assert.deepEqual(
    setAlertSelection(
      ['hidden', 'visible', 'second'],
      ['visible', 'second'],
      false
    ),
    ['hidden']
  )
  assert.deepEqual(setAlertSelection(['hidden'], [], true), ['hidden'])
})

test('notification sources identify the initiating user and preserve publisher and legacy fallbacks', () => {
  const t = (key) =>
    ({
      'alertManagement.events.systemSource': '系统任务',
      'alertManagement.events.unknownUser': '未知用户'
    })[key]
  assert.equal(
    alertEventSource(
      {
        subject: 'run',
        detail: {
          user_display_name: '张三',
          username: 'zhangsan',
          assistant_name: '运维助手'
        }
      },
      t
    ),
    '张三'
  )
  assert.equal(
    alertEventSource({ subject: 'run', detail: { username: 'alice' } }, t),
    'alice'
  )
  assert.equal(
    alertEventSource(
      { subject: 'message', detail: { publisher_name: 'operator' } },
      t
    ),
    'operator'
  )
  for (const subject of ['sync', 'processing']) {
    assert.equal(alertEventSource({ subject }, t), '系统任务')
  }
  assert.equal(
    alertEventSource(
      { subject: 'run', detail: { assistant_name: '运维助手' } },
      t
    ),
    '未知用户'
  )
})
