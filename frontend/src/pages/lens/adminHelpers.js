/**
 * Pure helpers shared by the Lens admin page and its sub-components.
 *
 * These functions depend only on their arguments (no component state, no
 * i18n), so they live outside the SFC to keep Admin.vue focused on
 * orchestration.
 */

export const EMPTY_VALUE = '—'

const ASSISTANT_TYPE_TRANSLATION_KEYS = {
  code_analysis: 'codeAnalysis',
  general_chat: 'generalChat',
  knowledge_qa: 'knowledgeQa',
  qa: 'knowledgeQa'
}

export function formatAssistantType(value, t, fallback = '') {
  const normalizedValue = String(value || '').trim()
  if (!normalizedValue) {
    return EMPTY_VALUE
  }

  const translationKey = ASSISTANT_TYPE_TRANSLATION_KEYS[normalizedValue]
  if (translationKey) {
    return t(`lensAdmin.assistantTypes.${translationKey}`)
  }

  return fallback || normalizedValue.replace(/[_-]+/g, ' ')
}

export function compactUuid(value) {
  if (!value) {
    return EMPTY_VALUE
  }
  return `${String(value).slice(0, 8)}...${String(value).slice(-6)}`
}

export function formatTaskName(row) {
  return row.name || row.task_name || row.task_type || EMPTY_VALUE
}

export function formatLLMConfigLabel(config) {
  const model = config.config?.model || config.model || EMPTY_VALUE
  return `${config.provider || config.name || config.uuid} · ${model}`
}

export function splitList(value) {
  return String(value || '')
    .split(/[\n,]/)
    .map((item) => item.trim())
    .filter(Boolean)
}

export function listToText(value) {
  return Array.isArray(value) ? value.join(',') : ''
}

export function objectToRows(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    return []
  }
  return Object.entries(value).map(([key, rowValue]) => ({
    key,
    value: typeof rowValue === 'string' ? rowValue : String(rowValue)
  }))
}

export function stringifyJson(value) {
  return JSON.stringify(value, null, 2)
}

export function rowsToObject(rows) {
  return (rows || []).reduce((output, row) => {
    const key = String(row.key || '').trim()
    if (key) {
      output[key] = String(row.value || '').trim()
    }
    return output
  }, {})
}

export function normalizeList(payload) {
  if (Array.isArray(payload)) {
    return payload
  }
  if (Array.isArray(payload?.results)) {
    return payload.results
  }
  return []
}

export const DEFAULT_NODE_IMAGE = 'oneprolabs/lensnode:latest'

const COMPOSE_PLACEHOLDER = {
  serverUrl: 'REPLACE_ME__set_the_public_base_url'
}

/**
 * Render a ready-to-run docker-compose.yml for a node.
 *
 * The node only needs the base server URL; it derives the control WebSocket
 * and AI gateway endpoints from it on its own.
 */
export function buildLensNodeCompose({
  name,
  token,
  image,
  serverUrl,
  hostPath
}) {
  const safeName = name || 'lensnode'
  const safeHost = hostPath || '/workspace'
  return [
    'services:',
    '  lensnode:',
    `    image: ${image || DEFAULT_NODE_IMAGE}`,
    '    restart: unless-stopped',
    '    environment:',
    `      LENSNODE_NAME: ${JSON.stringify(safeName)}`,
    `      LENSNODE_TOKEN: ${JSON.stringify(token || '')}`,
    `      LENSNODE_SERVER_URL: ${JSON.stringify(
      serverUrl || COMPOSE_PLACEHOLDER.serverUrl
    )}`,
    '      LENSNODE_WORKSPACE_PATH: /workspace',
    '    volumes:',
    `      - ${safeHost}:/workspace`,
    ''
  ].join('\n')
}

/**
 * Pull the node compose settings out of the loaded global settings list.
 *
 * The server URL falls back to the configured public base URL or the current
 * origin; the node derives WS / AI gateway endpoints from it.
 */
export function lensNodeComposeSettings(globalSettings) {
  const find = (key) =>
    (globalSettings || []).find((item) => item.key === key)?.value || ''
  const serverUrl =
    find('public_base_url') ||
    (typeof window !== 'undefined' ? window.location.origin : '')
  return {
    image: find('lensnode.image') || DEFAULT_NODE_IMAGE,
    serverUrl
  }
}
