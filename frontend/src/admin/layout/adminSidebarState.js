const menuRoutes = [
  { menu: 'platform', paths: ['/management/platform'] },
  {
    menu: 'data',
    paths: [
      '/management/lens/datasources',
      '/management/lens/resources/settings',
      '/management/lens/resources/skills',
      '/management/lens/resources/mcp'
    ]
  },
  {
    menu: 'plugins',
    paths: [
      '/management/lens/resources/connections',
      '/management/lens/resources/marketplace'
    ]
  },
  { menu: 'lens', paths: ['/management/lens'] },
  {
    menu: 'users',
    paths: ['/management/users', '/management/groups']
  },
  { menu: 'llm', paths: ['/management/llm'] },
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

export const getTenantMenuSearchItems = ({
  adminConsole,
  evaluations,
  delegated
}) => {
  if (!adminConsole) return []
  const groups = [
    [
      'tenantOverview.group',
      [
        ['/management/overview', 'tenantOverview.title'],
        ['/management/setup', 'tenantSetup.title']
      ]
    ],
    [
      'lensAdmin.menuTitle',
      [
        ['/management/lens/assistants', 'lensAdmin.pages.assistants.title'],
        ['/management/lens/runs', 'lensRuns.menu'],
        ...(evaluations
          ? [['/management/lens/evaluation', 'management.assistantEvaluation']]
          : []),
        ['/management/lens/shares', 'lens.qa.adminTitle']
      ]
    ],
    [
      'management.knowledgeManagement',
      [
        ['/management/lens/datasources', 'lensAdmin.pages.datasources.title'],
        [
          '/management/lens/resources/settings',
          'lensAdmin.pages.resourceSettings.title'
        ]
      ]
    ],
    [
      'management.pluginManagement',
      [
        ['/management/lens/resources/marketplace', 'catalog.marketplace'],
        [
          '/management/lens/resources/connections',
          'lensAdmin.pages.connections.title'
        ]
      ]
    ],
    ...(!delegated
      ? [
          [
            'management.tenantManagement',
            [
              ['/management/tenant', 'management.tenantInfo'],
              ['/management/users', 'management.userManagement'],
              ['/management/api-keys', 'management.tenantApiKeyTitle'],
              ['/management/groups', 'management.groupManagement']
            ]
          ]
        ]
      : []),
    [
      'llm.menuTitle',
      [
        ['/management/llm/stats', 'llm.stats.title'],
        ['/management/llm/usage', 'llm.usage.title'],
        ['/management/llm/config', 'llm.config.title']
      ]
    ],
    [
      'notificationManagement.menuTitle',
      [
        [
          '/management/notifier/channels',
          'notificationManagement.channels.menuTitle'
        ],
        ['/management/alerts/rules', 'alertManagement.rules.menuTitle'],
        ['/management/alerts/events', 'alertManagement.events.menuTitle']
      ]
    ]
  ]
  return groups.flatMap(([group, items]) =>
    items.map(([path, label]) => ({ path, label, group }))
  )
}
