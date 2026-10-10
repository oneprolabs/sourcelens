const menuRoutes = [
  {
    menu: 'data',
    paths: ['/management/lens/datasources', '/management/lens/resources']
  },
  { menu: 'lens', paths: ['/management/lens'] },
  {
    menu: 'users',
    paths: ['/management/users', '/management/groups']
  },
  { menu: 'llm', paths: ['/management/llm'] },
  { menu: 'tasks', paths: ['/management/task-management'] },
  {
    menu: 'notifications',
    paths: ['/management/notifier', '/management/alerts']
  }
]

const isWithinPath = (path, parentPath) => {
  return path === parentPath || path.startsWith(`${parentPath}/`)
}

export const getAdminSidebarMenu = (path) => {
  return (
    menuRoutes.find(({ paths }) =>
      paths.some((parentPath) => isWithinPath(path, parentPath))
    )?.menu ?? null
  )
}

export const toggleAdminSidebarMenu = (openMenus, selectedMenu) => {
  const nextMenus = new Set(openMenus || [])
  if (nextMenus.has(selectedMenu)) {
    nextMenus.delete(selectedMenu)
  } else {
    nextMenus.add(selectedMenu)
  }
  return [...nextMenus]
}
