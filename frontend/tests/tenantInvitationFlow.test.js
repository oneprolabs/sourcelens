import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import vm from 'node:vm'
import * as vue from 'vue'
import {
  classifyInvitationRows,
  invitationCsvTemplate,
  parseInvitationCsv,
  parseInvitationText
} from '../src/utils/invitations.js'

import {
  getFirstApiError,
  getPasswordPolicyError
} from '../src/utils/password.js'

async function component(file, values = {}) {
  const source = await readFile(
    new URL(`../src/${file}`, import.meta.url),
    'utf8'
  )
  const script = source
    .split('<script setup>')[1]
    .split('</script>')[0]
    .replace(/^import[^]*? from ['"][^'"]+['"]\n/gm, '')
  const events = []
  const props = vue.reactive({ show: false, invitation: false })
  const state = {
    ...vue,
    URLSearchParams,
    defineProps: () => props,
    defineEmits:
      () =>
      (...args) =>
        events.push(args),
    onMounted() {},
    onBeforeUnmount() {},
    KeyRound: {},
    Trash2: {},
    setTimeout: () => 1,
    clearTimeout() {},
    classifyInvitationRows,
    invitationCsvTemplate,
    parseInvitationCsv,
    parseInvitationText,
    useI18n: () => ({ t: (key) => key }),
    useToast: () => ({ showSuccess() {}, showError() {} }),
    ...values
  }
  const context = vm.createContext(state)
  vm.runInContext(script, context)
  return { props, events, run: (code) => vm.runInContext(code, context) }
}

const drawerFile = 'admin/pages/Management/TenantInvitationDrawer.vue'

test('invitation freezes a reviewed envelope and waits for delivery before signaling save', async () => {
  let release
  let requests = 0
  const { props, events, run } = await component(drawerFile, {
    getTenantGroups: async () => ({
      results: [{ id: 7, name: 'Engineering' }]
    }),
    previewTenantInvitations: async ({ entries }) => ({
      confirmation: 'proof',
      counts: { valid: 1 },
      items: [
        { index: 0, email: entries[0].email, status: 'valid', reason: '' }
      ]
    }),
    bulkInviteTenantMembers: (payload) => {
      requests++
      assert.deepEqual(JSON.parse(JSON.stringify(payload)), {
        entries: [
          { email: 'new@example.com', role: 'tenant_admin', group_ids: [7] }
        ],
        confirmation: 'proof'
      })
      return new Promise((resolve) => {
        release = resolve
      })
    }
  })
  props.show = true
  await vue.nextTick()
  run(
    "form.email = ' new@example.com '; form.role = 'tenant_admin'; form.group_ids = [7]"
  )
  await vue.nextTick()
  run('formElement.value = { reportValidity: () => false }')
  await run('submit()')
  assert.equal(requests, 0)
  run('formElement.value = { reportValidity: () => true }')
  await run('submit()')
  assert.equal(requests, 0)
  assert.equal(run('review.value.entries[0].email'), 'new@example.com')
  const saving = run('submit()')
  await run('submit()')
  run('close()')
  assert.equal(requests, 1)
  assert.equal(events.length, 0)
  release({ sent: 1, skipped: 0, failed: 0, items: [] })
  await saving
  assert.deepEqual(JSON.parse(JSON.stringify(events)), [
    ['saved', { keepOpen: true }]
  ])
  assert.equal(run('result.value.sent'), 1)
})

test('failed batch request preserves the reviewed draft for a safe retry', async () => {
  let calls = 0
  const { props, events, run } = await component(drawerFile, {
    getTenantGroups: async () => ({ results: [] }),
    previewTenantInvitations: async () => ({
      confirmation: 'proof',
      counts: { valid: 1 },
      items: [{ index: 0, email: 'keep@example.com', status: 'valid' }]
    }),
    bulkInviteTenantMembers: async () => {
      if (++calls === 1) throw { response: { status: 502 } }
      return { sent: 1, failed: 0, skipped: 0, items: [] }
    }
  })
  props.show = true
  await vue.nextTick()
  run(
    "form.email = 'keep@example.com'; form.group_ids = [7]; formElement.value = { reportValidity: () => true }"
  )
  await vue.nextTick()
  await run('submit()')
  await run('submit()')
  assert.equal(events.length, 0)
  assert.equal(run('form.email'), 'keep@example.com')
  assert.equal(run('review.value.confirmation'), 'proof')
  assert.equal(run('error.value'), 'invitations.failed')
  await run('submit()')
  assert.equal(events.length, 1)
})

