const MODEL_ERROR_KEYS = {
  MODEL_TIMEOUT: 'errorModelTimeout',
  MODEL_QUOTA_EXCEEDED: 'errorModelQuotaExceeded',
  MODEL_AUTHENTICATION_ERROR: 'errorModelAuthentication',
  MODEL_PERMISSION_DENIED: 'errorModelPermissionDenied',
  MODEL_RATE_LIMITED: 'errorModelRateLimited',
  MODEL_INVALID_REQUEST: 'errorModelInvalidRequest',
  MODEL_NOT_FOUND: 'errorModelNotFound',
  MODEL_UNAVAILABLE: 'errorModelUnavailable',
  MODEL_STREAM_ERROR: 'errorModelStream'
}

export function modelErrorMessage(code, t) {
  const key = MODEL_ERROR_KEYS[String(code || '').toUpperCase()]
  return key ? t(`lens.chat.${key}`) : ''
}
