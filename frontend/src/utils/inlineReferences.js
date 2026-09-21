/**
 * Render consulted sources as inline references within an answer.
 *
 * The answer carries a source marker from generation time — ``[[source:
 * <path>]]`` — placed immediately after the claim it supports. This module
 * turns each marker into an openable chip, then also links any bare path
 * mention the answer wrote in prose. A marker that does not match a citation
 * the run actually produced is removed, so a hallucinated source never shows.
 */

const SKIP_TAGS = new Set([
  'pre',
  'a',
  'script',
  'style',
  'textarea',
  'svg',
  'button'
])

// The answer emits ``[[source: <path>]]`` or ``[[source: <path>:<start>-<end>]]``.
const MARKER_PATTERN = /\[\[\s*source:\s*([^\]\n]+?)\s*\]\]/gi

// Matches a marker together with the horizontal space before it, so removing
// it from exported text never leaves a doubled or trailing space.
const MARKER_STRIP_PATTERN = /[ \t]*\[\[\s*source:[^\]\n]*\]\]/gi

// Path characters are excluded from the boundary so a candidate never matches
// inside a longer path (for example `guide.md` inside `my-guide.md`).
const LEADING_BOUNDARY = String.raw`(^|[^A-Za-z0-9_./\\-])`
const TRAILING_BOUNDARY = String.raw`(?=$|[^A-Za-z0-9_./\\-]|\.(?:$|[^A-Za-z0-9]))`
const LINE_SUFFIX = String.raw`(?::(\d+)(?:-(\d+))?)?`

// A document glyph drawn with only attributes the sanitizer keeps, so the
// inline chip survives DOMPurify without widening the allowlist.
const REFERENCE_ICON =
  '<svg class="inline-citation-icon" viewBox="0 0 24 24" fill="none" ' +
  'stroke="currentColor" stroke-width="2" aria-hidden="true">' +
  '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>' +
  '<path d="M14 2v6h6"/>' +
  '</svg>'

/**
 * Index citation paths under every form an answer may write them in.
 *
 * Each path is indexed as its full public path, its path without the leading
 * mount/datasource segment, and its basename when that basename is unique
 * across the citations. The returned map keeps every citation for a shared
 * text so a cited line can pick the right window.
 *
 * @param {Array<object>} references - Citation metadata with `id` and `path`.
 * @returns {Map<string, Array<{id: string, startLine: number, endLine: number}>>}
 */
export function buildReferenceIndex(references) {
  const list = Array.isArray(references) ? references : []
  const basenames = new Map()
  for (const reference of list) {
    const path = cleanPath(reference?.path)
    if (!path) continue
    const basename = path.split('/').pop()
    basenames.set(basename, (basenames.get(basename) || 0) + 1)
  }

  const index = new Map()
  for (const reference of list) {
    const path = cleanPath(reference?.path)
    const id = reference?.id ? String(reference.id) : ''
    if (!path || !id) continue
    const entry = {
      id,
      startLine: positiveInteger(reference?.startLine),
      endLine: positiveInteger(reference?.endLine)
    }
    const add = (text) => {
      addCandidate(index, text, entry)
      if (!text.startsWith('./')) addCandidate(index, `./${text}`, entry)
    }
    add(path)
    const parts = path.split('/').filter(Boolean)
    if (parts.length > 1) {
      const relative = parts.slice(1).join('/')
      if (relative.includes('/')) add(relative)
    }
    const basename = parts[parts.length - 1]
    if (basename && basenames.get(basename) === 1) add(basename)
  }
  return index
}

/**
 * Turn source markers and bare path mentions into inline reference chips.
 *
 * Text inside block code, links, and other non-prose containers is left
 * untouched, and a marker that resolves to no citation is dropped so the
 * reader never sees a raw marker or an invented source.
 *
 * @param {string} html - HTML produced by the markdown renderer.
 * @param {Array<object>} references - Citation metadata with `id` and `path`.
 * @returns {string} HTML with inline reference chips.
 */
export function linkifyReferences(html, references) {
  if (typeof html !== 'string' || !html) return ''
  const index = buildReferenceIndex(references)
  const pattern = index.size ? buildMatcher(index) : null

  let output = ''
  let cursor = 0
  const stack = []
  const tagPattern = /<[^>]*>/g
  let tagMatch
  while ((tagMatch = tagPattern.exec(html)) !== null) {
    output += replaceProse(
      html.slice(cursor, tagMatch.index),
      index,
      pattern,
      stack.length === 0
    )
    output += tagMatch[0]
    updateSkipStack(stack, tagMatch[0])
    cursor = tagPattern.lastIndex
  }
  output += replaceProse(html.slice(cursor), index, pattern, stack.length === 0)
  return output
}

/**
 * Remove every source marker from plain text.
 *
 * Copying or exporting an answer must not show the inline source markers, so
 * the text form drops them entirely while the on-screen answer keeps its chips.
 *
 * @param {string} text - Answer text that may contain markers.
 * @returns {string} Text without markers.
 */
export function stripSourceMarkers(text) {
  if (typeof text !== 'string' || !text) return ''
  return text.replace(MARKER_STRIP_PATTERN, '')
}

function replaceProse(text, index, pattern, enabled) {
  if (!enabled || !text) return text
  const { text: marked, buttons } = replaceMarkers(text, index)
  const linked = pattern ? replaceMentions(marked, index, pattern) : marked
  return restoreButtons(linked, buttons)
}

