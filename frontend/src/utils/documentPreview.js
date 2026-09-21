/**
 * Tidy spreadsheet-converted markdown before it is rendered as a preview.
 *
 * Uploaded documents are converted to markdown tables that reproduce the
 * original grid: fully blank rows, repeated separator rows, and columns that
 * are empty end to end all survive the conversion and make the rendered table
 * sparse and hard to read. This module drops that noise while keeping the
 * header, the single GFM delimiter row, and every non-empty cell aligned.
 */

const TABLE_ROW = /^\s*\|.*\|\s*$/
const RULE_CELL = /^:?-{2,}:?$/

/**
 * Return markdown with empty table rows, extra separators, and blank columns
 * removed and long runs of blank lines collapsed.
 *
 * @param {string} text - Converted document markdown.
 * @returns {string} Tidied markdown.
 */
export function normalizeMarkdownTables(text) {
  const lines = String(text || '')
    .replace(/\r\n?/g, '\n')
    .split('\n')
  const output = []
  let block = []
  const flush = () => {
    if (block.length) output.push(...renderTableBlock(block))
    block = []
  }
  for (const line of lines) {
    if (TABLE_ROW.test(line)) {
      block.push(line)
      continue
    }
    flush()
    output.push(line)
  }
  flush()
  return output.join('\n').replace(/\n{3,}/g, '\n\n')
}

function renderTableBlock(lines) {
  const rows = lines.map(splitRow)
  const rules = rows.map(isRuleRow)
  const headerIndex = rules.findIndex((rule) => !rule)
  if (headerIndex === -1) return []

  const keep = rows.map((cells, index) => {
    if (rules[index]) return index === headerIndex + 1
    return cells.some((cell) => cell !== '')
  })
  const keptCells = []
  const keptRules = []
  rows.forEach((cells, index) => {
    if (!keep[index]) return
    keptCells.push(cells)
    keptRules.push(rules[index])
  })
  if (!keptCells.length) return []

  const width = Math.max(...keptCells.map((cells) => cells.length))
  const padded = keptCells.map((cells) => {
    const copy = cells.slice(0, width)
    while (copy.length < width) copy.push('')
    return copy
  })

  const drop = []
  for (let column = 0; column < width; column += 1) {
    drop.push(
      padded.every((cells, index) =>
        keptRules[index] ? true : cells[column] === ''
      )
    )
  }

  return padded
    .map((cells) => cells.filter((cell, column) => !drop[column]))
    .filter((cells) => cells.length > 0)
    .map((cells) => `| ${cells.join(' | ')} |`)
}

function splitRow(line) {
  return line
    .trim()
    .replace(/^\|/, '')
    .replace(/\|$/, '')
    .split('|')
    .map((cell) => cell.trim())
}

function isRuleRow(cells) {
  return (
    cells.length > 0 && cells.every((cell) => !cell || RULE_CELL.test(cell))
  )
}
