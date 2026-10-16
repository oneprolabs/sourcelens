import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import { getVerificationErrorMessage } from '../src/utils/verificationError.js'

async function failedPasswordLogin(responseData) {
  const source = await readFile(
    new URL('../src/components/auth/LoginForm.vue', import.meta.url),
    'utf8'
  )
  const script = source
    .match(/<script setup>([\s\S]*?)<\/script>/)[1]
    .replace(/^import .*$/gm, '')
  const run = new Function(
    'computed',
    'reactive',
    'ref',
    'useI18n',
    'useUserStore',
    'getPasswordManagementText',
    'defineEmits',
    'getVerificationErrorMessage',
    'console',
    `${script}\nreturn { handleLogin, formData, errorMessage, loading };`
  )
  const component = run(
    () => ({}),
    (value) => value,
    (value) => ({ value }),
    () => ({ t: (key) => key, locale: { value: 'en' } }),
    () => ({
      login: async () => {
        const error = new Error('Login failed')
        error.response = { data: responseData }
        throw error
      }
    }),
    () => ({}),
    () => () => {},
    getVerificationErrorMessage,
    { error() {} }
  )
  component.formData.email = 'test@example.com'
  component.formData.password = 'test-password'
  await component.handleLogin()
  assert.equal(component.loading.value, false)
  return component.errorMessage.value
}

test('password login identifies human verification failures in both response formats', async () => {
  for (const response of [
    { error_code: 'TURNSTILE_FAILED' },
    {
      code: 400,
      message: 'Human verification failed.',
      data: { error_code: 'TURNSTILE_FAILED' }
    }
  ]) {
    assert.equal(
      await failedPasswordLogin(response),
      'auth.codeLogin.turnstileRequired'
    )
  }
})

test('password login retains the credential error for incorrect email or password', async () => {
  assert.equal(
    await failedPasswordLogin({
      code: 400,
      data: {
        non_field_errors: ['Unable to log in with provided credentials.']
      }
    }),
    'auth.loginError'
  )
})
