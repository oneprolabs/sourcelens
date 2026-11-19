import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import vm from 'node:vm'

async function handlerContext(api = {}) {
  const source = await readFile(new URL('../src/admin/pages/Notifications/Channels.vue', import.meta.url), 'utf8')
  const start = source.indexOf('async function handleRowAction(')
  const end = source.indexOf('\n}', start) + 2
  const calls = []
  const context = vm.createContext({
    mutating: { value: false }, notificationsAdminApi: api,
    openEditModal: row => calls.push(['edit', row.uuid]),
    confirmDelete: row => calls.push(['delete', row.uuid]),
    loadList: async () => calls.push(['reload']),
    showSuccess: message => calls.push(['success', message]),
    showError: message => calls.push(['error', message]),
    extractErrorMessage: error => error.message,
    t: key => key
  })
  vm.runInContext(source.slice(start, end), context)
  return { context, calls }
}

test('channel row actions accept the menu string key for edit and delete', async () => {
  const { context, calls } = await handlerContext()
  await vm.runInContext("handleRowAction('edit', { uuid: 'owned' })", context)
  await vm.runInContext("handleRowAction('delete', { uuid: 'owned' })", context)
  assert.deepEqual(calls, [['edit', 'owned'], ['delete', 'owned']])
})

test('channel toggle changes only active status and refreshes owned channels', async () => {
  let request
  const { context, calls } = await handlerContext({
    putChannel: async (uuid, body) => { request = JSON.parse(JSON.stringify({ uuid, body })) }
  })
  await vm.runInContext("handleRowAction('toggle', { uuid: 'owned', is_active: true })", context)
  assert.deepEqual(request, { uuid: 'owned', body: { is_active: false } })
  assert.deepEqual(calls, [['reload']])
  assert.equal(context.mutating.value, false)
})

test('saved-channel test displays provider rejection and unlocks actions', async () => {
  const { context, calls } = await handlerContext({
    testChannel: async uuid => ({ success: false, error: `Disabled: ${uuid}` })
  })
  await vm.runInContext("handleRowAction('test', { uuid: 'owned' })", context)
  assert.deepEqual(calls, [['error', 'Disabled: owned']])
  assert.equal(context.mutating.value, false)
})
