const runningEvents = new Set(['token_exceeded', 'rounds_exceeded', 'run_slow'])

export function eventsForSubject(catalog, subject, mode) {
  return (catalog[subject] || []).filter(
    (event) => mode !== 'running' || runningEvents.has(event)
  )
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
