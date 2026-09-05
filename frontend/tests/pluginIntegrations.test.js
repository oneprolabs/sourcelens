import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

const source = (path) =>
  readFile(new URL(`../src/${path}`, import.meta.url), 'utf8')

test('Plugin data sources use installed manifests and Connections', async () => {
  const [page, drawer] = await Promise.all([
    source('pages/lens/DataSources.vue'),
    source('pages/lens/DataSourceFormDrawer.vue')
  ])

  assert.match(page, /listPlugins/)
  assert.match(page, /pluginManifests/)
  assert.match(page, /isPluginSourceType/)
  assert.match(page, /payload\.plugin_key = form\.value\.plugin_key/)
  assert.match(page, /payload\.connection_uuid = form\.value\.connection_uuid/)
  assert.match(page, /payload\.datasource_config = buildPluginDatasourceConfig/)
  assert.match(page, /config\.repositories = \[config\.repository\]/)
  assert.match(page, /config\.projects = \[config\.project\]/)
  assert.match(page, /payload\.credential_uuid = null/)
  assert.match(page, /getConnectionResources/)
  assert.doesNotMatch(page, /GitHub resources are available/)
  assert.match(drawer, /isPluginSourceType/)
  assert.match(drawer, /form\.connection_uuid/)
  assert.match(drawer, /pluginResources/)
  assert.doesNotMatch(page, /getPluginManifest\('github'\)/)
  assert.doesNotMatch(drawer, /form\.source_type === 'github'/)
  assert.doesNotMatch(drawer, /githubPluginRepositories/)
  assert.doesNotMatch(drawer, /github\.repositories/)
  assert.doesNotMatch(drawer, /github\.repository\.branches/)
})

test('Tool-only Plugins stay out of datasource creation', async () => {
  const page = await source('pages/lens/DataSources.vue')

  assert.match(page, /const datasourcePlugins = computed/)
  assert.match(page, /plugin\.datasource && plugin\.datasource_source_type/)
  assert.match(page, /:plugins="datasourcePlugins"/)
})

test('Datasource-only Plugins stay out of Assistant configuration', async () => {
  const drawer = await source(
    'pages/lens/AssistantFormDrawerDirectEnvironment.vue'
  )

  assert.match(drawer, /const activePluginConnections = computed/)
  assert.match(drawer, /props\.pluginManifests\?\.\[connection\.plugin_key\]/)
  assert.match(drawer, /Array\.isArray\(manifest\?\.tools\)/)
  assert.match(drawer, /manifest\.tools\.length > 0/)
})

test('Feishu datasource URLs are checked before they can be saved', async () => {
  const [page, api] = await Promise.all([
    source('pages/lens/DataSources.vue'),
    source('api/lens.js')
  ])

  assert.match(api, /connections\/\$\{uuid\}\/validate-datasource/)
  assert.match(page, /validateConnectionDatasource/)
  assert.match(page, /form\.value\.plugin_key === 'feishu'/)
  assert.match(page, /datasource_config: buildPluginDatasourceConfig\(\)/)
  assert.match(
    page,
    /datasourceConnectionSignature\(true\) !==\s*datasourceConnectionBaseSignature\.value/
  )
})

