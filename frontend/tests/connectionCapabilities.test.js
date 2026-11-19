import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import { runInNewContext } from 'node:vm'
import { computed, ref } from 'vue'

const page = await readFile(
  new URL('../src/pages/lens/Connections.vue', import.meta.url),
  'utf8'
)
const labelFunction = page.slice(
  page.indexOf('function connectionHasDatasource('),
  page.indexOf(
    'async function load()',
    page.indexOf('function connectionHasDatasource(')
  )
)

for (const [pluginKey, expected] of [
  ['feishu', ['datasource']],
  ['jira', ['tool']],
  ['github', ['datasource', 'tool']]
]) {
  test(`${pluginKey} labels follow capabilities despite existing bindings`, async () => {
    const manifest = JSON.parse(
      await readFile(
        new URL(`../../plugins/${pluginKey}/plugin.json`, import.meta.url),
        'utf8'
      )
    )
    const labels = runInNewContext(
      `${labelFunction}; connectionUsageLabels(row)`,
      {
        pluginManifests: { value: { [pluginKey]: manifest } },
        t: (key) => key,
        row: {
          plugin_key: pluginKey,
          assistant_count: 60,
          datasource_count: 21
        }
      }
    )
    assert.deepEqual(
      Array.from(labels, (label) => label.key),
      expected
    )
  })
}

test('usage tabs follow capabilities and combine with keyword, plugin and status filters', async () => {
  const manifests = Object.fromEntries(
    await Promise.all(
      ['feishu', 'jira', 'github', 'typesafe'].map(async (key) => [
        key,
        JSON.parse(
          await readFile(
            new URL(`../../plugins/${key}/plugin.json`, import.meta.url),
            'utf8'
          )
        )
      ])
    )
  )
  const connections = ref([
    {
      uuid: 'docs',
      name: 'Company docs',
      plugin_key: 'feishu',
      status: 'active',
      datasource_count: 0
    },
    {
      uuid: 'issues',
      name: 'Company issues',
      plugin_key: 'jira',
      status: 'active',
      datasource_count: 99
    },
    {
      uuid: 'code',
      name: 'Code archive',
      plugin_key: 'github',
      status: 'disabled'
    },
    {
      uuid: 'decision',
      name: 'Decisions',
      plugin_key: 'typesafe',
      status: 'active'
    }
  ])
  const context = {
    computed,
    connections,
    pluginManifests: ref(manifests),
    connectionUsageFilter: ref('all'),
    connectionSearch: ref(''),
    connectionPluginFilter: ref('all'),
    connectionStatusFilter: ref('all'),
    t: (key) => key
  }
  const filters = page.slice(
    page.indexOf('const connectionUsageTabs ='),
    page.indexOf('const detailScopeValues =')
  )
  const state = runInNewContext(
    `${labelFunction}\n${filters}\n;({connectionUsageTabs, filteredConnections})`,
    context
  )
  const ids = () =>
    Array.from(state.filteredConnections.value, (row) => row.uuid)
  assert.deepEqual(
    Array.from(state.connectionUsageTabs.value, (tab) => tab.count),
    [4, 2, 3]
  )
  context.connectionUsageFilter.value = 'datasource'
  assert.deepEqual(ids(), ['docs', 'code'])
  context.connectionUsageFilter.value = 'assistant'
  assert.deepEqual(ids(), ['issues', 'code', 'decision'])
  context.connectionSearch.value = 'COMPANY'
  assert.deepEqual(ids(), ['issues'])
  context.connectionSearch.value = ''
  context.connectionPluginFilter.value = 'github'
  context.connectionStatusFilter.value = 'active'
  assert.deepEqual(ids(), [])
  context.connectionStatusFilter.value = 'disabled'
  assert.deepEqual(ids(), ['code'])
  context.pluginManifests.value = {}
  assert.deepEqual(ids(), [])
})
