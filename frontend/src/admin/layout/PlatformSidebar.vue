<template>
  <div
    v-if="showMobileMenu && isMobile"
    class="fixed inset-0 z-40 bg-ink-950/55 lg:hidden"
    @click="$emit('close')"
  />
  <aside
    id="admin-sidebar"
    :inert="isMobile && !showMobileMenu"
    :aria-hidden="isMobile && !showMobileMenu"
    :class="[
      'layout-admin-sidebar flex h-full w-[282px] shrink-0 flex-col ',
      isMobile ? 'fixed inset-y-0 left-0 z-50 transition-transform' : 'static',
      isMobile && !showMobileMenu ? '-translate-x-full' : 'translate-x-0'
    ]"
  >
    <div class="admin-nav-brand">
      <router-link to="/management/platform/overview" @click="$emit('close')">
        <strong>SourceLens</strong>
        <small>{{ t('management.platformConsole') }}</small>
      </router-link>
      <button
        v-if="isMobile"
        type="button"
        :aria-label="t('common.close')"
        class="h-11 w-11 rounded-md text-ink-300 hover:bg-white/10"
        @click="$emit('close')"
      >
        <X class="mx-auto h-5 w-5" />
      </button>
    </div>
    <SidebarMenuSearch
      v-model:searching="searching"
      :items="searchItems"
      :active="!isMobile || showMobileMenu"
      @navigate="$emit('close')"
    />
    <nav
      v-show="!searching"
      :aria-label="t('management.platformConsole')"
      class="flex-1 space-y-5 overflow-y-auto px-3 py-4"
    >
      <section
        v-for="(group, index) in groups"
        :key="group.label"
        class="space-y-1"
      >
        <button
          v-if="group.label"
          type="button"
          :aria-expanded="!collapsedGroups.has(group.label)"
          :aria-controls="`platform-menu-${index}`"
          class="flex min-h-9 w-full items-center justify-between px-3 py-1 text-left text-xs font-medium text-ink-400 hover:text-white"
          @click="toggleGroup(group.label)"
        >
          {{ t(group.label) }}
          <ChevronDown
            class="h-4 w-4 transition-transform"
            :class="collapsedGroups.has(group.label) ? '-rotate-90' : ''"
            aria-hidden="true"
          />
        </button>
        <div
          :id="`platform-menu-${index}`"
          v-show="!collapsedGroups.has(group.label)"
        >
          <router-link
            v-for="item in group.items"
            :key="item.path"
            :to="item.path"
            :aria-current="route.path === item.path ? 'page' : undefined"
            :class="[
              'flex min-h-11 items-center gap-3 rounded-lg px-3 py-2 text-sm',
              route.path === item.path
                ? 'bg-brand-500/90 text-white'
                : 'text-ink-300 hover:bg-white/10 hover:text-white'
            ]"
            @click="$emit('close')"
          >
            <component
              :is="item.icon"
              class="h-4 w-4 shrink-0"
              aria-hidden="true"
            />
            {{ t(item.label) }}
          </router-link>
        </div>
      </section>
    </nav>
    <AdminAccountMenu />
  </aside>
</template>

<script setup>
import {
  Activity,
  Bell,
  Building2,
  Blocks,
  Sparkles,
  ChartColumn,
  ChevronDown,
  ListChecks,
  KeyRound,
  ScrollText,
  Server,
  Settings,
  Users,
  X
} from '@lucide/vue'
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute } from 'vue-router'

import AdminAccountMenu from './AdminAccountMenu.vue'
import SidebarMenuSearch from './SidebarMenuSearch.vue'
import { useIsMobile } from '@/composables/useIsMobile'
import { useUiStore } from '@/store/ui'

