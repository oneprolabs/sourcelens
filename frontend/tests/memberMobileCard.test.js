import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import vm from 'node:vm'
import * as vue from 'vue'
import { compileScript, compileTemplate, parse } from '@vue/compiler-sfc'
import { renderToString } from '@vue/server-renderer'

async function loadCard() {
  const source = await readFile(
    new URL(
      '../src/admin/pages/Management/MemberMobileCard.vue',
      import.meta.url
    ),
    'utf8'
  )
  let code = compileScript(parse(source).descriptor, {
    id: 'member-mobile',
    inlineTemplate: true
  }).content
  const context = vm.createContext({
    useI18n: () => ({
      t: (key, values) =>
        key === 'common.selectRow' ? `Select ${values.name}` : key
    })
  })
  code = code
    .replace(/import \{([^}]+)\} from "vue"/g, (_, names) => {
      for (const item of names.split(',')) {
        const [name, alias] = item.trim().split(/\s+as\s+/)
        context[alias || name] = vue[name]
      }
      return ''
    })
    .replace(/^import[^]*? from ['"][^'"]+['"];?\n/gm, '')
  context.RowActionMenu = {
    props: ['actions', 'label'],
    render() {
      return vue.h(
        'button',
        { 'aria-label': this.label },
        this.actions.map((action) => action.label).join(' / ')
      )
    }
  }
  code = code.replace('export default', 'globalThis.component =')
  vm.runInContext(code, context)
  return { component: context.component, menu: context.RowActionMenu }
}

const member = {
  id: 7,
  display_name: '<Long member>',
  username: 'member',
  email: 'very-long-email@example.com',
  role: 'tenant_admin',
  status: 'active',
  groups: [{ id: 1, name: 'Engineering & Operations' }],
  runs_30d: 24,
  last_login: '2026-11-14T01:00:00Z'
}
const base = {
  member,
  selected: true,
  disabled: false,
  roleLabel: 'Tenant admin',
  statusLabel: 'Active',
  lastLogin: '2 hours ago',
  actions: [
    { key: 'detail', label: 'Details' },
    { key: 'toggle', label: 'Disable' }
  ]
}

test('mobile card renders escaped identity, role, groups, runs and last login with accessible actions', async () => {
  const { component } = await loadCard()
  const html = await renderToString(vue.createSSRApp(component, base))
  assert.match(html, /&lt;Long member&gt;/)
  assert.match(html, /very-long-email@example.com/)
  assert.match(html, /Tenant admin/)
  assert.match(html, /Active/)
  assert.match(html, /Engineering &amp; Operations/)
  assert.match(html, /24/)
  assert.match(html, /2 hours ago/)
  assert.match(html, /datetime="2026-11-14T01:00:00Z"/)
  assert.match(html, /type="checkbox"[^>]*checked/)
  assert.match(html, /aria-label="Select &lt;Long member&gt;"/)
  assert.match(html, /Details \/ Disable/)
})

function nodes(node) {
  if (!node || typeof node !== 'object') return []
  return [
    node,
    ...(Array.isArray(node.children) ? node.children.flatMap(nodes) : [])
  ]
}

test('mobile card forwards only the explicit select, detail and action events', async () => {
  const { component, menu } = await loadCard()
  const events = []
  const emit = (...args) => events.push(args)
  const render = component.setup(base, { emit })
  const tree = render({ $emit: emit }, [])
  const all = nodes(tree)
  all.find((node) => node.type === 'input').props.onChange()
  all
    .find((node) => node.type === 'button' && node.props.onClick)
    .props.onClick()
  all.find((node) => node.type === menu).props.onSelect('toggle')
  assert.deepEqual(events, [['select'], ['detail'], ['action', 'toggle']])
})

test('mobile selection honors the shared loading guard and empty metadata remains readable', async () => {
  const { component } = await loadCard()
  const events = []
  const props = {
    ...base,
    disabled: true,
    selected: false,
    member: { ...member, groups: [], last_login: null, runs_30d: null },
    lastLogin: 'Never'
  }
  const render = component.setup(props, {
    emit: (...args) => events.push(args)
  })
  const checkbox = nodes(render({}, [])).find((node) => node.type === 'input')
  assert.equal(checkbox.props.disabled, true)
  checkbox.props.onChange()
  assert.equal(events.length, 0)
  const html = await renderToString(vue.createSSRApp(component, props))
  assert.match(html, /Never/)
  assert.match(html, /—/)
  assert.doesNotMatch(html, /datetime=/)
  assert.doesNotMatch(html, /null|undefined/)
})

