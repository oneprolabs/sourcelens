const LENSNODE_ERROR_KEYS = {
  ASSISTANT_ARCHIVED: 'assistantArchived',
  LENSNODE_REQUIRED: 'required',
  LENSNODE_UNAVAILABLE: 'offline',
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
  ATTACHMENT_UNREADABLE: 'attachmentUnreadable',
  DATASOURCE_CONVERSION_TYPE_REQUIRED: 'datasourceConversionTypeRequired',
  DATASOURCE_CONVERSION_NOT_SUPPORTED: 'datasourceConversionNotSupported'
}

const DATASOURCE_UNAVAILABLE_PREFIX = 'DATASOURCE_UNAVAILABLE'
const DATASOURCE_TARGET_UNAVAILABLE_PREFIX = 'DATASOURCE_TARGET_UNAVAILABLE'

export function lensNodeErrorMessage(code, t) {
  const raw = String(code || '').trim()
  const normalized = raw.toUpperCase()
  if (
    normalized === DATASOURCE_UNAVAILABLE_PREFIX ||
    normalized.startsWith(`${DATASOURCE_UNAVAILABLE_PREFIX}:`)
  ) {
    const name = raw
      .slice(DATASOURCE_UNAVAILABLE_PREFIX.length)
      .replace(/^:/, '')
    return name
      ? t('lensNodeErrors.datasourceUnavailable', { name })
      : t('lensNodeErrors.datasourceUploadRequired')
  }
  if (normalized.startsWith(DATASOURCE_TARGET_UNAVAILABLE_PREFIX)) {
    return t('lensNodeErrors.datasourceUploadRequired')
  }
  const key = LENSNODE_ERROR_KEYS[normalized]
  return key ? t(`lensNodeErrors.${key}`) : ''
}
