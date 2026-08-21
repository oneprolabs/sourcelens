import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

const source = (path) =>
  readFile(new URL(`../src/${path}`, import.meta.url), 'utf8')

test('active chat run exposes a localized stop action', async () => {
  const [chat, english, chinese] = await Promise.all([
    source('pages/lens/Chat.vue'),
    source('locales/en.json').then(JSON.parse),
    source('locales/zh-CN.json').then(JSON.parse)
  ])

  assert.match(chat, /isRunActive \? t\('common\.stop'\)/)
  assert.equal(english.common.stop, 'Stop')
  assert.equal(chinese.common.stop, '停止')
})

test('admin tables and assistant slugs preserve backend-compatible values', async () => {
  const [users, drawer, assistants, english, chinese] = await Promise.all([
    source('admin/pages/Management/Users.vue'),
    source('pages/lens/AssistantFormDrawerDirectEnvironment.vue'),
    source('pages/lens/Assistants.vue'),
    source('admin/locales/en.json').then(JSON.parse),
    source('admin/locales/zh-CN.json').then(JSON.parse)
  ])

  assert.match(users, /overflow-auto rounded-lg border/)
  assert.match(users, /min-w-\[20\.5rem\].*md:min-w-\[62\.5rem\]/)
  assert.match(drawer, /pattern="\[-a-zA-Z0-9_\]\+"/)
  assert.match(drawer, /const canonicalSlug = computed\(\(\) => String\(/)
  assert.match(assistants, /slug: form\.value\.slug\?\.trim\(\) \|\| ''/)
  assert.match(english.lensAdmin.wizard.slugHint, /Letters/)
  assert.match(chinese.lensAdmin.wizard.slugHint, /字母/)
})

test('final synthesis progress reacts to locale changes', async () => {
  const [chat, english, chinese, { computed }, { createI18n }] =
    await Promise.all([
      source('pages/lens/Chat.vue'),
      source('locales/en.json').then(JSON.parse),
      source('locales/zh-CN.json').then(JSON.parse),
      import('vue'),
      import('vue-i18n')
    ])
  const i18n = createI18n({
    legacy: false,
    locale: 'en',
    messages: { en: english, 'zh-CN': chinese }
  })
  const progress = { completed: 4, total: 4 }
  const progressText = computed(() =>
    i18n.global.t('lens.chat.runtime.planCompletedAnswering', progress)
  )

  assert.match(chat, /t\('lens\.chat\.runtime\.planCompletedAnswering'/)
  assert.doesNotMatch(chat, /计划已完成|Generating final answer/)
  assert.equal(
    progressText.value,
    'Plan completed 4/4 · Generating final answer…'
  )

  i18n.global.locale.value = 'zh-CN'

  assert.equal(progressText.value, '计划已完成 4/4 · 正在生成最终回答……')
})

test('task statistics request all users unless a user is selected', async () => {
  const stats = await source('admin/pages/TaskManagement/Stats.vue')
  const fetchStart = stats.indexOf('async function fetchStats')
  const fetchEnd = stats.indexOf(
    'const res = await taskManagementApi.getStats(params)',
    fetchStart
  )
  const requestSetup = stats.slice(fetchStart, fetchEnd)

  assert.match(requestSetup, /my_tasks: 'false'/)
  assert.match(requestSetup, /params\.created_by = userScope\.value/)
})

test('drawer leave transition completes when animations are suspended', async () => {
  const drawer = await source('components/ui/BaseDrawer.vue')
  const { runDrawerTransition } = await import(
    '../src/components/ui/drawerTransition.js'
  )

  let cancelledAnimations = 0
  const suspendedAnimation = () => ({
    cancel() {
      cancelledAnimations += 1
    },
    finished: new Promise(() => {})
  })
  const panel = { animate: suspendedAnimation }
  const element = {
    animate: suspendedAnimation,
    querySelector() {
      return panel
    }
  }

  await new Promise((resolve, reject) => {
    const timeout = setTimeout(
      () => reject(new Error('drawer transition did not complete')),
      500
    )

    runDrawerTransition(element, 'leave', 10, () => {
      clearTimeout(timeout)
      resolve()
    })
  })

  assert.match(drawer, /<Transition\s+:css="false"/)
  assert.equal(cancelledAnimations, 2)
})

test('drawer skips visual transitions in a hidden document', async () => {
  const { runDrawerTransition } = await import(
    '../src/components/ui/drawerTransition.js'
  )
  let animationCount = 0
  let completed = false
  const panel = {
    animate() {
      animationCount += 1
    }
  }
  const element = {
    ownerDocument: { hidden: true },
    animate() {
      animationCount += 1
    },
    querySelector() {
      return panel
    }
  }

  runDrawerTransition(element, 'enter', 300, () => {
    completed = true
  })

  assert.equal(completed, true)
  assert.equal(animationCount, 0)
})

test('assistant exposes one execution strategy without a token budget picker', async () => {
  const [drawer, english, chinese] = await Promise.all([
    source('pages/lens/AssistantFormDrawerDirectEnvironment.vue'),
    source('admin/locales/en.json').then(JSON.parse),
    source('admin/locales/zh-CN.json').then(JSON.parse)
  ])

  assert.equal(english.lensAdmin.fields.agentRounds, 'Execution strategy')
  assert.equal(chinese.lensAdmin.fields.agentRounds, '执行策略')
  assert.doesNotMatch(drawer, /tokenBudgetProfiles/)
  assert.doesNotMatch(drawer, /v-model="form\.token_budget_profile"/)
  assert.doesNotMatch(drawer, /token_budget_profile:/)
})

test('assistant Skill picker supports search and environment configuration', async () => {
  const [drawer, english, chinese] = await Promise.all([
    source('pages/lens/AssistantFormDrawerDirectEnvironment.vue'),
    source('admin/locales/en.json').then(JSON.parse),
    source('admin/locales/zh-CN.json').then(JSON.parse)
  ])

  assert.match(drawer, /v-model="skillSearch"/)
  assert.match(drawer, /type="search"/)
  assert.match(drawer, /class="form-input skill-search-input"/)
  assert.match(drawer, /\.skill-search-input\s*{\s*@apply pl-9;/)
  assert.match(drawer, /v-for="skill in filteredSelectableSkills"/)
  assert.match(drawer, /filterSelectableSkills/)
  assert.match(drawer, /data-testid="assistant-skill-option"/)
  assert.match(
    drawer,
    /isSkillSelected\(skill\.uuid\)\s*&& skillEnvironment\(skill\)\.length/
  )
  assert.match(drawer, /data-testid="assistant-skill-environments"/)
  assert.match(drawer, /class="ml-8 border-l border-primary-200 pl-3"/)
  assert.doesNotMatch(drawer, /selectedSkillsWithEnvironment/)
  assert.equal(english.lensAdmin.wizard.searchSkills, 'Search Skills')
  assert.equal(chinese.lensAdmin.wizard.searchSkills, '搜索 Skills')
  assert.match(english.lensAdmin.wizard.skillSearchResults, /showing/i)
  assert.match(chinese.lensAdmin.wizard.skillSearchResults, /显示/)
  assert.equal(
    english.lensAdmin.wizard.environmentSection,
    'Environment variables'
  )
  assert.equal(chinese.lensAdmin.wizard.environmentSection, '环境变量')
  assert.match(english.lensAdmin.wizard.environmentSectionHint, /this Skill/i)
  assert.match(chinese.lensAdmin.wizard.environmentSectionHint, /当前 Skill/)
})

test('chat exposes actionable messages for dispatch configuration failures', async () => {
  const [chat, nodeErrors, english, chinese] = await Promise.all([
    source('pages/lens/Chat.vue'),
    source('utils/lensNodeErrors.js'),
    source('locales/en.json').then(JSON.parse),
    source('locales/zh-CN.json').then(JSON.parse)
  ])

  assert.match(chat, /GENERAL_CHAT_SKILL_REQUIRED/)
  assert.match(nodeErrors, /LENSNODE_OFFLINE/)
  assert.equal(
    english.lens.chat.errorSkillRequired,
    'This assistant has no executable Skill bound for the request. Ask an administrator to bind the required Skill.'
  )
  assert.match(chinese.lens.chat.errorSkillRequired, /未绑定.*Skill/)
})

test('MCP config masks credential values while preserving edit feedback', async () => {
  const [page, editor, english, chinese] = await Promise.all([
    source('pages/lens/Mcp.vue'),
    source('pages/lens/components/KeyValueEditor.vue'),
    source('admin/locales/en.json').then(JSON.parse),
    source('admin/locales/zh-CN.json').then(JSON.parse)
  ])

  assert.match(page, /:mask-sensitive-values="true"/)
  assert.match(page, /lensAdmin\.mcp\.sensitiveConfigHint/)
  assert.match(editor, /isSensitiveMcpConfigKey/)
  assert.match(editor, /:type="isSensitiveRow\(row\) \? 'password' : 'text'"/)
  assert.match(editor, /isMaskedSensitiveRow\(row\)/)
  assert.match(english.lensAdmin.mcp.sensitiveConfigHint, /masked/i)
  assert.match(chinese.lensAdmin.mcp.sensitiveConfigHint, /掩码/)
})

test('MCP declarations and assistant bindings configure environment variables nearby', async () => {
  const [mcpPage, drawer, english, chinese] = await Promise.all([
    source('pages/lens/Mcp.vue'),
    source('pages/lens/AssistantFormDrawerDirectEnvironment.vue'),
    source('admin/locales/en.json').then(JSON.parse),
    source('admin/locales/zh-CN.json').then(JSON.parse)
  ])

  assert.match(mcpPage, /v-model="form\.environment"/)
  assert.match(mcpPage, /environment: buildMcpEnvironment/)
  assert.match(mcpPage, /mcpConfigToRows/)
  assert.match(mcpPage, /mcpRowsToConfig/)
  assert.match(
    drawer,
    /isMcpSelected\(mcp\.uuid\)\s*&& mcpEnvironment\(mcp\)\.length/
  )
  assert.match(drawer, /data-testid="assistant-mcp-environments"/)
  assert.match(drawer, /mcp_environment_set_uuids/)
  assert.match(drawer, /mcpRequiredEnvironmentNames/)
  assert.match(english.lensAdmin.wizard.mcpEnvironmentSectionHint, /MCP Server/)
  assert.match(chinese.lensAdmin.wizard.mcpEnvironmentSectionHint, /MCP Server/)
  assert.match(
    chinese.lensAdmin.mcp.environmentPlaceholderHint,
    /\{placeholder\}/
  )
})

test('environment variable sets show their Skill and MCP references', async () => {
  const [page, english, chinese] = await Promise.all([
    source('pages/lens/EnvironmentVariables.vue'),
    source('admin/locales/en.json').then(JSON.parse),
    source('admin/locales/zh-CN.json').then(JSON.parse)
  ])

  assert.match(page, /row\.usages\?\.length/)
  assert.match(page, /usage\.resource_name/)
  assert.match(page, /usage\.assistant_name/)
  assert.equal(english.lensAdmin.environmentVariables.usages, 'Referenced by')
  assert.equal(chinese.lensAdmin.environmentVariables.usages, '引用方')
})
