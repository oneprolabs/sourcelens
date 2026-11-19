import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import vm from 'node:vm'
import * as vue from 'vue'

async function component(file, values = {}) {
  const source = await readFile(
    new URL(`../src/admin/pages/Management/${file}.vue`, import.meta.url),
    'utf8'
  )
  const script = source
    .split('<script setup>')[1]
    .split('</script>')[0]
    .replace(/^import[^]*? from ['"][^'"]+['"]\n/gm, '')
  const props = vue.reactive({ action: '', targets: [], groups: [] })
  const events = []
  const context = vm.createContext({
    ...vue,
    Eye: {},
    Play: {},
    Ban: {},
    KeyRound: {},
    Trash2: {},
    onMounted() {},
    onBeforeUnmount() {},
    clearTimeout,
    setTimeout,
    defineProps: () => props,
    defineEmits:
      () =>
      (...args) =>
        events.push(args),
    useI18n: () => ({ t: (key) => key }),
    useToast: () => ({ showSuccess() {}, showError() {} }),
    useUserStore: () => ({ user: { id: 99 }, setUser() {} }),
    useRouter: () => ({ replace() {} }),
    ...values
  })
  vm.runInContext(script, context)
  return { props, events, run: (code) => vm.runInContext(code, context) }
}

const plain = (value) => JSON.parse(JSON.stringify(value))

const deletionReview = (version = 'v1') => ({
  members: [
    {
      id: 4,
      version,
      email: 'target@example.test',
      impact: {
        assistants: 1,
        shares: 2,
        sessions: 3,
        runs: 4,
        api_keys: 1,
        models: 0
      },
      assistants: [{ uuid: 'assistant', name: 'Retained assistant' }],
      skip_reason: ''
    }
  ],
  confirmation: `proof-${version}`
})

test('member deletion requires exact confirmation and sends one fixed reviewed payload', async () => {
  const requests = []
  let release
  const { props, events, run } = await component('MemberDeleteDialog', {
    deleteTenantMembers: async (payload) => {
      requests.push(plain(payload))
      if (payload.preview) return deletionReview()
      return new Promise((resolve) => {
        release = resolve
      })
    }
  })
  props.targets = [
    { id: 4, version: 'v1', email: 'target@example.test', label: 'Target' }
  ]
  await vue.nextTick()
  await new Promise((resolve) => setImmediate(resolve))
  run("confirmations.value[4] = 'target@example.test '")
  assert.equal(run('canDelete.value'), false)
  run("confirmations.value[4] = 'target@example.test'")
  const pending = run('submit()')
  await run('submit()')
  run('close()')
  assert.equal(events.length, 0)
  props.targets[0].version = 'mutated'
  assert.equal(requests.length, 2)
  assert.deepEqual(requests[1], {
    members: [
      { id: 4, version: 'v1', confirmation_email: 'target@example.test' }
    ],
    preview: false,
    confirmation: 'proof-v1'
  })
  release({ deleted: 1, skipped: 0, deleted_ids: [4], skipped_items: [] })
  await pending
  assert.equal(events[0][0], 'applied')
  assert.equal(run('result.value.deleted'), 1)
})

test('deletion failures preserve typed input, conflicts require a new version and new confirmation', async () => {
  let version = 'v1'
  let status = 503
  const { props, run } = await component('MemberDeleteDialog', {
    deleteTenantMembers: async (payload) => {
      if (payload.preview) return deletionReview(version)
      throw { response: { status } }
    },
    getTenantMembers: async () => ({
      results: [{ id: 4, version, email: 'target@example.test' }]
    })
  })
  props.targets = [{ id: 4, version, email: 'target@example.test' }]
  await vue.nextTick()
  await new Promise((resolve) => setImmediate(resolve))
  run("confirmations.value[4] = 'target@example.test'")
  await run('submit()')
  assert.equal(run('confirmations.value[4]'), 'target@example.test')
  assert.equal(run('canDelete.value'), true)
  status = 409
  await run('submit()')
  assert.equal(run('canDelete.value'), false)
  assert.equal(run('conflict.value'), true)
  version = 'v2'
  await run('load(true)')
  assert.equal(run('review.value.members[0].version'), 'v2')
  assert.equal(run('confirmations.value[4]'), undefined)
  assert.equal(run('canDelete.value'), false)
})

