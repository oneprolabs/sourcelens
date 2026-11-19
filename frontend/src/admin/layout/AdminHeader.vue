<template>
  <header class="layout-admin-header">
    <button
      type="button"
      class="admin-navigation-toggle"
      aria-controls="admin-sidebar"
      :aria-expanded="showMobileMenu"
      :aria-label="t('management.openNavigation')"
      @click="$emit('open-menu')"
    >
      <Menu :size="16" aria-hidden="true" />
    </button>
    <nav class="admin-breadcrumb" :aria-label="t('management.breadcrumb')">
      <span class="admin-breadcrumb-context">{{ consoleTitle }}</span>
      <span class="admin-breadcrumb-context admin-breadcrumb-separator">/</span>
      <span v-if="sectionTitle" class="admin-breadcrumb-context">{{
        sectionTitle
      }}</span>
      <span
        v-if="sectionTitle"
        class="admin-breadcrumb-context admin-breadcrumb-separator"
        >/</span
      >
      <strong>{{ pageTitle }}</strong>
    </nav>
    <router-link
      :to="assistantReturnPath"
      :aria-label="t('management.backToUserPlatform')"
      class="admin-workbench-link"
      @click="clearAssistantReturnPath"
    >
      <ArrowLeft :size="14" aria-hidden="true" />
      <span>{{ t('management.workbench') }}</span>
    </router-link>
  </header>
</template>

<script setup>
import { ArrowLeft, Menu } from '@lucide/vue'
import { computed, ref } from 'vue'
import { useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { useUserStore } from '@/store/user'
import {
  consumeAdminReturnPath,
  getAdminReturnPath
} from '@/utils/platformAccess'

defineProps({ showMobileMenu: { type: Boolean, default: false } })
defineEmits(['open-menu'])
const { t } = useI18n()
const route = useRoute()
const userStore = useUserStore()
const assistantReturnPath = ref(getAdminReturnPath())
function clearAssistantReturnPath() {
  consumeAdminReturnPath()
}
const consoleTitle = computed(() => {
  if (route.meta.console === 'platform') return t('management.platformConsole')
  const tenant =
    userStore.delegation?.tenant?.name || userStore.userInfo?.tenant?.name
  return [t('management.manage'), tenant].filter(Boolean).join(' · ')
})
const sectionTitle = computed(() => {
  const path = route.path
  if (/^\/management\/(users|groups|api-keys|tenant)(\/|$)/.test(path))
    return t('management.tenantManagement')
  if (path.includes('/llm/')) return t('llm.menuTitle')
  if (path.includes('/notifier/') || path.includes('/alerts/'))
    return t('notificationManagement.menuTitle')
  if (path.includes('/lens/datasources'))
    return t('management.knowledgeManagement')
  if (path.includes('/lens/resources/')) return t('management.pluginManagement')
  if (path.includes('/lens/')) return t('lensAdmin.menuTitle')
  return ''
})
const pageTitle = computed(() => {
  const routeNames = {
    ManagementTenantOverview: t('tenantOverview.title'),
    ManagementTenantSetup: t('tenantSetup.title'),
    ManagementTenant: t('management.tenantInfo'),
    ManagementPlatformTenants: t('management.platformTenantsTitle'),
    ManagementPlatformOverview: t('management.platformOverviewTitle'),
    ManagementPlatformTenantStats: t('management.platformStatsTitle'),
    ManagementPlatformMembers: t('management.platformMembersTitle'),
    ManagementPlatformApiKeys: t('management.platformApiKeysTitle'),
    ManagementPlatformAudit: t('management.platformAuditTitle'),
    PlatformNodes: t('lensAdmin.pages.lensnodes.title'),
    PlatformNodeSettings: t('management.platformNodeSettings'),
    PlatformSettings: t('lensAdmin.pages.settings.title'),
    ManagementUsers: t('management.userManagement'),
    ManagementGroups: t('management.groupManagement'),
    ManagementApiKeys: t('management.tenantApiKeyTitle'),
    LensAssistants: t('lensAdmin.pages.assistants.title'),
    LensNodes: t('lensAdmin.pages.lensnodes.title'),
    LensDataSources: t('lensAdmin.pages.datasources.title'),
    LensConnections: t('lensAdmin.pages.connections.title'),
    LensMarketplace: t('catalog.marketplace'),
    PlatformCatalogSkills: t('catalog.officialSkills'),
    PlatformCatalogConnectors: t('catalog.connectors'),
    PlatformCatalogMcp: t('catalog.officialMcp'),
    LensSkills: t('lensAdmin.pages.skills.title'),
    LensMcp: t('lensAdmin.pages.mcp.title'),
    LensResourceSettings: t('lensAdmin.pages.resourceSettings.title'),
    LensSettings: t('lensAdmin.pages.settings.title'),
    LensRunObservation: t('lensRuns.title'),
    LensEvaluation: t('evaluation.datasets.title'),
    LensEvaluationDataset: t('evaluation.datasets.detailTitle'),
    LensEvaluationReport: t('evaluation.report.title'),
    LensShareReview: t('lens.qa.adminTitle'),
    LLMStats: t('llm.stats.title'),
    LLMUsage: t('llm.usage.title'),
    LLMConfig: t('llm.config.title'),
    LLMDataSettings: t('llm.dataSettings.title'),
    PlatformLLMStats: t('llm.stats.title'),
    PlatformLLMUsage: t('llm.usage.title'),
    PlatformLLMConfig: t('llm.config.title'),
    PlatformLLMDataSettings: t('llm.dataSettings.title'),
    TaskManagementList: t('taskManagement.list.title'),
    TaskManagementStats: t('taskManagement.stats.title'),
    TaskManagementSettings: t('taskManagement.settings.title'),
    AdminAlertRules: t('alertManagement.rules.title'),
    AdminAlertEvents: t('alertManagement.events.title'),
    AdminAlertMessages: t('alertManagement.messages.title'),
    PlatformNotificationsRules: t('alertManagement.rules.title'),
    PlatformNotificationsEvents: t('alertManagement.events.title'),
    PlatformNotificationsMessages: t('alertManagement.messages.title'),
    PlatformNotificationsStats: t('notificationManagement.stats.title'),
    PlatformNotificationsRecords: t('notificationManagement.records.title'),
    PlatformNotificationsChannels: t(
      'notificationManagement.channels.menuTitle'
    ),
    PlatformNotificationsSettings: t(
      'notificationManagement.settings.menuTitle'
    ),
    AdminNotificationsStats: t('notificationManagement.stats.title'),
    AdminNotificationsRecords: t('notificationManagement.records.title'),
    AdminNotificationsChannels: t('notificationManagement.channels.menuTitle'),
    AdminNotificationsSettings: t('notificationManagement.settings.menuTitle')
  }
  return routeNames[route.name] || t('management.logoTitle')
})
</script>
