import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import vm from 'node:vm'
import { collectPaginatedResults } from '../src/api/pagination.js'

const ref = (value) => ({ value })

async function componentFunction(path, name) {
  const source = await readFile(new URL(path, import.meta.url), 'utf8')
  let start = source.indexOf(`function ${name}(`)
  if (source.slice(start - 6, start) === 'async ') start -= 6
  const end = source.indexOf('\n}', start) + 2
  return source.slice(start, end)
}

test('role operators load rules without calling the staff-only channel API', async () => {
  const context = vm.createContext({
    loading: ref(false),
    error: ref(''),
    ready: ref(false),
    catalog: ref({}),
    channels: ref([]),
    options: ref({}),
    page: ref(1),
    pageSize: ref(20),
    total: ref(0),
    rules: ref([]),
    alertsAdminApi: {
      getCatalog: async () => ({ run: ['run_failed'] }),
      getChannels: async () => ({
        results: [
          {
            uuid: 'mail',
            name: 'Email',
            channel_type: 'email',
            is_active: true
          }
        ]
      }),
      getRules: async () => ({
        count: 1,
        results: [{ uuid: 'rule', name: 'Failures' }]
      })
    },
    notificationsAdminApi: {
      getChannels: async () => {
        throw { response: { status: 403 } }
      }
    },
    listAssistants: async () => [],
    listDataSources: async () => [],
    listLensNodes: async () => [],
    managementApi: {
      getAllUsers: async () => [],
      getAllGroups: async () => []
    },
    collectPaginatedResults,
    choices: (rows) => rows,
    alertApiError: () => 'Forbidden'
  })
  for (const name of ['availableScopes', 'loadRules', 'initialize']) {
    vm.runInContext(
      await componentFunction('../src/admin/pages/Alerts/Rules.vue', name),
      context
    )
  }
  await vm.runInContext('initialize()', context)
  assert.equal(context.ready.value, true)
  assert.equal(context.error.value, '')
  assert.equal(context.rules.value[0].uuid, 'rule')
  assert.equal(context.options.value.channels[0].uuid, 'mail')
  assert.equal(context.channels.value[0].name, 'Email')
})

test('publication dialog opens on HTTP when randomUUID is unavailable', async () => {
  const utility = await readFile(
    new URL('../src/utils/uuid.js', import.meta.url),
    'utf8'
  )
  const start = utility.indexOf('function generateUUID(')
  const end = utility.indexOf('\n}', start) + 2
  const context = vm.createContext({
    crypto: {
      getRandomValues: (bytes) => {
        bytes.fill(7)
        return bytes
      }
    },
    form: ref({}),
    topic: ref('release.ready'),
    publishError: ref(''),
    published: ref(false),
    showPublisher: ref(false)
  })
  vm.runInContext(utility.slice(start, end), context)
  vm.runInContext(
    await componentFunction(
      '../src/admin/pages/Alerts/Messages.vue',
      'openPublisher'
    ),
    context
  )
  vm.runInContext('openPublisher()', context)
  assert.equal(context.showPublisher.value, true)
  assert.match(
    context.form.value.idempotency_key,
    /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/
  )
})
