import { computed, ref } from 'vue'
import { defineStore } from 'pinia'

import {
  getSystemHealth,
  listAssistants,
  listDataSources,
  listGlobalSettings,
  listLensNodes,
  listMcpServers,
  listSkills
} from '@/api/lens'

export const useLensStore = defineStore('lens', () => {
  const assistants = ref([])
  const dataSources = ref([])
  const lensnodes = ref([])
  const skills = ref([])
  const mcps = ref([])
  const globalSettings = ref([])
  const systemHealth = ref([])
  const loading = ref(false)
  const error = ref('')

  const activeAssistants = computed(() =>
    assistants.value.filter(
      (assistant) =>
        assistant.status === 'active' && assistant.is_enabled !== false
    )
  )

  async function loadAssistants() {
    loading.value = true
    error.value = ''
    try {
      assistants.value = await listAssistants()
      return assistants.value
    } catch (err) {
      error.value = 'Failed to load Lens assistants.'
      throw err
    } finally {
      loading.value = false
    }
  }

  async function loadAdminResources() {
    loading.value = true
    error.value = ''
    try {
      const [
        assistantList,
        nodes,
        sources,
        skillList,
        mcpList,
        settings,
        health
      ] = await Promise.all([
        listAssistants(),
        listLensNodes(),
        listDataSources(),
        listSkills(),
        listMcpServers(),
        listGlobalSettings(),
        getSystemHealth()
      ])
      assistants.value = assistantList
      lensnodes.value = nodes
      dataSources.value = sources
      skills.value = skillList
      mcps.value = mcpList
      globalSettings.value = settings
      systemHealth.value = health
      return {
        assistants: assistantList,
        lensnodes: nodes,
        dataSources: sources,
        skills: skillList,
        mcps: mcpList,
        globalSettings: settings,
        systemHealth: health
      }
    } catch (err) {
      error.value = 'Failed to load Lens admin resources.'
      throw err
    } finally {
      loading.value = false
    }
  }

  return {
    activeAssistants,
    assistants,
    dataSources,
    error,
    globalSettings,
    loading,
    loadAdminResources,
    loadAssistants,
    lensnodes,
    mcps,
    skills,
    systemHealth
  }
})
