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

function wrapLabel(label, maxChars = 28) {
  const chars = Array.from(label)
  const lines = []
  for (let index = 0; index < chars.length; index += maxChars) {
    lines.push(chars.slice(index, index + maxChars).join(''))
  }
  return lines.length ? lines : ['']
}

function layoutTree(root) {
  const nodes = flattenTree(root)
  let nextY = 28

  const place = (node) => {
    node.x = 24 + node.depth * 236
    if (!node.children.length) {
      node.y = nextY
      nextY += Math.max(30, wrapLabel(node.label).length * 20 + 10)
      return
    }
    node.children.forEach(place)
    node.y =
      (node.children[0].y + node.children[node.children.length - 1].y) / 2
  }
  place(root)

  const maxTextWidth = nodes.reduce((width, node) => {
    const longestLine = Math.max(
      ...wrapLabel(node.label).map((line) => Array.from(line).length)
    )
    return Math.max(width, longestLine * 9)
  }, 0)

  return {
    nodes,
    width: Math.max(560, 24 + rootDepth(root) * 236 + maxTextWidth + 60),
    height: Math.max(150, nextY + 12)
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
  return wrapLabel(node.label)
    .map(
      (line, index) =>
        `<tspan x="12" dy="${index === 0 ? 0 : 18}">${escapeXml(line)}</tspan>`
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
      const middleX = (parent.x + node.x) / 2
      return (
        `<path class="mindmap-link" data-mindmap-link="${node.path}" ` +
        `d="M${parent.x},${parent.y} C${middleX},${parent.y} ` +
        `${middleX},${node.y} ${node.x},${node.y}" stroke="${color}" />`
      )
    })
    .join('')

  const renderedNodes = nodes
    .map((node) => {
      const color = branchColor(node)
      const hasChildren = node.children.length > 0
      const radius = node.depth === 0 ? 7 : hasChildren ? 6 : 4
      const label = node.depth === 0 ? '' : renderText(node)
      return (
        `<g class="mindmap-node" data-mindmap-toggle="true" ` +
        `data-mindmap-path="${node.path}" ` +
        `data-mindmap-has-children="${hasChildren}" ` +
        `role="treeitem" tabindex="0" aria-expanded="${hasChildren}" ` +
        `aria-label="${escapeXml(node.label)}" ` +
        `transform="translate(${node.x} ${node.y})">` +
        `<circle cx="0" cy="0" r="${radius}" ` +
        `stroke="${color}" fill="white" />` +
        `<text fill="currentColor">${label}</text></g>`
      )
    })
    .join('')

  return (
    `<div class="markdown-mindmap" data-mindmap-root>` +
    `<div class="mindmap-toolbar" role="toolbar" ` +
    `aria-label="思维导图操作">` +
    `<span class="mindmap-toolbar-title">思维导图</span>` +
    `<button type="button" data-mindmap-action="expand" ` +
    `aria-label="全部展开">全部展开</button>` +
    `<button type="button" data-mindmap-action="collapse" ` +
    `aria-label="全部收起">全部收起</button>` +
    `</div>` +
    `<div class="mindmap-canvas" role="group" ` +
    `aria-label="${escapeXml(root.label)}">` +
    `<svg class="mindmap-svg" role="tree" ` +
    `viewBox="0 0 ${width} ${height}" ` +
    `preserveAspectRatio="xMinYMin meet" ` +
    `aria-label="${escapeXml(root.label)}">` +
    `<g class="mindmap-links">${links}</g>` +
    `<g class="mindmap-nodes">${renderedNodes}</g>` +
    `</svg></div></div>`
  )
}
