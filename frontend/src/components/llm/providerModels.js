// Model picker filtering for the LLM config UI.
//
// The LLM config page configures chat models only. Embedding and
// image-generation models stay in the provider catalog (other surfaces may
// need them, and model_type derivation uses them) but must not be offered as
// selectable chat models.

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
