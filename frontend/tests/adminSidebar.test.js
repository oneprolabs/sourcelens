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
    ['/management/task-management/list', 'tasks'],
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