test('late deletion previews cannot populate a newly opened selection', async () => {
  const releases = []
  const { props, run } = await component('MemberDeleteDialog', {
    deleteTenantMembers: () => new Promise((resolve) => releases.push(resolve))
  })
  props.targets = [{ id: 4, version: 'v1' }]
  await vue.nextTick()
  props.targets = [{ id: 5, version: 'v2' }]
  await vue.nextTick()
  releases[0](deletionReview())
  await new Promise((resolve) => setImmediate(resolve))
  assert.equal(run('review.value'), null)
  releases[1]({
    ...deletionReview('v2'),
    members: [{ ...deletionReview('v2').members[0], id: 5 }]
  })
  await new Promise((resolve) => setImmediate(resolve))
  assert.equal(run('review.value.members[0].id'), 5)
})

test('deletion menu blocks self and freezes selection; deleting closes only the deleted detail', async () => {
  const { run } = await component('Members', {
    getTenantMembers: async () => ({ results: [], count: 0 })
  })
  assert.equal(
    run(
      "rowActions({ id: 1, is_self: true }).find(a => a.key === 'delete').disabled"
    ),
    true
  )
  await run("handleRowAction('delete', { id: 1, is_self: true })")
  assert.equal(run('deleteTargets.value.length'), 0)
  run(
    "members.value = [{ id: 4, version: 'v1', email: 'target@example.test' }]; toggleSelection(members.value[0]); openDelete(selectedMembers.value); members.value[0].version = 'changed'"
  )
  assert.equal(run('deleteTargets.value[0].version'), 'v1')
  assert.equal(run('deleteTargets.value[0].email'), 'target@example.test')
  run("openDetail({ id: 5, role: 'member', status: 'active' })")
  await run('membersDeleted({ deleted_ids: [4] })')
  assert.equal(run('detailMember.value.id'), 5)
  await run('membersDeleted({ deleted_ids: [5] })')
  assert.equal(run('detailMember.value'), null)
})

test('member password recovery API posts only the reviewed version and unwraps its result', async () => {
  const source = await readFile(
    new URL('../src/api/tenant.js', import.meta.url),
    'utf8'
  )
  const requests = []
  const context = vm.createContext({
    api: {
      post: async (...args) => {
        requests.push(plain(args))
        return { data: { data: { status: 'sent', member_id: 4 } } }
      }
    }
  })
  vm.runInContext(
    source.replace(/^import[^\n]+\n/gm, '').replace(/export /g, ''),
    context
  )
  const response = await vm.runInContext(
    "resetTenantMemberPassword(4, 'reviewed')",
    context
  )
  assert.deepEqual(requests, [
    ['/v1/tenant/members/4/password-reset', { version: 'reviewed' }]
  ])
  assert.deepEqual(plain(response), { status: 'sent', member_id: 4 })
})

test('password recovery freezes the member, blocks repeats and preserves newly opened details', async () => {
  const calls = []
  const messages = []
  let release
  const { run } = await component('Members', {
    resetTenantMemberPassword: (id, version) => {
      calls.push([id, version])
      return new Promise((resolve) => {
        release = resolve
      })
    },
    useI18n: () => ({ t: (key, values) => values?.email || key }),
    useToast: () => ({
      showSuccess: (text) => messages.push(text),
      showError: assert.fail
    })
  })
  run(
    "members.value = [{ id: 4, version: 'reviewed', email: 'original@example.test', can_reset_password: true }]"
  )
  assert.equal(
    run(
      "rowActions(members.value[0]).find(a => a.key === 'reset-password').disabled"
    ),
    false
  )
  const pending = run("handleRowAction('reset-password', members.value[0])")
  assert.equal(
    run(
      "rowActions(members.value[0]).find(a => a.key === 'reset-password').disabled"
    ),
    true
  )
  run(
    "members.value[0].email = 'changed@example.test'; members.value[0].version = 'new'"
  )
  await run("handleRowAction('reset-password', members.value[0])")
  run(
    "openDetail({ id: 5, version: 'other', role: 'member', status: 'active' })"
  )
  release({ status: 'sent', member_id: 4 })
  await pending
  assert.deepEqual(calls, [[4, 'reviewed']])
  assert.deepEqual(messages, ['original@example.test'])
  assert.equal(run('detailMember.value.id'), 5)
  assert.equal(run('resettingMember.value'), null)
})

