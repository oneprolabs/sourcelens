import assert from 'node:assert/strict'
import test from 'node:test'

import { buildPluginGitPathCheckConfig } from '../src/pages/lens/dataSourcePathCheck.js'

test('GitHub path checks describe all selected repositories', () => {
  const result = buildPluginGitPathCheckConfig({
    pluginKey: 'github',
    endpoint: 'https://github.com/',
    datasourceConfig: {
      repositories: ['oneprolabs/sourcelens', 'oneprolabs/hyperbdr']
    }
  })

  assert.deepEqual(result, {
    repositories: [
      {
        repo_url: 'https://github.com/oneprolabs/sourcelens.git',
        target_subdir: 'oneprolabs/sourcelens',
        enabled: true
      },
      {
        repo_url: 'https://github.com/oneprolabs/hyperbdr.git',
        target_subdir: 'oneprolabs/hyperbdr',
        enabled: true
      }
    ]
  })
})

test('GitLab path checks normalize a legacy single project', () => {
  const result = buildPluginGitPathCheckConfig({
    pluginKey: 'gitlab',
    endpoint: 'https://gitlab.example.com',
    datasourceConfig: { project: 'group/repo' }
  })

  assert.deepEqual(result.repositories, [
    {
      repo_url: 'https://gitlab.example.com/group/repo.git',
      target_subdir: 'group/repo',
      enabled: true
    }
  ])
})

test('non-Git plugins do not add repository path semantics', () => {
  assert.deepEqual(
    buildPluginGitPathCheckConfig({
      pluginKey: 'feishu',
      endpoint: 'https://open.feishu.cn',
      datasourceConfig: { resources: ['doc'] }
    }),
    {}
  )
})
