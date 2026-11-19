import assert from 'node:assert/strict'
import test from 'node:test'

import {
  dataSourceBranch,
  dataSourceRepositories,
  dataSourceRepository,
  dataSourceRepositoryUrl,
  isOrganizationDataSource,
  latestUploadTasksByFilename
} from '../src/pages/lens/datasourceHelpers.js'

test('plugin repository fields are used as the datasource resource', () => {
  assert.equal(
    dataSourceRepository({
      plugin_key: 'github',
      datasource_config: { repository: 'oneprolabs/devify' },
      config: {}
    }),
    'oneprolabs/devify'
  )
  assert.equal(
    dataSourceRepository({
      plugin_key: 'gitlab',
      datasource_config: { project: 'hypermotion/mass' },
      config: {}
    }),
    'hypermotion/mass'
  )
})

test('plugin repository URLs include the Connection endpoint', () => {
  assert.equal(
    dataSourceRepositoryUrl(
      {
        plugin_key: 'github',
        datasource_config: { repository: 'oneprolabs/devify' },
        config: {}
      },
      'https://github.com/'
    ),
    'https://github.com/oneprolabs/devify'
  )
  assert.equal(
    dataSourceRepositoryUrl(
      {
        plugin_key: 'gitlab',
        datasource_config: { project: 'hypermotion/mass' },
        config: {}
      },
      'http://gitlab.example.com:20080/'
    ),
    'http://gitlab.example.com:20080/hypermotion/mass'
  )
})

test('multi-resource Plugin datasources expose their first resource and count', () => {
  const row = {
    plugin_key: 'gitlab',
    datasource_config: {
      projects: ['hypermotion/alpha', 'hypermotion/beta']
    },
    config: {}
  }
  assert.equal(dataSourceRepository(row), 'hypermotion/alpha')
  assert.equal(
    dataSourceRepositoryUrl(row, 'https://gitlab.example.com'),
    'https://gitlab.example.com/hypermotion/alpha'
  )
  assert.equal(dataSourceRepositories(row).length, 2)
  assert.equal(isOrganizationDataSource(row), true)
})

test('single-resource Plugin datasources preserve branch details', () => {
  const row = {
    source_type: 'git',
    plugin_key: 'gitlab',
    datasource_config: {
      projects: ['hypermotion/alpha'],
      branch: 'develop'
    },
    config: {}
  }

  assert.equal(isOrganizationDataSource(row), false)
  assert.equal(dataSourceBranch(row), 'develop')
  assert.equal(dataSourceRepositories(row)[0].branch, 'develop')
})

test('legacy and Feishu datasource URLs remain directly readable', () => {
  assert.equal(
    dataSourceRepositoryUrl(
      {
        source_type: 'git',
        config: { repo_url: 'https://example.com/team/repository.git' }
      },
      ''
    ),
    'https://example.com/team/repository.git'
  )
  assert.equal(
    dataSourceRepositoryUrl(
      {
        plugin_key: 'feishu',
        datasource_config: {
          resource_urls: ['https://example.feishu.cn/drive/folder/folder-token']
        },
        config: {}
      },
      'https://open.feishu.cn'
    ),
    'https://example.feishu.cn/drive/folder/folder-token'
  )
})

function uploadTask(filename, status, metadata = {}) {
  return { status, metadata: { filename, ...metadata } }
}

test('deleted uploads are dropped from the latest-file baseline', () => {
  const files = latestUploadTasksByFilename([
    uploadTask('report.pdf', 'SUCCESS', { deleted: true, byte_size: 10 })
  ])

  assert.equal(files.size, 0)
})

test('a deleted latest version never falls back to an older upload', () => {
  const files = latestUploadTasksByFilename([
    uploadTask('report.pdf', 'SUCCESS', { deleted: true }),
    uploadTask('report.pdf', 'SUCCESS', { upload_version: 1 })
  ])

  assert.equal(files.has('report.pdf'), false)
})

test('a failed latest version falls back to the previous upload', () => {
  const files = latestUploadTasksByFilename([
    uploadTask('report.pdf', 'FAILURE', { upload_version: 2 }),
    uploadTask('report.pdf', 'SUCCESS', { upload_version: 1, byte_size: 42 })
  ])

  assert.equal(files.get('report.pdf').metadata.upload_version, 1)
})

test('a deduplicated re-upload keeps the upload that stored the file', () => {
  const files = latestUploadTasksByFilename([
    uploadTask('report.pdf', 'SUCCESS', { duplicate: true }),
    uploadTask('report.pdf', 'SUCCESS', { upload_version: 1, byte_size: 42 })
  ])

  assert.equal(files.get('report.pdf').metadata.upload_version, 1)
})

test('the newest successful upload wins per filename', () => {
  const files = latestUploadTasksByFilename([
    uploadTask('report.pdf', 'SUCCESS', { upload_version: 2, byte_size: 20 }),
    uploadTask('report.pdf', 'SUCCESS', { upload_version: 1, byte_size: 10 }),
    uploadTask('notes.txt', 'SUCCESS', { byte_size: 5 })
  ])

  assert.equal(files.size, 2)
  assert.equal(files.get('report.pdf').metadata.byte_size, 20)
  assert.equal(files.get('notes.txt').metadata.byte_size, 5)
})

test('knowledge policy labels use actual schedules and manual source capabilities', async () => {
  const { dataSourcePolicyLabel } = await import(
    '../src/pages/lens/datasourceHelpers.js'
  )
  const t = (key, args) => (args ? `${args.seconds}s` : key)
  assert.equal(
    dataSourcePolicyLabel(
      {
        sync_policy: {
          mode: 'crontab',
          cron: '0 2 * * *',
          timezone: 'Asia/Shanghai'
        }
      },
      t
    ),
    '0 2 * * * · Asia/Shanghai'
  )
  assert.equal(
    dataSourcePolicyLabel({ sync_policy: { interval_seconds: 21600 } }, t),
    '21600s'
  )
  assert.equal(dataSourcePolicyLabel({}, t), '86400s')
  assert.equal(
    dataSourcePolicyLabel({ sync_policy: { mode: 'manual' } }, t),
    'lensAdmin.knowledgeList.manual'
  )
  assert.equal(
    dataSourcePolicyLabel(
      {
        source_type: 'managed_workspace',
        sync_policy: { interval_seconds: 600 }
      },
      t
    ),
    'lensAdmin.knowledgeList.manual'
  )
  assert.equal(
    dataSourcePolicyLabel({ sync_policy: { interval_seconds: 'invalid' } }, t),
    '—'
  )
})

test('unmeasured and partial storage never appear as measured zero', async () => {
  const { dataSourceStorageLabel } = await import(
    '../src/pages/lens/datasourceHelpers.js'
  )
  const t = (key) => key
  const unknown = 'lensAdmin.datasourceDetail.storage.notMeasured'
  for (const usage of [
    undefined,
    {},
    { status: 'partial', total_bytes: 4096 },
    { status: 'complete', total_bytes: null },
    { status: 'complete', total_bytes: -1 }
  ]) {
    assert.equal(dataSourceStorageLabel({ storage_usage: usage }, t), unknown)
  }
  assert.equal(
    dataSourceStorageLabel(
      { storage_usage: { status: 'complete', total_bytes: 0 } },
      t
    ),
    '0 B'
  )
  assert.equal(
    dataSourceStorageLabel(
      { storage_usage: { status: 'complete', total_bytes: 1536 } },
      t
    ),
    '1.5 KB'
  )
})
