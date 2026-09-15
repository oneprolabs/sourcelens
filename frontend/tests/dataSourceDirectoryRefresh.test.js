import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

import {
  directoryRefreshPaths,
  mergeRefreshedDirectories
} from '../src/pages/lens/directoryRefresh.js'

const source = (path) =>
  readFile(new URL(`../src/${path}`, import.meta.url), 'utf8')

test('data source wizard renders an icon-only directory refresh control', async () => {
  const contents = await source('pages/lens/DataSourceFormDrawer.vue')

  assert.match(
    contents,
    /<BaseSelect v-model="syncPolicyMode" class="min-w-0 flex-1">[\s\S]*<BaseButton[\s\S]*RefreshCwIcon/
  )
  assert.match(
    contents,
    /:aria-label="t\('lensAdmin\.datasourceWizard\.refreshDirectories'\)"/
  )
  assert.match(
    contents,
    /workspaceRoot[\s\S]*refreshingDirectories[\s\S]*RefreshCwIcon/
  )
  assert.match(
    contents,
    /:title="\s*t\('lensAdmin\.datasourceWizard\.refreshDirectories'\)\s*"/
  )
  assert.match(
    contents,
    /:disabled="refreshingDirectories \|\| !form\.lensnode_uuid"/
  )
  assert.match(contents, /:class="\{ 'animate-spin': refreshingDirectories \}"/)
  assert.match(contents, /@click="\$emit\('refresh-dirs'\)"/)
  assert.match(contents, /@click="emit\('refresh-dirs'\)"/)
  assert.doesNotMatch(
    contents,
    /t\('lensAdmin\.datasourceWizard\.refreshDirectories'\)\s*<\//
  )
})

test('data source parent refreshes directories for the selected LensNode', async () => {
  const contents = await source('pages/lens/DataSources.vue')

  assert.match(
    contents,
    /import \{[\s\S]*scanLensNodeDirs,[\s\S]*\} from '@\/api\/lens'/
  )
  assert.match(contents, /:refreshing-directories="refreshingDirectories"/)
  assert.match(contents, /@refresh-dirs="refreshDirectories"/)
  assert.match(
    contents,
    /const workspacePath = lensnode\.workspace_path \|\| '\/workspace'/
  )
  assert.match(
    contents,
    /scanLensNodeDirs\(\s*lensnodeUuid,\s*directoryRefreshPaths\(lensnode\)\s*\)/
  )
  assert.match(contents, /mergeRefreshedDirectories\(/)
  assert.match(contents, /available_dirs: directories/)
  assert.match(contents, /refreshingDirectories\.value = true/)
  assert.match(
    contents,
    /showError\(extractErrorMessage\(error, t\('lensAdmin\.messages\.loadFailed'\)\)\)/
  )
  assert.match(contents, /refreshingDirectories\.value = false/)
})

test('directory refresh normalizes supported list-dirs response shapes', async () => {
  const helper = await source('pages/lens/directoryRefresh.js')

  assert.match(helper, /const dirs = result\?\.dirs \?\? result/)
  assert.match(helper, /if \(Array\.isArray\(dirs\)/)
  assert.match(helper, /const rootDirs = dirs\[workspacePath\]/)
})

test('LensNode directory refresh uses the list-dirs endpoint', async () => {
  const contents = await source('api/lens.js')

  assert.match(
    contents,
    /api\.post\(`\/lens\/admin\/lensnodes\/\$\{uuid\}\/list-dirs\/`, \{/
  )
  assert.match(contents, /paths\s+\}\)/)
})

test('directory refresh scans the workspace and known top-level directories', () => {
  const paths = directoryRefreshPaths({
    workspace_path: '/workspace',
    available_dirs: [
      { path: '/workspace/project', name: 'project' },
      { path: '/workspace/archive', name: 'archive' }
    ]
  })

  assert.deepEqual(paths, [
    '/workspace',
    '/workspace/project',
    '/workspace/archive'
  ])
})

test('directory refresh preserves and updates nested directory entries', () => {
  const directories = mergeRefreshedDirectories(
    [
      {
        path: '/workspace/project',
        name: 'project',
        children: [{ path: '/workspace/project/old', name: 'old' }]
      }
    ],
    {
      dirs: {
        '/workspace': [
          { path: '/workspace/project', name: 'project' },
          { path: '/workspace/new', name: 'new' }
        ],
        '/workspace/project': [
          { path: '/workspace/project/new-child', name: 'new-child' }
        ],
        '/workspace/new': []
      }
    },
    '/workspace'
  )

  assert.deepEqual(directories, [
    {
      path: '/workspace/project',
      name: 'project',
      children: [{ path: '/workspace/project/new-child', name: 'new-child' }]
    },
    {
      path: '/workspace/new',
      name: 'new',
      children: []
    }
  ])
})