test('member page binds cards to the shared selection, details and disable confirmation', async () => {
  const resets = []
  const source = await readFile(
    new URL('../src/admin/pages/Management/Members.vue', import.meta.url),
    'utf8'
  )
  const { descriptor } = parse(source)
  const script = compileScript(descriptor, { id: 'members-page' })
  const template = compileTemplate({
    source: descriptor.template.content,
    filename: 'Members.vue',
    id: 'members-page',
    compilerOptions: { bindingMetadata: script.bindings }
  })
  assert.deepEqual(template.errors, [])
  const renderedTrees = []
  const wrapper = {
    render() {
      const node = vue.h('div', this.$slots.default?.())
      renderedTrees.push(node)
      return node
    }
  }
  const hidden = {
    render() {
      return null
    }
  }
  const cards = []
  const menu = { render: () => null }
  const card = {
    props: [
      'member',
      'selected',
      'disabled',
      'roleLabel',
      'statusLabel',
      'lastLogin',
      'actions'
    ],
    emits: ['select', 'detail', 'action'],
    setup(props, { emit }) {
      cards.push({ props, emit })
      return () => vue.h('article')
    }
  }
  const context = vm.createContext({
    ...vue,
    Plus: {},
    Search: {},
    Eye: {},
    Play: {},
    Ban: {},
    KeyRound: {},
    Trash2: {},
    useI18n: () => ({ t: (key) => key }),
    useToast: () => ({ showSuccess() {}, showError() {} }),
    AdminLayout: wrapper,
    BaseButton: wrapper,
    BaseSelect: wrapper,
    RowActionMenu: menu,
    BaseLoading: hidden,
    PaginationBar: hidden,
    BaseDrawer: hidden,
    BaseModal: hidden,
    MemberBulkDialog: hidden,
    MemberDeleteDialog: hidden,
    TenantInvitationDrawer: hidden,
    MemberMobileCard: card,
    getTenantMembers() {
      assert.fail('Rendering must not issue a second member request')
    },
    getTenantGroups() {},
    resendTenantInvitation() {},
    resetTenantMemberPassword: async (...args) => resets.push(args),
    updateTenantMember() {},
    authApi: {},
    useUserStore() {},
    router: {}
  })
  const removeImports = (code) =>
    code
      .replace(/import \{([^}]+)\} from "vue"/g, (_, names) => {
        for (const item of names.split(',')) {
          const [name, alias] = item.trim().split(/\s+as\s+/)
          context[alias || name] = vue[name]
        }
        return ''
      })
      .replace(/^import[^]*? from ['"][^'"]+['"];?\n/gm, '')
  vm.runInContext(
    removeImports(script.content).replace(
      'export default',
      'globalThis.component ='
    ),
    context
  )
  vm.runInContext(
    removeImports(template.code).replace(
      'export function render',
      'globalThis.render = function'
    ),
    context
  )
  let state
  const app = vue.createSSRApp({
    setup() {
      state = context.component.setup({}, { expose() {} })
      state.members.value = [
        { ...member, version: 'reviewed', can_reset_password: true }
      ]
      state.selectedMembers.value = [{ id: member.id, version: 'reviewed' }]
      const bindings = vue.proxyRefs(state)
      return () => context.render({}, [], {}, bindings, {}, {})
    }
  })
  await renderToString(app)
  const actionCell = renderedTrees
    .flatMap(nodes)
    .find(
      (node) =>
        node.type === 'td' && nodes(node).some((child) => child.type === menu)
    )
  let propagationStopped = false
  actionCell.props.onClick({
    stopPropagation() {
      propagationStopped = true
    }
  })
  assert.equal(propagationStopped, true)
  assert.equal(state.detailMember.value, null)
  assert.equal(cards.length, 1)
  const rendered = cards[0]
  assert.equal(rendered.props.member.id, member.id)
  assert.equal(rendered.props.selected, true)
  assert.equal(rendered.props.disabled, false)
  assert.equal(rendered.props.roleLabel, 'management.tenantAdminRole')
  assert.equal(rendered.props.statusLabel, 'management.memberActive')
  assert.equal(
    rendered.props.actions.find((action) => action.key === 'reset-password')
      .disabled,
    false
  )
  rendered.emit('action', 'reset-password')
  await new Promise((resolve) => setImmediate(resolve))
  assert.deepEqual(resets, [[member.id, 'reviewed']])
  rendered.emit('select')
  assert.equal(state.selectedMembers.value.length, 0)
  rendered.emit('select')
  assert.equal(state.selectedMembers.value[0].version, 'reviewed')
  state.loading.value = true
  rendered.emit('select')
  assert.equal(state.selectedMembers.value.length, 1)
  state.loading.value = false
  rendered.emit('detail')
  assert.equal(state.detailMember.value.id, member.id)
  rendered.emit('action', 'toggle')
  assert.equal(state.disableTarget.value.member.id, member.id)
  assert.equal(state.disableTarget.value.payload.status, 'disabled')
  assert.equal(state.disableSaving.value, false)
})
