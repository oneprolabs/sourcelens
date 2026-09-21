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
  DATASOURCE_CONVERSION_NOT_SUPPORTED: 'datasourceConversionNotSupported',
  LENS_SOURCE_SYNC_TIMEOUT: 'datasourceSyncTimeout',
  LENS_SOURCE_CREDENTIAL_INVALID: 'datasourceCredentialInvalid',
  LENS_SOURCE_CONFIG_INVALID: 'datasourceConfigInvalid',
  LENS_SOURCE_TARGET_PATH_INVALID: 'datasourceTargetPathInvalid',
  LENS_SOURCE_TARGET_PATH_REQUIRED: 'datasourceTargetPathRequired',
  DATASOURCE_SYNC_ORPHANED: 'datasourceSyncOrphaned',
  DATASOURCE_QUEUE_TIMEOUT: 'datasourceQueueTimeout',
  DATASOURCE_NOT_FOUND: 'datasourceNotFound',
  DATASOURCE_DISABLED: 'datasourceDisabled',
  DATASOURCE_UPLOAD_LINK_INVALID: 'datasourceUploadLinkInvalid',
  DATASOURCE_UPLOAD_ORPHANED: 'datasourceUploadOrphaned',
  DATASOURCE_UPLOAD_TIMEOUT: 'datasourceUploadTimeout',
  DATASOURCE_UPLOAD_FAILED: 'datasourceUploadFailed',
  DATASOURCE_UPLOAD_CONTENT_INVALID: 'datasourceUploadContentInvalid',
  DATASOURCE_UPLOAD_TOO_LARGE: 'datasourceUploadTooLarge',
  DATASOURCE_UPLOAD_FILE_LIMIT: 'datasourceUploadFileLimit',
  DATASOURCE_UPLOAD_SIZE_LIMIT: 'datasourceUploadSizeLimit',
  DATASOURCE_UPLOAD_FILE_EXISTS: 'datasourceUploadFileExists',
  DATASOURCE_UPLOAD_FILE_REQUIRED: 'datasourceUploadRequired',
  DATASOURCE_UPLOAD_ARCHIVE_INVALID: 'datasourceUploadArchiveInvalid',
  DATASOURCE_UPLOAD_ARCHIVE_PATH_INVALID: 'datasourceUploadArchivePathInvalid',
  DATASOURCE_UPLOAD_NOT_SUPPORTED: 'datasourceUploadNotSupported',
  DATASOURCE_CONVERSION_ORPHANED: 'datasourceConversionOrphaned',
  DATASOURCE_CONVERSION_FAILED: 'datasourceConversionFailed',
  DATASOURCE_CONVERSION_TIMEOUT: 'datasourceConversionTimeout',
  CONNECTION_IN_USE: 'connectionInUse'
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
