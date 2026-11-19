<template>
  <Transition
    enter-active-class="transition-opacity duration-200"
    enter-from-class="opacity-0"
    enter-to-class="opacity-100"
    leave-active-class="transition-opacity duration-150"
    leave-from-class="opacity-100"
    leave-to-class="opacity-0"
  >
    <div
      v-if="showMobileMenu && isMobile"
      @click="$emit('close')"
      class="layout-admin-overlay fixed inset-0 z-40 bg-ink-950/55 lg:hidden"
    />
  </Transition>

  <aside
    id="admin-sidebar"
    :inert="isMobile && !showMobileMenu"
    :aria-hidden="isMobile && !showMobileMenu"
    :class="[
      'layout-admin-sidebar flex h-full w-[282px] flex-shrink-0 flex-col  transition-transform duration-300 ease-in-out',
      isMobile ? 'fixed inset-y-0 left-0 z-50' : 'static',
      isMobile && !showMobileMenu ? '-translate-x-full' : 'translate-x-0'
    ]"
  >
    <div class="admin-nav-brand">
      <router-link to="/management" @click="isMobile && $emit('close')">
        <strong>SourceLens</strong>
        <small>{{ t('management.tenantManagement') }}</small>
      </router-link>
      <button
        v-if="isMobile"
        type="button"
        :aria-label="`${t('common.close')} ${t('management.logoTitle')}`"
        @click="$emit('close')"
        class="h-11 w-11 rounded-md p-2 text-ink-400 hover:bg-white/10 hover:text-white"
      >
        <svg
          class="w-5 h-5"
          fill="none"
          stroke="currentColor"
          viewBox="0 0 24 24"
        >
          <path
            stroke-linecap="round"
            stroke-linejoin="round"
            stroke-width="2"
            d="M6 18L18 6M6 6l12 12"
          />
        </svg>
      </button>
    </div>

    <SidebarMenuSearch
      v-model:searching="searching"
      :items="searchItems"
      :active="!isMobile || showMobileMenu"
      @navigate="isMobile && $emit('close')"
    />
    <nav
      v-show="!searching"
      ref="navigation"
      :aria-label="t('management.adminConsole')"
      class="admin-sidebar-navigation flex flex-1 flex-col space-y-1 overflow-y-auto px-3 py-4"
      tabindex="0"
      @keydown="handleNavigationKeydown"
      @scroll.passive="saveScrollPosition"
    >
      <div class="flex-1 space-y-1">
        <div v-if="userStore.userHasFeature('admin_console')" class="mb-4">
          <p class="px-3 pb-2 text-xs font-medium text-ink-400">
            {{ t('tenantOverview.group') }}
          </p>
          <router-link
            v-for="item in [
              { path: '/management/overview', label: 'tenantOverview.title' },
              { path: '/management/setup', label: 'tenantSetup.title' }
            ]"
            :key="item.path"
            :to="item.path"
            class="admin-nav-item"
            :class="isActive(item.path) ? 'admin-nav-item-active' : ''"
            @click="isMobile && $emit('close')"
          >
            <component
              :is="item.path.endsWith('setup') ? Zap : Sparkles"
              :size="16"
              aria-hidden="true"
            /><span>{{ t(item.label) }}</span>
          </router-link>
        </div>
        <div
          v-if="userStore.userHasFeature('admin_console')"
          class="menu-group"
        >
          <button
            @click="toggleLensMenu"
            :aria-expanded="lensMenuOpen"
            aria-controls="admin-sidebar-lens-menu"
            :class="[
              'admin-nav-item admin-nav-item-parent w-full',
              parentActiveClass('lens')
            ]"
          >
            <svg
              class="w-5 h-5"
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
            >
              <path
                stroke-linecap="round"
                stroke-linejoin="round"
                stroke-width="2"
                d="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z"
              />
            </svg>
            <span class="flex-1 text-left">{{ t('lensAdmin.menuTitle') }}</span>
            <svg
              class="w-4 h-4 transition-transform"
              :class="lensMenuOpen ? 'rotate-90' : ''"
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
            >
              <path
                stroke-linecap="round"
                stroke-linejoin="round"
                stroke-width="2"
                d="M9 5l7 7-7 7"
              />
            </svg>
          </button>
          <Transition
            @after-enter="keepActiveItemVisible"
            enter-active-class="transition-all duration-200 ease-out"
            enter-from-class="opacity-0 max-h-0"
            enter-to-class="opacity-100 max-h-96"
            leave-active-class="transition-all duration-200 ease-in"
            leave-from-class="opacity-100 max-h-96"
            leave-to-class="opacity-0 max-h-0"
          >
            <div
              v-if="lensMenuOpen"
              id="admin-sidebar-lens-menu"
              class="submenu"
            >
              <router-link
                to="/management/lens/assistants"
                class="admin-nav-item admin-nav-item-child"
                :class="
                  isActive('/management/lens/assistants')
                    ? 'admin-nav-item-active'
                    : ''
                "
                @click="isMobile && $emit('close')"
                @mouseenter="preloadRoute('/management/lens/assistants')"
              >
                <svg
                  class="w-4 h-4"
                  fill="none"
                  stroke="currentColor"
                  viewBox="0 0 24 24"
                >
                  <path
                    stroke-linecap="round"
                    stroke-linejoin="round"
                    stroke-width="2"
                    d="M8 10h.01M12 10h.01M16 10h.01M21 12c0 4.418-4.03 8-9 8a9.79 9.79 0 01-4-.82L3 20l1.82-4.91A7.8 7.8 0 013 12c0-4.418 4.03-8 9-8s9 3.582 9 8z"
                  />
                </svg>
                <span>{{ t('lensAdmin.pages.assistants.title') }}</span>
              </router-link>
              <router-link
                to="/management/lens/runs"
                class="admin-nav-item admin-nav-item-child"
                :class="
                  isActive('/management/lens/runs')
                    ? 'admin-nav-item-active'
                    : ''
                "
                @click="isMobile && $emit('close')"
                @mouseenter="preloadRoute('/management/lens/runs')"
              >
                <svg
                  class="w-4 h-4"
                  fill="none"
                  stroke="currentColor"
                  viewBox="0 0 24 24"
                >
                  <path
                    stroke-linecap="round"
                    stroke-linejoin="round"
                    stroke-width="2"
                    d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z"
                  />
                </svg>
                <span>{{ t('lensRuns.menu') }}</span>
              </router-link>
              <router-link
                v-if="userStore.userHasFeature('run_evaluations')"
                to="/management/lens/evaluation"
                class="admin-nav-item admin-nav-item-child"
                :class="
                  isActive('/management/lens/evaluation')
                    ? 'admin-nav-item-active'
                    : ''
                "
                @click="isMobile && $emit('close')"
                @mouseenter="preloadRoute('/management/lens/evaluation')"
              >
                <span class="w-4 text-center text-xs">◎</span>
                <span>{{ t('management.assistantEvaluation') }}</span>
              </router-link>
              <router-link
                to="/management/lens/shares"
                class="admin-nav-item admin-nav-item-child"
                :class="
                  isActive('/management/lens/shares')
                    ? 'admin-nav-item-active'
                    : ''
                "
                @click="isMobile && $emit('close')"
                @mouseenter="preloadRoute('/management/lens/shares')"
              >
                <svg
                  class="w-4 h-4"
                  fill="none"
                  stroke="currentColor"
                  viewBox="0 0 24 24"
                >
                  <path
                    stroke-linecap="round"
                    stroke-linejoin="round"
                    stroke-width="2"
                    d="M8.684 13.342C8.886 12.938 9 12.482 9 12c0-.482-.114-.938-.316-1.342m0 2.684a3 3 0 110-2.684m0 2.684l6.632 3.316m-6.632-6l6.632-3.316m0 0a3 3 0 105.367-2.684 3 3 0 00-5.367 2.684zm0 9.316a3 3 0 105.368 2.684 3 3 0 00-5.368-2.684z"
                  />
                </svg>
                <span>{{ t('lens.qa.adminTitle') }}</span>
              </router-link>
            </div>
          </Transition>
        </div>

        <div
          v-if="userStore.userHasFeature('admin_console')"
          class="menu-group"
        >
          <button
            @click="toggleDataManagementMenu"
            :aria-expanded="dataManagementMenuOpen"
            aria-controls="admin-sidebar-data-menu"
            :class="[
              'admin-nav-item admin-nav-item-parent w-full',
              parentActiveClass('data')
            ]"
          >
            <svg
              class="w-5 h-5"
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
            >
              <path
                stroke-linecap="round"
                stroke-linejoin="round"
                stroke-width="2"
                d="M4 7v10c0 2.21 3.582 4 8 4s8-1.79 8-4V7M4 7c0 2.21 3.582 4 8 4s8-1.79 8-4"
              />
            </svg>
            <span class="flex-1 text-left">{{
              t('management.knowledgeManagement')
            }}</span>
            <svg
              class="w-4 h-4 transition-transform"
              :class="dataManagementMenuOpen ? 'rotate-90' : ''"
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
            >
              <path
                stroke-linecap="round"
                stroke-linejoin="round"
                stroke-width="2"
                d="M9 5l7 7-7 7"
              />
            </svg>
          </button>
          <Transition
            @after-enter="keepActiveItemVisible"
            enter-active-class="transition-all duration-200 ease-out"
            enter-from-class="opacity-0 max-h-0"
            enter-to-class="opacity-100 max-h-96"
            leave-active-class="transition-all duration-200 ease-in"
            leave-from-class="opacity-100 max-h-96"
            leave-to-class="opacity-0 max-h-0"
          >
            <div
              v-if="dataManagementMenuOpen"
              id="admin-sidebar-data-menu"
              class="submenu"
            >
              <router-link
                to="/management/lens/datasources"
                class="admin-nav-item admin-nav-item-child"
                :class="
                  isActive('/management/lens/datasources')
                    ? 'admin-nav-item-active'
                    : ''
                "
                @click="isMobile && $emit('close')"
                @mouseenter="preloadRoute('/management/lens/datasources')"
              >
                <svg
                  class="w-4 h-4"
                  fill="none"
                  stroke="currentColor"
                  viewBox="0 0 24 24"
                >
                  <path
                    stroke-linecap="round"
                    stroke-linejoin="round"
                    stroke-width="2"
                    d="M4 7v10c0 2.21 3.582 4 8 4s8-1.79 8-4V7M4 7c0 2.21 3.582 4 8 4s8-1.79 8-4"
                  />
                </svg>
                <span>{{ t('lensAdmin.pages.datasources.title') }}</span>
              </router-link>
              <router-link
                to="/management/lens/resources/settings"
                class="admin-nav-item admin-nav-item-child"
                :class="
                  isActive('/management/lens/resources/settings')
                    ? 'admin-nav-item-active'
                    : ''
                "
                @click="isMobile && $emit('close')"
                @mouseenter="
                  preloadRoute('/management/lens/resources/settings')
                "
              >
                <svg
                  class="w-4 h-4"
                  fill="none"
                  stroke="currentColor"
                  viewBox="0 0 24 24"
                >
                  <path
                    stroke-linecap="round"
                    stroke-linejoin="round"
                    stroke-width="2"
                    d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z"
                  />
                  <path
                    stroke-linecap="round"
                    stroke-linejoin="round"
                    stroke-width="2"
                    d="M15 12a3 3 0 11-6 0 3 3 0 016 0z"
                  />
                </svg>
                <span>{{ t('lensAdmin.pages.resourceSettings.title') }}</span>
              </router-link>
            </div>
          </Transition>
        </div>

        <div
          v-if="userStore.userHasFeature('admin_console')"
          class="menu-group"
        >
          <button
            class="admin-nav-item admin-nav-item-parent w-full"
            :aria-expanded="pluginMenuOpen"
            aria-controls="admin-sidebar-plugin-menu"
            @click="toggleMenu('plugins')"
          >
            <span class="flex-1 text-left">{{
              t('management.pluginManagement')
            }}</span>
            <ChevronDown
              :size="12"
              :class="pluginMenuOpen ? '' : '-rotate-90'"
              aria-hidden="true"
            />
          </button>
          <div
            v-if="pluginMenuOpen"
            id="admin-sidebar-plugin-menu"
            class="submenu"
          >
            <router-link
              to="/management/lens/resources/connections"
              class="admin-nav-item admin-nav-item-child"
              :class="
                isActive('/management/lens/resources/connections')
                  ? 'admin-nav-item-active'
                  : ''
              "
              @click="isMobile && $emit('close')"
              @mouseenter="
                preloadRoute('/management/lens/resources/connections')
              "
            >
              <svg
                class="w-4 h-4"
                fill="none"
                stroke="currentColor"
                viewBox="0 0 24 24"
              >
                <path
                  stroke-linecap="round"
                  stroke-linejoin="round"
                  stroke-width="2"
                  d="M8 12h8m-4-4v8m9-4a9 9 0 11-18 0 9 9 0 0118 0z"
                />
              </svg>
              <span>{{ t('lensAdmin.pages.connections.title') }}</span>
            </router-link>
            <router-link
              to="/management/lens/resources/marketplace"
              class="admin-nav-item admin-nav-item-child"
              :class="
                isActive('/management/lens/resources/marketplace')
                  ? 'admin-nav-item-active'
                  : ''
              "
              @click="isMobile && $emit('close')"
            >
              <Puzzle class="h-4 w-4" aria-hidden="true" />
              <span>{{ t('catalog.marketplace') }}</span>
            </router-link>
          </div>
        </div>

        <div
          v-if="
            userStore.userHasFeature('admin_console') && !userStore.delegation
          "
          class="menu-group"
        >
          <button
            @click="toggleUserManagementMenu"
            :aria-expanded="userManagementMenuOpen"
            aria-controls="admin-sidebar-users-menu"
            :class="[
              'admin-nav-item admin-nav-item-parent w-full',
              parentActiveClass('users')
            ]"
          >
            <svg
              class="w-5 h-5"
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
            >
              <path
                stroke-linecap="round"
                stroke-linejoin="round"
                stroke-width="2"
                d="M12 4.354a4 4 0 110 5.292M15 21H3v-1a6 6 0 0112 0v1zm0 0h6v-1a6 6 0 00-9-5.197M13 7a4 4 0 11-8 0 4 4 0 018 0z"
              />
            </svg>
            <span class="flex-1 text-left">{{
              t('management.tenantManagement')
            }}</span>
            <svg
              class="w-4 h-4 transition-transform"
              :class="userManagementMenuOpen ? 'rotate-90' : ''"
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
            >
              <path
                stroke-linecap="round"
                stroke-linejoin="round"
                stroke-width="2"
                d="M9 5l7 7-7 7"
              />
            </svg>
          </button>
          <Transition
            @after-enter="keepActiveItemVisible"
            enter-active-class="transition-all duration-200 ease-out"
            enter-from-class="opacity-0 max-h-0"
            enter-to-class="opacity-100 max-h-96"
            leave-active-class="transition-all duration-200 ease-in"
            leave-from-class="opacity-100 max-h-96"
            leave-to-class="opacity-0 max-h-0"
          >
            <div
              v-if="userManagementMenuOpen"
              id="admin-sidebar-users-menu"
              class="submenu"
            >
              <router-link
                to="/management/tenant"
                class="admin-nav-item admin-nav-item-child"
                :class="
                  isActive('/management/tenant') ? 'admin-nav-item-active' : ''
                "
                @click="isMobile && $emit('close')"
                @mouseenter="preloadRoute('/management/tenant')"
              >
                <svg
                  class="w-4 h-4"
                  fill="none"
                  stroke="currentColor"
                  viewBox="0 0 24 24"
                >
                  <path
                    stroke-linecap="round"
                    stroke-linejoin="round"
                    stroke-width="2"
                    d="M3 21h18M5 21V7l7-4 7 4v14M9 21v-6h6v6"
                  />
                </svg>
                <span>{{ t('management.tenantInfo') }}</span>
              </router-link>
              <router-link
                v-if="!userStore.delegation"
                to="/management/users"
                class="admin-nav-item admin-nav-item-child"
                :class="
                  isActive('/management/users') ? 'admin-nav-item-active' : ''
                "
                @click="isMobile && $emit('close')"
                @mouseenter="preloadRoute('/management/users')"
              >
                <svg
                  class="w-4 h-4"
                  fill="none"
                  stroke="currentColor"
                  viewBox="0 0 24 24"
                >
                  <path
                    stroke-linecap="round"
                    stroke-linejoin="round"
                    stroke-width="2"
                    d="M12 4.354a4 4 0 110 5.292M15 21H3v-1a6 6 0 0112 0v1zm0 0h6v-1a6 6 0 00-9-5.197M13 7a4 4 0 11-8 0 4 4 0 018 0z"
                  />
                </svg>
                <span>{{ t('management.userManagement') }}</span>
              </router-link>
              <router-link
                v-if="!userStore.delegation"
                to="/management/api-keys"
                class="admin-nav-item admin-nav-item-child"
                :class="
                  isActive('/management/api-keys')
                    ? 'admin-nav-item-active'
                    : ''
                "
                @click="isMobile && $emit('close')"
                @mouseenter="preloadRoute('/management/api-keys')"
              >
                <svg
                  class="w-4 h-4"
                  fill="none"
                  stroke="currentColor"
                  viewBox="0 0 24 24"
                >
                  <path
                    stroke-linecap="round"
                    stroke-linejoin="round"
                    stroke-width="2"
                    d="M15 7a3 3 0 11-6 0 3 3 0 016 0zm-3 7v8m0-3h3m-3-3h3"
                  />
                </svg>
                <span>{{ t('management.tenantApiKeyTitle') }}</span>
              </router-link>
              <router-link
                v-if="!userStore.delegation"
                to="/management/groups"
                class="admin-nav-item admin-nav-item-child"
                :class="
                  isActive('/management/groups') ? 'admin-nav-item-active' : ''
                "
                @click="isMobile && $emit('close')"
                @mouseenter="preloadRoute('/management/groups')"
              >
                <svg
                  class="w-4 h-4"
                  fill="none"
                  stroke="currentColor"
                  viewBox="0 0 24 24"
                >
                  <path
                    stroke-linecap="round"
                    stroke-linejoin="round"
                    stroke-width="2"
                    d="M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0zm6 3a2 2 0 11-4 0 2 2 0 014 0zM7 10a2 2 0 11-4 0 2 2 0 014 0z"
                  />
                </svg>
                <span>{{ t('management.groupManagement') }}</span>
              </router-link>
            </div>
          </Transition>
        </div>

        <div
          v-if="userStore.userHasFeature('admin_console')"
          class="menu-group"
        >
          <button
            @click="toggleLLMMenu"
            :aria-expanded="llmMenuOpen"
            aria-controls="admin-sidebar-llm"
            :class="[
              'admin-nav-item admin-nav-item-parent w-full',
              parentActiveClass('llm')
            ]"
          >
            <svg
              class="w-5 h-5"
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
            >
              <path
                stroke-linecap="round"
                stroke-linejoin="round"
                stroke-width="2"
                d="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z"
              />
            </svg>
            <span class="flex-1 text-left">{{ t('llm.menuTitle') }}</span>
            <svg
              class="w-4 h-4 transition-transform"
              :class="llmMenuOpen ? 'rotate-90' : ''"
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
            >
              <path
                stroke-linecap="round"
                stroke-linejoin="round"
                stroke-width="2"
                d="M9 5l7 7-7 7"
              />
            </svg>
          </button>
          <Transition
            @after-enter="keepActiveItemVisible"
            enter-active-class="transition-all duration-200 ease-out"
            enter-from-class="opacity-0 max-h-0"
            enter-to-class="opacity-100 max-h-96"
            leave-active-class="transition-all duration-200 ease-in"
            leave-from-class="opacity-100 max-h-96"
            leave-to-class="opacity-0 max-h-0"
          >
            <div v-if="llmMenuOpen" id="admin-sidebar-llm" class="submenu">
              <router-link
                to="/management/llm/stats"
                class="admin-nav-item admin-nav-item-child"
                :class="
                  isActive('/management/llm/stats')
                    ? 'admin-nav-item-active'
                    : ''
                "
                @click="isMobile && $emit('close')"
                @mouseenter="preloadRoute('/management/llm/stats')"
              >
                <svg
                  class="w-4 h-4"
                  fill="none"
                  stroke="currentColor"
                  viewBox="0 0 24 24"
                >
                  <path
                    stroke-linecap="round"
                    stroke-linejoin="round"
                    stroke-width="2"
                    d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z"
                  />
                </svg>
                <span>{{ t('llm.stats.title') }}</span>
              </router-link>
              <router-link
                to="/management/llm/usage"
                class="admin-nav-item admin-nav-item-child"
                :class="
                  isActive('/management/llm/usage')
                    ? 'admin-nav-item-active'
                    : ''
                "
                @click="isMobile && $emit('close')"
                @mouseenter="preloadRoute('/management/llm/usage')"
              >
                <svg
                  class="w-4 h-4"
                  fill="none"
                  stroke="currentColor"
                  viewBox="0 0 24 24"
                >
                  <path
                    stroke-linecap="round"
                    stroke-linejoin="round"
                    stroke-width="2"
                    d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2m-3 7h3m-3 4h3m-6-4h.01M9 16h.01"
                  />
                </svg>
                <span>{{ t('llm.usage.title') }}</span>
              </router-link>
              <router-link
                to="/management/llm/config"
                class="admin-nav-item admin-nav-item-child"
                :class="
                  isActive('/management/llm/config')
                    ? 'admin-nav-item-active'
                    : ''
                "
                @click="isMobile && $emit('close')"
                @mouseenter="preloadRoute('/management/llm/config')"
              >
                <svg
                  class="w-4 h-4"
                  fill="none"
                  stroke="currentColor"
                  viewBox="0 0 24 24"
                >
                  <path
                    stroke-linecap="round"
                    stroke-linejoin="round"
                    stroke-width="2"
                    d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z"
                  />
                </svg>
                <span>{{ t('llm.config.title') }}</span>
              </router-link>
            </div>
          </Transition>
        </div>

        <div
          v-if="userStore.userHasFeature('admin_console')"
          class="menu-group"
        >
          <button
            @click="toggleNotificationManagementMenu"
            :aria-expanded="notificationManagementMenuOpen"
            aria-controls="admin-sidebar-notifications-menu"
            :class="[
              'admin-nav-item admin-nav-item-parent w-full',
              parentActiveClass('notifications')
            ]"
          >
            <svg
              class="w-5 h-5"
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
            >
              <path
                stroke-linecap="round"
                stroke-linejoin="round"
                stroke-width="2"
                d="M15 17h5l-1.405-1.405A2.032 2.032 0 0118 14.158V11a6.002 6.002 0 00-4-5.659V5a2 2 0 10-4 0v.341C7.67 6.165 6 8.388 6 11v3.159c0 .538-.214 1.055-.595 1.436L4 17h5m6 0v1a3 3 0 11-6 0v-1m6 0H9"
              />
            </svg>
            <span class="flex-1 text-left">{{
              t('notificationManagement.menuTitle')
            }}</span>
            <svg
              class="w-4 h-4 transition-transform"
              :class="notificationManagementMenuOpen ? 'rotate-90' : ''"
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
            >
              <path
                stroke-linecap="round"
                stroke-linejoin="round"
                stroke-width="2"
                d="M9 5l7 7-7 7"
              />
            </svg>
          </button>
          <Transition
            @after-enter="keepActiveItemVisible"
            enter-active-class="transition-all duration-200 ease-out"
            enter-from-class="opacity-0 max-h-0"
            enter-to-class="opacity-100 max-h-screen"
            leave-active-class="transition-all duration-200 ease-in"
            leave-from-class="opacity-100 max-h-screen"
            leave-to-class="opacity-0 max-h-0"
          >
            <div
              v-if="notificationManagementMenuOpen"
              id="admin-sidebar-notifications-menu"
              class="submenu"
            >
              <router-link
                to="/management/notifier/channels"
                class="admin-nav-item admin-nav-item-child"
                :class="
                  isActive('/management/notifier/channels')
                    ? 'admin-nav-item-active'
                    : ''
                "
                @click="isMobile && $emit('close')"
                @mouseenter="preloadRoute('/management/notifier/channels')"
              >
                <svg
                  class="w-4 h-4"
                  fill="none"
                  stroke="currentColor"
                  viewBox="0 0 24 24"
                >
                  <path
                    stroke-linecap="round"
                    stroke-linejoin="round"
                    stroke-width="2"
                    d="M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0z"
                  />
                </svg>
                <span>{{
                  t('notificationManagement.channels.menuTitle')
                }}</span>
              </router-link>
              <router-link
                to="/management/alerts/rules"
                class="admin-nav-item admin-nav-item-child"
                :class="
                  isActive('/management/alerts/rules')
                    ? 'admin-nav-item-active'
                    : ''
                "
                @click="isMobile && $emit('close')"
                @mouseenter="preloadRoute('/management/alerts/rules')"
              >
                <svg
                  class="w-4 h-4"
                  fill="none"
                  stroke="currentColor"
                  viewBox="0 0 24 24"
                >
                  <path
                    stroke-linecap="round"
                    stroke-linejoin="round"
                    stroke-width="2"
                    d="M12 9v3m0 0v3m0-3h3m-3 0H9m12 0a9 9 0 11-18 0 9 9 0 0118 0z"
                  />
                </svg>
                <span>{{ t('alertManagement.rules.menuTitle') }}</span>
              </router-link>
              <router-link
                to="/management/alerts/events"
                class="admin-nav-item admin-nav-item-child"
                :class="
                  isActive('/management/alerts/events')
                    ? 'admin-nav-item-active'
                    : ''
                "
                @click="isMobile && $emit('close')"
                @mouseenter="preloadRoute('/management/alerts/events')"
              >
                <svg
                  class="w-4 h-4"
                  fill="none"
                  stroke="currentColor"
                  viewBox="0 0 24 24"
                >
                  <path
                    stroke-linecap="round"
                    stroke-linejoin="round"
                    stroke-width="2"
                    d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2"
                  />
                </svg>
                <span>{{ t('alertManagement.events.menuTitle') }}</span>
              </router-link>
            </div>
          </Transition>
        </div>
      </div>
    </nav>
    <AdminAccountMenu />
  </aside>
