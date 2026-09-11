export function bindingKey(binding) {
  return `${binding.datasource_uuid}:${binding.item_uuid || ''}`
}

export function toggleBinding(bindings, source, item, checked) {
  const candidate = {
    datasource_uuid: source.uuid,
    item_uuid: item?.uuid || null,
    mount_name: `ds_${source.uuid.replaceAll('-', '')}${item ? `_${item.uuid.replaceAll('-', '')}` : ''}`,
    required: true
  }
  const rows = bindings.filter((binding) => bindingKey(binding) !== bindingKey(candidate))
  if (!checked) return rows
  return [...rows.filter((binding) => binding.datasource_uuid !== source.uuid ||
    (item && binding.item_uuid)), candidate]
}
