export function citationLocation(citation = {}) {
  const path = citation.path || ''
  const start = Number(citation.start_line) || 1
  const end = Number(citation.end_line) || start
  return end === start ? `${path}:${start}` : `${path}:${start}-${end}`
}

export function citationSourceUrl(runUuid, citationId) {
  return `/lens/runs/${encodeURIComponent(runUuid)}/citations/${encodeURIComponent(
    citationId
  )}/`
}

export function isMarkdownCitationPath(path) {
  return /\.(?:md|markdown)$/i.test(String(path || ''))
}

export function assistantShowsCitations(assistant) {
  if (!assistant) return true
  if (assistant.show_citations === false) return false
  return assistant.settings?.features?.citations !== false
}
