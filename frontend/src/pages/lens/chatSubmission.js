import { generateUUID } from '../../utils/uuid.js'

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
