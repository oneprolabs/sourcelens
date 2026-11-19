import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

const source = (path) =>
  readFile(new URL(`../src/${path}`, import.meta.url), 'utf8')

test('admin shell follows the prototype navigation and header dimensions', async () => {
  const [header, sidebar, styles] = await Promise.all([
    source('admin/layout/AdminHeader.vue'),
    source('admin/layout/AdminSidebar.vue'),
    source('assets/css/admin-prototype.css')
  ])
  assert.match(header, /layout-admin-header/)
  assert.match(sidebar, /layout-admin-sidebar/)
  assert.match(styles, /width: 282px/)
  assert.match(styles, /height: 48px/)
  assert.match(styles, /#f4f7fd/)
  assert.match(styles, /#6384f4/)
})

test('statistics pages share the compact surface and typography contract', async () => {
  const [styles, ...pages] = await Promise.all([
    source('assets/css/main.css'),
    source('admin/pages/LLM/Stats.vue'),
    source('admin/pages/TaskManagement/Stats.vue'),
    source('admin/pages/Notifications/Stats.vue')
  ])

  assert.match(styles, /\.admin-stats-page/)
  assert.match(styles, /\.admin-filter-toolbar/)
  assert.match(styles, /\.admin-metric-value/)
  for (const contents of pages) {
    assert.match(contents, /admin-stats-page/)
    assert.match(contents, /admin-filter-toolbar/)
  }
})