test('late group requests cannot replace groups after closing and reopening the invitation', async () => {
  const releases = []
  const { props, run } = await component(drawerFile, {
    getTenantGroups: () => new Promise((resolve) => releases.push(resolve))
  })
  props.show = true
  await vue.nextTick()
  props.show = false
  await vue.nextTick()
  props.show = true
  await vue.nextTick()
  releases[1]({ results: [{ id: 2, name: 'Current' }] })
  await vue.nextTick()
  releases[0]({ results: [{ id: 1, name: 'Old' }] })
  await vue.nextTick()
  assert.equal(run('groups.value[0].id'), 2)
})

test('accept invitation uses the hash credential and clears password inputs after acceptance', async () => {
  let release
  let calls = 0
  const replaced = []
  const { props, run } = await component('pages/ResetPassword.vue', {
    useRoute: () => ({
      path: '/accept-invitation',
      hash: '#token=email-proof',
      params: {}
    }),
    useRouter: () => ({ replace: (value) => replaced.push(value) }),
    getFirstApiError,
    getPasswordPolicyError,
    authApi: {
      acceptTenantInvitation: (data) => {
        calls++
        assert.equal(data.token, 'email-proof')
        assert.equal(data.newPassword1, 'Strong-Password-79!')
        return new Promise((resolve) => {
          release = resolve
        })
      },
      confirmPasswordReset: () =>
        assert.fail('Invitation must use its own endpoint')
    }
  })
  props.invitation = true
  run(
    "form.newPassword1 = 'Strong-Password-79!'; form.newPassword2 = form.newPassword1"
  )
  const pending = run('handleSubmit()')
  await run('handleSubmit()')
  assert.equal(calls, 1)
  assert.equal(run('resetComplete.value'), false)
  release({ data: { status: 'active' } })
  await pending
  assert.equal(run('resetComplete.value'), true)
  assert.equal(run('form.newPassword1'), '')
  assert.equal(run('form.newPassword2'), '')
  assert.equal(replaced[0].hash, '')
})

test('expired invitation stays on the password form and asks for a new invitation', async () => {
  const { props, run } = await component('pages/ResetPassword.vue', {
    useRoute: () => ({ hash: '#token=expired', params: {} }),
    useRouter: () => ({}),
    getFirstApiError,
    getPasswordPolicyError,
    authApi: {
      acceptTenantInvitation: async () => {
        throw {
          response: { data: { token: ['INVITATION_INVALID_OR_EXPIRED'] } }
        }
      }
    }
  })
  props.invitation = true
  run(
    "form.newPassword1 = 'Strong-Password-79!'; form.newPassword2 = form.newPassword1"
  )
  await run('handleSubmit()')
  assert.equal(run('resetComplete.value'), false)
  assert.equal(run('errorMessage.value'), 'invitation.invalid')
})

test('member resend prevents duplicate delivery while in flight', async () => {
  let release
  let calls = 0
  const { run } = await component('admin/pages/Management/Members.vue', {
    resendTenantInvitation: (id) => {
      calls++
      assert.equal(id, 9)
      return new Promise((resolve) => {
        release = resolve
      })
    }
  })
  const pending = run("handleRowAction('resend', { id: 9 })")
  await run("handleRowAction('resend', { id: 9 })")
  assert.equal(calls, 1)
  release()
  await pending
  assert.equal(run('resendingMember.value'), null)
})

test('pending and revoked invitations never offer direct activation', async () => {
  const { run } = await component('admin/pages/Management/Members.vue', {
    Eye: {},
    Play: {},
    Ban: {}
  })
  assert.equal(
    run(
      "rowActions({ status: 'invited' }).find(action => action.key === 'toggle').label"
    ),
    'invitations.revoke'
  )
  assert.equal(
    run(
      "rowActions({ status: 'disabled', requires_invitation: true }).find(action => action.key === 'toggle').disabled"
    ),
    true
  )
  assert.equal(
    run(
      "rowActions({ status: 'disabled', requires_invitation: false }).find(action => action.key === 'toggle').disabled"
    ),
    false
  )
})

