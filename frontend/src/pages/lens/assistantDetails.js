function bindingItem(binding, name) {
  if (!binding || typeof binding !== 'object') return null
  return {
    name: name || '',
    enabled: binding.enabled !== false
  }
}

/** Normalize assistant data for read-only detail views. */
export function buildAssistantDetail(assistant = {}) {
  const workspaceDirectories = (assistant.selected_dirs || [])
    .map((directory) =>
      typeof directory === 'string' ? directory : directory?.path
    )
    .filter(Boolean)

  const skills = (assistant.skill_bindings || [])
    .map((binding) =>
      bindingItem(binding, binding?.skill_name || binding?.skill?.name)
    )
    .filter(Boolean)

  const mcps = (assistant.mcp_bindings || [])
    .map((binding) =>
      bindingItem(
        binding,
        binding?.mcp_name || binding?.mcp_server?.name || binding?.mcp?.name
      )
    )
    .filter(Boolean)

  const plugins = (assistant.plugin_bindings || [])
    .map((binding) => bindingItem(binding, binding?.connection_name))
    .filter(Boolean)

  const grants = assistant.access_grants || []
  const authorizedUsers = grants
    .filter((grant) => grant?.type === 'user')
    .map((grant) => ({
      id: grant.id,
      username: grant.username || grant.name || '',
      email: grant.email || ''
    }))
  const authorizedGroups = grants
    .filter((grant) => grant?.type === 'group')
    .map((grant) => ({
      id: grant.id,
      name: grant.name || ''
    }))

  return {
    workspaceDirectories,
    plugins,
    skills,
    mcps,
    authorizedUsers,
    authorizedGroups
  }
}

/** Keep list and detail labels aligned with explicit datasource bindings. */
export function assistantDataMode() {
  return 'selected'
}
