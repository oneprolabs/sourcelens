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
    :class="[
      'layout-admin-sidebar flex h-full w-60 flex-shrink-0 flex-col border-r border-ink-700 bg-ink-900 transition-transform duration-300 ease-in-out',
      isMobile ? 'fixed inset-y-0 left-0 z-50' : 'static',
      isMobile && !showMobileMenu ? '-translate-x-full' : 'translate-x-0'
    ]"
  >
    <div
      class="flex h-14 items-center justify-between border-b border-ink-800 px-4"
    >
      <router-link
        to="/management"
        class="flex flex-1 items-center"
        @click="isMobile && $emit('close')"
      >
        <BrandLogo
          variant="wordmark"
          tone="dark"
          wrapperClass="-ml-[25px] mix-blend-screen [&_img]:!w-48"
        />
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

    <nav
      ref="navigation"
      :aria-label="t('management.adminConsole')"
      class="admin-sidebar-navigation flex flex-1 flex-col space-y-1 overflow-y-auto px-3 py-4"
      tabindex="0"
      @keydown="handleNavigationKeydown"
      @scroll.passive="saveScrollPosition"
    >
      <div class="flex-1 space-y-1">
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
              <router-link
                to="/management/lens/lensnodes"
                class="admin-nav-item admin-nav-item-child"
                :class="
                  isActive('/management/lens/lensnodes')
                    ? 'admin-nav-item-active'
                    : ''
                "
                @click="isMobile && $emit('close')"
                @mouseenter="preloadRoute('/management/lens/lensnodes')"
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
                    d="M5 12h14M5 12a2 2 0 01-2-2V6a2 2 0 012-2h14a2 2 0 012 2v4a2 2 0 01-2 2M5 12a2 2 0 00-2 2v4a2 2 0 002 2h14a2 2 0 002-2v-4a2 2 0 00-2-2M7 8h.01M7 16h.01"
                  />
                </svg>
                <span>{{ t('lensAdmin.pages.lensnodes.title') }}</span>
              </router-link>
              <router-link
                to="/management/lens/settings"
                class="admin-nav-item admin-nav-item-child"
                :class="
                  isActive('/management/lens/settings')
                    ? 'admin-nav-item-active'
                    : ''
                "
                @click="isMobile && $emit('close')"
                @mouseenter="preloadRoute('/management/lens/settings')"
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
                    d="M12 6V4m0 2a2 2 0 100 4m0-4a2 2 0 110 4m-6 8a2 2 0 100-4m0 4a2 2 0 110-4m0 4v2m0-6V4m6 6v10m6-2a2 2 0 100-4m0 4a2 2 0 110-4m0 4v2m0-6V4"
                  />
                </svg>
                <span>{{ t('lensAdmin.tabs.settings') }}</span>
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
              t('dataManagement.menuTitle')
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
                to="/management/lens/resources/credentials"
                class="admin-nav-item admin-nav-item-child"
                :class="
                  isActive('/management/lens/resources/credentials')
                    ? 'admin-nav-item-active'
                    : ''
                "
                @click="isMobile && $emit('close')"
                @mouseenter="
                  preloadRoute('/management/lens/resources/credentials')
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
                    d="M15 7a2 2 0 012 2m4 0a6 6 0 01-7.743 5.743L11 17H9v2H7v2H3v-4.586l5.257-5.257A6 6 0 1121 9z"
                  />
                </svg>
                <span>{{ t('lensAdmin.pages.credentials.title') }}</span>
              </router-link>
              <router-link
                to="/management/lens/resources/skills"
                class="admin-nav-item admin-nav-item-child"
                :class="
                  isActive('/management/lens/resources/skills')
                    ? 'admin-nav-item-active'
                    : ''
                "
                @click="isMobile && $emit('close')"
                @mouseenter="preloadRoute('/management/lens/resources/skills')"
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
                    d="M13 10V3L4 14h7v7l9-11h-7z"
                  />
                </svg>
                <span>{{ t('lensAdmin.tabs.skills') }}</span>
              </router-link>
              <router-link
                to="/management/lens/resources/mcp"
                class="admin-nav-item admin-nav-item-child"
                :class="
                  isActive('/management/lens/resources/mcp')
                    ? 'admin-nav-item-active'
                    : ''
                "
                @click="isMobile && $emit('close')"
                @mouseenter="preloadRoute('/management/lens/resources/mcp')"
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
                    d="M8 9l3 3-3 3m5 0h3M5 20h14a2 2 0 002-2V6a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z"
                  />
                </svg>
                <span>{{ t('lensAdmin.tabs.mcp') }}</span>
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
              t('management.userManagement')
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
              <router-link
                to="/management/llm/data-settings"
                class="admin-nav-item admin-nav-item-child"
                :class="
                  isActive('/management/llm/data-settings')
                    ? 'admin-nav-item-active'
                    : ''
                "
                @click="isMobile && $emit('close')"
                @mouseenter="preloadRoute('/management/llm/data-settings')"
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
                    d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z"
                  />
                </svg>
                <span>{{ t('llm.dataSettings.title') }}</span>
              </router-link>
            </div>
          </Transition>
        </div>

        <div
          v-if="userStore.userHasFeature('admin_console')"
          class="menu-group"
        >
          <button
            @click="toggleTaskManagementMenu"
            :aria-expanded="taskManagementMenuOpen"
            aria-controls="admin-sidebar-tasks-menu"
            :class="[
              'admin-nav-item admin-nav-item-parent w-full',
              parentActiveClass('tasks')
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
                d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2m-3 7h3m-3 4h3m-6-4h.01M9 16h.01"
              />
            </svg>
            <span class="flex-1 text-left">{{
              t('taskManagement.menuTitle')
            }}</span>
            <svg
              class="w-4 h-4 transition-transform"
              :class="taskManagementMenuOpen ? 'rotate-90' : ''"
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
              v-if="taskManagementMenuOpen"
              id="admin-sidebar-tasks-menu"
              class="submenu"
            >
              <router-link
                to="/management/task-management/stats"
                class="admin-nav-item admin-nav-item-child"
                :class="
                  isActive('/management/task-management/stats')
                    ? 'admin-nav-item-active'
                    : ''
                "
                @click="isMobile && $emit('close')"
                @mouseenter="preloadRoute('/management/task-management/stats')"
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
                <span>{{ t('taskManagement.stats.title') }}</span>
              </router-link>
              <router-link
                to="/management/task-management/list"
                class="admin-nav-item admin-nav-item-child"
                :class="
                  isActive('/management/task-management/list')
                    ? 'admin-nav-item-active'
                    : ''
                "
                @click="isMobile && $emit('close')"
                @mouseenter="preloadRoute('/management/task-management/list')"
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
                    d="M4 6h16M4 10h16M4 14h16M4 18h16"
                  />
                </svg>
                <span>{{ t('taskManagement.list.title') }}</span>
              </router-link>
              <router-link
                to="/management/task-management/settings"
                class="admin-nav-item admin-nav-item-child"
                :class="
                  isActive('/management/task-management/settings')
                    ? 'admin-nav-item-active'
                    : ''
                "
                @click="isMobile && $emit('close')"
                @mouseenter="
                  preloadRoute('/management/task-management/settings')
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
                </svg>
                <span>{{ t('taskManagement.settings.title') }}</span>
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
            enter-to-class="opacity-100 max-h-96"
            leave-active-class="transition-all duration-200 ease-in"
            leave-from-class="opacity-100 max-h-96"
            leave-to-class="opacity-0 max-h-0"
          >
            <div
              v-if="notificationManagementMenuOpen"
              id="admin-sidebar-notifications-menu"
              class="submenu"
            >
              <router-link
                to="/management/notifier/stats"
                class="admin-nav-item admin-nav-item-child"
                :class="
                  isActive('/management/notifier/stats')
                    ? 'admin-nav-item-active'
                    : ''
                "
                @click="isMobile && $emit('close')"
                @mouseenter="preloadRoute('/management/notifier/stats')"
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
                <span>{{ t('notificationManagement.stats.title') }}</span>
              </router-link>
              <router-link
                to="/management/notifier/records"
                class="admin-nav-item admin-nav-item-child"
                :class="
                  isActive('/management/notifier/records')
                    ? 'admin-nav-item-active'
                    : ''
                "
                @click="isMobile && $emit('close')"
                @mouseenter="preloadRoute('/management/notifier/records')"
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
                    d="M4 6h16M4 10h16M4 14h16M4 18h16"
                  />
                </svg>
                <span>{{ t('notificationManagement.records.title') }}</span>
              </router-link>
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
                to="/management/notifier/settings"
                class="admin-nav-item admin-nav-item-child"
                :class="
                  isActive('/management/notifier/settings')
                    ? 'admin-nav-item-active'
                    : ''
                "
                @click="isMobile && $emit('close')"
                @mouseenter="preloadRoute('/management/notifier/settings')"
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
                <span>{{
                  t('notificationManagement.settings.menuTitle')
                }}</span>
              </router-link>
            </div>
          </Transition>
        </div>
      </div>

      <div class="mt-auto border-t border-ink-800 pt-4">
        <router-link
          :to="assistantReturnPath"
          class="admin-nav-item"
          @click="clearAssistantReturnPath"
          @mouseenter="preloadRoute('/')"
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
              d="M10 19l-7-7m0 0l7-7m-7 7h18"
            />
          </svg>
          <span>{{ t('management.backToUserPlatform') }}</span>
        </router-link>
      </div>
    </nav>
  </aside>
