import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

const source = (path) =>
  readFile(new URL(`../src/${path}`, import.meta.url), 'utf8')

test('data-heavy admin pages provide mobile cards and desktop tables', async () => {
  const pages = [
    ['admin/pages/LLM/Config.vue', 'llm-config'],
    ['admin/pages/LLM/Usage.vue', 'llm-usage'],
    ['admin/pages/lens/RunObservation.vue', 'run-observation']
  ]

  for (const [path, name] of pages) {
    const contents = await source(path)

    assert.match(contents, new RegExp(`data-testid="mobile-${name}-list"`))
    assert.match(contents, new RegExp(`data-testid="desktop-${name}-table"`))
    assert.match(contents, /md:hidden/)
    assert.match(contents, /hidden[^"\n]*md:/)
  }

  const config = await source('admin/pages/LLM/Config.vue')
  assert.match(config, /data-testid="mobile-llm-config-select-all"/)
})

test('compact controls keep mobile touch targets at least 44 pixels', async () => {
  const [button, rowAction, pagination, header, sidebar, styles] =
    await Promise.all([
      source('components/ui/BaseButton.vue'),
      source('components/ui/RowActionMenu.vue'),
      source('components/ui/PaginationBar.vue'),
      source('admin/layout/AdminHeader.vue'),
      source('admin/layout/AdminSidebar.vue'),
      source('assets/css/admin-prototype.css')
    ])

  assert.match(button, /sm: '[^']*h-11[^']*md:h-8/)
  assert.match(button, /md: '[^']*h-11[^']*md:h-9/)
  assert.match(button, /min-w-11[^']*md:min-w-0/)
  assert.match(rowAction, /h-11 w-11[^"\n]*md:h-8 md:w-8/)
  assert.match(rowAction, /window\.innerWidth < 768 \? 44 : 40/)
  assert.match(pagination, /min-h-11[^"\n]*md:min-h-0/)
  assert.match(pagination, /grid-cols-\[2\.75rem_1fr_2\.75rem\]/)
  assert.match(header, /admin-navigation-toggle/)
  assert.match(
    styles,
    /\.admin-navigation-toggle\s*\{\s*width: 44px;\s*height: 44px;/
  )
  assert.match(
    styles,
    /\.admin-workbench-link\s*\{\s*min-height: 44px;\s*min-width: 44px;/
  )
  assert.match(sidebar, /h-11 w-11[^"\n]*text-ink-400/)
  assert.match(sidebar, /admin-nav-item\s*\{[\s\S]{0,80}min-height: 44px/)
})
