const PLUGIN_RESOURCE_FIELDS = {
  github: ['repositories', 'repository'],
  gitlab: ['projects', 'project']
}

function selectedResources(pluginKey, datasourceConfig) {
  const [pluralKey, singularKey] = PLUGIN_RESOURCE_FIELDS[pluginKey] || []
  if (!pluralKey) return []
  const plural = datasourceConfig?.[pluralKey]
  const values = Array.isArray(plural)
    ? plural
    : [datasourceConfig?.[singularKey]]
  return values
    .map((value) =>
      String(value || '')
        .trim()
        .replace(/^\/+|\/+$/g, '')
    )
    .filter(Boolean)
}

export function buildPluginGitPathCheckConfig({
  pluginKey,
  endpoint,
  datasourceConfig
}) {
  const resources = selectedResources(pluginKey, datasourceConfig)
  if (!resources.length) return {}
  const baseUrl = String(endpoint || '')
    .trim()
    .replace(/\/+$/, '')
  return {
    repositories: resources.map((resource) => ({
      repo_url: baseUrl ? `${baseUrl}/${resource}.git` : '',
      target_subdir: resource,
      enabled: true
    }))
  }
}