test('password recovery reports failures without changing the selected member and allows retries', async () => {
  const errors = []
  let status = 503
  const { run } = await component('Members', {
    resetTenantMemberPassword: async () => {
      throw { response: { status } }
    },
    useToast: () => ({
      showError: (text) => errors.push(text),
      showSuccess: assert.fail
    })
  })
  run(
    "openDetail({ id: 4, version: 'v1', role: 'member', status: 'locked', can_reset_password: true })"
  )
  for (const [code, key] of [
    [503, 'Failed'],
    [429, 'Limited'],
    [409, 'Conflict'],
    [403, 'Forbidden'],
    [400, 'Unavailable']
  ]) {
    status = code
    await run("handleRowAction('reset-password', detailMember.value)")
    assert.equal(errors.at(-1), `management.memberReset${key}`)
    assert.equal(run('resettingMember.value'), null)
    assert.equal(run('detailMember.value.status'), 'locked')
  }
})

test('password recovery for unaccepted invitations is disabled in the menu and guarded in the handler', async () => {
  const { run } = await component('Members', {
    resetTenantMemberPassword: assert.fail
  })
  run(
    "members.value = [{ id: 4, status: 'invited', can_reset_password: false }]"
  )
  assert.equal(
    run(
      "rowActions(members.value[0]).find(a => a.key === 'reset-password').disabled"
    ),
    true
  )
  assert.equal(
    run(
      "rowActions(members.value[0]).find(a => a.key === 'reset-password').disabledReason"
    ),
    'management.memberResetUnavailable'
  )
  await run("handleRowAction('reset-password', members.value[0])")
})

test('member selection freezes reviewed versions and clears on page/filter reload', async () => {
  const { run } = await component('Members', {
    getTenantMembers: async () => ({
      results: [{ id: 2, version: 'new' }],
      count: 1
    })
  })
  run(
    "members.value = [{ id: 1, version: 'v1', display_name: 'First' }]; toggleSelection(members.value[0]); members.value[0].version = 'v2'; openBulk('role')"
  )
  assert.equal(run('bulkTargets.value[0].version'), 'v1')
  assert.equal(run('bulkTargets.value[0].label'), 'First')
  await run('fetchMembers()')
  assert.equal(run('selectedMembers.value.length'), 0)
  assert.equal(run('bulkTargets.value[0].version'), 'v1')
})

test('member selection selects the current page only and toggles without duplicates', async () => {
  const { run } = await component('Members')
  run(
    "members.value = [{ id: 1, version: 'v1' }, { id: 2, version: 'v2' }]; togglePageSelection()"
  )
  assert.equal(run('selectedMembers.value.length'), 2)
  run('toggleSelection(members.value[0])')
  assert.equal(run('selectedMembers.value.length'), 1)
  run('togglePageSelection(); togglePageSelection()')
  assert.equal(run('selectedMembers.value.length'), 0)
})