</template>

<script setup>
import { ChevronDown, Puzzle, Sparkles, Zap } from '@lucide/vue'
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import {
  getAdminSidebarMenu,
  getTenantMenuSearchItems,
  toggleAdminSidebarMenu
} from '@/admin/layout/adminSidebarState'
import AdminAccountMenu from './AdminAccountMenu.vue'
import SidebarMenuSearch from './SidebarMenuSearch.vue'
import { useIsMobile } from '@/composables/useIsMobile'
import { useUiStore } from '@/store/ui'
import { useUserStore } from '@/store/user'

defineProps({
  showMobileMenu: {
    type: Boolean,
    default: false
  }
})

defineEmits(['close'])

const { t } = useI18n()
const route = useRoute()
const router = useRouter()
const uiStore = useUiStore()
const userStore = useUserStore()
const searching = ref(false)
const searchItems = computed(() =>
  getTenantMenuSearchItems({
    adminConsole: userStore.userHasFeature('admin_console'),
    evaluations: userStore.userHasFeature('run_evaluations'),
    delegated: !!userStore.delegation
  }).map((item) => ({
    ...item,
    label: t(item.label),
    group: t(item.group)
  }))
)
const navigation = ref(null)
const activeMenu = computed(() => getAdminSidebarMenu(route.path))
const openMenus = ref(
  new Set(['lens', 'data', 'plugins', 'users', 'llm', 'notifications'])
)
const userManagementMenuOpen = computed(() => openMenus.value.has('users'))
const lensMenuOpen = computed(() => openMenus.value.has('lens'))
const pluginMenuOpen = computed(() => openMenus.value.has('plugins'))
const dataManagementMenuOpen = computed(() => openMenus.value.has('data'))
const llmMenuOpen = computed(() => openMenus.value.has('llm'))
const notificationManagementMenuOpen = computed(() =>
  openMenus.value.has('notifications')
)