defineProps({ showMobileMenu: { type: Boolean, default: false } })
defineEmits(['close'])
const { t } = useI18n()
const route = useRoute()
const searching = ref(false)
const uiStore = useUiStore()
const collapsedGroups = computed(() => uiStore.platformSidebarCollapsedGroups)
const searchItems = computed(() =>
  groups.flatMap((group) =>
    group.items.map((item) => ({
      ...item,
      label: t(item.label),
      group: group.label ? t(group.label) : ''
    }))
  )
)
function toggleGroup(label) {
  if (collapsedGroups.value.has(label)) collapsedGroups.value.delete(label)
  else collapsedGroups.value.add(label)
}
const { isMobile } = useIsMobile(821)
const groups = [
  {
    label: '',
    items: [
      {
        path: '/management/platform/overview',
        label: 'management.platformOverviewTitle',
        icon: Activity
      }
    ]
  },
  {
    label: 'management.tenantManagement',
    items: [
      {
        path: '/management/platform/tenants',
        label: 'management.platformTenantsTitle',
        icon: Building2
      },
      {
        path: '/management/platform/tenant-stats',
        label: 'management.platformStatsTitle',
        icon: ChartColumn
      },
      {
        path: '/management/platform/members',
        label: 'management.platformMembersTitle',
        icon: Users
      },
      {
        path: '/management/platform/api-keys',
        label: 'management.platformApiKeysTitle',
        icon: KeyRound
      }
    ]
  },
  {
    label: 'management.platformNodeManagement',
    items: [
      {
        path: '/management/platform/nodes',
        label: 'lensAdmin.pages.lensnodes.title',
        icon: Server
      },
      {
        path: '/management/platform/node-settings',
        label: 'management.platformNodeSettings',
        icon: Settings
      }
    ]
  },
  {
    label: 'catalog.officialDirectory',
    items: [
      {
        path: '/management/platform/catalog/skills',
        label: 'lensAdmin.tabs.skills',
        icon: Sparkles
      },
      {
        path: '/management/platform/catalog/mcp',
        label: 'lensAdmin.pages.mcp.title',
        icon: Blocks
      },
      {
        path: '/management/platform/catalog/connectors',
        label: 'catalog.connectors',
        icon: Blocks
      }
    ]
  },
  {
    label: 'notificationManagement.menuTitle',
    items: [
      {
        path: '/management/platform/notifications/channels',
        label: 'notificationManagement.channels.title',
        icon: Bell
      },
      {
        path: '/management/platform/notifications/rules',
        label: 'alertManagement.rules.title',
        icon: ListChecks
      },
      {
        path: '/management/platform/notifications/events',
        label: 'alertManagement.events.title',
        icon: Bell
      },
      {
        path: '/management/platform/notifications/messages',
        label: 'alertManagement.messages.title',
        icon: Bell
      },
      {
        path: '/management/platform/notifications/stats',
        label: 'notificationManagement.stats.title',
        icon: ChartColumn
      },
      {
        path: '/management/platform/notifications/records',
        label: 'notificationManagement.records.title',
        icon: ListChecks
      },
      {
        path: '/management/platform/notifications/settings',
        label: 'notificationManagement.settings.title',
        icon: Settings
      }
    ]
  },
  {
    label: 'llm.menuTitle',
    items: [
      {
        path: '/management/platform/llm/config',
        label: 'llm.config.title',
        icon: Settings
      },
      {
        path: '/management/platform/llm/stats',
        label: 'llm.stats.title',
        icon: ChartColumn
      },
      {
        path: '/management/platform/llm/usage',
        label: 'llm.usage.title',
        icon: ListChecks
      },
      {
        path: '/management/platform/llm/data-settings',
        label: 'llm.dataSettings.title',
        icon: Settings
      }
    ]
  },
  {
    label: 'taskManagement.menuTitle',
    items: [
      {
        path: '/management/platform/tasks/stats',
        label: 'taskManagement.stats.title',
        icon: ChartColumn
      },
      {
        path: '/management/platform/tasks/list',
        label: 'taskManagement.list.title',
        icon: ListChecks
      },
      {
        path: '/management/platform/tasks/settings',
        label: 'taskManagement.settings.title',
        icon: Settings
      }
    ]
  },
  {
    label: 'management.platformAuditAndConfig',
    items: [
      {
        path: '/management/platform/audit',
        label: 'management.platformAuditTitle',
        icon: ScrollText
      },
      {
        path: '/management/platform/settings',
        label: 'lensAdmin.pages.settings.title',
        icon: Settings
      }
    ]
  }
]
watch(
  () => route.path,
  (path) => {
    const group = groups.find((group) =>
      group.items.some(
        (item) => path === item.path || path.startsWith(`${item.path}/`)
      )
    )
    if (group) collapsedGroups.value.delete(group.label)
  },
  { immediate: true }
)
</script>
