/**
 * Admin (management) routes. Mount with ...adminRoutes in the main router (before 404/catch-all).
 */
export const adminRoutes = [
  {
    path: '/management/platform/catalog/connectors',
    name: 'PlatformCatalogConnectors',
    component: () => import('@/pages/lens/Marketplace.vue'),
    meta: {
      requiresAuth: true,
      requiredFeature: 'admin_console',
      connectorOnly: true
    }
  },
  {
    path: '/management/platform/catalog/skills',
    name: 'PlatformCatalogSkills',
    component: () => import('@/pages/lens/Skills.vue'),
    meta: {
      requiresAuth: true,
      requiredFeature: 'admin_console',
      catalogMode: 'official'
    }
  },
  {
    path: '/management/platform/catalog/mcp',
    name: 'PlatformCatalogMcp',
    component: () => import('@/pages/lens/Mcp.vue'),
    meta: {
      requiresAuth: true,
      requiredFeature: 'admin_console',
      catalogMode: 'official'
    }
  },
  {
    path: '/management/lens/resources/marketplace',
    name: 'LensMarketplace',
    component: () => import('@/pages/lens/Marketplace.vue'),
    meta: { requiresAuth: true, requiredFeature: 'admin_console' }
  },
  {
    path: '/management',
    redirect: '/management/overview'
  },
  {
    path: '/management/overview',
    name: 'ManagementTenantOverview',
    component: () => import('@/admin/pages/Management/TenantOverview.vue'),
    meta: { requiresAuth: true, requiredFeature: 'admin_console' }
  },
  {
    path: '/management/setup',
    name: 'ManagementTenantSetup',
    component: () => import('@/admin/pages/Management/TenantSetup.vue'),
    meta: { requiresAuth: true, requiredFeature: 'admin_console' }
  },
  {
    path: '/management/platform/overview',
    name: 'ManagementPlatformOverview',
    component: () => import('@/admin/pages/Platform/PlatformOverview.vue'),
    meta: { requiresAuth: true, requiredFeature: 'admin_console' }
  },
  {
    path: '/management/platform/tenant-stats',
    name: 'ManagementPlatformTenantStats',
    component: () => import('@/admin/pages/Platform/PlatformTenantStats.vue'),
    meta: { requiresAuth: true, requiredFeature: 'admin_console' }
  },
  {
    path: '/management/platform/members',
    name: 'ManagementPlatformMembers',
    component: () => import('@/admin/pages/Platform/PlatformMembers.vue'),
    meta: { requiresAuth: true, requiredFeature: 'admin_console' }
  },
  {
    path: '/management/platform/api-keys',
    name: 'ManagementPlatformApiKeys',
    component: () => import('@/admin/pages/Platform/PlatformApiKeys.vue'),
    meta: { requiresAuth: true, requiredFeature: 'admin_console' }
  },
  {
    path: '/management/platform/audit',
    name: 'ManagementPlatformAudit',
    component: () => import('@/admin/pages/Platform/PlatformAudit.vue'),
    meta: { requiresAuth: true, requiredFeature: 'admin_console' }
  },
  {
    path: '/management/platform/tenants',
    name: 'ManagementPlatformTenants',
    component: () => import('@/admin/pages/Platform/PlatformTenants.vue'),
    meta: { requiresAuth: true, requiredFeature: 'admin_console' }
  },
  {
    path: '/management/platform/nodes',
    name: 'PlatformNodes',
    component: () => import('@/pages/lens/LensNodes.vue'),
    meta: { requiresAuth: true, requiredFeature: 'admin_console' }
  },
  {
    path: '/management/platform/node-settings',
    name: 'PlatformNodeSettings',
    component: () => import('@/pages/lens/Settings.vue'),
    meta: {
      requiresAuth: true,
      requiredFeature: 'admin_console',
      settingsScope: 'nodes'
    }
  },
  {
    path: '/management/platform/settings',
    name: 'PlatformSettings',
    component: () => import('@/pages/lens/Settings.vue'),
    meta: {
      requiresAuth: true,
      requiredFeature: 'admin_console',
      settingsScope: 'global'
    }
  },
  {
    path: '/management/tenant',
    name: 'ManagementTenant',
    component: () => import('@/admin/pages/Management/Tenant.vue'),
    meta: { requiresAuth: true, requiredFeature: 'admin_console' }
  },
  {
    path: '/management/users',
    name: 'ManagementUsers',
    component: () => import('@/admin/pages/Management/Members.vue'),
    meta: { requiresAuth: true, requiredFeature: 'admin_console' }
  },
  {
    path: '/management/groups',
    name: 'ManagementGroups',
    component: () => import('@/admin/pages/Management/TenantGroups.vue'),
    meta: { requiresAuth: true, requiredFeature: 'admin_console' }
  },
  {
    path: '/management/api-keys',
    name: 'ManagementApiKeys',
    component: () => import('@/admin/pages/Management/TenantApiKeys.vue'),
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
    path: '/management/lens/evaluation',
    name: 'LensEvaluation',
    component: () => import('@/admin/pages/lens/Evaluation.vue'),
    meta: {
      requiresAuth: true,
      requiredFeature: 'run_evaluations',
      requiredPermission: 'lens.run_evaluations'
    }
  },
  {
    path: '/management/lens/evaluation/datasets/:uuid',
    name: 'LensEvaluationDataset',
    component: () => import('@/admin/pages/lens/EvaluationDataset.vue'),
    meta: {
      requiresAuth: true,
      requiredFeature: 'run_evaluations',
      requiredPermission: 'lens.run_evaluations'
    }
  },
  {
    path: '/management/lens/evaluation/runs/:uuid',
    name: 'LensEvaluationReport',
    component: () => import('@/admin/pages/lens/EvaluationReport.vue'),
    meta: {
      requiresAuth: true,
      requiredFeature: 'run_evaluations',
      requiredPermission: 'lens.run_evaluations'
    }
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
    redirect: '/management/platform/nodes'
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
    redirect: '/management/platform/settings'
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
    redirect: '/management/platform/llm/data-settings'
  },
  {
    path: '/management/platform/llm/stats',
    name: 'PlatformLLMStats',
    component: () => import('@/admin/pages/LLM/Stats.vue'),
    meta: { requiresAuth: true, platformOnly: true }
  },
  {
    path: '/management/platform/llm/usage',
    name: 'PlatformLLMUsage',
    component: () => import('@/admin/pages/LLM/Usage.vue'),
    meta: { requiresAuth: true, platformOnly: true }
  },
  {
    path: '/management/platform/llm/config',
    name: 'PlatformLLMConfig',
    component: () => import('@/admin/pages/LLM/Config.vue'),
    meta: { requiresAuth: true, platformOnly: true }
  },
  {
    path: '/management/platform/llm/data-settings',
    name: 'PlatformLLMDataSettings',
    component: () => import('@/admin/pages/LLM/DataSettings.vue'),
    meta: { requiresAuth: true, platformOnly: true }
  },
  {
    path: '/management/platform/tasks',
    redirect: '/management/platform/tasks/list'
  },
  {
    path: '/management/platform/tasks/list',
    name: 'TaskManagementList',
    component: () => import('@/admin/pages/TaskManagement/List.vue'),
    meta: { requiresAuth: true, requiredFeature: 'admin_console' }
  },
  {
    path: '/management/platform/tasks/stats',
    name: 'TaskManagementStats',
    component: () => import('@/admin/pages/TaskManagement/Stats.vue'),
    meta: { requiresAuth: true, requiredFeature: 'admin_console' }
  },
  {
    path: '/management/platform/tasks/settings',
    name: 'TaskManagementSettings',
    component: () => import('@/admin/pages/TaskManagement/Settings.vue'),
    meta: { requiresAuth: true, requiredFeature: 'admin_console' }
  },
  {
    path: '/management/task-management/:section(list|stats|settings)?',
    redirect: (to) => ({
      path: `/management/platform/tasks/${to.params.section || 'list'}`,
      query: to.query,
      hash: to.hash
    })
  },
  {
    path: '/management/platform/notifications/channels',
    name: 'PlatformNotificationsChannels',
    component: () => import('@/admin/pages/Notifications/Channels.vue'),
    meta: { requiresAuth: true, requiredFeature: 'admin_console' }
  },
  {
    path: '/management/platform/notifications/stats',
    name: 'PlatformNotificationsStats',
    component: () => import('@/admin/pages/Notifications/Stats.vue'),
    meta: { requiresAuth: true, requiredFeature: 'admin_console' }
  },
  {
    path: '/management/platform/notifications/records',
    name: 'PlatformNotificationsRecords',
    component: () => import('@/admin/pages/Notifications/Records.vue'),
    meta: { requiresAuth: true, requiredFeature: 'admin_console' }
  },
  {
    path: '/management/platform/notifications/settings',
    name: 'PlatformNotificationsSettings',
    component: () => import('@/admin/pages/Notifications/Config.vue'),
    meta: { requiresAuth: true, requiredFeature: 'admin_console' }
  },
  {
    path: '/management/platform/notifications/rules',
    name: 'PlatformNotificationsRules',
    component: () => import('@/admin/pages/Alerts/Rules.vue'),
    meta: { requiresAuth: true, requiredFeature: 'admin_console' }
  },
  {
    path: '/management/platform/notifications/events',
    name: 'PlatformNotificationsEvents',
    component: () => import('@/admin/pages/Alerts/Events.vue'),
    meta: { requiresAuth: true, requiredFeature: 'admin_console' }
  },
  {
    path: '/management/platform/notifications/messages',
    name: 'PlatformNotificationsMessages',
    component: () => import('@/admin/pages/Alerts/Messages.vue'),
    meta: { requiresAuth: true, requiredFeature: 'admin_console' }
  },
  {
    path: '/management/notifier',
    redirect: '/management/notifier/channels'
  },
  {
    path: '/management/notifier/stats',
    redirect: (to) => ({
      path: '/management/platform/notifications/stats',
      query: to.query,
      hash: to.hash
    })
  },
  {
    path: '/management/notifier/records',
    redirect: (to) => ({
      path: '/management/platform/notifications/records',
      query: to.query,
      hash: to.hash
    })
  },
  {
    path: '/management/notifier/channels',
    name: 'AdminNotificationsChannels',
    component: () => import('@/admin/pages/Notifications/Channels.vue'),
    meta: { requiresAuth: true, requiredFeature: 'admin_console' }
  },
  {
    path: '/management/notifier/settings',
    redirect: (to) => ({
      path: '/management/platform/notifications/settings',
      query: to.query,
      hash: to.hash
    })
  },
  {
    path: '/management/notifier/config',
    redirect: '/management/notifier/settings'
  },
  {
    path: '/management/alerts',
    redirect: '/management/alerts/rules'
  },
  {
    path: '/management/alerts/rules',
    name: 'AdminAlertRules',
    component: () => import('@/admin/pages/Alerts/Rules.vue'),
    meta: { requiresAuth: true, requiredFeature: 'admin_console' }
  },
  {
    path: '/management/alerts/messages',
    name: 'AdminAlertMessages',
    component: () => import('@/admin/pages/Alerts/Messages.vue'),
    meta: { requiresAuth: true, requiredFeature: 'admin_console' }
  },
  {
    path: '/management/alerts/events',
    name: 'AdminAlertEvents',
    component: () => import('@/admin/pages/Alerts/Events.vue'),
    meta: { requiresAuth: true, requiredFeature: 'admin_console' }
  }
].map((route) => ({
  ...route,
  meta: {
    ...route.meta,
    console: route.path.startsWith('/management/platform/')
      ? 'platform'
      : 'tenant',
    modelScope: /\/management\/(?:platform\/)?llm/.test(route.path)
      ? route.path.startsWith('/management/platform/')
        ? 'platform'
        : 'tenant'
      : undefined,
    notificationScope:
      /\/management\/(?:platform\/notifications|notifier|alerts)/.test(
        route.path
      )
        ? route.path.startsWith('/management/platform/')
          ? 'platform'
          : 'tenant'
        : undefined,
    tenantIdentityOnly: [
      '/management/users',
      '/management/groups',
      '/management/api-keys'
    ].includes(route.path)
  }
}))