test('sending an invitation clears member filters and reads the real pending list', async () => {
  const { run } = await component('admin/pages/Management/Members.vue', {
    getTenantMembers: async (params) => {
      assert.deepEqual(JSON.parse(JSON.stringify(params)), {
        page: 1,
        page_size: 20,
        status: 'invited'
      })
      return { count: 1, results: [{ id: 9, status: 'invited' }] }
    }
  })
  run(
    "invitationOpen.value = true; search.value = 'old'; roleFilter.value = 'tenant_admin'; groupFilter.value = '7'"
  )
  await run('invitationSent()')
  assert.equal(run('invitationOpen.value'), false)
  assert.equal(run('members.value[0].status'), 'invited')
  assert.equal(run('totalCount.value'), 1)
})

const membersFile = 'admin/pages/Management/Members.vue'

test('disabling a member waits for an explicit key choice and submits it once', async () => {
  const requests = []
  let release
  const { run } = await component(membersFile, {
    updateTenantMember: (id, payload) => {
      requests.push([id, JSON.parse(JSON.stringify(payload))])
      return new Promise((resolve) => {
        release = resolve
      })
    },
    getTenantMembers: async () => ({ results: [], count: 0 })
  })
  await run("toggleMember({ id: 4, status: 'active' })")
  assert.equal(requests.length, 0)
  const pending = run('confirmDisable(true)')
  await run('confirmDisable(false)')
  assert.deepEqual(requests, [
    [4, { status: 'disabled', disable_api_keys: true }]
  ])
  run('closeDisableDialog()')
  assert.ok(run('disableTarget.value'))
  release({})
  await pending
  assert.equal(run('disableTarget.value'), null)
})

test('detail edits preserve their snapshot through a rejected disable and a member-only retry', async () => {
  const requests = []
  const { run } = await component(membersFile, {
    updateTenantMember: async (id, payload) => {
      requests.push([id, JSON.parse(JSON.stringify(payload))])
      if (requests.length === 1)
        throw { response: { data: { role: 'LAST_TENANT_ADMIN_REQUIRED' } } }
      return {}
    },
    getTenantMembers: async () => ({ results: [], count: 0 })
  })
  run(
    "openDetail({ id: 4, role: 'tenant_admin', status: 'active', groups: [{ id: 7 }] })"
  )
  run("detailForm.role = 'member'; detailForm.status = 'disabled'")
  await run('saveDetail()')
  assert.equal(requests.length, 0)
  run('detailForm.groupIds = [99]')
  await run('confirmDisable(true)')
  assert.ok(run('disableTarget.value'))
  assert.equal(run('disableError.value'), 'management.lastAdminRequired')
  await run('confirmDisable(false)')
  assert.deepEqual(requests[1], [
    4,
    {
      role: 'member',
      status: 'disabled',
      group_ids: [7],
      disable_api_keys: false
    }
  ])
  assert.equal(run('detailMember.value'), null)
})

test('older availability responses cannot replace a newer recipient draft', async () => {
  const releases = []
  const { props, run } = await component(drawerFile, {
    getTenantGroups: async () => ({ results: [] }),
    previewTenantInvitations: ({ entries }) =>
      new Promise((resolve) => releases.push([entries, resolve]))
  })
  props.show = true
  await vue.nextTick()
  run("form.email = 'old@example.com'")
  await vue.nextTick()
  const old = run('validateDraft()')
  run("form.email = 'new@example.com'")
  await vue.nextTick()
  const current = run('validateDraft()')
  releases[1][1]({
    confirmation: 'new',
    counts: { valid: 1 },
    items: [{ email: 'new@example.com', status: 'valid' }]
  })
  await current
  releases[0][1]({
    confirmation: 'old',
    counts: { valid: 0 },
    items: [{ email: 'old@example.com', status: 'existing' }]
  })
  await old
  assert.equal(run('validation.value.confirmation'), 'new')
  assert.equal(run('displayItems.value[0].email'), 'new@example.com')
})

