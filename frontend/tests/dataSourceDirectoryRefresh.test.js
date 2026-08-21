import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

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
    /:disabled="refreshingDirectories \|\| !form\.lensnode_uuid"/
  )
  assert.match(contents, /:class="\{ 'animate-spin': refreshingDirectories \}"/)
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
  assert.match(contents, /scanLensNodeDirs\(lensnodeUuid, \[workspacePath\]\)/)
  assert.match(contents, /available_dirs: directories/)
  assert.match(contents, /refreshingDirectories\.value = true/)
  assert.match(
    contents,
    /showError\(extractErrorMessage\(error, t\('lensAdmin\.messages\.loadFailed'\)\)\)/
  )
  assert.match(contents, /refreshingDirectories\.value = false/)
})

test('directory refresh normalizes supported list-dirs response shapes', async () => {
  const contents = await source('pages/lens/DataSources.vue')

  assert.match(contents, /const dirs = result\?\.dirs \?\? result/)
  assert.match(contents, /if \(Array\.isArray\(dirs\)\) return dirs/)
  assert.match(contents, /const workspaceDirs = dirs\[workspacePath\]/)
  assert.match(contents, /return Object\.values\(dirs\)\.flatMap\(/)
})

test('LensNode directory refresh uses the list-dirs endpoint', async () => {
  const contents = await source('api/lens.js')

  assert.match(
    contents,
    /api\.post\(`\/lens\/admin\/lensnodes\/\$\{uuid\}\/list-dirs\/`, \{/
  )
  assert.match(contents, /paths\s+\}\)/)
})
