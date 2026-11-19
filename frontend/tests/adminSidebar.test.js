import assert from 'node:assert/strict'
import test from 'node:test'

import {
  getAdminSidebarMenu,
  toggleAdminSidebarMenu
} from '../src/admin/layout/adminSidebarState.js'

test('admin routes select their owning sidebar menu', () => {
  const routes = [
    ['/management/lens/runs/42', 'lens'],
    ['/management/lens/datasources', 'data'],
    ['/management/lens/resources/skills', 'data'],
    ['/management/users/42', 'users'],
    ['/management/groups', 'users'],
    ['/management/llm/config', 'llm'],
    ['/management/platform/tasks/list', 'platform'],
    ['/management/notifier/settings', 'notifications'],
    ['/management/alerts/events', 'notifications']
  ]

  for (const [path, expectedMenu] of routes) {
    assert.equal(getAdminSidebarMenu(path), expectedMenu)
  }
})

test('standalone routes do not belong to an accordion menu', () => {
  assert.equal(getAdminSidebarMenu('/management'), null)
  assert.equal(getAdminSidebarMenu('/'), null)
})

test('parent menu toggles preserve other expanded menus', () => {
  assert.deepEqual(toggleAdminSidebarMenu([], 'lens'), ['lens'])
  assert.deepEqual(toggleAdminSidebarMenu(['lens'], 'data'), ['lens', 'data'])
  assert.deepEqual(toggleAdminSidebarMenu(['lens', 'data'], 'data'), ['lens'])
})

const { getTenantMenuSearchItems } = await import(
  '../src/admin/layout/adminSidebarState.js'
)

test('tenant search includes collapsed menu destinations and applies delegation and feature visibility', () => {
  const paths = (options) =>
    getTenantMenuSearchItems(options).map((item) => item.path)
  assert.deepEqual(paths({ adminConsole: false, evaluations: true }), [])
  const ordinary = paths({ adminConsole: true, evaluations: false })
  assert.ok(ordinary.includes('/management/users'))
  assert.ok(ordinary.includes('/management/lens/resources/marketplace'))
  assert.ok(!ordinary.includes('/management/lens/evaluation'))
  const delegated = paths({
    adminConsole: true,
    evaluations: true,
    delegated: true
  })
  for (const path of [
    '/management/users',
    '/management/groups',
    '/management/api-keys',
    '/management/tenant'
  ]) {
    assert.ok(!delegated.includes(path))
  }
  assert.ok(delegated.includes('/management/lens/evaluation'))
  assert.ok(
    delegated.every((path) => !path.startsWith('/management/platform/'))
  )
})
