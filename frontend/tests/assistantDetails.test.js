import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

import {
  assistantDataMode,
  buildAssistantDetail
} from '../src/pages/lens/assistantDetails.js'

const source = (path) =>
  readFile(new URL(`../src/${path}`, import.meta.url), 'utf8')

test('assistant detail preserves directories, bindings, and grants', () => {
  const detail = buildAssistantDetail({
    selected_dirs: [
      { path: '/workspace/api' },
      { path: '/workspace/frontend' }
    ],
    skill_bindings: [
      { skill_name: 'Repository search', enabled: true },
      { skill: { name: 'Release notes' }, enabled: false }
    ],
    mcp_bindings: [
      { mcp_name: 'GitHub', enabled: true },
      { mcp_server: { name: 'Sentry' }, enabled: false }
    ],
    access_grants: [
      {
        type: 'user',
        id: 7,
        username: 'ada',
        email: 'ada@example.com'
      },
      { type: 'group', id: 3, name: 'Platform' }
    ]
  })

  assert.deepEqual(detail.workspaceDirectories, [
    '/workspace/api',
    '/workspace/frontend'
  ])
  assert.deepEqual(detail.skills, [
    { name: 'Repository search', enabled: true },
    { name: 'Release notes', enabled: false }
  ])
  assert.deepEqual(detail.mcps, [
    { name: 'GitHub', enabled: true },
    { name: 'Sentry', enabled: false }
  ])
  assert.deepEqual(detail.authorizedUsers, [
    {
      id: 7,
      username: 'ada',
      email: 'ada@example.com'
    }
  ])
  assert.deepEqual(detail.authorizedGroups, [{ id: 3, name: 'Platform' }])
})

test('assistant detail safely normalizes incomplete list data', () => {
  const detail = buildAssistantDetail({
    selected_dirs: ['/workspace/docs', null],
    skill_bindings: [{ skill_uuid: 'skill-1' }],
    mcp_bindings: [{ mcp_uuid: 'mcp-1' }],
    access_grants: [{ type: 'user', id: 9, name: 'grace' }]
  })

  assert.deepEqual(detail.workspaceDirectories, ['/workspace/docs'])
  assert.deepEqual(detail.skills, [{ name: '', enabled: true }])
  assert.deepEqual(detail.mcps, [{ name: '', enabled: true }])
  assert.deepEqual(detail.authorizedUsers, [
    { id: 9, username: 'grace', email: '' }
  ])
  assert.deepEqual(detail.authorizedGroups, [])
})

test('assistant data access always uses explicit selection', () => {
  assert.equal(
    assistantDataMode({ settings: { datasource_routing: 'auto' } }),
    'selected'
  )
  assert.equal(assistantDataMode({ datasource_routing: 'all' }), 'selected')
})

test('assistant list delegates lower-frequency data to a drawer', async () => {
  const [page, drawer, detailView, button] = await Promise.all([
    source('pages/lens/Assistants.vue'),
    source('pages/lens/AssistantDetailDrawer.vue'),
    source('pages/lens/AssistantDetailView.vue'),
    source('components/ui/BaseButton.vue')
  ])

  assert.doesNotMatch(page, /row\.selected_dirs\?\.\[0\]\?\.path/)
  assert.doesNotMatch(page, /\{\{ shareUrl\(row\) \}\}/)
  assert.match(page, /AssistantDetailDrawer/)
  assert.match(page, /data-testid="assistant-tool-counts"/)
  assert.match(page, /:aria-label="skillCountLabel\(row\)"/)
  assert.match(page, /:aria-label="mcpCountLabel\(row\)"/)
  assert.match(page, /<BaseModal[\s\S]*archiveTitle/)
  assert.match(page, /variant="danger-outline"/)
  assert.match(button, /'danger-outline'/)
  assert.doesNotMatch(page, /archiveConfirmUuid/)
  assert.match(
    page,
    new RegExp(
      'function startEditFromDetail\\(row\\) \\{\\s*' +
        'closeDetails\\(\\)\\s*startEdit\\(row\\)'
    )
  )
  assert.match(drawer, /<AssistantDetailView/)
  assert.match(drawer, /width="6xl"/)
  assert.match(detailView, /activeTab === 'data'/)
  assert.match(detailView, /activeTab === 'capabilities'/)
  assert.match(detailView, /assistant.visibility === 'private'/)
  assert.match(detailView, /:disabled="assistant.status !== 'active'"/)
  assert.match(detailView, /\$emit\('edit', assistant\)/)
})

