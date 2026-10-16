import assert from 'node:assert/strict'
import { readdir, readFile } from 'node:fs/promises'
import test from 'node:test'

import {
  localizePluginManifest,
  pluginDisplayName
} from '../src/utils/pluginI18n.js'

const root = new URL('../../', import.meta.url)

async function readJson(path) {
  return JSON.parse(await readFile(new URL(path, root), 'utf8'))
}

async function readPluginManifest(directory) {
  try {
    return await readJson(`plugins/${directory}/plugin.json`)
  } catch (error) {
    if (error.code !== 'ENOENT') throw error
  }
  const entries = await readdir(new URL(`plugins/${directory}/`, root), {
    withFileTypes: true
  })
  const versions = entries
    .filter(
      (entry) => entry.isDirectory() && /^\d+\.\d+\.\d+$/.test(entry.name)
    )
    .map((entry) => entry.name)
    .sort()
  return readJson(`plugins/${directory}/${versions.at(-1)}/plugin.json`)
}

function nestedValue(messages, key) {
  return key.split('.').reduce((value, part) => value?.[part], messages)
}

function translator(messages) {
  return {
    t: (key) => nestedValue(messages, key),
    te: (key) => typeof nestedValue(messages, key) === 'string'
  }
}

test('English remains the default UI language and fallback', async () => {
  const i18n = await readFile(
    new URL('frontend/src/i18n/index.js', root),
    'utf8'
  )

  assert.match(i18n, /\? language : 'en'/)
  assert.match(i18n, /fallbackLocale: 'en'/)
})

test('built-in Plugin configuration copy is complete in every locale', async () => {
  const pluginEntries = await readdir(new URL('plugins/', root), {
    withFileTypes: true
  })
  const pluginDirectories = pluginEntries
    .filter((entry) => entry.isDirectory() && !entry.name.startsWith('_'))
    .map((entry) => entry.name)
  const manifests = await Promise.all(pluginDirectories.map(readPluginManifest))
  const locales = await Promise.all(
    ['en', 'zh-CN', 'es'].map((locale) =>
      readJson(`frontend/src/admin/locales/${locale}.json`)
    )
  )

  for (const [localeIndex, messages] of locales.entries()) {
    for (const manifest of manifests) {
      const pluginMessages = messages.lensAdmin.plugins?.[manifest.key]
      assert.equal(typeof pluginMessages?.displayName, 'string')
      assert.equal(typeof pluginMessages?.description, 'string')
      if (localeIndex === 0) {
        assert.equal(pluginMessages.displayName, manifest.display_name)
        assert.equal(pluginMessages.description, manifest.description)
      }

      for (const [schemaName, messageKey] of [
        ['connection_schema', 'connectionFields'],
        ['datasource_schema', 'datasourceFields']
      ]) {
        const properties = manifest[schemaName]?.properties || {}
        for (const [fieldKey, field] of Object.entries(properties)) {
          assert.equal(
            typeof pluginMessages?.[messageKey]?.[fieldKey]?.title,
            'string'
          )
          if (localeIndex === 0) {
            assert.equal(
              pluginMessages[messageKey][fieldKey].title,
              field.title
            )
          }
          if (field.description) {
            assert.equal(
              typeof pluginMessages?.[messageKey]?.[fieldKey]?.description,
              'string'
            )
            if (localeIndex === 0) {
              assert.equal(
                pluginMessages[messageKey][fieldKey].description,
                field.description
              )
            }
          }
        }
      }
    }
  }
})

test('Plugin manifest localization falls back to its English copy', async () => {
  const [manifest, chinese] = await Promise.all([
    readPluginManifest('github'),
    readJson('frontend/src/admin/locales/zh-CN.json')
  ])
  const { t, te } = translator(chinese)
  const localized = localizePluginManifest(manifest, t, te)

  assert.equal(pluginDisplayName(manifest, t, te), 'GitHub')
  assert.equal(
    localized.description,
    '连接 GitHub.com 仓库，用于文件同步和助手只读查询。'
  )
  assert.equal(
    localized.connection_schema.properties.secret_value.title,
    '个人访问令牌'
  )
  assert.equal(
    localized.datasource_schema.properties.directory.description,
    '可选。填写需要同步的仓库相对目录。'
  )

  const externalManifest = {
    key: 'external',
    display_name: 'External Plugin',
    description: 'Provided by an external package.'
  }
  assert.equal(pluginDisplayName(externalManifest, t, te), 'External Plugin')
  assert.equal(
    localizePluginManifest(externalManifest, t, te).description,
    'Provided by an external package.'
  )
})

test('Plugin configuration surfaces use localized manifest copy', async () => {
  const [connections, datasourceDrawer, assistantDrawer, schemaForm] =
    await Promise.all([
      readFile(
        new URL('frontend/src/pages/lens/Connections.vue', root),
        'utf8'
      ),
      readFile(
        new URL('frontend/src/pages/lens/DataSourceFormDrawer.vue', root),
        'utf8'
      ),
      readFile(
        new URL(
          'frontend/src/pages/lens/AssistantFormDrawerDirectEnvironment.vue',
          root
        ),
        'utf8'
      ),
      readFile(
        new URL('frontend/src/components/lens/ManifestSchemaForm.vue', root),
        'utf8'
      )
    ])

  assert.match(connections, /localizePluginManifest/)
  assert.match(datasourceDrawer, /localizePluginManifest/)
  assert.match(assistantDrawer, /translatedPluginDisplayName/)
  assert.doesNotMatch(
    datasourceDrawer,
    /aria-label="Datasource creation progress"/
  )
  assert.match(schemaForm, /loadingOptionsLabel/)
  assert.doesNotMatch(schemaForm, />\s*Loading options…\s*</)
})
