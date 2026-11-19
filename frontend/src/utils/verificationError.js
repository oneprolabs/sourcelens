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

  if (errorCode === 'PASSWORD_LOGIN_LOCKED') {
    const date = new Date(details.locked_until)
    return Number.isNaN(date.getTime())
      ? translate('auth.passwordLock.locked')
      : translate('auth.passwordLock.lockedUntil', {
          time: date.toLocaleTimeString([], {
            hour: '2-digit',
            minute: '2-digit'
          })
        })
  }
  if (errorCode === 'PASSWORD_LOGIN_INVALID') {
    const count = Number(details.remaining_attempts)
    if (Number.isInteger(count) && count >= 1 && count <= 4)
      return translate('auth.passwordLock.remaining', { count })
  }

  if (ERROR_TRANSLATIONS[errorCode]) {
    return translate(ERROR_TRANSLATIONS[errorCode])
  }

  return translate(fallbackKey)
}
