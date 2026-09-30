import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import vm from 'node:vm'
import { normalizeGitHubRepositoryAddress } from '../src/utils/githubRepository.js'

const source = await readFile(
  new URL('../src/pages/lens/DataSources.vue', import.meta.url),
  'utf8'
)
const functions = [
  source.slice(
    source.indexOf('function resetDatasourceConnectionResult()'),
    source.indexOf('function validateFeishuResources()')
  ),
  source.slice(
    source.indexOf('async function testDatasourceConnection()'),
    source.indexOf('function githubDatasourceAccessError(')
  ),
  source.slice(
    source.indexOf('async function loadPluginResourceOptions('),
    source.indexOf('function shouldUseDatasourceCredential()')
  )
].join('\n')

function setup(overrides = {}) {
  const context = vm.createContext({
    form: {
      value: {
        plugin_key: 'github',
        source_type: 'plugin:github',
        connection_uuid: 'public-connection'
      }
    },
    datasourceConfig: {
      value: { repositories: ['https://github.com/owner/repo.git'] }
    },
    datasourceConnectionRequestId: 0,
    pluginResourceRequestId: 0,
    datasourceConnectionBaseSignature: { value: '' },
    datasourceConnectionResult: { value: null },
    testingDatasourceConnection: { value: false },
    loadingPluginResourceOptions: { value: '' },
    feishuValidation: { reset() {} },
    isPluginSourceType: () => true,
    datasourceConnectionSignature: () => 'signature',
    buildPluginDatasourceConfig: () => ({
      repositories: ['https://github.com/owner/repo.git']
    }),
    getConnectionResources: async () => ({
      resources: { repositories: { items: [] } }
    }),
    validateConnectionDatasource: async () => ({
      valid: true,
      resources: [{ repository: 'owner/repo' }]
    }),
    ...overrides
  })
  vm.runInContext(functions, context)
  return context
}

test('discovering repositories does not approve an unchecked GitHub datasource', async () => {
  const context = setup({ datasourceConfig: { value: { repositories: [] } } })
  await context.testDatasourceConnection()
  assert.equal(context.datasourceConnectionResult.value.status, 'unchecked')
})

test('manually entered repositories must pass the access check', async () => {
  let request
  const context = setup({
    validateConnectionDatasource: async (connection, payload) => {
      request = { connection, payload }
      return {
        valid: true,
        resources: [{ repository: 'owner/repo', default_branch: 'main' }]
      }
    }
  })
  await context.testDatasourceConnection()
  assert.equal(request.connection, 'public-connection')
  assert.equal(
    request.payload.datasource_config.repositories[0],
    'https://github.com/owner/repo.git'
  )
  assert.equal(context.datasourceConnectionResult.value.status, 'success')
  assert.equal(
    context.datasourceConnectionResult.value.details.validatedRepositories[0]
      .default_branch,
    'main'
  )
})

test('changing the repository discards a late successful access check', async () => {
  let finish
  const context = setup({
    validateConnectionDatasource: () =>
      new Promise((resolve) => {
        finish = resolve
      })
  })
  const pending = context.testDatasourceConnection()
  context.datasourceConfig.value.repositories = ['owner/private']
  context.resetDatasourceConnectionResult()
  finish({ valid: true, resources: [] })
  await pending
  assert.equal(context.datasourceConnectionResult.value.status, 'unchecked')
})

test('loading branch options cannot approve a GitHub datasource', async () => {
  const context = setup()
  context.resetDatasourceConnectionResult()
  await context.loadPluginResourceOptions({
    resource: 'branches',
    selectedValues: { repositories: ['owner/repo'] }
  })
  assert.equal(context.datasourceConnectionResult.value.status, 'unchecked')
})

test('repository URLs become owner/name without altering unsafe input for backend validation', () => {
  assert.equal(
    normalizeGitHubRepositoryAddress(
      ' https://github.com/octocat/Hello-World.git '
    ),
    'octocat/Hello-World'
  )
  assert.equal(
    normalizeGitHubRepositoryAddress('https://github.com/octocat/Hello-World/'),
    'octocat/Hello-World'
  )
  assert.equal(
    normalizeGitHubRepositoryAddress('octocat/Hello-World'),
    'octocat/Hello-World'
  )
  for (const value of [
    'https://example.com/owner/repo',
    'https://github.com/owner/repo/tree/main',
    'https://user@github.com/owner/repo'
  ]) {
    assert.equal(normalizeGitHubRepositoryAddress(value), value)
  }
})
