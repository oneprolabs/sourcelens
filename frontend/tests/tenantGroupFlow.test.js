import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import vm from 'node:vm'
import * as vue from 'vue'

async function component(name, overrides = {}) {
  const source = await readFile(
    new URL(`../src/admin/pages/Management/${name}.vue`, import.meta.url),
    'utf8'
  )
  const script = source
    .split('<script setup>')[1]
    .split('</script>')[0]
    .replace(/^import[^]*? from ['"][^'"]+['"]\n/gm, '')
  const props = vue.reactive({ targets: [] })
  const events = []
  const context = vm.createContext({
    ...vue,
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
    getTenantGroups: async () => ({ results: [] }),
    getTenantMembers: async () => ({ results: [] }),
    ...overrides
  })
  vm.runInContext(script, context)
  return { props, events, run: (code) => vm.runInContext(code, context) }
}
const flush = () => new Promise((resolve) => setImmediate(resolve))
const plain = (value) => JSON.parse(JSON.stringify(value))

test('group delete submits the frozen signed review once and blocks dismissal in flight', async () => {
  let release
  const calls = []
  const s = await component('GroupDeleteDialog', {
    deleteTenantGroups: async (payload) => {
      calls.push(plain(payload))
      if (payload.preview)
        return {
          groups: [{ uuid: 'one', skip_reason: '' }],
          confirmation: 'signed'
        }
      return new Promise((resolve) => {
        release = resolve
      })
    }
  })
  s.props.targets = [{ uuid: 'one', version: 'v1' }]
  await vue.nextTick()
  await flush()
  s.props.targets[0].version = 'mutated'
  const pending = s.run('submit()')
  await s.run('submit()')
  s.run('close()')
  assert.equal(s.events.length, 0)
  assert.deepEqual(calls[1], {
    groups: [{ uuid: 'one', version: 'v1' }],
    preview: false,
    confirmation: 'signed'
  })
  release({ deleted: 1, deleted_uuids: ['one'], skipped_items: [] })
  await pending
  assert.equal(s.events[0][0], 'applied')
})

test('conflicting group deletion requires rereview of the same IDs and fresh versions', async () => {
  const calls = []
  const s = await component('GroupDeleteDialog', {
    getTenantGroups: async () => ({
      results: [
        { uuid: 'one', version: 'v2' },
        { uuid: 'two', version: 'other' }
      ]
    }),
    deleteTenantGroups: async (payload) => {
      calls.push(plain(payload))
      if (!payload.preview) throw { response: { status: 409 } }
      return {
        groups: [{ uuid: 'one', skip_reason: '' }],
        confirmation: 'signed'
      }
    }
  })
  s.props.targets = [{ uuid: 'one', version: 'v1' }]
  await vue.nextTick()
  await flush()
  await s.run('submit()')
  assert.equal(s.run('canDelete.value'), false)
  await s.run('submit()')
  assert.equal(calls.length, 2)
  await s.run('load(true)')
  assert.deepEqual(calls[2].groups, [{ uuid: 'one', version: 'v2' }])
  assert.equal(s.run('canDelete.value'), true)
})

test('late group previews cannot populate a new selection and foreign-only selections cannot commit', async () => {
  const releases = []
  const s = await component('GroupDeleteDialog', {
    deleteTenantGroups: () => new Promise((resolve) => releases.push(resolve))
  })
  s.props.targets = [{ uuid: 'old', version: 'v1' }]
  await vue.nextTick()
  s.props.targets = [{ uuid: 'new', version: 'v2' }]
  await vue.nextTick()
  releases[0]({
    groups: [{ uuid: 'old', skip_reason: '' }],
    confirmation: 'old'
  })
  await flush()
  assert.equal(s.run('review.value'), null)
  releases[1]({
    groups: [{ uuid: 'new', skip_reason: 'foreign_associations' }],
    confirmation: 'new'
  })
  await flush()
  assert.equal(s.run('canDelete.value'), false)
})

test('late detail failures cannot overwrite another group and member mutations retain their target', async () => {
  let rejectOld, releaseMutation
  const calls = []
  const s = await component('TenantGroups', {
    getTenantGroup: (uuid) =>
      uuid === 'old'
        ? new Promise((_, reject) => {
            rejectOld = reject
          })
        : Promise.resolve({ uuid, members: [] }),
    addTenantGroupMember: (uuid, id) => {
      calls.push([uuid, id])
      return new Promise((resolve) => {
        releaseMutation = resolve
      })
    }
  })
  const old = s.run("openDetail({ uuid: 'old', name: 'Old' })")
  await s.run("openDetail({ uuid: 'new', name: 'New' })")
  rejectOld({ response: { data: { detail: 'late failure' } } })
  await old
  assert.equal(s.run('detailError.value'), '')
  s.run("memberToAdd.value = '7'")
  const pending = s.run('addMember()')
  await s.run('addMember()')
  await s.run("openDetail({ uuid: 'other', name: 'Other' })")
  s.run('closeDetail()')
  assert.equal(s.run('detailGroup.value.uuid'), 'new')
  assert.deepEqual(calls, [['new', 7]])
  releaseMutation({})
  await pending
  assert.equal(s.run('detailSaving.value'), false)
})

test('group pagination selects only the visible page and clears selection when the page changes', async () => {
  const s = await component('TenantGroups')
  s.run(
    'groups.value = Array.from({length:45}, (_,i) => ({uuid:String(i)})); toggleAll()'
  )
  assert.equal(s.run('selectedGroups.value.length'), 20)
  s.run('currentPage.value = 3')
  await vue.nextTick()
  assert.equal(s.run('selectedGroups.value.length'), 0)
  s.run('toggleAll()')
  assert.deepEqual(plain(s.run('selectedGroups.value.map(row => row.uuid)')), [
    '40',
    '41',
    '42',
    '43',
    '44'
  ])
})
