import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

import { computed, ref } from 'vue'
import { createI18n } from 'vue-i18n'

const componentSource = await readFile(
  new URL(
    '../src/components/settings/AgentIntegrationSettings.vue',
    import.meta.url
  ),
  'utf8'
)
const script = componentSource
  .match(/<script setup>([\s\S]*?)<\/script>/)[1]
  .replace(/^import .*$/gm, '')

for (const language of ['en', 'zh-CN', 'es']) {
  const messages = JSON.parse(
    await readFile(
      new URL(`../src/locales/${language}.json`, import.meta.url),
      'utf8'
    )
  )

  test(`${language}: setup prompts preserve permanent and finite validity through revocation`, async () => {
    const i18n = createI18n({
      legacy: false,
      locale: language,
      messages: { [language]: messages }
    })
    const requestedMonths = []
    let refreshes = 0
    const run = new Function(
      'computed',
      'ref',
      'useI18n',
      'useToast',
      'generateAgentToken',
      'extractResponseData',
      'extractErrorMessage',
      'publicBaseUrl',
      `${script}\nreturn { lifetimeMonths, tokenList, generatePrompt, prompt, validityText, handleRevoked, validityOptions };`
    )
    const component = run(
      computed,
      ref,
      () => i18n.global,
      () => ({ showError: assert.fail }),
      async (months) => {
        requestedMonths.push(months)
        return {
          id: `authorization-${months}`,
          access: 'sample-agent-credential',
          expires_at: months === 0 ? null : 1800000000
        }
      },
      (data) => data,
      (error) => error.message,
      () => 'https://example.test'
    )
    component.tokenList.value = {
      refresh: async () => {
        refreshes += 1
      }
    }
    assert.equal(component.lifetimeMonths.value, 1)
    assert.ok(
      component.validityOptions.value.some((option) => option.value === 0)
    )

    component.lifetimeMonths.value = 0
    await component.generatePrompt()
    assert.ok(component.prompt.value.includes('sample-agent-credential'))
    assert.ok(
      component.prompt.value.includes(
        messages.settings.modal.agentIntegrationNeverExpires
      )
    )
    assert.doesNotMatch(
      component.prompt.value,
      /Invalid Date|\{date\}|\{validity\}/
    )
    const permanentPrompt = component.prompt.value
    component.handleRevoked('another-authorization')
    assert.equal(component.prompt.value, permanentPrompt)
    component.handleRevoked('authorization-0')
    assert.equal(component.prompt.value, '')

    component.lifetimeMonths.value = 3
    await component.generatePrompt()
    assert.ok(component.prompt.value.includes(component.validityText.value))
    assert.ok(
      !component.prompt.value.includes(
        messages.settings.modal.agentIntegrationNeverExpires
      )
    )
    assert.deepEqual(requestedMonths, [0, 3])
    assert.equal(refreshes, 2)
  })
}

test('authorization list removes a revoked token only after the server confirms', async () => {
  const source = await readFile(
    new URL('../src/components/settings/AgentTokenList.vue', import.meta.url),
    'utf8'
  )
  const listScript = source
    .match(/<script setup>([\s\S]*?)<\/script>/)[1]
    .replace(/^import .*$/gm, '')
  const events = []
  const errors = []
  let fails = true
  const dependencies = {
    ref,
    onMounted() {},
    defineExpose() {},
    defineEmits:
      () =>
      (...args) =>
        events.push(args),
    useI18n: () => ({ t: (key) => key, locale: ref('en') }),
    useToast: () => ({ showError: (message) => errors.push(message) }),
    listAgentTokens: async () => ({ tokens: [{ id: 'one' }, { id: 'two' }] }),
    revokeAgentToken: async () => {
      if (fails) throw new Error('Request failed')
    },
    extractResponseData: (data) => data,
    extractErrorMessage: (error) => error.message
  }
  const run = new Function(
    ...Object.keys(dependencies),
    `${listScript}\nreturn { refresh, revoke, tokens };`
  )
  const list = run(...Object.values(dependencies))
  await list.refresh()
  await list.revoke('one')
  assert.deepEqual(
    list.tokens.value.map((token) => token.id),
    ['one', 'two']
  )
  assert.deepEqual(events, [])
  assert.deepEqual(errors, ['Request failed'])
  fails = false
  await list.revoke('one')
  assert.deepEqual(
    list.tokens.value.map((token) => token.id),
    ['two']
  )
  assert.deepEqual(events, [['revoked', 'one']])
})
