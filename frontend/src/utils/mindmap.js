const BRANCH_COLORS = ['#0057ff', '#27ce6e', '#ff9500', '#f94d4d']

function cleanLabel(value) {
  return value.replace(/^\s+|\s+$/g, '').replace(/^(\*\*|__)([\s\S]+)\1$/, '$2')
}

function createNode(label, depth, path) {
  return {
    label: cleanLabel(label),
    depth,
    path,
    children: []
  }
}

/**
 * Parse a Markdown outline into a tree suitable for an interactive mindmap.
 *
 * Supported input is intentionally small and predictable: headings become
 * first-level branches and indented Markdown bullets become descendants.
 */
export function parseMindmap(markdown) {
  const root = createNode('思维导图', 0, '0')
  const stack = [{ node: root, indent: -1 }]
  let heading = root

  const lines = String(markdown || '')
    .replace(/\r\n/g, '\n')
    .replace(/\r/g, '\n')
    .split('\n')

  for (const line of lines) {
    const headingMatch = line.match(/^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$/)
    if (headingMatch) {
      const node = createNode(
        headingMatch[1],
        1,
        `0.${root.children.length + 1}`
      )
      root.children.push(node)
      heading = node
      stack.length = 0
      stack.push({ node: root, indent: -1 }, { node, indent: -1 })
      continue
    }

    const bulletMatch = line.match(/^(\s*)(?:[-*+]|\d+\.)\s+(.+?)\s*$/)
    if (!bulletMatch) continue

    const indent = bulletMatch[1].replace(/\t/g, '  ').length
    while (stack.length > 2 && indent <= stack[stack.length - 1].indent) {
      stack.pop()
    }
    const parent = stack[stack.length - 1]?.node || heading
    const path = `${parent.path}.${parent.children.length + 1}`
    const node = createNode(bulletMatch[2], parent.depth + 1, path)
    parent.children.push(node)
    stack.push({ node, indent })
  }

  if (!root.children.length) {
    const fallback = String(markdown || '').trim()
    if (fallback) {
      root.label = cleanLabel(fallback.split('\n')[0])
    }
  }
  return root
}

function escapeXml(value) {
  return String(value)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&apos;')
}

function flattenTree(root) {
  const nodes = []
  const walk = (node) => {
    nodes.push(node)
    node.children.forEach(walk)
  }
  walk(root)
  return nodes
}

const ROOT_X = 112
const DOT_GAP = 12
const TEXT_GAP = 10
const COLUMN_GAP = 40
const ROOT_GAP = 104
const LINE_HEIGHT = 20
const LEAF_GAP = 12
const MIN_LEAF_ADVANCE = 34
const WRAP_WIDTH = 240

function measureWidth(text) {
  let width = 0
  for (const char of String(text)) {
    width += char.codePointAt(0) > 0x2e7f ? 14 : 7.5
  }
  return width
}

function wrapLabel(label, maxWidth = WRAP_WIDTH) {
  const chars = Array.from(String(label))
  if (!chars.length) return ['']
  const lines = []
  let current = ''
  let width = 0
  for (const char of chars) {
    const charWidth = char.codePointAt(0) > 0x2e7f ? 14 : 7.5
    if (current && width + charWidth > maxWidth) {
      lines.push(current)
      current = ''
      width = 0
    }
    current += char
    width += charWidth
  }
  lines.push(current)
  return lines
}

function layoutTree(root) {
  const nodes = flattenTree(root)
  const maxDepth = rootDepth(root)

  nodes.forEach((node) => {
    node.lines = node.depth === 0 ? [] : wrapLabel(node.label)
    node.textWidth = node.lines.reduce(
      (width, line) => Math.max(width, measureWidth(line)),
      0
    )
  })

  const columnWidths = []
  nodes.forEach((node) => {
    columnWidths[node.depth] = Math.max(
      columnWidths[node.depth] || 0,
      node.textWidth
    )
  })

  // Labels are left-aligned per depth. Only nodes with children get a dot,
  // and it sits just after the label; leaves have no dot. A branch leaves
  // from its parent's dot and stops before the child label, so connectors
  // remain visually separated from text.
  const labelX = [ROOT_X]
  for (let depth = 1; depth <= maxDepth; depth += 1) {
    labelX[depth] =
      depth === 1
        ? ROOT_X + ROOT_GAP
        : labelX[depth - 1] + columnWidths[depth - 1] + DOT_GAP + COLUMN_GAP
  }

  let nextY = 24
  const place = (node) => {
    node.labelX = labelX[node.depth]
    node.labelEnd = node.labelX + node.textWidth
    node.hasChildren = node.children.length > 0
    node.dotX = node.depth === 0 ? ROOT_X : node.labelEnd + DOT_GAP
    if (!node.children.length) {
      node.y = nextY
      const lineCount = Math.max(1, node.lines.length)
      nextY += Math.max(MIN_LEAF_ADVANCE, lineCount * LINE_HEIGHT + LEAF_GAP)
      return
    }
    node.children.forEach(place)
    node.y =
      (node.children[0].y + node.children[node.children.length - 1].y) / 2
  }
  place(root)

  const lastWidth = columnWidths[maxDepth] || 0
  const lastDot =
    maxDepth === 0 ? ROOT_X : labelX[maxDepth] + lastWidth + DOT_GAP
  return {
    nodes,
    width: Math.max(560, lastDot + 24),
    height: Math.max(150, nextY + 8)
  }
}

