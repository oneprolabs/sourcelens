export function selectableReplacementAssistants(assistants, field, query = '') {
  const search = query.trim().toLowerCase()
  return assistants.filter((row) => {
    if (row.status !== 'active' || row.is_system) return false
    if (
      field === 'multimodal_model_ref' &&
      (row.mode || row.routing_mode) === 'smart'
    )
      return false
    return (
      !search ||
      [row.name, row.slug]
        .filter(Boolean)
        .join(' ')
        .toLowerCase()
        .includes(search)
    )
  })
}

export function toggleReplacementSelection(selectedUuids, assistants, checked) {
  const selected = new Set(selectedUuids)
  assistants.forEach((row) =>
    checked ? selected.add(row.uuid) : selected.delete(row.uuid)
  )
  return [...selected]
}

export function replacementPickerPosition(rect, viewport, menuHeight) {
  const margin = 8
  const gap = 4
  const below = Math.max(0, viewport.height - rect.bottom - gap - margin)
  const above = Math.max(0, rect.top - gap - margin)
  const opensAbove = below < menuHeight && above > below
  const maxHeight = Math.min(menuHeight, opensAbove ? above : below)
  const width = Math.min(rect.width, viewport.width - margin * 2)
  return {
    left: Math.max(
      margin,
      Math.min(rect.left, viewport.width - width - margin)
    ),
    top: opensAbove ? rect.top - gap - maxHeight : rect.bottom + gap,
    width,
    maxHeight
  }
}

export function eligibleReplacementModel(model, field) {
  if (
    model.is_active === false ||
    model.scope !== 'global' ||
    model.model_type !== 'llm'
  )
    return false
  if (field === 'agent_model_ref') return true
  const declared = model.config?.supports_vision ?? model.config?.vision
  return (
    model.vision_capability === 'supported' ||
    model.supports_vision === true ||
    declared === true ||
    model.capabilities?.includes?.('vision') === true
  )
}
