export const FEATURE_DEFINITIONS = [
  {
    key: 'workspace',
    labelKey: 'platforms.workspace',
    defaultPath: '/dashboard',
    matchers: ['/dashboard', '/settings']
  },
  {
    key: 'admin_console',
    labelKey: 'platforms.adminConsole',
    defaultPath: '/management/users',
    matchers: [
      '/management',
      '/lens/admin',
      '/llm',
      '/task-management',
      '/notifier'
    ]
  },
  {
    key: 'run_evaluations',
    labelKey: 'platforms.runEvaluations',
    defaultPath: '/management/lens/evaluation',
    matchers: ['/management/lens/evaluation']
  }
]

const ADMIN_RETURN_PATH_KEY = 'sourcelens.adminReturnPath'

export function saveAdminReturnPath(path) {
  if (!path || !path.startsWith('/')) return
  window.sessionStorage.setItem(ADMIN_RETURN_PATH_KEY, path)
}

export function getAdminReturnPath() {
  const path = window.sessionStorage.getItem(ADMIN_RETURN_PATH_KEY)
  return path && path.startsWith('/') ? path : '/'
}

export function consumeAdminReturnPath() {
  const path = getAdminReturnPath()
  window.sessionStorage.removeItem(ADMIN_RETURN_PATH_KEY)
  return path
}

export const FEATURE_KEY_SET = new Set(
  FEATURE_DEFINITIONS.map((item) => item.key)
)

const FEATURE_MAP = new Map(FEATURE_DEFINITIONS.map((item) => [item.key, item]))

const FEATURE_ALIASES = {
  management: 'admin_console',
  llm_console: 'admin_console',
  task_management_console: 'admin_console',
  notification_console: 'admin_console'
}

export function isAdminUser(user) {
  return !!(user?.is_staff || user?.is_superuser)
}

export function normalizeFeatureKeys(values) {
  if (!Array.isArray(values)) return []

  const seen = new Set()
  return FEATURE_DEFINITIONS.map((item) => item.key).filter((key) => {
    const matches = values.some((value) => {
      const normalized = FEATURE_ALIASES[value] || value
      return normalized === key
    })
    return matches && !seen.has(key) && seen.add(key)
  })
}

export function normalizePlatformKey(value) {
  const normalized = FEATURE_ALIASES[value] || value
  return FEATURE_KEY_SET.has(normalized) ? normalized : ''
}

export function getAccessProfile(user) {
  return (
    user?.access_profile || {
      visible_features: [],
      available_platforms: [],
      preferred_platform: '',
      landing_path: '/dashboard'
    }
  )
}

export function hasFeature(user, featureKey) {
  const normalizedFeatureKey = FEATURE_ALIASES[featureKey] || featureKey

  if (normalizedFeatureKey === 'workspace') {
    return !!user
  }

  if (normalizedFeatureKey === 'run_evaluations' && isAdminUser(user)) return true

  if (normalizedFeatureKey === 'admin_console') {
    return isAdminUser(user)
  }

  const visibleFeatures = normalizeFeatureKeys(
    getAccessProfile(user).visible_features
  )
  return visibleFeatures.includes(normalizedFeatureKey)
}

export function hasPermission(user, permission) {
  if (!permission || (permission === 'lens.run_evaluations' && isAdminUser(user))) return true
  const permissions = Array.isArray(user?.permissions) ? user.permissions : []
  return permissions.includes(permission)
}

export function hasAnyPermission(user, permissions) {
  if (!Array.isArray(permissions) || permissions.length === 0) return true
  return permissions.some((permission) => hasPermission(user, permission))
}

export function getAvailablePlatforms(user, t) {
  if (!user) return []

  return FEATURE_DEFINITIONS.filter((item) => hasFeature(user, item.key)).map(
    (item) => ({
      key: item.key,
      label: t ? t(item.labelKey) : item.key,
      defaultPath: item.defaultPath
    })
  )
}

export function getLandingPath(user) {
  if (!user) return '/dashboard'
  // All authenticated users land on the assistant-centric home, which
  // resolves a default assistant and enters its chat (or shows the
  // no-assistant guide). This unifies admin and regular-user landing.
  return '/'
}

export function getCurrentPlatformKey(path) {
  const matched = FEATURE_DEFINITIONS.flatMap((item) =>
    item.matchers.filter((matcher) => path.startsWith(matcher)).map((matcher) => ({ item, matcher }))
  ).sort((a, b) => b.matcher.length - a.matcher.length)[0]?.item
  return matched?.key || 'workspace'
}

export function getPlatformByKey(platformKey, t) {
  const definition = FEATURE_MAP.get(platformKey)
  if (!definition) return null
  return {
    key: definition.key,
    label: t ? t(definition.labelKey) : definition.key,
    defaultPath: definition.defaultPath
  }
}
