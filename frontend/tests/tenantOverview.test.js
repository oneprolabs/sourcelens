import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import vm from 'node:vm'
import { computed, nextTick, ref } from 'vue'

async function setup(file, extras = {}) {
  const source = await readFile(
    new URL(`../src/admin/pages/Management/${file}`, import.meta.url),
    'utf8'
  )
  const script = source
    .split('<script setup>')[1]
    .split('</script>')[0]
    .replace(/^import[\s\S]*?from ['"][^'"]+['"]\n/gm, '')
  const context = vm.createContext({
    computed,
    ref,
    nextTick,
    Intl,
    Date,
    onMounted() {},
    defineAsyncComponent() {},
    document: { getElementById: () => null },
    useI18n: () => ({ t: (key) => key, locale: ref('en') }),
    useUserStore: () => ({ delegation: null }),
    ...extras
  })
  vm.runInContext(script, context)
  return (code) => vm.runInContext(code, context)
}

test('setup requires a knowledge source for knowledge assistants and skips it for general chat', async () => {
  const run = await setup('TenantSetup.vue')
  run(
    'overview.value = { setup: { model_configured: true, assistant_configured: true, knowledge_required: false }, member_count: 1 }'
  )
  assert.equal(
    run("stages.value.find(row => row.key === 'datasource').state"),
    'skipped'
  )
  assert.equal(run('completedRequired.value'), 2)
  assert.equal(run('nextStage.value'), '')
  run('overview.value.setup.knowledge_required = true')
  assert.equal(run('requiredCount.value'), 3)
  assert.equal(
    run("stages.value.find(row => row.key === 'datasource').state"),
    'pending'
  )
  assert.equal(run('nextStage.value'), 'datasource')
})

test('overview preserves waiting and disabled sources without treating them as healthy', async () => {
  const run = await setup('TenantOverview.vue', {
    ChartJS: { register() {} },
    BarElement: {},
    CategoryScale: {},
    LinearScale: {},
    Tooltip: {},
    CircleCheck: 'check',
    TriangleAlert: 'warning',
    RefreshCw: 'refresh'
  })
  run(
    'overview.value = { datasource_health: { synced: 3, running: 1, failed: 2, waiting: 4, disabled: 5 } }'
  )
  assert.deepEqual(
    JSON.parse(
      run(
        'JSON.stringify(sourceHealthRows.value.map(row => [row.state, row.count]))'
      )
    ),
    [
      ['synced', 3],
      ['running', 1],
      ['failed', 2],
      ['waiting', 4],
      ['disabled', 5]
    ]
  )
  run(
    'overview.value.datasource_health = { synced: 0, running: 0, failed: 0, waiting: 0, disabled: 0 }'
  )
  assert.equal(run('sourceHealthRows.value.length'), 3)
})

test('readonly delegation cannot open billable or mutating setup editors', async () => {
  const run = await setup('TenantSetup.vue', {
    useUserStore: () => ({ delegation: { mode: 'readonly' } })
  })
  run("openEditor('model')")
  assert.equal(run('editor.value'), '')
  assert.equal(run('readonly.value'), true)
})

test('failed setup refresh keeps existing progress available and shows a retryable error', async () => {
  const run = await setup('TenantSetup.vue', {
    getTenantOverview: async () => {
      throw new Error('Temporarily unavailable')
    }
  })
  run('overview.value = { member_count: 12 }')
  await run('load()')
  assert.equal(run('overview.value.member_count'), 12)
  assert.equal(run('error.value'), 'Temporarily unavailable')
  assert.equal(run('loading.value'), false)
})

test('successful in-page save refreshes authoritative setup progress', async () => {
  const run = await setup('TenantSetup.vue', {
    getTenantOverview: async () => ({
      setup: { model_configured: true, complete: false },
      member_count: 1
    })
  })
  run("editor.value = 'model'")
  await run('saved()')
  assert.equal(run('overview.value.setup.model_configured'), true)
  assert.equal(run('editor.value'), '')
  run('closeEditor()')
  assert.equal(run('editor.value'), '')
})

test('setup invites members in place and refreshes only after confirmed delivery', async () => {
  let refreshes = 0
  const run = await setup('TenantSetup.vue', {
    getTenantOverview: async () => {
      refreshes++
      return { setup: {}, member_count: 2 }
    }
  })
  run("openEditor('members')")
  assert.equal(run('editor.value'), 'members')
  assert.equal(refreshes, 0)
  await run('saved()')
  assert.equal(refreshes, 1)
  assert.equal(run('editor.value'), '')
  assert.equal(run('overview.value.member_count'), 2)
})