test('CSV import respects per-row configuration and ignores a late read after closing', async () => {
  let release
  const { props, run } = await component(drawerFile, {
    getTenantGroups: async () => ({ results: [{ id: 7, name: 'Engineering' }] })
  })
  props.show = true
  await vue.nextTick()
  await new Promise((resolve) => setImmediate(resolve))
  await run(
    "importCsv({ target: { files: [{ name: 'members.csv', size: 100, text: async () => 'email,role,groups\\na@example.com,tenant_admin,Engineering' }] } })"
  )
  assert.equal(run('parsed.value.entries[0].role'), 'tenant_admin')
  assert.equal(run('parsed.value.entries[0].group_ids[0]'), 7)
  const event = {
    target: {
      files: [
        {
          name: 'late.csv',
          size: 100,
          text: () =>
            new Promise((resolve) => {
              release = resolve
            })
        }
      ]
    }
  }
  const pending = run('importing.value = false')
  await pending
  const state = { event }
  const { props: nextProps, run: nextRun } = await component(drawerFile, {
    getTenantGroups: async () => ({ results: [] }),
    ...state
  })
  nextProps.show = true
  await vue.nextTick()
  const reading = nextRun('importCsv(event)')
  nextProps.show = false
  await vue.nextTick()
  release('email,role,groups\na@example.com,member,')
  await reading
  assert.equal(nextRun('csvSource.value'), '')
})

test('partial batch results remain visible and never resend accepted recipients', async () => {
  let calls = 0
  const { props, events, run } = await component(drawerFile, {
    getTenantGroups: async () => ({ results: [] }),
    previewTenantInvitations: async () => ({
      confirmation: 'proof',
      counts: { valid: 2 },
      items: [
        { index: 0, email: 'a@example.com', status: 'valid' },
        { index: 1, email: 'b@example.com', status: 'valid' }
      ]
    }),
    bulkInviteTenantMembers: async () => {
      calls++
      return {
        sent: 1,
        skipped: 0,
        failed: 1,
        items: [
          { index: 0, email: 'a@example.com', status: 'sent', reason: '' },
          {
            index: 1,
            email: 'b@example.com',
            status: 'failed',
            reason: 'delivery_failed'
          }
        ]
      }
    }
  })
  props.show = true
  await vue.nextTick()
  run(
    "form.email = 'a@example.com,b@example.com'; formElement.value = { reportValidity: () => true }"
  )
  await vue.nextTick()
  await run('submit()')
  await run('submit()')
  await run('submit()')
  assert.equal(calls, 1)
  assert.equal(run('result.value.failed'), 1)
  assert.equal(events[0][1].keepOpen, true)
  run('close()')
  assert.equal(events[1][0], 'close')
})

test('edited review discards the old proof and requests current availability', async () => {
  let calls = 0
  const { props, run } = await component(drawerFile, {
    getTenantGroups: async () => ({ results: [] }),
    previewTenantInvitations: async () => ({
      confirmation: `proof-${++calls}`,
      counts: { valid: 1 },
      items: [{ index: 0, email: 'a@example.com', status: 'valid' }]
    }),
    bulkInviteTenantMembers: async () => {
      throw { response: { status: 409 } }
    }
  })
  props.show = true
  await vue.nextTick()
  run(
    "form.email = 'a@example.com'; formElement.value = { reportValidity: () => true }"
  )
  await vue.nextTick()
  await run('submit()')
  await run('submit()')
  assert.equal(run('error.value'), 'invitations.bulk.reviewExpired')
  run('editDraft()')
  await new Promise((resolve) => setImmediate(resolve))
  assert.equal(run('review.value'), null)
  assert.equal(run('validation.value.confirmation'), 'proof-2')
})

test('revoked invitation restores only the frozen target and current reviewed version', async () => {
  const requests = []
  const { props, events, run } = await component(drawerFile, {
    getTenantGroups: async () => ({
      results: [{ id: 7, name: 'Engineering' }]
    }),
    inviteTenantMember: async (payload) => {
      requests.push(JSON.parse(JSON.stringify(payload)))
      return { member_id: 8 }
    },
    previewTenantInvitations: () =>
      assert.fail('Explicit reinstatement must not enumerate bulk recipients')
  })
  props.reinvite = {
    id: 8,
    email: 'restore@example.com',
    role: 'member',
    version: 'v1',
    groups: [{ id: 7 }]
  }
  props.show = true
  await vue.nextTick()
  run(
    "form.role = 'tenant_admin'; formElement.value = { reportValidity: () => true }"
  )
  await vue.nextTick()
  await run('submit()')
  assert.deepEqual(requests, [
    {
      email: 'restore@example.com',
      role: 'tenant_admin',
      group_ids: [7],
      target_id: 8,
      version: 'v1'
    }
  ])
  assert.equal(events[0][1].keepOpen, true)
  assert.equal(run('result.value.sent'), 1)
})

