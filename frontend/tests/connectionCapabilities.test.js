import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import { runInNewContext } from 'node:vm'

const page = await readFile(
  new URL('../src/pages/lens/Connections.vue', import.meta.url),
  'utf8'
)
const labelFunction = page.slice(
  page.indexOf('function connectionUsageLabels('),
  page.indexOf('async function load()', page.indexOf('function connectionUsageLabels('))
)

for (const [pluginKey, expected] of [
  ['feishu', ['datasource']],
  ['jira', ['tool']],
  ['github', ['datasource', 'tool']]
]) {
  test(`${pluginKey} labels follow capabilities despite existing bindings`, async () => {
    const manifest = JSON.parse(await readFile(
      new URL(`../../plugins/${pluginKey}/plugin.json`, import.meta.url),
      'utf8'
    ))
    const labels = runInNewContext(`${labelFunction}; connectionUsageLabels(row)`, {
      pluginManifests: { value: { [pluginKey]: manifest } },
      t: (key) => key,
      row: { plugin_key: pluginKey, assistant_count: 60, datasource_count: 21 }
    })
    assert.deepEqual(Array.from(labels, (label) => label.key), expected)
  })
}