const { isMobile } = useIsMobile(821)

const saveScrollPosition = () => {
  if (!navigation.value) return
  uiStore.adminSidebarScrollTop = navigation.value.scrollTop
}

const handleNavigationKeydown = (event) => {
  const container = navigation.value
  if (!container || event.target !== container) return

  const scrollAmounts = {
    ArrowDown: 40,
    ArrowUp: -40,
    PageDown: container.clientHeight,
    PageUp: -container.clientHeight
  }
  const top = scrollAmounts[event.key]
  if (top === undefined) return

  event.preventDefault()
  container.scrollBy({ top })
}

const keepActiveItemVisible = () => {
  const container = navigation.value
  const activeItem = container?.querySelector('.admin-nav-item-active')
  if (!container || !activeItem) return

  const containerRect = container.getBoundingClientRect()
  const activeRect = activeItem.getBoundingClientRect()

  if (activeRect.top < containerRect.top) {
    container.scrollTop -= containerRect.top - activeRect.top
  } else if (activeRect.bottom > containerRect.bottom) {
    container.scrollTop += activeRect.bottom - containerRect.bottom
  }

  uiStore.adminSidebarScrollTop = container.scrollTop
}

const isActive = (path) => {
  return route.path === path || route.path.startsWith(path + '/')
}

