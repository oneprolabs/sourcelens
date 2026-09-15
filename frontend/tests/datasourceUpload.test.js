import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import vm from 'node:vm'

const source = await readFile(
  new URL('../src/pages/lens/DataSourceUploadModal.vue', import.meta.url),
  'utf8'
)

function harness(send = async () => {}) {
  const calls = []
  const events = []
  const context = {
    entries: { value: [] },
    error: { value: '' },
    uploading: { value: false },
    maxBytes: { value: 1024 },
    canSave: { value: true },
    props: { datasource: { uuid: 'selected-datasource' } },
    t: (key) => key,
    extractErrorMessage: (error) => error.message,
    emit: (...args) => events.push(args),
    uploadDataSourceFile: async (...args) => {
      calls.push(args)
      return send(...args)
    }
  }
  vm.createContext(context)
  vm.runInContext(
    `let nextId = 0; ${source.slice(source.indexOf('function addFiles('), source.indexOf('\nwatch('))}`,
    context
  )
  return { context, calls, events }
}

const file = (name, size = 20) => ({ name, size, lastModified: 1 })

test('multiple selected ZIPs remain local until Save and target the selected datasource', async () => {
  const { context, calls } = harness()
  context.addFiles([file('first.zip'), file('second.zip'), file('remove.zip')])
  assert.equal(context.entries.value.length, 3)
  assert.equal(calls.length, 0)
  context.entries.value = context.entries.value.filter(
    (entry) => entry.file.name !== 'remove.zip'
  )
  await context.save()
  assert.deepEqual(
    calls.map(([uuid, item]) => [uuid, item.name]),
    [
      ['selected-datasource', 'first.zip'],
      ['selected-datasource', 'second.zip']
    ]
  )
  assert.ok(
    context.entries.value.every((entry) => entry.status === 'submitted')
  )
})

test('retry submits only failed files and preserves accepted files', async () => {
  let failing = true
  const { context, calls, events } = harness(async (_, item) => {
    if (item.name === 'second.zip' && failing) throw new Error('request failed')
  })
  context.addFiles([file('first.zip'), file('second.zip')])
  await context.save()
  assert.equal(events[0][2], false)
  assert.equal(context.entries.value[1].error, 'request failed')
  failing = false
  await context.save()
  assert.deepEqual(
    calls.map(([, item]) => item.name),
    ['first.zip', 'second.zip', 'second.zip']
  )
  assert.equal(events[1][2], true)
})

test('unsupported, oversized and duplicate files do not enter the upload queue', () => {
  const { context, calls } = harness()
  context.addFiles([
    file('notes.txt'),
    file('large.zip', 2000),
    file('one.zip'),
    file('one.zip')
  ])
  assert.equal(context.entries.value.length, 1)
  assert.equal(context.entries.value[0].file.name, 'one.zip')
  assert.equal(calls.length, 0)
})