test('assistant management loads form resources only when editing', async () => {
  const page = await source('pages/lens/Assistants.vue')

  assert.match(page, /async function loadFormResources\(\)/)
  assert.match(page, /await loadFormResources\(\)/)
  assert.doesNotMatch(
    page,
    /async function load\(\)[\s\S]*?Promise\.all\(\[\s*listAssistants[\s\S]*?listSkills/
  )
})

test('assistant detail drawer is rendered outside the list panel', async () => {
  const page = await source('pages/lens/Assistants.vue')

  assert.match(page, /<\/section>\s*<\/div>\s*<AssistantDetailDrawer/)
})

test('assistant detail localizes and formats updated timestamps', async () => {
  const [detailView, zh, en, es] = await Promise.all([
    source('pages/lens/AssistantDetailView.vue'),
    source('admin/locales/zh-CN.json'),
    source('admin/locales/en.json'),
    source('admin/locales/es.json')
  ])

  assert.match(detailView, /useShortDateTime/)
  assert.match(detailView, /formatDateTime\(assistant\.updated_at/)
  assert.match(zh, /"columns"[\s\S]*?"updatedAt": "更新时间"/)
  assert.match(en, /"columns"[\s\S]*?"updatedAt": "Updated"/)
  assert.match(es, /"columns"[\s\S]*?"updatedAt": "Actualizado"/)
})

test('assistant detail tabs remain discoverable on narrow screens', async () => {
  const detailView = await source('pages/lens/AssistantDetailView.vue')

  assert.match(detailView, /@media \(max-width: 640px\)/)
  assert.match(detailView, /\.detail-tabs[\s\S]*?flex-wrap: wrap/)
})

test('assistant tool counts use singular forms in English and Spanish', async () => {
  const [en, es] = await Promise.all([
    source('admin/locales/en.json'),
    source('admin/locales/es.json')
  ])

  assert.match(en, /"skillCount": "\{count\} Skill \| \{count\} Skills"/)
  assert.match(es, /"skillCount": "\{count\} Skill \| \{count\} Skills"/)
  assert.match(
    en,
    /"mcpCount": "\{count\} MCP Server \| \{count\} MCP Servers"/
  )
  assert.match(
    es,
    /"mcpCount": "\{count\} Servidor MCP \| \{count\} Servidores MCP"/
  )
})

test('assistant creation does not expose or submit concurrency tuning', async () => {
  const [drawer, page] = await Promise.all([
    source('pages/lens/AssistantFormDrawerDirectEnvironment.vue'),
    source('pages/lens/Assistants.vue')
  ])

  assert.match(
    drawer,
    /<FormRow\s+v-if="mode === 'edit'"\s+:label="t\('lensAdmin\.fields\.maxConcurrency'\)"/s
  )
  assert.match(
    page,
    /\.\.\.\(mode\.value === 'edit'[\s\S]*max_concurrency: Number\(form\.value\.max_concurrency\)/
  )
})

test('smart assistants do not retain direct datasource bindings', async () => {
  const [drawer, page] = await Promise.all([
    source('pages/lens/AssistantFormDrawerDirectEnvironment.vue'),
    source('pages/lens/Assistants.vue')
  ])

  assert.match(drawer, /props\.form\.datasource_bindings\s*=\s*\[\]/)
  assert.match(
    page,
    /datasource_bindings:\s*\n?\s*form\.value\.mode === 'smart'\s*\?\s*\[\]/
  )
})