function rootDepth(root) {
  let depth = 0
  const walk = (node) => {
    depth = Math.max(depth, node.depth)
    node.children.forEach(walk)
  }
  walk(root)
  return depth
}

function branchColor(node) {
  if (node.depth === 0) return '#0057ff'
  const firstSegment = Number(node.path.split('.')[1] || 1)
  return BRANCH_COLORS[(firstSegment - 1) % BRANCH_COLORS.length]
}

function renderText(node) {
  const textX = node.labelX
  const firstLineOffset = -((node.lines.length - 1) * LINE_HEIGHT) / 2
  return node.lines
    .map(
      (line, index) =>
        `<tspan x="${textX}" ` +
        `dy="${index === 0 ? firstLineOffset : LINE_HEIGHT}">` +
        `${escapeXml(line)}</tspan>`
    )
    .join('')
}

/**
 * Render a tree as a self-contained, sanitizable SVG mindmap.
 */
export function renderMindmap(markdown) {
  const root = parseMindmap(markdown)
  const { nodes, width, height } = layoutTree(root)
  const links = nodes
    .filter((node) => node.depth > 0)
    .map((node) => {
      const parent = nodes.find((candidate) =>
        node.path.startsWith(`${candidate.path}.`)
          ? node.path.split('.').length === candidate.path.split('.').length + 1
          : false
      )
      if (!parent) return ''
      const color = branchColor(node)
      const startX = parent.dotX
      const endX = node.labelX - TEXT_GAP
      const middleX = (startX + endX) / 2
      return (
        `<path class="mindmap-link" data-mindmap-link="${node.path}" ` +
        `d="M${startX},${parent.y} C${middleX},${parent.y} ` +
        `${middleX},${node.y} ${endX},${node.y}" stroke="${color}" />`
      )
    })
    .join('')

  const renderedNodes = nodes
    .map((node) => {
      const color = branchColor(node)
      const radius = node.depth === 0 ? 7 : 6
      const label = node.depth === 0 ? '' : renderText(node)
      const dot = node.hasChildren
        ? `<circle cx="${node.dotX}" cy="${node.y}" r="${radius}" ` +
          `stroke="${color}" fill="white" />`
        : ''
      return (
        `<g class="mindmap-node" data-mindmap-toggle="true" ` +
        `data-mindmap-path="${node.path}" ` +
        `data-mindmap-has-children="${node.hasChildren}" ` +
        `role="treeitem" tabindex="0" aria-expanded="${node.hasChildren}" ` +
        `aria-label="${escapeXml(node.label)}">` +
        dot +
        `<text x="${node.labelX}" y="${node.y}" ` +
        `fill="currentColor" dominant-baseline="middle">${label}</text></g>`
      )
    })
    .join('')

  return (
    `<div class="markdown-mindmap" data-mindmap-root>` +
    `<div class="mindmap-canvas" role="group" ` +
    `aria-label="${escapeXml(root.label)}">` +
    `<svg class="mindmap-svg" role="tree" ` +
    `viewBox="0 0 ${width} ${height}" ` +
    `data-mindmap-width="${width}" data-mindmap-height="${height}" ` +
    `preserveAspectRatio="xMinYMin meet" ` +
    `aria-label="${escapeXml(root.label)}">` +
    `<g class="mindmap-links">${links}</g>` +
    `<g class="mindmap-nodes">${renderedNodes}</g>` +
    `</svg>` +
    `<pre class="mindmap-code-panel" aria-hidden="true">` +
    `<code>${escapeXml(markdown)}</code></pre></div>` +
    `<div class="mindmap-toolbar" role="toolbar" ` +
    `aria-label="思维导图操作">` +
    `<button type="button" class="mindmap-tool-button ` +
    `mindmap-tool-zoom-out" data-mindmap-action="zoom-out" ` +
    `aria-label="缩小" title="缩小"></button>` +
    `<button type="button" class="mindmap-tool-button ` +
    `mindmap-tool-zoom-in" data-mindmap-action="zoom-in" ` +
    `aria-label="放大" title="放大"></button>` +
    `<button type="button" class="mindmap-tool-button ` +
    `mindmap-tool-fit" data-mindmap-action="fit" ` +
    `aria-label="适应画布" title="适应画布"></button>` +
    `<button type="button" class="mindmap-tool-button ` +
    `mindmap-tool-fullscreen" data-mindmap-action="fullscreen" ` +
    `aria-label="全屏查看" title="全屏查看"></button>` +
    `<span class="mindmap-toolbar-divider" aria-hidden="true"></span>` +
    `<button type="button" class="mindmap-tool-code" ` +
    `data-mindmap-action="code" aria-label="查看代码" ` +
    `title="查看代码"><span aria-hidden="true">&lt;/&gt;</span>` +
    `<span class="mindmap-tool-code-label">查看代码</span></button>` +
    `</div></div>`
  )
}
