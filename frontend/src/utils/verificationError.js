const ERROR_TRANSLATIONS = {
  INVALID: 'auth.codeLogin.invalidCode',
  EXPIRED: 'auth.codeLogin.expiredCode',
  TOO_MANY_ATTEMPTS: 'auth.codeLogin.tooManyAttempts',
  TURNSTILE_FAILED: 'auth.codeLogin.turnstileRequired'
}

export const getVerificationErrorMessage = (
  error,
  translate,
  fallbackKey = 'auth.codeLogin.verifyFailed'
) => {
  const response = error?.response?.data || {}
  const details = response?.data || response
  const errorCode = details?.error_code || response?.error_code

  if (ERROR_TRANSLATIONS[errorCode]) {
    return translate(ERROR_TRANSLATIONS[errorCode])
  }

  return translate(fallbackKey)
}