test('last login uses translated relative units and preserves empty, old and invalid dates', async () => {
  const { run } = await component('Members', {
    useI18n: () => ({
      t: (key, values) => (values ? `${key}:${values.count}` : key)
    })
  })
  const now = Date.now()
  for (const [elapsed, expected] of [
    [20 * 1000, 'management.memberJustNow'],
    [5 * 60000, 'time.minutesAgo:5'],
    [3 * 3600000, 'time.hoursAgo:3'],
    [8 * 86400000, 'time.daysAgo:8']
  ]) {
    assert.equal(
      run(`formatLastLogin('${new Date(now - elapsed).toISOString()}')`),
      expected
    )
  }
  assert.equal(run('formatLastLogin(null)'), 'management.memberNever')
  assert.equal(run("formatLastLogin('invalid')"), 'invalid')
  const old = new Date(now - 40 * 86400000)
  assert.equal(
    run(`formatLastLogin('${old.toISOString()}')`),
    old.toLocaleDateString()
  )
})

test('locked details keep membership status and unlock freezes the reviewed version', async () => {
  const calls = []
  let release
  const { run } = await component('Members', {
    unlockTenantMember: (id, version) => {
      calls.push([id, version])
      return new Promise((resolve) => {
        release = resolve
      })
    },
    getTenantMembers: async () => ({ results: [], count: 0 })
  })
  run(
    "members.value = [{ id: 4, version: 'reviewed', status: 'locked', membership_status: 'active', role: 'member' }]; openDetail(members.value[0])"
  )
  assert.equal(run('detailForm.status'), 'active')
  assert.equal(
    run("rowActions(members.value[0]).some(action => action.key === 'unlock')"),
    true
  )
  const pending = run('unlockMember(members.value[0])')
  await run('unlockMember(members.value[0])')
  assert.deepEqual(calls, [[4, 'reviewed']])
  run(
    "openDetail({ id: 5, version: 'new', status: 'disabled', role: 'member' })"
  )
  release({ id: 4, version: 'unlocked', status: 'active' })
  await pending
  assert.equal(run('detailMember.value.id'), 5)
  assert.equal(run('unlockingMember.value'), null)
})

test('unlock failures preserve the detail and allow an explicit retry', async () => {
  let failed = true
  const errors = []
  const { run } = await component('Members', {
    useToast: () => ({
      showError: (value) => errors.push(value),
      showSuccess() {}
    }),
    unlockTenantMember: async () => {
      if (failed) throw { response: { status: 409 } }
      return {
        id: 4,
        version: 'fresh',
        status: 'active',
        membership_status: 'active'
      }
    },
    getTenantMembers: async () => ({ results: [], count: 0 })
  })
  run(
    "openDetail({ id: 4, version: 'reviewed', status: 'locked', membership_status: 'active', role: 'member' })"
  )
  await run('unlockMember(detailMember.value)')
  assert.equal(run('detailMember.value.status'), 'locked')
  assert.equal(run('unlockingMember.value'), null)
  assert.deepEqual(errors, ['management.memberUnlockConflict'])
  failed = false
  await run(
    "detailMember.value.version = 'rereviewed'; unlockMember(detailMember.value)"
  )
  assert.equal(run('detailMember.value.status'), 'active')
})

test('disable preview response cannot populate a subsequently opened action', async () => {
  let release
  const { props, run } = await component('MemberBulkDialog', {
    bulkUpdateTenantMembers: () =>
      new Promise((resolve) => {
        release = resolve
      })
  })
  props.targets = [{ id: 1, version: 'v1', label: 'One' }]
  props.action = 'disable'
  await vue.nextTick()
  assert.equal(run('previewLoading.value'), true)
  props.action = 'role'
  await vue.nextTick()
  release({ key_summary: { total: 9 } })
  await vue.nextTick()
  assert.equal(run('keySummary.value'), null)
  assert.equal(run('previewLoading.value'), false)
})

