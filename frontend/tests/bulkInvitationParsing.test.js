import assert from 'node:assert/strict'
import test from 'node:test'
import {
  parseInvitationText,
  parseInvitationCsv,
  invitationCsvTemplate,
  classifyInvitationRows
} from '../src/utils/invitations.js'

test('pasted recipients normalize separators and retain duplicate rows for explicit feedback', () => {
  const rows = parseInvitationText(
    ' First@Example.com, second@example.com\nfirst@example.com；bad@local ',
    { role: 'tenant_admin', group_ids: [7] }
  )
  assert.deepEqual(
    rows.map((row) => row.email),
    [
      'first@example.com',
      'second@example.com',
      'first@example.com',
      'bad@local'
    ]
  )
  assert.deepEqual(rows[0].group_ids, [7])
  const classified = classifyInvitationRows(rows)
  assert.deepEqual(
    classified.map((row) => row.status),
    ['valid', 'valid', 'duplicate', 'invalid']
  )
})

test('CSV honors quoted values, BOM, localized headers and per-recipient role/group overrides', () => {
  const groups = [
    { id: 7, name: '研发,平台' },
    { id: 8, name: 'Operations' }
  ]
  const rows = parseInvitationCsv(
    '\uFEFF邮箱,角色,用户组\r\none@example.com,租户管理员,"研发,平台|Operations"\r\ntwo@example.com,,\r\n',
    groups,
    { role: 'member', group_ids: [8] }
  )
  assert.deepEqual(rows, [
    { email: 'one@example.com', role: 'tenant_admin', group_ids: [7, 8] },
    { email: 'two@example.com', role: 'member', group_ids: [8] }
  ])
})

test('CSV keeps bad configuration as row feedback without dropping valid recipients', () => {
  const rows = parseInvitationCsv(
    'email,role,groups\none@example.com,super_admin,Private\ntwo@example.com,member,Engineering',
    [{ id: 7, name: 'Engineering' }]
  )
  assert.equal(rows.length, 2)
  assert.equal(rows[0].role, 'super_admin')
  assert.deepEqual(rows[0].group_ids, [0])
  assert.deepEqual(rows[1], {
    email: 'two@example.com',
    role: 'member',
    group_ids: [7]
  })
})

test('CSV rejects malformed quotes, unsupported headers, extra columns and excessive rows', () => {
  for (const csv of [
    'email,role,groups\n"unclosed',
    'name,email\nName,a@example.com',
    'email,role,groups\na@example.com,member,,extra',
    'email,role,groups\n' +
      Array.from({ length: 101 }, (_, i) => `n${i}@example.com,member,`).join(
        '\n'
      )
  ]) {
    assert.throws(() => parseInvitationCsv(csv, []))
  }
})

test('CSV template round-trips with portable role identifiers and no required private group names', () => {
  const rows = parseInvitationCsv(invitationCsvTemplate(), [])
  assert.equal(rows.length, 2)
  assert.equal(rows[0].role, 'member')
  assert.equal(rows[1].role, 'tenant_admin')
})

test('rendered invitation outcomes escape addresses and keep partial failures visible', async () => {
  const { readFile } = await import('node:fs/promises')
  const vm = await import('node:vm')
  const vue = await import('vue')
  const { parse, compileScript } = await import('@vue/compiler-sfc')
  const { renderToString } = await import('@vue/server-renderer')
  const source = await readFile(
    new URL(
      '../src/admin/pages/Management/TenantInvitationDrawer.vue',
      import.meta.url
    ),
    'utf8'
  )
  let compiled = compileScript(parse(source).descriptor, {
    id: 'bulk-invite',
    inlineTemplate: true
  }).content
  const context = vm.createContext({
    ...vue,
    parseInvitationText,
    parseInvitationCsv,
    classifyInvitationRows,
    invitationCsvTemplate,
    setTimeout: () => 1,
    clearTimeout() {},
    getTenantGroups: async () => ({ results: [] }),
    useI18n: () => ({ t: (key) => key })
  })
  compiled = compiled
    .replace(/import \{([^}]+)\} from "vue"/g, (_, names) => {
      for (const item of names.split(',')) {
        const [name, alias] = item.trim().split(/\s+as\s+/)
        context[alias || name] = vue[name]
      }
      return ''
    })
    .replace(/^import[^]*? from ['"][^'"]+['"];?\n/gm, '')
  const wrapper = {
    render() {
      return vue.h('div', [
        ...(this.$slots.default?.() || []),
        ...(this.$slots.footer?.() || [])
      ])
    }
  }
  const button = {
    props: ['disabled', 'loading'],
    render() {
      return vue.h(
        'button',
        { disabled: this.disabled || this.loading },
        this.$slots.default?.()
      )
    }
  }
  Object.assign(context, {
    BaseDrawer: wrapper,
    BaseButton: button,
    BaseSelect: wrapper
  })
  compiled = compiled
    .replace('export default', 'globalThis.component =')
    .replace(
      'const result = ref(null)',
      `const result = ref({ sent: 1, skipped: 1, failed: 1, items: [
      { index: 0, email: '<safe>@example.com', status: 'sent', reason: '' },
      { index: 1, email: 'existing@example.com', status: 'skipped', reason: 'email_unavailable' },
      { index: 2, email: 'failed@example.com', status: 'failed', reason: 'delivery_failed' }
    ] })`
    )
    .replace('result.value = null', '')
  vm.runInContext(compiled, context)
  const markup = await renderToString(
    vue.createSSRApp(context.component, { show: true })
  )
  assert.match(markup, /&lt;safe&gt;@example.com/)
  assert.match(markup, /existing@example.com/)
  assert.match(markup, /invitations.bulk.reasons.email_unavailable/)
  assert.match(markup, /invitations.bulk.reasons.delivery_failed/)
  assert.match(markup, /invitations.bulk.recovery/)
  assert.doesNotMatch(
    markup,
    /invitations.bulk.sendCount|invitations.bulk.review</
  )
})
