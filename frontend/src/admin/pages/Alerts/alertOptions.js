const runningEvents = new Set(['token_exceeded', 'rounds_exceeded', 'run_slow'])

export function alertEventTitle(event, t) {
  const detail = event.detail || {}
  const normalize = (value) =>
    typeof value === 'string' ? value.replace(/\s+/g, ' ').trim() : ''
  const title = normalize(
    event.subject === 'run'
      ? detail.question
      : event.subject === 'message'
        ? detail.title
        : ''
  )
  if (title) return title
  const eventLabel = t(`alertManagement.unified.events.${event.event_type}`)
  const resource = normalize(
    detail.assistant_name ||
      detail.datasource_name ||
      detail.topic ||
      event.rule_name
  )
  return resource ? `${resource} · ${eventLabel}` : eventLabel
}

export function alertEventSource(event, t) {
  const detail = event.detail || {}
  return (
    detail.user_display_name ||
    detail.username ||
    detail.publisher_name ||
    t(
      ['sync', 'processing'].includes(event.subject)
        ? 'alertManagement.events.systemSource'
        : 'alertManagement.events.unknownUser'
    )
  )
}

export function eventsForSubject(catalog, subject, mode) {
  return (catalog[subject] || []).filter(
    (event) => mode !== 'running' || runningEvents.has(event)
  )
}

export function createAlertDestination(locale = 'en-US') {
  return {
    name: '',
    enabled: true,
    language: locale.startsWith('zh')
      ? 'zh-CN'
      : locale.startsWith('es')
        ? 'es'
        : 'en-US',
    channel_uuid: '',
    email_recipients: '',
    users: [],
    groups: []
  }
}

export function createAlertRuleForm(value, locale = 'en-US') {
  const existing = JSON.parse(JSON.stringify(value || {}))
  return {
    name: '',
    subject: 'run',
    topic: '',
    events: ['run_failed', 'run_partial', 'run_timeout'],
    enabled: true,
    evaluation_mode: 'terminal',
    diagnosis_mode: 'direct',
    diagnosis_timeout_seconds: 60,
    diagnosis_max_tokens: 2048,
    assistants: [],
    datasources: [],
    lensnodes: [],
    cooldown_seconds: 0,
    personal_subscription_enabled: false,
    personal_channel_uuid: '',
    ...existing,
    subscriptions: (
      existing.subscriptions || (value ? [] : [createAlertDestination(locale)])
    ).map((item) => ({
      ...item,
      email_recipients: Array.isArray(item.email_recipients)
        ? item.email_recipients.join('\n')
        : item.email_recipients || ''
    }))
  }
}

export function alertRulePayload(form) {
  const events = [...form.events]
  return {
    name: (form.name || '').trim(),
    subject: form.subject,
    topic: form.subject === 'message' ? (form.topic || '').trim() : '',
    enabled: form.enabled ?? true,
    events,
    evaluation_mode: form.evaluation_mode || 'terminal',
    diagnosis_mode:
      form.subject === 'message' || form.evaluation_mode === 'running'
        ? 'direct'
        : form.diagnosis_mode || 'direct',
    diagnosis_timeout_seconds: Number(form.diagnosis_timeout_seconds ?? 60),
    diagnosis_max_tokens: Number(form.diagnosis_max_tokens ?? 2048),
    token_threshold: events.includes('token_exceeded')
      ? Number(form.token_threshold)
      : null,
    rounds_threshold: events.includes('rounds_exceeded')
      ? Number(form.rounds_threshold)
      : null,
    duration_threshold: events.includes('run_slow')
      ? Number(form.duration_threshold)
      : null,
    cooldown_seconds: Number(form.cooldown_seconds || 0),
    assistants: form.subject === 'run' ? form.assistants || [] : [],
    datasources: ['sync', 'processing'].includes(form.subject)
      ? form.datasources || []
      : [],
    lensnodes: form.subject === 'message' ? [] : form.lensnodes || [],
    personal_subscription_enabled:
      ['run', 'message'].includes(form.subject) &&
      !!form.personal_subscription_enabled,
    personal_channel_uuid: form.personal_channel_uuid || null,
    channel_uuid: null,
    email_recipients: [],
    subscriptions: (form.subscriptions || []).map((item) => ({
      ...(item.uuid ? { uuid: item.uuid } : {}),
      name: item.name || '',
      language: item.language || 'en-US',
      enabled: item.enabled ?? true,
      channel_uuid: item.channel_uuid || null,
      email_recipients: [
        ...new Set(
          (Array.isArray(item.email_recipients)
            ? item.email_recipients
            : (item.email_recipients || '').split(/[\n,]/)
          )
            .map((value) => value.trim())
            .filter(Boolean)
        )
      ],
      users: item.users || [],
      groups: item.groups || []
    }))
  }
}

export function alertApiError(error) {
  const response = error?.response?.data
  const data = response?.data ?? response
  if (typeof data?.detail === 'string') return data.detail
  if (data && typeof data === 'object') {
    const messages = Object.values(data)
      .flat()
      .map((value) =>
        typeof value === 'string' ? value : JSON.stringify(value)
      )
      .filter(Boolean)
    if (messages.length) return messages.join('; ')
  }
  return response?.message || error?.message || 'Request failed'
}

export function setAlertSelection(selected, values, checked) {
  const next = new Set(selected)
  for (const value of values) {
    if (checked) next.add(value)
    else next.delete(value)
  }
  return [...next]
}