</template>

<script setup>
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import {
  getAdminSidebarMenu,
  toggleAdminSidebarMenu
} from '@/admin/layout/adminSidebarState'
import BrandLogo from '@/components/layout/BrandLogo.vue'
import { useIsMobile } from '@/composables/useIsMobile'
import { useUiStore } from '@/store/ui'
import { useUserStore } from '@/store/user'
import {
  consumeAdminReturnPath,
  getAdminReturnPath
} from '@/utils/platformAccess'

defineProps({
  showMobileMenu: {
    type: Boolean,
    default: false
  }
})

const emit = defineEmits(['close'])

const { t } = useI18n()
const route = useRoute()
const router = useRouter()
const uiStore = useUiStore()
const userStore = useUserStore()
const assistantReturnPath = ref(getAdminReturnPath())

const clearAssistantReturnPath = () => {
  consumeAdminReturnPath()
  if (isMobile.value) emit('close')
}

const navigation = ref(null)
const activeMenu = computed(() => getAdminSidebarMenu(route.path))
const openMenus = ref(
  activeMenu.value ? new Set([activeMenu.value]) : new Set()
)
const userManagementMenuOpen = computed(() => openMenus.value.has('users'))
const lensMenuOpen = computed(() => openMenus.value.has('lens'))
const dataManagementMenuOpen = computed(() => openMenus.value.has('data'))
const llmMenuOpen = computed(() => openMenus.value.has('llm'))
const taskManagementMenuOpen = computed(() => openMenus.value.has('tasks'))
const notificationManagementMenuOpen = computed(() =>
  openMenus.value.has('notifications')
)

