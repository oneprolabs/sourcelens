function sortNodes(nodes) {
  nodes.sort((left, right) => {
    if (left.type !== right.type) {
      return left.type === 'directory' ? -1 : 1
    }
    return left.name.localeCompare(right.name, undefined, {
      numeric: true,
      sensitivity: 'base'
    })
  })
  nodes.forEach((node) => {
    if (node.type === 'directory') {
      sortNodes(node.children)
    }
  })
  return nodes
}

export function buildDataSourceFileTree(files) {
  const root = []

  ;(Array.isArray(files) ? files : []).forEach((file) => {
    const segments = String(file?.path || '')
      .replaceAll('\\', '/')
      .split('/')
      .filter(Boolean)
    if (!segments.length) return

    let children = root
    let parentPath = ''
    segments.slice(0, -1).forEach((name) => {
      const path = parentPath ? `${parentPath}/${name}` : name
      let directory = children.find(
        (node) => node.type === 'directory' && node.name === name
      )
      if (!directory) {
        directory = {
          type: 'directory',
          name,
          path,
          children: []
        }
        children.push(directory)
      }
      children = directory.children
      parentPath = path
    })

    const name = segments.at(-1)
    const path = parentPath ? `${parentPath}/${name}` : name
    const existing = children.find(
      (node) => node.type === 'file' && node.path === path
    )
    if (existing) {
      existing.file = file
      return
    }
    children.push({ type: 'file', name, path, file })
  })

  return sortNodes(root)
}
