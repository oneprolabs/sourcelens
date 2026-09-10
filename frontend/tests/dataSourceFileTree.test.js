import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

import { buildDataSourceFileTree } from '../src/pages/lens/dataSourceFileTree.js'

const source = (path) =>
  readFile(new URL(`../src/${path}`, import.meta.url), 'utf8')

test('datasource files are grouped into sorted directory nodes', () => {
  const files = [
    {
      path: 'team/backend/api.py',
      extension: 'py',
      sync_status: 'synced'
    },
    {
      path: 'README.md',
      extension: 'md',
      sync_status: 'synced'
    },
    {
      path: 'team/frontend/App.vue',
      extension: 'vue',
      sync_status: 'synced'
    }
  ]

  assert.deepEqual(buildDataSourceFileTree(files), [
    {
      type: 'directory',
      name: 'team',
      path: 'team',
      children: [
        {
          type: 'directory',
          name: 'backend',
          path: 'team/backend',
          children: [
            {
              type: 'file',
              name: 'api.py',
              path: 'team/backend/api.py',
              file: files[0]
            }
          ]
        },
        {
          type: 'directory',
          name: 'frontend',
          path: 'team/frontend',
          children: [
            {
              type: 'file',
              name: 'App.vue',
              path: 'team/frontend/App.vue',
              file: files[2]
            }
          ]
        }
      ]
    },
    {
      type: 'file',
      name: 'README.md',
      path: 'README.md',
      file: files[1]
    }
  ])
})

test('datasource file tree normalizes separators and ignores empty paths', () => {
  const file = { path: '\\docs\\guide.md', extension: 'md' }

  assert.deepEqual(buildDataSourceFileTree([file, { path: '' }, null]), [
    {
      type: 'directory',
      name: 'docs',
      path: 'docs',
      children: [
        {
          type: 'file',
          name: 'guide.md',
          path: 'docs/guide.md',
          file
        }
      ]
    }
  ])
})

test('datasource file tree keeps one node for each normalized path', () => {
  const previous = { path: 'docs/guide.md', sync_status: 'missing' }
  const current = { path: 'docs\\guide.md', sync_status: 'synced' }

  const tree = buildDataSourceFileTree([previous, current])

  assert.equal(tree[0].children.length, 1)
  assert.equal(tree[0].children[0].file, current)
})

test('datasource detail renders the paginated file rows as a tree', async () => {
  const drawer = await source('pages/lens/DataSourceDetailDrawer.vue')
  const tree = await source('pages/lens/components/DataSourceFileTree.vue')
  const node = await source('pages/lens/components/DataSourceFileTreeNode.vue')

  assert.match(drawer, /<DataSourceFileTree[\s\S]*:files="files"/)
  assert.match(tree, /role="tree"/)
  assert.match(tree, /buildDataSourceFileTree/)
  assert.match(node, /aria-expanded/)
  assert.match(node, /role="group"/)
  assert.match(node, /sm:hidden/)
  assert.match(node, /class="hidden text-xs[^"]*sm:block"/)
})
