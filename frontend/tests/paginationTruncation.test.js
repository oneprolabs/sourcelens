import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

const readSource = (path) =>
  readFile(new URL(`../src/${path}`, import.meta.url), 'utf8')

test('complete assistant resource reads collect every API page', async () => {
  const source = await readSource('api/lens.js')
  const assistants = source.slice(
    source.indexOf('export async function listAssistants'),
    source.indexOf('export async function getPublicAssistant')
  )
  const lensnodes = source.slice(
    source.indexOf('export async function listLensNodes'),
    source.indexOf('export async function getAdminRuns')
  )
  const skills = source.slice(
    source.indexOf('export async function listSkills'),
    source.indexOf('export async function createSkill')
  )
  const environmentSets = source.slice(
    source.indexOf('export async function listEnvironmentVariableSets'),
    source.indexOf('export async function createEnvironmentVariableSet')
  )
  const mcps = source.slice(
    source.indexOf('export async function listMcpServers'),
    source.indexOf('export async function createMcpServer')
  )

  assert.match(assistants, /collectPaginatedResults/)
  assert.match(lensnodes, /\/lens\/admin\/lensnodes\/available\//)
  assert.match(skills, /collectPaginatedResults/)
  assert.match(environmentSets, /collectPaginatedResults/)
  assert.match(mcps, /collectPaginatedResults/)
})

test('shared Q&A review uses backend pagination metadata', async () => {
  const source = await readSource('admin/pages/lens/ShareReview.vue')

  assert.match(source, /const totalCount = ref\(0\)/)
  assert.match(source, /page: currentPage\.value/)
  assert.match(source, /page_size: pageSize\.value/)
  assert.match(source, /:total="totalCount"/)
  assert.match(source, /goPrevPage[\s\S]*load\(\)/)
  assert.match(source, /goNextPage[\s\S]*load\(\)/)
})

test('shared pagination applies the selected page size before reloading', async () => {
  const source = await readSource('components/ui/PaginationBar.vue')

  assert.match(source, /@update:model-value="updatePageSize"/)
  assert.doesNotMatch(source, /@change="updatePageSize"/)
  assert.match(
    source,
    /function updatePageSize\(value\)[\s\S]*update:pageSize[\s\S]*page-size-change/
  )
})

test('management selectors collect all user and group pages', async () => {
  const [api, groups, users, tasks] = await Promise.all([
    readSource('admin/api/management.js'),
    readSource('admin/pages/Management/Groups.vue'),
    readSource('admin/pages/Management/Users.vue'),
    readSource('admin/pages/TaskManagement/List.vue')
  ])

  assert.match(api, /getAllUsers[\s\S]*collectPaginatedResults/)
  assert.match(api, /getAllGroups[\s\S]*collectPaginatedResults/)
  assert.match(groups, /managementApi\.getAllUsers/)
  assert.match(groups, /const userOptionsLoaded = ref\(false\)/)
  assert.match(
    groups,
    /if \(userOptionsLoaded\.value\)[\s\S]*payload\.user_ids/
  )
  assert.match(users, /managementApi\.getAllGroups/)
  assert.match(tasks, /managementApi\.getAllUsers/)
})
