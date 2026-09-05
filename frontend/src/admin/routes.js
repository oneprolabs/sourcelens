/**
 * Admin (management) routes. Mount with ...adminRoutes in the main router (before 404/catch-all).
 */
export const adminRoutes = [
  {
    path: '/management',
    redirect: '/management/users'
  },
  {
    path: '/management/users',
    name: 'ManagementUsers',
    component: () => import('@/admin/pages/Management/Users.vue'),
    meta: { requiresAuth: true, requiredFeature: 'admin_console' }
  },
  {
    path: '/management/groups',
    name: 'ManagementGroups',
    component: () => import('@/admin/pages/Management/Groups.vue'),
    meta: { requiresAuth: true, requiredFeature: 'admin_console' }
  },
  {
    path: '/management/roles',
    redirect: '/management/users'
  },
  {
    path: '/management/lens',
    redirect: '/management/lens/assistants'
  },
  {
    path: '/management/lens/runs',
    name: 'LensRunObservation',
    component: () => import('@/admin/pages/lens/RunObservation.vue'),
    meta: { requiresAuth: true, requiredFeature: 'admin_console' }
  },
  {
    path: '/management/lens/shares',
    name: 'LensShareReview',
    component: () => import('@/admin/pages/lens/ShareReview.vue'),
    meta: { requiresAuth: true, requiredFeature: 'admin_console' }
  },
  {
    path: '/management/lens/assistants',
    name: 'LensAssistants',
    component: () => import('@/pages/lens/Assistants.vue'),
    meta: { requiresAuth: true, requiredFeature: 'admin_console' }
  },
  {
    path: '/management/lens/lensnodes',
    name: 'LensNodes',
    component: () => import('@/pages/lens/LensNodes.vue'),
    meta: { requiresAuth: true, requiredFeature: 'admin_console' }
  },
  {
    path: '/management/lens/datasources',
    name: 'LensDataSources',
    component: () => import('@/pages/lens/DataSources.vue'),
    meta: { requiresAuth: true, requiredFeature: 'admin_console' }
  },
  {
    path: '/management/lens/resources/connections',
    name: 'LensConnections',
    component: () => import('@/pages/lens/Connections.vue'),
    meta: { requiresAuth: true, requiredFeature: 'admin_console' }
  },
  {
    path: '/management/lens/resources/credentials',
    name: 'LensCredentials',
    component: () => import('@/pages/lens/Credentials.vue'),
    meta: { requiresAuth: true, requiredFeature: 'admin_console' }
  },
  {
    path: '/management/lens/resources/skills',
    name: 'LensSkills',
    component: () => import('@/pages/lens/Skills.vue'),
    meta: { requiresAuth: true, requiredFeature: 'admin_console' }
  },
  {
    path: '/management/lens/resources/mcp',
    name: 'LensMcp',
    component: () => import('@/pages/lens/Mcp.vue'),
    meta: { requiresAuth: true, requiredFeature: 'admin_console' }
  },
  {
    path: '/management/lens/resources/settings',
    name: 'LensResourceSettings',
    component: () => import('@/pages/lens/ResourceSettings.vue'),
    meta: { requiresAuth: true, requiredFeature: 'admin_console' }
  },
  {
    path: '/management/lens/settings',
    name: 'LensSettings',
    component: () => import('@/pages/lens/Settings.vue'),
    meta: { requiresAuth: true, requiredFeature: 'admin_console' }
  },
  {
    path: '/management/llm',
    redirect: '/management/llm/stats'
  },
  {
    path: '/management/llm/stats',
    name: 'LLMStats',
    component: () => import('@/admin/pages/LLM/Stats.vue'),
    meta: { requiresAuth: true, requiredFeature: 'admin_console' }
  },
  {
    path: '/management/llm/usage',
    name: 'LLMUsage',
    component: () => import('@/admin/pages/LLM/Usage.vue'),
    meta: { requiresAuth: true, requiredFeature: 'admin_console' }
  },
  {
    path: '/management/llm/config',
    name: 'LLMConfig',
    component: () => import('@/admin/pages/LLM/Config.vue'),
    meta: { requiresAuth: true, requiredFeature: 'admin_console' }
  },
  {
    path: '/management/llm/data-settings',
    name: 'LLMDataSettings',
    component: () => import('@/admin/pages/LLM/DataSettings.vue'),
    meta: { requiresAuth: true, requiredFeature: 'admin_console' }
  },
  {
    path: '/management/task-management',
    redirect: '/management/task-management/list'
  },
  {
    path: '/management/task-management/list',
    name: 'TaskManagementList',
    component: () => import('@/admin/pages/TaskManagement/List.vue'),
    meta: { requiresAuth: true, requiredFeature: 'admin_console' }
  },
  {
    path: '/management/task-management/stats',
    name: 'TaskManagementStats',
    component: () => import('@/admin/pages/TaskManagement/Stats.vue'),
    meta: { requiresAuth: true, requiredFeature: 'admin_console' }
  },
  {
    path: '/management/task-management/settings',
    name: 'TaskManagementSettings',
    component: () => import('@/admin/pages/TaskManagement/Settings.vue'),
    meta: { requiresAuth: true, requiredFeature: 'admin_console' }
  },
  {
    path: '/management/notifier',
    redirect: '/management/notifier/stats'
  },
  {
    path: '/management/notifier/stats',
    name: 'AdminNotificationsStats',
    component: () => import('@/admin/pages/Notifications/Stats.vue'),
    meta: { requiresAuth: true, requiredFeature: 'admin_console' }
  },
  {
    path: '/management/notifier/records',
    name: 'AdminNotificationsRecords',
    component: () => import('@/admin/pages/Notifications/Records.vue'),
    meta: { requiresAuth: true, requiredFeature: 'admin_console' }
  },
  {
    path: '/management/notifier/channels',
    name: 'AdminNotificationsChannels',
    component: () => import('@/admin/pages/Notifications/Channels.vue'),
    meta: { requiresAuth: true, requiredFeature: 'admin_console' }
  },
  {
    path: '/management/notifier/settings',
    name: 'AdminNotificationsSettings',
    component: () => import('@/admin/pages/Notifications/Config.vue'),
    meta: { requiresAuth: true, requiredFeature: 'admin_console' }
  },
  {
    path: '/management/notifier/config',
    redirect: '/management/notifier/settings'
  }
]
