// Model picker filtering for the LLM config UI.
//
// Embedding and image-generation models stay in the provider catalog but
// must not be offered as selectable chat models. Decision models use their
// dedicated schema.

const NON_CHAT_CAPABILITIES = ['embedding', 'text-to-image']

export function isSelectableChatModel(model) {
  const capabilities = model?.capabilities || []
  const mode = model?.mode || 'chat'
  return (
    mode === 'chat' &&
    !capabilities.some((capability) =>
      NON_CHAT_CAPABILITIES.includes(capability)
    )
  )
}

export function selectableChatModels(models) {
  // Sorted by display name so the picker order is stable and predictable.
  // Both sides are guarded: an unguarded b.label would compare against the
  // literal string "undefined".
  return (models || [])
    .filter(isSelectableChatModel)
    .sort((a, b) => String(a.label || '').localeCompare(String(b.label || '')))
}

/** Select the documented schema without mixing chat and decision parameters. */
export function modelParamSchema(schemas, provider, modelType) {
  const schema = schemas?.[provider] || {}
  return modelType === 'decision' ? schema.decision || {} : schema
}

/** Mixed providers remain available for both explicitly selected model types. */
export function modelProviderOptions(providers, schemas, modelType) {
  return (providers || []).filter((provider) => {
    const schema = schemas?.[provider.id] || {}
    const modelTypes = provider.model_types || schema.model_types
    if (modelType === 'decision') return Boolean(schema.decision)
    // Custom chat models remain supported despite this provider's empty catalog.
    return (
      provider.id === 'openai_compatible' ||
      !modelTypes?.every((type) => type === 'decision')
    )
  })
}

/** Decision aliases come from agentcore's per-provider decision metadata. */
export function selectableDecisionModels(schema) {
  return (schema?.models || []).map((id) => ({
    id,
    label: id,
    capabilities: ['decision']
  }))
}

/** Decision credentials follow provider requirements, including keyless endpoints. */
export function decisionEditableParams(schema) {
  return (schema?.editable_params || []).filter((parameter) => {
    if (parameter === 'account_id') return schema.requires_account_id === true
    if (parameter === 'api_key') return schema.api_key_required !== false
    return true
  })
}

/** Cloudflare derives its native endpoint from the configured account identity. */
export function decisionAccountApiBase(schema, accountId) {
  const value = String(accountId || '').trim()
  return schema?.requires_account_id && /^[a-fA-F0-9]{32}$/.test(value)
    ? `https://api.cloudflare.com/client/v4/accounts/${value}/ai/run`
    : ''
}