test('failed restored invitation exposes pending result and does not retry its stale proof', async () => {
  let calls = 0
  const { props, run } = await component(drawerFile, {
    getTenantGroups: async () => ({ results: [] }),
    inviteTenantMember: async () => {
      calls++
      throw {
        response: {
          status: 502,
          data: { detail: 'INVITATION_DELIVERY_FAILED', member_id: 8 }
        }
      }
    }
  })
  props.reinvite = {
    id: 8,
    email: 'restore@example.com',
    role: 'member',
    version: 'v1',
    groups: []
  }
  props.show = true
  await vue.nextTick()
  run('formElement.value = { reportValidity: () => true }')
  await run('submit()')
  await run('submit()')
  assert.equal(calls, 1)
  assert.equal(run('result.value.items[0].reason'), 'delivery_failed')
})

test('only revoked pending members offer the explicit reinstatement action', async () => {
  const { run } = await component(membersFile, { Eye: {}, Play: {}, Ban: {} })
  assert.equal(
    run(
      "rowActions({ status: 'disabled', requires_invitation: true }).some(action => action.key === 'reinvite')"
    ),
    true
  )
  assert.equal(
    run(
      "rowActions({ status: 'disabled', requires_invitation: false }).some(action => action.key === 'reinvite')"
    ),
    false
  )
  await run(
    "handleRowAction('reinvite', { id: 8, email: 'restore@example.com', version: 'v1' })"
  )
  assert.equal(run('invitationOpen.value'), true)
  assert.equal(run('reinvitationTarget.value.version'), 'v1')
  run('openInvitation()')
  assert.equal(run('reinvitationTarget.value'), null)
})

test('all existing recipients cannot proceed to a send confirmation', async () => {
  const { props, run } = await component(drawerFile, {
    getTenantGroups: async () => ({ results: [] }),
    previewTenantInvitations: async () => ({
      confirmation: 'proof',
      counts: { valid: 0, existing: 1 },
      items: [
        {
          index: 0,
          email: 'existing@example.com',
          status: 'existing',
          reason: 'email_unavailable'
        }
      ]
    }),
    bulkInviteTenantMembers: () => assert.fail('No eligible recipients')
  })
  props.show = true
  await vue.nextTick()
  run(
    "form.email = 'existing@example.com'; formElement.value = { reportValidity: () => true }"
  )
  await vue.nextTick()
  await run('submit()')
  assert.equal(run('review.value'), null)
  assert.equal(run('canSubmit.value'), false)
})

test('oversized and malformed CSV imports leave the pasted draft intact', async () => {
  const { props, run } = await component(drawerFile, {
    getTenantGroups: async () => ({ results: [] })
  })
  props.show = true
  await vue.nextTick()
  run("form.email = 'keep@example.com'")
  await run(
    "importCsv({ target: { files: [{ name: 'large.csv', size: 200000, text: async () => '' }] } })"
  )
  assert.equal(run('importError.value'), 'invitations.bulk.fileTooLarge')
  assert.equal(run('form.email'), 'keep@example.com')
  await run(
    "importCsv({ target: { files: [{ name: 'bad.csv', size: 100, text: async () => 'wrong,header' }] } })"
  )
  assert.equal(run('csvSource.value'), '')
  assert.equal(run('importError.value'), 'invitations.bulk.invalidCsv')
  assert.equal(run('form.email'), 'keep@example.com')
})

test('late submission failures cannot overwrite a reopened invitation drawer', async () => {
  let reject
  const { props, run } = await component(drawerFile, {
    getTenantGroups: async () => ({ results: [] }),
    previewTenantInvitations: async () => ({
      confirmation: 'proof',
      counts: { valid: 1 },
      items: [{ index: 0, email: 'old@example.com', status: 'valid' }]
    }),
    bulkInviteTenantMembers: () =>
      new Promise((resolve, fail) => {
        reject = fail
      })
  })
  props.show = true
  await vue.nextTick()
  run(
    "form.email = 'old@example.com'; formElement.value = { reportValidity: () => true }"
  )
  await vue.nextTick()
  await run('submit()')
  const pending = run('submit()')
  props.show = false
  await vue.nextTick()
  props.show = true
  await vue.nextTick()
  run("form.email = 'new@example.com'; error.value = 'current draft'")
  await vue.nextTick()
  reject({ response: { status: 409 } })
  await pending
  assert.equal(run('form.email'), 'new@example.com')
  assert.equal(run('error.value'), '')
  assert.equal(run('conflict.value'), false)
  assert.equal(run('result.value'), null)
})