const parentActiveClass = (menu) => {
  return activeMenu.value === menu && !openMenus.value.has(menu)
    ? 'admin-nav-item-parent-active'
    : ''
}

const toggleMenu = (menu) => {
  openMenus.value = new Set(toggleAdminSidebarMenu(openMenus.value, menu))
}

const toggleUserManagementMenu = () => toggleMenu('users')
const toggleLensMenu = () => toggleMenu('lens')
const toggleDataManagementMenu = () => toggleMenu('data')
const toggleLLMMenu = () => toggleMenu('llm')
const toggleNotificationManagementMenu = () => toggleMenu('notifications')

watch(
  () => route.path,
  async (newPath) => {
    const routeMenu = getAdminSidebarMenu(newPath)
    if (routeMenu) openMenus.value.add(routeMenu)

    if (navigation.value) {
      await nextTick()
      keepActiveItemVisible()
    }
  },
  { immediate: true }
)

onMounted(async () => {
  await nextTick()
  navigation.value.scrollTop = uiStore.adminSidebarScrollTop
  keepActiveItemVisible()
})

const preloadCache = new Set()
const preloadRoute = (path) => {
  if (preloadCache.has(path)) return
  try {
    const r = router.resolve(path)
    if (r.matched.length > 0 && r.matched[0].components) {
      Object.values(r.matched[0].components).forEach((component) => {
        if (typeof component === 'function') {
          preloadCache.add(path)
          component().catch(() => preloadCache.delete(path))
        }
      })
    }
  } catch (_) {
    preloadCache.delete(path)
  }
}
</script>

