const LENSNODE_ERROR_KEYS = {
  ASSISTANT_ARCHIVED: 'assistantArchived',
  LENSNODE_REQUIRED: 'required',
  LENSNODE_OFFLINE: 'offline',
  LENSNODE_NOT_APPROVED: 'notApproved',
  LENSNODE_TOKEN_REVOKED: 'tokenRevoked',
  LENSNODE_DRAINING: 'draining',
  LENSNODE_TASK_UNAVAILABLE: 'taskUnavailable',
  LENSNODE_DIR_UNAVAILABLE: 'directoryUnavailable',
  LENSNODE_RESULT_TIMEOUT: 'resultTimeout',
  LENS_CHANNEL_LAYER_UNAVAILABLE: 'channelUnavailable',
  GENERAL_CHAT_SKILL_REQUIRED: 'skillRequired',
  SKILL_ENVIRONMENT_REQUIRED: 'skillEnvironmentRequired',
  MCP_ENVIRONMENT_REQUIRED: 'mcpEnvironmentRequired',
  DOCUMENT_ATTACHMENTS_UNSUPPORTED_BY_LENSNODE: 'documentUnsupported',
  DOCUMENT_ATTACHMENT_STATE_UNAVAILABLE: 'documentStateUnavailable',
  DOCUMENT_ATTACHMENT_UNAVAILABLE: 'documentUnavailable',
  ATTACHMENT_UNREADABLE: 'attachmentUnreadable'
}

export function lensNodeErrorMessage(code, t) {
  const normalized = String(code || '')
    .trim()
    .toUpperCase()
  const key = LENSNODE_ERROR_KEYS[normalized]
  return key ? t(`lensNodeErrors.${key}`) : ''
}