test('bulk submit freezes parameters, blocks double submits, and retains failed drafts', async () => {
  let release
  const requests = []
  const { props, events, run } = await component('MemberBulkDialog', {
    bulkUpdateTenantMembers: (payload) => {
      requests.push(plain(payload))
      if (requests.length === 1)
        return Promise.reject({ response: { status: 502 } })
      return new Promise((resolve) => {
        release = resolve
      })
    }
  })
  props.targets = [{ id: 1, version: 'v1', label: 'One' }]
  props.action = 'groups'
  await vue.nextTick()
  run('groupIds.value = [7]')
  await run('submit()')
  assert.equal(events.length, 0)
  assert.deepEqual(plain(run('groupIds.value')), [7])
  const pending = run('submit()')
  await run('submit()')
  run('close()')
  assert.equal(requests.length, 2)
  assert.equal(events.length, 0)
  assert.deepEqual(requests[1], {
    action: 'groups',
    members: [{ id: 1, version: 'v1' }],
    group_ids: [7]
  })
  release({
    changed: 1,
    skipped: 0,
    failed: 0,
    items: [{ id: 1, status: 'changed', reason: '' }]
  })
  await pending
  assert.equal(run('result.value.changed'), 1)
  assert.equal(events[0][0], 'applied')
})

test('version conflict requires reselection and cannot silently retry', async () => {
  let calls = 0
  const { props, run } = await component('MemberBulkDialog', {
    bulkUpdateTenantMembers: async () => {
      calls++
      throw { response: { status: 409 } }
    }
  })
  props.targets = [{ id: 1, version: 'v1' }]
  props.action = 'enable'
  await vue.nextTick()
  await run('submit()')
  await run('submit()')
  assert.equal(calls, 1)
  assert.equal(run('conflict.value'), true)
  assert.equal(run('error.value'), 'management.memberBulk.selectionChanged')
})

test('bulk disable requires successful preview and preserves explicit key treatment', async () => {
  const requests = []
  const { props, run } = await component('MemberBulkDialog', {
    bulkUpdateTenantMembers: async (payload) => {
      requests.push(plain(payload))
      return payload.preview
        ? { key_summary: { total: 3, active: 2, calls_7d: 4 } }
        : { changed: 1, skipped: 0, failed: 0, items: [] }
    }
  })
  props.targets = [{ id: 1, version: 'v1' }]
  props.action = 'disable'
  await vue.nextTick()
  await vue.nextTick()
  await new Promise((resolve) => setImmediate(resolve))
  assert.equal(run('keySummary.value.total'), 3)
  await run('submit(true)')
  assert.equal(requests[1].disable_api_keys, true)
})

test('partial delivery results stay visible without resending successful recipients', async () => {
  let calls = 0
  const { props, run } = await component('MemberBulkDialog', {
    bulkUpdateTenantMembers: async () => {
      calls++
      return {
        changed: 1,
        skipped: 0,
        failed: 1,
        items: [
          { id: 1, status: 'changed', reason: '' },
          { id: 2, status: 'failed', reason: 'delivery_failed' }
        ]
      }
    }
  })
  props.targets = [
    { id: 1, version: 'v1', label: 'First' },
    { id: 2, version: 'v2', label: 'Second' }
  ]
  props.action = 'resend'
  await vue.nextTick()
  await run('submit()')
  await run('submit()')
  assert.equal(calls, 1)
  assert.equal(run('targetLabel(2)'), 'Second')
  assert.equal(
    run('itemReason(result.value.items[1])'),
    'management.memberBulk.reasons.delivery_failed'
  )
})

test('late member page responses cannot replace the current filtered page', async () => {
  const releases = []
  const { run } = await component('Members', {
    getTenantMembers: () => new Promise((resolve) => releases.push(resolve))
  })
  const first = run('fetchMembers()')
  const second = run("search.value = 'new'; fetchMembers()")
  releases[1]({ results: [{ id: 2, version: 'new' }], count: 1 })
  await second
  run('toggleSelection(members.value[0])')
  releases[0]({ results: [{ id: 1, version: 'old' }], count: 100 })
  await first
  assert.equal(run('members.value[0].id'), 2)
  assert.equal(run('selectedMembers.value[0].id'), 2)
  assert.equal(run('totalCount.value'), 1)
})

