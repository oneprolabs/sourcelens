import { createRouter, createWebHistory } from 'vue-router'
import { useUserStore } from '@/store/user'
import { adminRoutes } from '@/admin/routes'
import {
  getLandingPath,
  hasAnyPermission,
  hasFeature,
  hasPermission
} from '@/utils/platformAccess'

const routes = [
  {
    path: '/',
    name: 'Home',
    component: () => import('@/pages/Home.vue'),
    meta: { requiresAuth: true }
  },
  {
    path: '/login',
    name: 'Login',
    component: () => import('@/pages/Auth.vue'),
    meta: { requiresGuest: true }
  },
  {
    path: '/reset-password/:uid/:token',
    name: 'ResetPassword',
    component: () => import('@/pages/ResetPassword.vue'),
    meta: { requiresGuest: true }
  },
  {
    path: '/dashboard',
    name: 'Dashboard',
    component: () => import('@/pages/Dashboard.vue'),
    meta: { requiresAuth: true, requiredFeature: 'workspace' }
  },
  {
    path: '/settings',
    redirect: '/dashboard'
  },
  {
    path: '/settings/profile',
    redirect: '/dashboard'
  },
  {
    path: '/llm',
    redirect: '/management/llm/stats'
  },
  {
    path: '/llm/stats',
    redirect: '/management/llm/stats'
  },
  {
    path: '/llm/usage',
    redirect: '/management/llm/usage'
  },
  {
    path: '/llm/config',
    redirect: '/management/llm/config'
  },
  {
    path: '/llm/:pathMatch(.*)*',
    redirect: '/management/llm/stats'
  },
  {
    path: '/task-management',
    redirect: '/management/task-management/list'
  },
  {
    path: '/task-management/list',
    redirect: '/management/task-management/list'
  },
  {
    path: '/task-management/stats',
    redirect: '/management/task-management/stats'
  },
  {
    path: '/task-management/settings',
    redirect: '/management/task-management/settings'
  },
  {
    path: '/task-management/:pathMatch(.*)*',
    redirect: '/management/task-management/list'
  },
  {
    path: '/lens/chat',
    name: 'LensSmartChat',
    component: () => import('@/pages/lens/Chat.vue'),
    meta: { requiresAuth: true }
  },
  {
    path: '/lens/assistants/:slug',
    redirect: (to) => `/lens/assistants/${to.params.slug}/chat`
  },
  {
    path: '/lens/assistants/:slug/chat',
    name: 'LensAssistantChat',
    component: () => import('@/pages/lens/Chat.vue'),
    props: (route) => ({
      assistantSlug: route.params.slug
    }),
    meta: { allowAnonymous: true }
  },
  {
    path: '/lens/assistants/:slug/qa',
    name: 'PublicQaList',
    component: () => import('@/pages/lens/PublicQaList.vue'),
    props: (route) => ({ slug: route.params.slug }),
    meta: { allowAnonymous: true }
  },
  {
    path: '/lens/qa/:token',
    name: 'PublicQa',
    component: () => import('@/pages/lens/PublicQa.vue'),
    props: (route) => ({ token: route.params.token }),
    meta: { allowAnonymous: true }
  },
  {
    path: '/lens/assistants/:slug/history',
    name: 'LensAssistantHistory',
    component: () => import('@/pages/lens/History.vue'),
    meta: { requiresAuth: true }
  },
  {
    path: '/lens/admin/:pathMatch(.*)*',
    redirect: (to) => {
      const pathMatch = to.params.pathMatch
      const suffix = Array.isArray(pathMatch)
        ? pathMatch.join('/')
        : pathMatch || 'assistants'
      return `/management/lens/${suffix}`
    }
  },
  {
    path: '/notifier',
    redirect: '/management/notifier/channels'
  },
  {
    path: '/notifier/stats',
    redirect: '/management/notifier/channels'
  },
  {
    path: '/notifier/records',
    redirect: '/management/notifier/records'
  },
  {
    path: '/notifier/channels',
    redirect: '/management/notifier/channels'
  },
  {
    path: '/notifier/settings',
    redirect: '/management/notifier/settings'
  },
  {
    path: '/notifier/config',
    redirect: '/management/notifier/settings'
  },
  {
    path: '/notifier/:pathMatch(.*)*',
    redirect: '/management/notifier/channels'
  },
  ...adminRoutes,
  {
    path: '/404',
    name: 'NotFound',
    component: () => import('@/pages/NotFound.vue')
  },
  {
    path: '/:pathMatch(.*)*',
    name: 'CatchAll',
    component: () => import('@/pages/NotFound.vue')
  }
]

const router = createRouter({
  history: createWebHistory(),
  routes,
  scrollBehavior() {
    // Always scroll to top on route change for better UX
    return { top: 0 }
  }
})

// Navigation guards
router.beforeEach(async (to, from, next) => {
  const userStore = useUserStore()

  if (to.meta.requiresAuth) {
    const hasToken = !!localStorage.getItem('access_token')

    if (!hasToken) {
      next({ path: '/login', query: { next: to.fullPath } })
      return
    }

    if (!userStore.user) {
      try {
        const authSuccess = await userStore.checkAuth()
        if (!authSuccess) {
          next({ path: '/login', query: { next: to.fullPath } })
          return
        }
      } catch {
        next({ path: '/login', query: { next: to.fullPath } })
        return
      }
    }

    if (
      to.meta.requiredFeature &&
      !hasFeature(userStore.userInfo, to.meta.requiredFeature)
    ) {
      next(getLandingPath(userStore.userInfo))
      return
    }

    if (
      to.meta.requiredPermission &&
      !hasPermission(userStore.userInfo, to.meta.requiredPermission)
    ) {
      next(getLandingPath(userStore.userInfo))
      return
    }

    if (
      to.meta.requiredAnyPermission &&
      !hasAnyPermission(userStore.userInfo, to.meta.requiredAnyPermission)
    ) {
      next(getLandingPath(userStore.userInfo))
      return
    }

    next()
  } else if (to.meta.requiresGuest && userStore.isAuthenticated) {
    next(getLandingPath(userStore.userInfo))
  } else {
    next()
  }
})

export default router