test('Feishu Connection keeps setup guidance beside editable fields', async () => {
  const [page, guide, drawer, chinese, manifestText] = await Promise.all([
    source('pages/lens/Connections.vue'),
    source('components/lens/FeishuConnectionGuide.vue'),
    source('components/ui/BaseDrawer.vue'),
    source('admin/locales/zh-CN.json'),
    readFile(
      new URL('../../plugins/feishu/plugin.json', import.meta.url),
      'utf8'
    )
  ])
  const manifest = JSON.parse(manifestText)

  assert.match(page, /v-if="form\.plugin_key === 'feishu'"/)
  assert.match(page, /width="6xl"/)
  assert.match(
    page,
    /class="connection-form-layout grid gap-5 md:grid-cols-\[minmax\(0,1fr\)_20rem\]/
  )
  assert.doesNotMatch(page, /form\.plugin_key === 'feishu'\s*\? 'grid gap-5/)
  assert.match(page, /<FeishuConnectionGuide/)
  assert.match(guide, /<aside/)
  assert.match(guide, /md:sticky md:top-0/)
  assert.match(guide, /md:max-h-\[calc\(100vh-10rem\)\]/)
  assert.match(guide, /md:overflow-y-auto/)
  assert.doesNotMatch(guide, /<details/)
  assert.match(guide, /drive:file:readonly/)
  assert.match(guide, /wiki:wiki:readonly/)
  assert.match(guide, /configure-app-data-permissions/)
  assert.match(guide, /application-scope/)
  assert.match(guide, /scope-list/)
  assert.match(drawer, /'6xl': 'max-w-6xl'/)
  assert.match(chinese, /仅配置 API 权限并不会自动开放全部文档/)
  assert.match(chinese, /保存数据源时会验证每个地址是否可访问/)
  assert.match(manifest.description, /Feishu custom app/)
  assert.match(
    manifest.datasource_schema.properties.resource_urls.description,
    /One HTTPS folder/
  )
})

test('GitHub Connection explains PAT permissions and datasource boundaries', async () => {
  const [page, guide, chinese, manifestText] = await Promise.all([
    source('pages/lens/Connections.vue'),
    source('components/lens/GitHubConnectionGuide.vue'),
    source('admin/locales/zh-CN.json'),
    readFile(
      new URL('../../plugins/github/plugin.json', import.meta.url),
      'utf8'
    )
  ])
  const manifest = JSON.parse(manifestText)

  assert.match(page, /form\.plugin_key === 'github'/)
  assert.match(page, /<GitHubConnectionGuide/)
  assert.match(guide, /github\.com\/settings\/personal-access-tokens/)
  assert.match(guide, /managing-your-personal-access-tokens/)
  assert.match(guide, /Contents/)
  assert.match(guide, /Issues/)
  assert.match(guide, /Pull requests/)
  assert.match(guide, /Actions/)
  assert.match(chinese, /GitHub 目前建议优先使用细粒度个人访问令牌/)
  assert.equal(
    manifest.description,
    'Connect GitHub.com repositories for file sync and read-only assistant queries.'
  )
  assert.match(manifest.datasource.description, /repository files/)
  assert.match(
    manifest.datasource.description,
    /Issues and pull requests remain available through read-only tools/
  )
})

test('Connection Plugin summary stays one line with concise GitHub copy', async () => {
  const [page, drawer, chineseText, manifestText] = await Promise.all([
    source('pages/lens/Connections.vue'),
    source('components/ui/BaseDrawer.vue'),
    source('admin/locales/zh-CN.json'),
    readFile(
      new URL('../../plugins/github/plugin.json', import.meta.url),
      'utf8'
    )
  ])
  const chinese = JSON.parse(chineseText)
  const manifest = JSON.parse(manifestText)

  assert.match(
    page,
    /class="mt-1 truncate text-sm leading-5 text-ink-600"[\s\S]*:title="localizedManifest\.description"/
  )
  assert.match(page, /:show="drawerOpen"[\s\S]*width="6xl"/)
  assert.match(
    page,
    /md:grid-cols-\[minmax\(0,1fr\)_20rem\][\s\S]*xl:grid-cols-\[minmax\(0,1fr\)_22rem\]/
  )
  assert.match(drawer, /'6xl': 'max-w-6xl'/)
  assert.equal(
    chinese.lensAdmin.plugins.github.description,
    '连接 GitHub.com 仓库，用于文件同步和助手只读查询。'
  )
  assert.equal(
    manifest.description,
    'Connect GitHub.com repositories for file sync and read-only assistant queries.'
  )
})

test('GitLab Connection explains PAT scopes and datasource boundaries', async () => {
  const [page, guide, chinese, manifestText] = await Promise.all([
    source('pages/lens/Connections.vue'),
    source('components/lens/GitLabConnectionGuide.vue'),
    source('admin/locales/zh-CN.json'),
    readFile(
      new URL('../../plugins/gitlab/plugin.json', import.meta.url),
      'utf8'
    )
  ])
  const manifest = JSON.parse(manifestText)

  assert.match(page, /form\.plugin_key === 'gitlab'/)
  assert.match(page, /<GitLabConnectionGuide/)
  assert.match(
    guide,
    /docs\.gitlab\.com\/user\/profile\/personal_access_tokens/
  )
  assert.match(
    guide,
    /docs\.gitlab\.com\/security\/tokens\/access_token_scopes/
  )
  assert.match(guide, /read_api/)
  assert.match(guide, /read_repository/)
  assert.match(guide, /GitLab\.com/)
  assert.match(guide, /self-managed GitLab/)
  assert.match(chinese, /SourceLens 当前仅支持个人访问令牌，不支持 OAuth/)
  assert.match(chinese, /允许项目范围不会授予 GitLab 权限/)
  assert.match(manifest.description, /GitLab\.com or self-managed/)
  assert.match(manifest.description, /commits, merge requests, and issues/)
  assert.match(manifest.datasource.description, /repository files/)
  assert.match(
    manifest.datasource.description,
    /Issues and merge requests remain available through read-only tools/
  )
})

test('Jira Connection explains Cloud and self-hosted read-only setup', async () => {
  const [page, guide, chinese, manifestText] = await Promise.all([
    source('pages/lens/Connections.vue'),
    source('components/lens/JiraConnectionGuide.vue'),
    source('admin/locales/zh-CN.json'),
    readFile(new URL('../../plugins/jira/plugin.json', import.meta.url), 'utf8')
  ])
  const manifest = JSON.parse(manifestText)

  assert.match(page, /form\.plugin_key === 'jira'/)
  assert.match(page, /<JiraConnectionGuide/)
  assert.match(
    guide,
    /id\.atlassian\.com\/manage-profile\/security\/api-tokens/
  )
  assert.match(guide, /basic-auth-for-rest-apis/)
  assert.match(guide, /Jira Cloud/)
  assert.match(guide, /self-hosted/)
  assert.match(guide, /Browse projects/)
  assert.match(chinese, /Jira 目前仅作为助手的只读 Tool/)
  assert.match(chinese, /允许项目范围不会授予 Jira 权限/)
  assert.match(manifest.description, /Jira Cloud or self-hosted/)
  assert.match(manifest.description, /search issues/)
  assert.match(manifest.description, /read-only assistant tools/)
  assert.equal(manifest.datasource, undefined)
})

test('Feishu datasource creation uses the Plugin while legacy rows remain editable', async () => {
  const [page, drawer] = await Promise.all([
    source('pages/lens/DataSources.vue'),
    source('pages/lens/DataSourceFormDrawer.vue')
  ])

  assert.match(
    page,
    /if \(row\?\.plugin_key && row\?\.connection\) \{\s*return `plugin:\$\{row\.plugin_key\}`/
  )
  assert.match(
    drawer,
    /props\.mode === 'edit' && props\.form\.source_type === 'feishu'/
  )
  assert.match(
    drawer,
    /props\.mode === 'edit' &&\s*props\.form\.source_type === 'gitlab'/
  )
  assert.match(drawer, /!props\.form\.connection_uuid/)
  assert.match(
    drawer,
    /if \(isPluginSourceType\(props\.form\.source_type\)\) \{\s*return Boolean\([\s\S]*schemaRequiredFieldsHaveValues/
  )
})

test('Direct Assistants bind installed Plugin tools without provider branches', async () => {
  const [page, drawer] = await Promise.all([
    source('pages/lens/Assistants.vue'),
    source('pages/lens/AssistantFormDrawerDirectEnvironment.vue')
  ])

  assert.match(page, /plugin_bindings:/)
  assert.match(page, /form\.value\.plugin_bindings/)
  assert.match(page, /listPlugins/)
  assert.match(page, /pluginManifests/)
  assert.doesNotMatch(page, /plugin_bindings[\s\S]{0,300}secret/)
  assert.match(drawer, /pluginManifests/)
  assert.match(drawer, /form\.plugin_bindings/)
  assert.match(drawer, /togglePluginConnection/)
  assert.match(drawer, /missingSkillPluginRequirements/)
  assert.match(drawer, /hasGeneralChatExecutionTool/)
  assert.doesNotMatch(drawer, /pluginAllTools/)
  assert.doesNotMatch(drawer, /togglePluginTool/)
  assert.doesNotMatch(drawer, /pluginTools\(/)
  assert.doesNotMatch(page, /tools:\s*\[\.\.\.\(binding\.tools/)
  assert.doesNotMatch(drawer, /githubConnections/)
  assert.doesNotMatch(drawer, /githubPluginTools/)
  assert.doesNotMatch(drawer, /githubPluginManifest/)
})

test('Assistant Plugin choices only show icon, name and Plugin type', async () => {
  const [page, drawer] = await Promise.all([
    source('pages/lens/Assistants.vue'),
    source('pages/lens/AssistantFormDrawerDirectEnvironment.vue')
  ])

  assert.match(page, /getPluginIcon/)
  assert.match(page, /:plugin-icon-urls="pluginIconUrls"/)
  assert.match(drawer, /pluginIconUrl\(connection\.plugin_key\)/)
  assert.match(drawer, /\{\{ connection\.name \}\}/)
  assert.match(drawer, /pluginDisplayName\(connection\.plugin_key\)/)
  assert.doesNotMatch(drawer, /connectionScope\(connection\)/)
  assert.doesNotMatch(drawer, /lensAdmin\.wizard\.pluginSectionHint/)
  assert.doesNotMatch(drawer, /lensAdmin\.wizard\.pluginAllTools/)
})

test('General Chat treats Skills as optional when Plugin tools are enabled', async () => {
  const [drawer, chinese, english] = await Promise.all([
    source('pages/lens/AssistantFormDrawerDirectEnvironment.vue'),
    source('admin/locales/zh-CN.json'),
    source('admin/locales/en.json')
  ])

  assert.match(drawer, /t\('lensAdmin\.wizard\.skillsSection'\)/)
  assert.doesNotMatch(drawer, /skillsSectionRequired/)
  assert.match(drawer, /hasGeneralChatExecutionTool/)
  assert.doesNotMatch(chinese, /skillsSectionRequired/)
  assert.doesNotMatch(english, /skillsSectionRequired/)
  assert.match(chinese, /至少需要一个已启用的 Skill 或内置插件工具/)
  assert.match(english, /at least one enabled Skill or built-in Plugin tool/)
})

test('manifest resource options preserve an existing dependent value', async () => {
  const renderer = await source('components/lens/ManifestSchemaForm.vue')

  assert.match(renderer, /candidate\.depends_on/)
  assert.match(renderer, /resource-options-request/)
  assert.match(renderer, /isResourceOptionLoading/)
  assert.match(renderer, /fieldValue\(field\)/)
})

test('Connection management keeps Plugin secrets write-only in the form', async () => {
  const [page, renderer, api] = await Promise.all([
    source('pages/lens/Connections.vue'),
    source('components/lens/ManifestSchemaForm.vue'),
    source('api/lens.js')
  ])

  assert.match(renderer, /field\.format === 'password'/)
  assert.match(renderer, /new-password/)
  assert.match(page, /field\.format === 'password'/)
  assert.match(page, /!hasFieldValue\(value\)/)
  assert.doesNotMatch(page, /row\.secret_value/)
  assert.match(api, /connections\/\$\{uuid\}\/validate/)
  assert.match(api, /connections\/\$\{uuid\}\/resources/)
  assert.match(api, /connections\/\$\{uuid\}\/resource-candidates/)
  assert.match(api, /connections\/resource-preview/)
  assert.match(page, /previewConnectionResources/)
  assert.match(page, /getConnectionResourceCandidates/)
  assert.match(page, /discoverConnectionResources/)
  assert.match(page, /canDiscoverConnectionResources/)
  assert.match(page, /storedSecretPlaceholder/)
  assert.match(page, /row\.secret_hint/)
  assert.doesNotMatch(api, /connections\/\$\{uuid\}\/revoke/)
  assert.doesNotMatch(page, /revokeConnection/)
  assert.match(page, /row\.status === 'active'/)
  assert.doesNotMatch(page, /revokeConfirm/)
})

test('manifest schema renderer supports safe scalar, secret, array and resource fields', async () => {
  const renderer = await source('components/lens/ManifestSchemaForm.vue')

  assert.match(renderer, /provider-resource/)
  assert.match(renderer, /provider-resource-option/)
  assert.match(renderer, /resources/)
  assert.match(renderer, /depends_on/)
  assert.match(renderer, /field\.format === 'password'/)
  assert.match(renderer, /type="checkbox"/)
  assert.match(renderer, /update:modelValue/)
  assert.match(renderer, /properties/)
})

test('plain manifest arrays use independently removable input rows', async () => {
  const [renderer, drawer, page] = await Promise.all([
    source('components/lens/ManifestSchemaForm.vue'),
    source('pages/lens/DataSourceFormDrawer.vue'),
    source('pages/lens/DataSources.vue')
  ])

  assert.match(renderer, /v-for="\(item, index\) in arrayRows\(field\)"/)
  assert.match(renderer, /updateArrayItem\(field, index/)
  assert.match(renderer, /addArrayItem\(field\)/)
  assert.match(renderer, /removeArrayItem\(field, index\)/)
  assert.match(renderer, /await nextTick\(\)/)
  assert.match(renderer, /document\.getElementById[\s\S]*\.focus\(\)/)
  assert.doesNotMatch(renderer, /arrayValue\(field\)\.join\('\n'\)/)
  assert.match(drawer, /:add-array-item-label="t\('common\.add'\)"/)
  assert.match(drawer, /:remove-array-item-label="t\('common\.delete'\)"/)
  assert.match(drawer, /fieldValue\.some/)
  assert.match(page, /normalizePluginDatasourceField/)
  assert.match(page, /\.map\(\(item\) => String\(item \?\? ''\)\.trim\(\)\)/)
  assert.match(page, /\.filter\(Boolean\)/)
})

test('connection resource trees stay hidden until scope values exist', async () => {
  const renderer = await source('components/lens/ManifestSchemaForm.vue')

  assert.match(renderer, /shouldRenderTree\(field\)/)
  assert.match(renderer, /optionsFor\(field\)\.length > 0/)
  assert.match(renderer, /if \(isTreeField\(field\)\) return normalized/)
  assert.match(renderer, /emptyResourceText/)
  assert.match(renderer, /treeSearchQuery/)
  assert.match(renderer, /filteredTreeGroups/)
  assert.match(renderer, /toggleTreeGroup/)
  assert.match(renderer, /aria-expanded/)
  assert.match(renderer, /treeSearchPlaceholder/)
})

test('connection resource candidates are reset when the secret changes', async () => {
  const page = await source('pages/lens/Connections.vue')

  assert.match(page, /@update:model-value="updateConnectionForm"/)
  assert.match(page, /connectionResourceCandidates\.value = \[\]/)
  assert.match(page, /nextForm\[resourceKey\] = \[\]/)
})

test('connection forms pass a shared input style to manifest fields', async () => {
  const [page, renderer] = await Promise.all([
    source('pages/lens/Connections.vue'),
    source('components/lens/ManifestSchemaForm.vue')
  ])

  assert.match(page, /control-class="connection-form-input"/)
  assert.match(renderer, /controlClass/)
})

test('datasource wizard exposes completed steps for quick navigation', async () => {
  const drawer = await source('pages/lens/DataSourceFormDrawer.vue')

  assert.match(
    drawer,
    /aria-current="i \+ 1 === wizardStep \? 'step' : undefined"/
  )
  assert.match(drawer, /:disabled="i \+ 1 > wizardStep"/)
  assert.match(drawer, /goToWizardStep\(i \+ 1\)/)
})

test('datasource wizard groups creation into three steps', async () => {
  const drawer = await source('pages/lens/DataSourceFormDrawer.vue')

  assert.match(drawer, /key: 'basic'/)
  assert.match(drawer, /key: 'connection'/)
  assert.match(drawer, /key: 'sync'/)
  assert.match(drawer, /processingTitle/)
  assert.match(drawer, /conversionOpen/)
  assert.doesNotMatch(drawer, /key: 'conversion'/)
})

test('datasource target path check follows the semantic sync step', async () => {
  const drawer = await source('pages/lens/DataSourceFormDrawer.vue')

  assert.match(drawer, /activeStepKey\.value !== 'sync'/)
  assert.doesNotMatch(drawer, /wizardStep\.value !== 4/)
})

test('plugin datasource resource discovery does not require a LensNode', async () => {
  const page = await source('pages/lens/DataSources.vue')
  const pluginBranch = page.indexOf(
    'if (isPluginSourceType(form.value.source_type))'
  )
  const nodeGuard = page.indexOf('if (!form.value.lensnode_uuid) return')

  assert.ok(pluginBranch >= 0)
  assert.ok(nodeGuard >= 0)
  assert.ok(pluginBranch < nodeGuard)
})

test('plugin datasource resources load on the connection step', async () => {
  const drawer = await source('pages/lens/DataSourceFormDrawer.vue')

  assert.match(drawer, /activeStepKey\.value === 'connection'/)
  assert.match(drawer, /activeStepKey\.value === 'sync'/)
})

test('Connection management renders manifest connection fields instead of GitHub-only inputs', async () => {
  const page = await source('pages/lens/Connections.vue')

  assert.match(page, /ManifestSchemaForm/)
  assert.match(page, /manifest\.connection_schema/)
  assert.match(page, /listPlugins/)
  assert.match(page, /plugin\.version/)
  assert.match(page, /field\.write_to/)
  assert.doesNotMatch(page, /v-model="form\.repositories"/)
  assert.doesNotMatch(page, /<option value="github">/)
  assert.doesNotMatch(page, /plugin_key: 'github'/)
})

test('Plugin datasource fields use only the generic manifest resource contract', async () => {
  const [page, drawer, api] = await Promise.all([
    source('pages/lens/DataSources.vue'),
    source('pages/lens/DataSourceFormDrawer.vue'),
    source('api/lens.js')
  ])

  assert.match(drawer, /ManifestSchemaForm/)
  assert.match(drawer, /datasourceSchema/)
  assert.match(drawer, /pluginResources/)
  assert.match(drawer, /updatePluginConfig/)
  assert.match(drawer, /request-resource-options/)
  assert.match(page, /loadPluginResourceOptions/)
  assert.match(api, /getConnectionResources\(uuid, params = \{\}\)/)
  assert.doesNotMatch(drawer, /githubDatasourceSchema/)
  assert.doesNotMatch(drawer, /githubResourceOptions/)
  assert.doesNotMatch(drawer, /selectedPluginRepository/)
})

test('Plugin datasource setup avoids redundant Connection identity', async () => {
  const drawer = await source('pages/lens/DataSourceFormDrawer.vue')

  assert.doesNotMatch(
    drawer,
    /selectedConnection\.name }} · {{ selectedConnection\.plugin_key/
  )
})

test('Manifest datasource inputs use a local compact control style', async () => {
  const renderer = await source('components/lens/ManifestSchemaForm.vue')
  const controlStyle = renderer.match(
    /\.manifest-schema-control \{[\s\S]*?\n\}/
  )?.[0]

  assert.match(
    renderer,
    /controlClass: \{ type: String, default: 'manifest-schema-control' \}/
  )
  assert.ok(controlStyle)
  assert.match(controlStyle, /text-sm/)
  assert.match(controlStyle, /px-3/)
  assert.match(controlStyle, /py-2/)
})

test('MCP Plugin adapters select dynamic Connections and manifest tools', async () => {
  const page = await source('pages/lens/Mcp.vue')

  assert.match(page, /<option value="plugin">/)
  assert.match(page, /listConnections/)
  assert.match(page, /listPlugins/)
  assert.match(page, /getPluginManifest/)
  assert.match(page, /form\.connection_uuid/)
  assert.match(page, /pluginTools/)
  assert.match(page, /connection_uuid:/)
  assert.match(page, /tools:/)
  assert.doesNotMatch(page, /github_read_file/)
  assert.doesNotMatch(page, /plugin_key === 'github'/)
})

test('connection management uses compact cards with summarized scope', async () => {
  const [page, renderer] = await Promise.all([
    source('pages/lens/Connections.vue'),
    source('components/lens/ManifestSchemaForm.vue')
  ])

  assert.match(page, /connections-toolbar/)
  assert.match(page, /connection-card/)
  assert.match(page, /connectionUsageLabels/)
  assert.match(page, /datasourceLabel/)
  assert.match(page, /toolLabel/)
  assert.match(page, /pluginIconUrl/)
  assert.match(page, /connection-usage-summary/)
  assert.match(page, /class="connection-card group relative/)
  assert.match(
    page,
    /:aria-label="`\$\{t\('common\.viewDetails'\)\}: \$\{row\.name\}`"[\s\S]*@click="openConnectionDetail\(row\)"/
  )
  assert.doesNotMatch(
    page,
    /<BaseButton[^>]*@click="openConnectionDetail\(row\)"/
  )
  assert.match(page, /variant="danger"[\s\S]*@click\.stop="removeRow\(row\)"/)
  assert.match(page, /@click\.stop="startEdit\(row\)"/)
  assert.match(page, /connectionDetailOpen/)
  assert.match(page, /:schema="detailScopeSchema"/)
  assert.match(page, /:model-value="detailScopeModel"/)
  assert.match(page, /:resources="detailScopeResourceOptions"/)
  assert.match(page, /:read-only="true"/)
  assert.doesNotMatch(page, /connection-detail-scope/)
  assert.doesNotMatch(page, /toggleDetailScopeGroup/)
  assert.match(renderer, /readOnly: \{ type: Boolean, default: false \}/)
  assert.match(renderer, /:disabled="readOnly"/)
  assert.match(page, /detailConnection\.secret_hint/)
})

test('datasource management groups source, target and run information', async () => {
  const [page, rowActions, skills] = await Promise.all([
    source('pages/lens/DataSources.vue'),
    source('pages/lens/components/RowActions.vue'),
    source('pages/lens/Skills.vue')
  ])

  assert.match(page, /datasource-toolbar/)
  assert.match(page, /datasource-card/)
  assert.match(page, /pluginIconUrl/)
  assert.match(page, /datasource-source-summary/)
  assert.match(page, /datasource-target-summary/)
  assert.match(page, /datasource-run-summary/)
  assert.match(
    page,
    /dataSourceRepositoryUrl\(row, connectionEndpoint\(row\)\)/
  )
  assert.doesNotMatch(page, /min-h-64/)
  assert.match(page, /grid-cols-1/)
  assert.match(page, /datasource-card flex min-w-0/)
  assert.match(rowActions, /showDownload/)
  assert.match(skills, /show-download/)
  assert.doesNotMatch(page, /<table/)
})

test('datasource detail organizes metadata into focused data blocks', async () => {
  const drawer = await source('pages/lens/DataSourceDetailDrawer.vue')

  assert.match(drawer, /datasource-overview-block/)
  assert.match(drawer, /datasource-resource-block/)
  assert.match(drawer, /datasource-sync-block/)
  assert.match(drawer, /datasource-retrieval-block/)
  assert.match(drawer, /datasourceRetrievalGroups/)
  assert.match(drawer, /dataSourceRepositoryUrl/)
  assert.match(drawer, /connection_name/)
})

test('creation forms do not expose generic active or disabled selectors', async () => {
  const [connections, drawer, detailDrawer] = await Promise.all([
    source('pages/lens/Connections.vue'),
    source('pages/lens/DataSourceFormDrawer.vue'),
    source('pages/lens/DataSourceDetailDrawer.vue')
  ])

  assert.doesNotMatch(connections, /v-model="form\.status"/)
  assert.doesNotMatch(drawer, /v-model="form\.status"/)
  assert.match(connections, /connections\.pause/)
  assert.match(connections, /connections\.resume/)
  assert.match(detailDrawer, /actions\.disableDatasource/)
  assert.match(detailDrawer, /actions\.enableDatasource/)
})

test('datasource wizard presents an explicit configuration summary', async () => {
  const drawer = await source('pages/lens/DataSourceFormDrawer.vue')

  assert.match(drawer, /datasource-wizard-summary/)
  assert.match(drawer, /selectedConnectionScopeSummary/)
  assert.doesNotMatch(
    drawer,
    /JSON\.stringify\(selectedConnection\.allowed_scope\)/
  )
})
