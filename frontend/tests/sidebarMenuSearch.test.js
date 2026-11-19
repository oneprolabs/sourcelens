import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import vm from 'node:vm'
import { parse as parseTemplate } from '@vue/compiler-dom'
import { parse } from '@vue/compiler-sfc'
import * as vue from 'vue'
import { getTenantMenuSearchItems } from '../src/admin/layout/adminSidebarState.js'

async function component(file, values = {}) {
  const source = await readFile(
    new URL(`../src/admin/layout/${file}`, import.meta.url),
    'utf8'
  )
  const imports = [...source.matchAll(/^import[^]*? from ['"][^'"]+['"]\n/gm)]
  const icons = {}
  for (const [statement] of imports) {
    if (!statement.includes('@lucide/vue')) continue
    for (const name of statement.match(/\{([^]*?)\}/)[1].split(','))
      icons[name.trim()] = name.trim()
  }
  const props = vue.reactive({ items: [], active: true })
  const route = vue.reactive({ path: '/management/platform/overview' })
  const events = []
  const listeners = new Map()
  const scope = vue.effectScope()
  const context = vm.createContext({
    ...vue,
    ...icons,
    BrandLogo: {},
    SidebarMenuSearch: {},
    defineProps: () => props,
    defineEmits:
      () =>
      (...args) =>
        events.push(args),
    useI18n: () => ({ t: (key) => key }),
    useRoute: () => route,
    useIsMobile: () => ({ isMobile: vue.ref(false) }),
    useUiStore: () =>
      vue.reactive({ platformSidebarCollapsedGroups: new Set() }),
    getAdminReturnPath: () => '/',
    onMounted: (callback) => callback(),
    onBeforeUnmount() {},
    window: {
      addEventListener: (type, fn) => listeners.set(type, fn),
      removeEventListener() {}
    },
    ...values
  })
  const script = source
    .split('<script setup>')[1]
    .split('</script>')[0]
    .replace(/^import[^]*? from ['"][^'"]+['"]\n/gm, '')
  scope.run(() => vm.runInContext(script, context))
  return {
    run: (code) => vm.runInContext(code, context),
    props,
    route,
    events,
    listeners,
    stop: () => scope.stop()
  }
}

test('search matches translated labels, groups and paths with whitespace and casing', async () => {
  const state = await component('SidebarMenuSearch.vue')
  state.props.items = [
    {
      label: '能力市场',
      path: '/management/lens/resources/marketplace',
      group: '资源管理'
    },
    { label: '成员管理', path: '/management/users', group: '租户管理' }
  ]
  state.run('query.value = " 能力 市场 "')
  assert.equal(state.run('matches.value[0].path'), state.props.items[0].path)
  assert.equal(state.run('matches.value.length'), 1)
  await vue.nextTick()
  assert.deepEqual(state.events, [['update:searching', true]])
  state.run('query.value = "MARKETPLACE"')
  assert.equal(state.run('matches.value.length'), 1)
  state.run('query.value = "不存在"')
  assert.equal(state.run('matches.value.length'), 0)
  state.run('select()')
  assert.equal(state.run('query.value'), '')
  assert.deepEqual(state.events.at(-1), ['navigate'])
  state.stop()
})

test('search shortcut respects editable fields, modifier keys and hidden mobile sidebar', async () => {
  const state = await component('SidebarMenuSearch.vue')
  let focused = 0
  const input = {
    focus: () => {
      focused++
    }
  }
  const inputRef = state.run('input')
  inputRef.value = input
  const listener = state.listeners.get('keydown')
  const key = (values = {}) => ({
    key: '/',
    preventDefault() {},
    target: { closest: () => null },
    ...values
  })
  listener(key())
  assert.equal(focused, 1)
  listener(key({ target: { closest: () => ({}) } }))
  listener(key({ ctrlKey: true }))
  listener(key({ defaultPrevented: true }))
  assert.equal(focused, 1)
  state.props.active = false
  listener(key())
  assert.equal(focused, 1)
  state.stop()
})

test('arrow keys focus results, Enter selects, and Escape clears and returns focus', async () => {
  const state = await component('SidebarMenuSearch.vue')
  let inputFocus = 0,
    resultFocus = 0,
    clicked = 0
  state.run('input').value = { focus: () => inputFocus++ }
  state.run('results').value = {
    querySelector: () => ({
      focus: () => resultFocus++,
      click: () => clicked++
    })
  }
  state.run('query.value = "market"')
  const event = (key) => ({ key, preventDefault() {}, stopPropagation() {} })
  state.run('handleInputKeydown')(event('ArrowDown'))
  state.run('handleInputKeydown')(event('Enter'))
  assert.equal(resultFocus, 1)
  assert.equal(clicked, 1)
  state.run('handleInputKeydown')(event('Escape'))
  assert.equal(state.run('query.value'), '')
  assert.equal(inputFocus, 1)
  state.stop()
})

test('platform groups toggle independently and routing reopens only the owning group', async () => {
  const state = await component('PlatformSidebar.vue')
  state.run(
    'toggleGroup("management.tenantManagement"); toggleGroup("catalog.officialDirectory")'
  )
  assert.equal(state.run('collapsedGroups.value.size'), 2)
  state.route.path = '/management/platform/catalog/skills'
  await vue.nextTick()
  assert.equal(
    state.run('collapsedGroups.value.has("catalog.officialDirectory")'),
    false
  )
  assert.equal(
    state.run('collapsedGroups.value.has("management.tenantManagement")'),
    true
  )
  assert.ok(
    state
      .run('searchItems.value')
      .some((item) => item.path === '/management/platform/tenants')
  )
  assert.ok(
    state
      .run('searchItems.value')
      .every((item) => item.path.startsWith('/management/platform/'))
  )
  state.stop()
})

test('tenant search catalog stays aligned with the actual template destinations and labels', async () => {
  const source = await readFile(
    new URL('../src/admin/layout/AdminSidebar.vue', import.meta.url),
    'utf8'
  )
  const ast = parseTemplate(parse(source).descriptor.template.content)
  const destinations = new Map()
  const walk = (node) => {
    if (node.tag === 'router-link') {
      const path = node.props.find((prop) => prop.name === 'to')?.value?.content
      if (path) destinations.set(path, node.loc.source)
    }
    for (const child of node.children || []) walk(child)
  }
  walk(ast)
  for (const item of getTenantMenuSearchItems({
    adminConsole: true,
    evaluations: true
  })) {
    if (['/management/overview', '/management/setup'].includes(item.path))
      continue
    assert.ok(destinations.has(item.path), item.path)
    assert.ok(destinations.get(item.path).includes(item.label), item.label)
  }
})

test('platform collapse preferences survive sidebar remounting while the current group remains open', async () => {
  const store = vue.reactive({ platformSidebarCollapsedGroups: new Set() })
  const values = { useUiStore: () => store }
  const first = await component('PlatformSidebar.vue', values)
  first.run('toggleGroup("catalog.officialDirectory")')
  first.stop()
  const second = await component('PlatformSidebar.vue', values)
  assert.equal(
    second.run('collapsedGroups.value.has("catalog.officialDirectory")'),
    true
  )
  second.route.path = '/management/platform/catalog/mcp'
  await vue.nextTick()
  assert.equal(
    second.run('collapsedGroups.value.has("catalog.officialDirectory")'),
    false
  )
  second.stop()
})