test('operational delegation cannot open the setup invitation drawer', async () => {
  const run = await setup('TenantSetup.vue', {
    useUserStore: () => ({ delegation: { mode: 'operational' } })
  })
  run("openEditor('members')")
  assert.equal(run('editor.value'), '')
})

test('save and continue waits for real progress before opening the next editor', async () => {
  let resolveOverview
  const run = await setup('TenantSetup.vue', {
    getTenantOverview: () =>
      new Promise((resolve) => {
        resolveOverview = resolve
      })
  })
  run("openEditor('model')")
  const saving = run('saved({ advance: true })')
  run('closeEditor()')
  assert.equal(run('editor.value'), 'model')
  assert.equal(run('finishing.value'), true)
  run("openEditor('assistant')")
  assert.equal(run('editor.value'), 'model')
  resolveOverview({ setup: { model_configured: true } })
  await saving
  assert.equal(run('editor.value'), 'datasource')
  assert.equal(run('overview.value.setup.model_configured'), true)
  assert.equal(run('finishing.value'), false)
})

test('failed progress refresh does not advance or fabricate completion', async () => {
  const run = await setup('TenantSetup.vue', {
    getTenantOverview: async () => {
      throw new Error('Unavailable')
    }
  })
  run(
    "editor.value = 'datasource'; overview.value = { setup: { datasource_configured: false } }"
  )
  await run('saved({ advance: true })')
  assert.equal(run('editor.value'), '')
  assert.equal(run('overview.value.setup.datasource_configured'), false)
  assert.equal(run('error.value'), 'Unavailable')
})

test('knowledge can be skipped only when existing assistants do not require it', async () => {
  const run = await setup('TenantSetup.vue')
  run('skipDatasource()')
  assert.equal(run('editor.value'), '')
  run('overview.value = { setup: { knowledge_required: true } }')
  run('skipDatasource()')
  assert.equal(run('editor.value'), '')
  run('overview.value.setup.knowledge_required = false')
  run('skipDatasource()')
  assert.equal(run('editor.value'), 'assistant')
})

test('readonly delegation and pending progress refresh cannot skip the knowledge stage', async () => {
  const readonly = await setup('TenantSetup.vue', {
    useUserStore: () => ({ delegation: { mode: 'readonly' } })
  })
  readonly(
    'overview.value = { setup: { knowledge_required: false } }; skipDatasource()'
  )
  assert.equal(readonly('editor.value'), '')
  const run = await setup('TenantSetup.vue')
  run(
    "overview.value = { setup: { knowledge_required: false } }; editor.value = 'datasource'; finishing.value = true; skipDatasource()"
  )
  assert.equal(run('editor.value'), 'datasource')
})

test('saving the assistant continues to the optional members stage without creating another resource', async () => {
  const run = await setup('TenantSetup.vue', {
    getTenantOverview: async () => ({ setup: { complete: true } })
  })
  run("editor.value = 'assistant'")
  await run('saved({ advance: true })')
  assert.equal(run('editor.value'), '')
  assert.equal(run('activeStage.value'), 'members')
})

test('connection save and continue refreshes the summary and opens the knowledge editor', async () => {
  const run = await setup('TenantSetup.vue', {
    getTenantOverview: async () => ({
      setup: {
        connection_count: 1,
        connections: [{ uuid: 'new', name: 'Connection' }]
      }
    })
  })
  run("openConnection('new')")
  assert.equal(run('editor.value'), 'connection')
  assert.equal(run('activeStage.value'), 'datasource')
  assert.equal(run('selectedConnectionUuid.value'), 'new')
  await run('saved({ advance: true })')
  assert.equal(run('editor.value'), 'datasource')
  assert.equal(run('overview.value.setup.connection_count'), 1)
})

test('readonly delegation cannot open connection editors', async () => {
  const run = await setup('TenantSetup.vue', {
    useUserStore: () => ({ delegation: { mode: 'readonly' } })
  })
  run("openConnection('existing')")
  assert.equal(run('editor.value'), '')
  assert.equal(run('selectedConnectionUuid.value'), '')
})
