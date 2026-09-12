// crypto.randomUUID() throws "is not a function" outside secure contexts
// (plain HTTP on a non-localhost host, e.g. LAN IP deployments without
// TLS), because browsers only expose it on https:// or localhost origins.
// crypto.getRandomValues() has no such restriction, so build a v4 UUID
// from it manually when randomUUID is unavailable.
function generateUUID() {
  if (globalThis.crypto?.randomUUID) {
    return globalThis.crypto.randomUUID()
  }
  const bytes = globalThis.crypto.getRandomValues(new Uint8Array(16))
  bytes[6] = (bytes[6] & 0x0f) | 0x40
  bytes[8] = (bytes[8] & 0x3f) | 0x80
  const hex = Array.from(bytes, (b) => b.toString(16).padStart(2, '0'))
  return [
    hex.slice(0, 4).join(''),
    hex.slice(4, 6).join(''),
    hex.slice(6, 8).join(''),
    hex.slice(8, 10).join(''),
    hex.slice(10, 16).join('')
  ].join('-')
}

function sameUuids(left, right) {
  const leftValues = left || []
  const rightValues = right || []
  return (
    leftValues.length === rightValues.length &&
    leftValues.every((value, index) => value === rightValues[index])
  )
}

function isSameSubmission(pending, candidate) {
  return Boolean(
    pending &&
      pending.sessionUuid === candidate.sessionUuid &&
      pending.question === candidate.question &&
      pending.agentRounds === candidate.agentRounds &&
      pending.retryOfRunUuid === candidate.retryOfRunUuid &&
      sameUuids(
        pending.routingAssistantUuids,
        candidate.routingAssistantUuids
      ) &&
      sameUuids(pending.attachmentUuids, candidate.attachmentUuids)
  )
}

export function updateSessionSubmissionSet(current, sessionUuid, submitting) {
  if (!sessionUuid) return new Set(current)
  const next = new Set(current)
  if (submitting) next.add(sessionUuid)
  else next.delete(sessionUuid)
  return next
}

export function prepareRunSubmission({
  sessionUuid,
  question,
  attachmentUuids = [],
  routingAssistantUuids = [],
  agentRounds = '',
  retryDraft = null,
  pendingSubmission = null,
  randomUUID = generateUUID
}) {
  const retryOfRunUuid =
    retryDraft?.question === question ? retryDraft.runUuid || '' : ''
  const candidate = {
    sessionUuid,
    question,
    attachmentUuids: [...attachmentUuids],
    routingAssistantUuids: [...routingAssistantUuids],
    agentRounds,
    retryOfRunUuid
  }
  const idempotencyKey = isSameSubmission(pendingSubmission, candidate)
    ? pendingSubmission.idempotencyKey
    : randomUUID()
  const submission = { ...candidate, idempotencyKey }
  const payload = {
    question,
    run_inline: false,
    enqueue: true,
    attachment_uuids: [...attachmentUuids],
    idempotency_key: idempotencyKey
  }
  if (retryOfRunUuid) {
    payload.retry_of_run_uuid = retryOfRunUuid
  }
  if (routingAssistantUuids.length) {
    payload.routing_assistant_uuids = [...routingAssistantUuids]
  }
  if (agentRounds) payload.agent_rounds = agentRounds
  return { payload, submission }
}