const { isMobile } = useIsMobile()

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
const toggleTaskManagementMenu = () => toggleMenu('tasks')
const toggleNotificationManagementMenu = () => toggleMenu('notifications')

watch(
  () => route.path,
  async (newPath) => {
    const routeMenu = getAdminSidebarMenu(newPath)
    if (routeMenu) openMenus.value = new Set([routeMenu])

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
  scrollbar-width: none;
}

.admin-sidebar-navigation::-webkit-scrollbar {
  display: none;
  width: 0;
  height: 0;
}

.admin-nav-item {
  @apply flex min-h-11 items-center gap-3 rounded-lg px-3 py-2.5 text-[13px] font-medium text-ink-300 transition-colors hover:bg-white/10 hover:text-white;
}

.admin-nav-item-active {
  @apply bg-brand-500/90 text-white hover:bg-brand-500/90 hover:text-white;
}

.admin-nav-item-parent {
  @apply w-full cursor-pointer font-semibold text-ink-200;
}

.admin-nav-item-parent:hover {
  @apply bg-white/10 text-white;
}

.admin-nav-item-parent-active {
  @apply bg-brand-500/20 text-brand-100;
}

.admin-nav-item-child {
  @apply relative pl-10 py-2 text-[13px] font-normal text-ink-400;
  margin-left: 0.75rem;
  border-radius: 0.375rem;
}

.admin-nav-item-child:hover {
  @apply bg-white/10 text-ink-100;
}

.admin-nav-item-child.admin-nav-item-active {
  @apply bg-brand-500/80 text-white font-medium;
}

.menu-group {
  @apply space-y-0 mb-1.5;
}

.submenu {
  @apply overflow-hidden pl-0 mt-1 space-y-0.5;
  transition: all 0.2s ease-in-out;
}

.submenu .admin-nav-item {
  @apply ml-0;
}

.admin-nav-item-child::before {
  content: '';
  @apply absolute left-6 top-1/2 h-5 w-0.5 -translate-y-1/2 rounded bg-ink-700;
}

.admin-nav-item-child.admin-nav-item-active::before {
  @apply w-1 bg-brand-200;
}

.admin-nav-item-parent svg:first-child,
.admin-nav-item-parent span {
  @apply flex-shrink-0;
}

.admin-nav-item-parent svg:last-child {
  @apply flex-shrink-0 ml-1 opacity-70;
  transition:
    transform 0.2s ease-in-out,
    opacity 0.2s;
}

.admin-nav-item-parent:hover svg:last-child {
  @apply opacity-100;
}
</style>