test('self demotion refreshes live identity and leaves member management', async () => {
  const userStore = {
    user: { id: 1, tenant_role: 'tenant_admin' },
    setUser(value) {
      this.user = value
    }
  }
  const destinations = []
  const { run } = await component('Members', {
    useUserStore: () => userStore,
    authApi: {
      getProfile: async () => ({ data: { id: 1, tenant_role: 'member' } })
    },
    router: { replace: async (path) => destinations.push(path) },
    getTenantMembers: () =>
      assert.fail('A demoted member must leave the management page')
  })
  await run(
    "bulkApplied({ action: 'role', role: 'member', targets: [{ id: 1, is_self: true }], result: { items: [{ id: 1, status: 'changed' }] } })"
  )
  assert.equal(userStore.user.tenant_role, 'member')
  assert.deepEqual(destinations, ['/lens/chat'])
})

test('self demotion still leaves management when profile refresh is unavailable', async () => {
  const userStore = {
    user: { id: 1, tenant_role: 'tenant_admin' },
    setUser(value) {
      this.user = value
    }
  }
  const destinations = []
  const { run } = await component('Members', {
    useUserStore: () => userStore,
    authApi: {
      getProfile: async () => {
        throw new Error('offline')
      }
    },
    router: { replace: async (path) => destinations.push(path) }
  })
  await run('leaveMemberManagement()')
  assert.equal(userStore.user.tenant_role, 'member')
  assert.deepEqual(destinations, ['/lens/chat'])
})

test('rendered bulk results show member names, skipped reasons and failures', async () => {
  const { parse, compileScript } = await import('@vue/compiler-sfc')
  const { renderToString } = await import('@vue/server-renderer')
  const source = await readFile(
    new URL(
      '../src/admin/pages/Management/MemberBulkDialog.vue',
      import.meta.url
    ),
    'utf8'
  )
  const compiled = compileScript(parse(source).descriptor, {
    id: 'member-bulk',
    inlineTemplate: true
  }).content
  const helpers = { ...vue }
  const context = vm.createContext({
    ...helpers,
    useI18n: () => ({ t: (key) => key }),
    bulkUpdateTenantMembers: async () => ({})
  })
  let script = compiled
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
    BaseModal: wrapper,
    BaseButton: button,
    BaseSelect: wrapper,
    BaseLoading: wrapper
  })
  script = script.replace('export default', 'globalThis.component =')
  script = script.replace(
    'const result = ref(null)',
    `const result = ref({ changed: 1, skipped: 1, failed: 1, items: [
    { id: 1, status: 'changed', reason: '' }, { id: 2, status: 'skipped', reason: 'not_invited' },
    { id: 3, status: 'failed', reason: 'delivery_failed' }
  ] })`
  )
  script = script.replace('result.value = null', '')
  vm.runInContext(script, context)
  const markup = await renderToString(
    vue.createSSRApp(context.component, {
      action: 'resend',
      targets: [
        { id: 1, label: '<First>' },
        { id: 2, label: 'Second' },
        { id: 3, label: 'Third' }
      ]
    })
  )
  assert.match(markup, /&lt;First&gt;/)
  assert.match(markup, /Second/)
  assert.match(markup, /Third/)
  assert.match(markup, /management.memberBulk.statuses.changed/)
  assert.match(markup, /management.memberBulk.reasons.not_invited/)
  assert.match(markup, /management.memberBulk.reasons.delivery_failed/)
  assert.doesNotMatch(markup, /common.confirm/)
})

test('closing an outdated confirmation reloads the list and clears the stale selection', async () => {
  const { run } = await component('Members', {
    getTenantMembers: async () => ({
      results: [{ id: 1, version: 'fresh' }],
      count: 1
    })
  })
  run(
    "selectedMembers.value = [{ id: 1, version: 'old' }]; bulkAction.value = 'enable'; closeBulk(true)"
  )
  await new Promise((resolve) => setImmediate(resolve))
  assert.equal(run('bulkAction.value'), '')
  assert.equal(run('selectedMembers.value.length'), 0)
  assert.equal(run('members.value[0].version'), 'fresh')
})
