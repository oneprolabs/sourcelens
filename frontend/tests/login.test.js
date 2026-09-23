import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

const source = (path) =>
  readFile(new URL(`../src/${path}`, import.meta.url), 'utf8')

test('password login submits an email credential', async () => {
  const [component, baseInput] = await Promise.all([
    source('components/auth/LoginForm.vue'),
    source('components/ui/BaseInput.vue')
  ])

  assert.match(component, /v-model="formData\.email"/)
  assert.match(component, /type="email"/)
  assert.match(component, /name="email"/)
  assert.match(component, /autocomplete="email"/)
  assert.match(component, /email: formData\.email/)
  assert.doesNotMatch(component, /username: formData\.username/)
  assert.match(baseInput, /:name="name"/)
  assert.match(baseInput, /name:\s*\{\s*type: String/)
})

test('password login is the default mode', async () => {
  const component = await source('components/auth/LoginForm.vue')

  assert.match(component, /const mode = ref\('password'\)/)
})

test('password login messages identify email in both locales', async () => {
  const [english, chinese] = await Promise.all([
    source('locales/en.json'),
    source('locales/zh-CN.json')
  ])
  const en = JSON.parse(english)
  const zh = JSON.parse(chinese)

  assert.equal(
    en.auth.loginError,
    'Incorrect email or password, please try again'
  )
  assert.equal(zh.auth.loginError, '邮箱或密码错误，请重试')
  assert.match(en.auth.loginFailedMessage, /email and password/)
  assert.match(zh.auth.loginFailedMessage, /邮箱和密码/)
})

test('email code verification submits the detected UI language', async () => {
  const component = await source('components/auth/EmailCodeLogin.vue')

  assert.match(
    component,
    /loginWithCode\(\{[\s\S]*language: locale\.value[\s\S]*\}\)/
  )
})

test('email code verification maps actionable errors in every locale', async () => {
  const [component, utility, english, chinese, spanish] = await Promise.all([
    source('components/auth/EmailCodeLogin.vue'),
    source('utils/verificationError.js'),
    source('locales/en.json'),
    source('locales/zh-CN.json'),
    source('locales/es.json')
  ])

  assert.match(component, /getVerificationErrorMessage\(error, t\)/)
  assert.match(utility, /data \|\| response/)

  const module = await import(
    new URL('../src/utils/verificationError.js', import.meta.url)
  )
  const translate = (key) => key
  for (const [code, key] of [
    ['INVALID', 'auth.codeLogin.invalidCode'],
    ['EXPIRED', 'auth.codeLogin.expiredCode'],
    ['TOO_MANY_ATTEMPTS', 'auth.codeLogin.tooManyAttempts']
  ]) {
    assert.equal(
      module.getVerificationErrorMessage(
        { response: { data: { error_code: code } } },
        translate
      ),
      key
    )
    assert.equal(
      module.getVerificationErrorMessage(
        { response: { data: { data: { error_code: code } } } },
        translate
      ),
      key
    )
  }
  assert.equal(
    module.getVerificationErrorMessage(
      { response: { data: { message: 'failed' } } },
      translate
    ),
    'auth.codeLogin.verifyFailed'
  )
  assert.equal(
    module.getVerificationErrorMessage(
      { response: { data: { data: { errors: { code: ['invalid'] } } } } },
      translate
    ),
    'auth.codeLogin.verifyFailed'
  )
  assert.equal(
    module.getVerificationErrorMessage(
      { response: { data: { detail: 'internal error' } } },
      translate
    ),
    'auth.codeLogin.verifyFailed'
  )

  for (const messages of [english, chinese, spanish].map(JSON.parse)) {
    assert.ok(messages.auth.codeLogin.invalidCode)
    assert.ok(messages.auth.codeLogin.expiredCode)
    assert.ok(messages.auth.codeLogin.tooManyAttempts)
  }
})
