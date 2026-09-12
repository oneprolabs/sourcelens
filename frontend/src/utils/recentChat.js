// Store navigation hints only; authorized API responses remain authoritative.
function storageKey(user) {
  const id = user?.uuid ?? user?.id ?? user?.pk
  return id === undefined || id === null ? null : `sourcelens:last-chat:${id}`
}

export function readRecentChat(user, storage) {
  try {
    storage ||= window.localStorage
    const key = storageKey(user)
    if (!key) return null
    const value = JSON.parse(storage.getItem(key) || 'null')
    if (
      !value ||
      typeof value.assistantSlug !== 'string' ||
      !value.assistantSlug ||
      typeof value.sessionUuid !== 'string'
    ) {
      storage.removeItem(key)
      return null
    }
    return {
      assistantSlug: value.assistantSlug,
      sessionUuid: value.sessionUuid
    }
  } catch {
    return null
  }
}

export function saveRecentChat(user, assistantSlug, sessionUuid = '', storage) {
  try {
    storage ||= window.localStorage
    const key = storageKey(user)
    if (!key || !assistantSlug) return
    storage.setItem(key, JSON.stringify({ assistantSlug, sessionUuid }))
  } catch {
    // Disabled or full browser storage must not interrupt chat.
  }
}

export function pickRecentAssistant(assistants, recent) {
  const active = assistants.filter(
    (item) => item.slug && item.status === 'active'
  )
  return active.find((item) => item.slug === recent?.assistantSlug) || active[0]
}

export function pickRecentSession(sessions, recent, assistantSlug) {
  if (recent?.assistantSlug !== assistantSlug) return ''
  return sessions.find((item) => item.uuid === recent.sessionUuid)?.uuid || ''
}
