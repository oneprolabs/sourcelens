/**
 * Turn source-path mentions in a rendered answer into inline references.
 *
 * The backend exposes the files a run consulted as bounded citations. The
 * answer itself repeats those paths as workspace-relative POSIX paths, but
 * without line ranges and usually without the mount prefix. This module links
 * every mention back to its citation so the reader can open the captured
 * source without leaving the answer, while the trailing citation list stays as
 * the complete fallback.
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

// Path characters are excluded from the boundary so a candidate never matches
// inside a longer path (for example `guide.md` inside `my-guide.md`).
const LEADING_BOUNDARY = String.raw`(^|[^A-Za-z0-9_./\\-])`
const TRAILING_BOUNDARY = String.raw`(?=$|[^A-Za-z0-9_./\\-]|\.(?:$|[^A-Za-z0-9]))`
const LINE_SUFFIX = String.raw`(?::(\d+)(?:-(\d+))?)?`

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
 * Link every indexed path mention in rendered HTML.
 *
 * Existing text and markup is preserved: only matched path tokens are wrapped
 * in a `<button data-inline-citation-id>`. Text inside block code, links, and
 * other non-prose containers is left untouched.
 *
 * @param {string} html - HTML produced by the markdown renderer.
 * @param {Array<object>} references - Citation metadata with `id` and `path`.
 * @returns {string} HTML with inline reference buttons.
 */
export function linkifyReferences(html, references) {
  if (typeof html !== 'string' || !html) return ''
  const index = buildReferenceIndex(references)
  if (!index.size) return html
  const pattern = buildMatcher(index)

  let output = ''
  let cursor = 0
  const stack = []
  const tagPattern = /<[^>]*>/g
  let tagMatch
  while ((tagMatch = tagPattern.exec(html)) !== null) {
    output += replaceText(
      html.slice(cursor, tagMatch.index),
      index,
      pattern,
      stack.length === 0
    )
    output += tagMatch[0]
    updateSkipStack(stack, tagMatch[0])
    cursor = tagPattern.lastIndex
  }
  output += replaceText(html.slice(cursor), index, pattern, stack.length === 0)
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

function replaceText(text, index, pattern, enabled) {
  if (!enabled || !text) return text
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
    output += inlineButton(path, startLine, endLine, entries)
    cursor = pattern.lastIndex
  }
  output += text.slice(cursor)
  return output
}

function inlineButton(path, startLine, endLine, entries) {
  let label = path
  if (startLine) label += `:${startLine}`
  if (startLine && endLine) label += `-${endLine}`
  const id = resolveReferenceId(entries, startLine)
  return (
    '<button type="button" class="inline-citation" ' +
    `data-inline-citation-id="${escapeAttribute(id)}">${label}</button>`
  )
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