<style scoped>
.admin-sidebar-navigation {
  scrollbar-width: thin;
  scrollbar-color: var(--sl-border-strong) transparent;
  padding: 8px;
}
.admin-nav-item {
  display: flex;
  min-height: 40px;
  align-items: center;
  gap: 9px;
  border-radius: 12px;
  padding: 0 8px;
  color: var(--admin-nav-fg);
  font-size: 14px;
  font-weight: 500;
  transition: background-color 150ms;
}
.admin-nav-item:hover {
  background: var(--admin-nav-hover);
}
.admin-nav-item-active,
.admin-nav-item-child.admin-nav-item-active {
  background: var(--admin-nav-selected);
  color: var(--admin-nav-on);
  font-weight: 600;
  box-shadow:
    0 1px 2px rgba(40, 50, 90, 0.08),
    0 2px 8px rgba(40, 50, 90, 0.05);
}
.admin-nav-item-parent {
  min-height: 28px;
  border-radius: 0;
  padding: 4px 8px 6px;
  color: var(--sl-text-subtle);
  font-size: 12.5px;
  font-weight: 600;
}
.admin-nav-item-parent > svg:first-child {
  display: none;
}
.admin-nav-item-parent > svg:last-child {
  width: 12px;
  height: 12px;
  margin-left: auto;
}
.admin-nav-item-child {
  margin: 0;
}
.admin-nav-item svg {
  width: 16px;
  height: 16px;
  flex-shrink: 0;
}
.menu-group {
  margin: 10px 0 0;
}
.submenu {
  margin: 0;
  padding: 0;
}
@media (max-width: 820px) {
  .admin-nav-item {
    min-height: 44px;
  }
}
</style>
