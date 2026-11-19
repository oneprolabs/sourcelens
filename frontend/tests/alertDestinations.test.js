import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import vm from 'node:vm'
import { compileScript, parse } from '@vue/compiler-sfc'
import { renderToString } from '@vue/server-renderer'
import * as vue from 'vue'

const source = await readFile(
  new URL('../src/admin/pages/Alerts/AlertDestinations.vue', import.meta.url), 'utf8'
)
const { descriptor } = parse(source)
const compiled = compileScript(descriptor, { id: 'destinations-test', inlineTemplate: true }).content
const helpers = {}
const script = compiled
  .replace(/import \{([^}]+)\} from "vue"/g, (_, names) => {
    for (const item of names.split(',')) {
      const [name, alias] = item.trim().split(/\s+as\s+/)
      helpers[alias || name] = vue[name]
    }
    return ''
  })
  .replace(/^import .*$/gm, '')
  .replace('export default', 'globalThis.component =')

test('empty destination editor renders the channel link for its notification scope', async () => {
  for (const [path, destination] of [
    ['/management/platform/notifications/rules', '/management/platform/notifications/channels'],
    ['/management/alerts/rules', '/management/notifier/channels']
  ]) {
    const context = vm.createContext({
      ...helpers,
      useRoute: () => ({ path }),
      useI18n: () => ({ t: key => key, locale: vue.ref('en-US') }),
      BaseButton: { render: () => null }
    })
    vm.runInContext(script, context)
    const app = vue.createSSRApp(context.component, { modelValue: [], channels: [], users: [], groups: [] })
    app.component('router-link', {
      props: ['to'],
      render() { return vue.h('a', { href: this.to }, this.$slots.default()) }
    })
    const html = await renderToString(app)
    assert.ok(html.includes(`href="${destination}"`))
  }
})