function replaceMarkers(text, index) {
  const buttons = []
  if (!/\[\[\s*source:/i.test(text)) return { text, buttons }
  const output = text.replace(MARKER_PATTERN, (match, rawReference) => {
    const reference = parseMarker(rawReference)
    const entries = lookupReferenceEntries(index, reference.path)
    if (!entries) return ''
    const entry = resolveReferenceEntry(entries, reference.startLine)
    buttons.push(
      referenceButton(
        entry.id,
        referenceLabel(reference.path, entry.startLine, entry.endLine),
        referenceTitle(reference.path)
      )
    )
    return buttonPlaceholder(buttons.length - 1)
  })
  return { text: output, buttons }
}

// A marker becomes a sentinel first so the later path-mention pass cannot
// match the path already wrapped in a chip. The sentinel contains no path
// character and is swapped back for the chip once the pass is done.
function buttonPlaceholder(position) {
  return `\uE000${position}\uE000`
}

function restoreButtons(text, buttons) {
  if (!buttons.length) return text
  return text.replace(/\uE000(\d+)\uE000/g, (match, position) => {
    const button = buttons[Number(position)]
    return button === undefined ? '' : button
  })
}

function replaceMentions(text, index, pattern) {
  pattern.lastIndex = 0
  let output = ''
  let cursor = 0
  let match
  while ((match = pattern.exec(text)) !== null) {
    const path = match[2]
    const entries = index.get(path)
    if (!entries) continue
    const startLine = match[3]
    const endLine = match[4]
    output += text.slice(cursor, match.index)
    output += match[1]
    output += referenceButton(
      resolveReferenceId(entries, startLine),
      referenceLabel(path, startLine, endLine),
      referenceTitle(path)
    )
    cursor = pattern.lastIndex
  }
  output += text.slice(cursor)
  return output
}

function buildMatcher(index) {
  const texts = [...index.keys()].sort(
    (left, right) => right.length - left.length
  )
  const alternation = texts.map(escapeRegExp).join('|')
  return new RegExp(
    LEADING_BOUNDARY +
      '(' +
      alternation +
      ')' +
      LINE_SUFFIX +
      TRAILING_BOUNDARY,
    'g'
  )
}

function parseMarker(rawReference) {
  const text = String(rawReference || '').trim()
  const lineMatch = /:(\d+)(?:-(\d+))?$/.exec(text)
  if (lineMatch) {
    return {
      path: text.slice(0, lineMatch.index).trim(),
      startLine: lineMatch[1]
    }
  }
  return { path: text, startLine: '' }
}

function lookupReferenceEntries(index, path) {
  const clean = cleanPath(path).replace(/^\.\//, '')
  if (!clean) return null
  const parts = clean.split('/').filter(Boolean)
  const candidates = [clean, `./${clean}`]
  if (parts.length > 1) candidates.push(parts.slice(1).join('/'))
  if (parts.length) candidates.push(parts[parts.length - 1])
  for (const candidate of candidates) {
    const entries = index.get(candidate)
    if (entries) return entries
  }
  return null
}

function referenceLabel(path, startLine, endLine) {
  const parts = cleanPath(path).split('/').filter(Boolean)
  let label = parts[parts.length - 1] || path
  if (startLine) label += `:${startLine}`
  if (startLine && endLine) label += `-${endLine}`
  return label
}

function referenceTitle(path) {
  return cleanPath(path)
}

function referenceButton(id, label, titleText) {
  const title = titleText ? ` title="${escapeAttribute(titleText)}"` : ''
  return (
    '<button type="button" class="inline-citation" ' +
    `data-inline-citation-id="${escapeAttribute(id)}"${title}>` +
    REFERENCE_ICON +
    label +
    '</button>'
  )
}

function resolveReferenceEntry(entries, startLine) {
  const id = resolveReferenceId(entries, startLine)
  return entries.find((entry) => entry.id === id) || entries[0]
}

function resolveReferenceId(entries, startLine) {
  const line = Number(startLine)
  if (Number.isFinite(line) && line > 0) {
    const hit = entries.find(
      (entry) =>
        entry.startLine > 0 && line >= entry.startLine && line <= entry.endLine
    )
    if (hit) return hit.id
  }
  return entries[0].id
}

function addCandidate(index, text, entry) {
  if (!text) return
  const entries = index.get(text) || []
  if (!entries.some((item) => item.id === entry.id)) entries.push(entry)
  index.set(text, entries)
}

function updateSkipStack(stack, tag) {
  const name = tagName(tag)
  if (!name || !SKIP_TAGS.has(name)) return
  if (tag.startsWith('</')) {
    const position = stack.lastIndexOf(name)
    if (position !== -1) stack.splice(position, 1)
    return
  }
  if (!tag.endsWith('/>')) stack.push(name)
}

function tagName(tag) {
  const match = /^<\/?\s*([a-zA-Z0-9]+)/.exec(tag)
  return match ? match[1].toLowerCase() : ''
}

function cleanPath(value) {
  return typeof value === 'string' ? value.trim() : ''
}

function positiveInteger(value) {
  const number = Number(value)
  return Number.isInteger(number) && number > 0 ? number : 0
}

function escapeRegExp(value) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

function escapeAttribute(value) {
  return String(value).replace(/&/g, '&amp;').replace(/"/g, '&quot;')
}
