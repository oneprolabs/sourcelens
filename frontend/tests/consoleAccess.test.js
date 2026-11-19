import assert from 'node:assert/strict'
import test from 'node:test'
import { createMemoryHistory, createRouter } from 'vue-router'
import { adminRoutes } from '../src/admin/routes.js'

import {
  getConsoleRedirect,
  getManagementEntry,
  hasFeature
} from '../src/utils/platformAccess.js'

const platform = { platform_role: 'super_admin' }
const admin = { tenant_role: 'tenant_admin' }

test('built-in admin roles can open their console without legacy staff', () => {
  assert.equal(hasFeature(platform, 'admin_console'), true)
  assert.equal(hasFeature(admin, 'admin_console'), true)
  assert.equal(getManagementEntry(platform), '/management/platform/overview')
  assert.equal(getManagementEntry(admin), '/management/overview')
})

test('tenant staff cannot enter the platform console', () => {
  assert.equal(
    getConsoleRedirect({ ...admin, is_staff: true }, { console: 'platform' }),
    '/'
  )
  assert.equal(getConsoleRedirect(platform, { console: 'platform' }), null)
})

test('platform users need active delegation to enter tenant content', () => {
  assert.equal(
    getConsoleRedirect(platform, { console: 'tenant' }),
    '/management/platform/tenants'
  )
  const active = {
    delegation_id: 'support',
    expires_at: '2099-01-01T00:00:00Z'
  }
  assert.equal(
    getConsoleRedirect(platform, { console: 'tenant' }, active),
    null
  )
  assert.equal(
    getConsoleRedirect(
      platform,
      { console: 'tenant', tenantIdentityOnly: true },
      active
    ),
    '/management/lens/assistants'
  )
  assert.equal(
    getConsoleRedirect(
      platform,
      { console: 'tenant' },
      { ...active, expires_at: '2000-01-01T00:00:00Z' }
    ),
    '/management/platform/tenants'
  )
})

test('legacy task URLs redirect to guarded platform pages and retain filters', async () => {
  for (const section of ['list', 'stats', 'settings']) {
    for (const user of [platform, { ...admin, is_staff: true }]) {
      const router = createRouter({
        history: createMemoryHistory(),
        routes: [
          { path: '/', component: {} },
          ...adminRoutes.map((route) => ({ ...route, component: {} }))
        ]
      })
      router.beforeEach((to) => getConsoleRedirect(user, to.meta) || true)
      await router.push(
        `/management/task-management/${section}?execution_id=42#details`
      )
      const current = router.currentRoute.value
      if (user === platform) {
        assert.equal(current.path, `/management/platform/tasks/${section}`)
        assert.equal(current.meta.console, 'platform')
        assert.equal(current.query.execution_id, '42')
        assert.equal(current.hash, '#details')
      } else {
        assert.equal(current.path, '/')
      }
    }
  }
})
