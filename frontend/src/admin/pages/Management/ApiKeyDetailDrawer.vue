<template>
  <BaseDrawer
    :show="true"
    :title="detail?.name || selected.name"
    :subtitle="
      (detail || selected).key_display ||
      `${(detail || selected).key_prefix}••••`
    "
    width="lg"
    @close="emit('close')"
  >
    <template #tabs>
      <div
        class="flex gap-1 border-b border-line px-6"
        role="tablist"
        :aria-label="t(`${prefix}.details`)"
      >
        <button
          v-for="value in ['basic', 'calls']"
          :key="value"
          type="button"
          role="tab"
          :aria-selected="tab === value"
          :id="`key-tab-${value}`"
          :aria-controls="`key-panel-${value}`"
          :tabindex="tab === value ? 0 : -1"
          :class="
            tab === value
              ? 'border-brand-600 text-brand-700'
              : 'border-transparent text-ink-500'
          "
          class="border-b-2 px-3 py-3 text-sm font-medium"
          @click="tab = value"
          @keydown.right.prevent="
            selectTab(value === 'basic' ? 'calls' : 'basic')
          "
          @keydown.left.prevent="
            selectTab(value === 'basic' ? 'calls' : 'basic')
          "
        >
          {{ t(`${prefix}.${value}`) }}
        </button>
      </div>
    </template>
    <BaseLoading v-if="loading" />
    <div v-else-if="error" class="space-y-3" role="alert">
      <p class="text-sm text-danger-700">{{ error }}</p>
      <BaseButton variant="outline" @click="load">{{
        t(`${prefix}.reload`)
      }}</BaseButton>
    </div>
    <div
      v-else-if="detail"
      :id="`key-panel-${tab}`"
      role="tabpanel"
      :aria-labelledby="`key-tab-${tab}`"
      class="space-y-5"
    >
      <template v-if="tab === 'basic'">
        <ApiKeyRiskSummary
          :summary="{
            expiring_soon: detail.expiring_soon ? 1 : 0,
            unused: detail.unused ? 1 : 0
          }"
        />
        <div v-if="detail.description">
          <h3 class="mb-2 text-sm font-medium text-ink-800">
            {{ t('management.apiKeyPurpose') }}
          </h3>
          <p class="whitespace-pre-wrap break-words text-sm text-ink-600">
            {{ detail.description }}
          </p>
        </div>
        <dl class="divide-y divide-line rounded-md border border-line">
          <div
            v-for="item in metadata"
            :key="item.label"
            class="flex justify-between gap-4 px-4 py-3 text-sm"
          >
            <dt class="text-ink-500">{{ item.label }}</dt>
            <dd class="break-words text-right text-ink-900">
              {{ item.value }}
            </dd>
          </div>
        </dl>
        <div>
          <h3 class="mb-2 text-sm font-medium text-ink-800">
            {{ t('management.apiKeyScope') }}
          </h3>
          <p v-if="detail.scope === 'all'" class="text-sm text-ink-600">
            {{ t('management.apiKeyAllAssistantsHint') }}
          </p>
          <ul v-else class="flex flex-wrap gap-2">
            <li
              v-for="assistant in detail.assistants"
              :key="assistant.uuid"
              class="rounded-md bg-surface-sunken px-2 py-1 text-sm text-ink-700"
            >
              {{ assistant.name }}
            </li>
          </ul>
        </div>
      </template>
      <template v-else>
        <RouterLink
          :to="historyRoute"
          class="text-sm font-medium text-brand-700 hover:underline"
        >
          {{ t(`${prefix}.fullHistory`) }}
        </RouterLink>
        <p class="text-xs leading-5 text-ink-500">
          {{ t(`${prefix}.callsHint`) }}
        </p>
        <p
          v-if="!detail.recent_calls?.length"
          class="py-6 text-sm text-ink-500"
        >
          {{ t(`${prefix}.noCalls`) }}
        </p>
        <ul v-else class="divide-y divide-line">
          <li
            v-for="call in detail.recent_calls"
            :key="call.id"
            class="space-y-1 py-3"
          >
            <div class="flex flex-wrap justify-between gap-2 text-sm">
              <strong class="text-ink-800">{{
                call.assistant_name || t(`${prefix}.unavailableAssistant`)
              }}</strong>
              <span
                :class="
                  call.status_code >= 400 || call.run_status === 'failed'
                    ? 'text-danger-700'
                    : 'text-ink-700'
                "
                >{{ callStatus(call) }}</span
              >
            </div>
            <p class="break-all font-mono text-xs text-ink-500">
              {{ call.method }} {{ call.route }} · {{ call.status_code }}
            </p>
            <p class="text-xs text-ink-500">
              {{ dateLabel(call.created_at) }} ·
              {{ t(`${prefix}.latency`, { ms: call.duration_ms }) }} ·
              {{ t(`${prefix}.version`, { version: call.key_version }) }}
            </p>
            <p
              v-if="
                call.execution_duration_ms !== null &&
                call.execution_duration_ms !== undefined
              "
              class="text-xs text-ink-500"
            >
              {{
                t(`${prefix}.executionDuration`, {
                  seconds: (call.execution_duration_ms / 1000).toFixed(1)
                })
              }}
            </p>
          </li>
        </ul>
      </template>
    </div>
    <template #footer>
      <div class="flex flex-wrap justify-end gap-2">
        <BaseButton
          v-if="detail && !error && detail.status !== 'revoked'"
          variant="outline"
          :disabled="busy || loading"
          @click="emit('action', 'rotate', detail)"
          >{{ t('management.apiKeyRotate') }}</BaseButton
        >
        <BaseButton
          v-if="
            detail && !error && !['revoked', 'expired'].includes(detail.status)
          "
          variant="outline"
          :disabled="busy || loading"
          @click="emit('action', 'toggle', detail)"
          >{{
            t(
              `management.${detail.raw_status === 'disabled' ? 'enable' : 'disable'}`
            )
          }}</BaseButton
        >
        <BaseButton
          v-if="detail && !error && detail.status !== 'revoked'"
          variant="danger"
          :disabled="busy || loading"
          @click="emit('action', 'revoke', detail)"
          >{{ t('management.apiKeyRevoke') }}</BaseButton
        >
        <BaseButton variant="outline" @click="emit('close')">{{
          t('common.close')
        }}</BaseButton>
      </div>
    </template>
  </BaseDrawer>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { RouterLink } from 'vue-router'
import { getTenantApiKey } from '@/api/tenant'
import BaseDrawer from '@/components/ui/BaseDrawer.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseLoading from '@/components/ui/BaseLoading.vue'
import ApiKeyRiskSummary from './ApiKeyRiskSummary.vue'

const props = defineProps({
  selected: { type: Object, required: true },
  busy: { type: Boolean, default: false },
  revision: { type: Number, default: 0 }
})
const emit = defineEmits(['close', 'action'])
const { t } = useI18n()
const prefix = 'management.apiKeyInsights'
const detail = ref(null)
const tab = ref('basic')
const loading = ref(false)
const error = ref('')
let version = 0
const historyRoute = computed(() => ({
  name: 'LensRunObservation',
  query: { api_key: props.selected.uuid }
}))

async function selectTab(value) {
  tab.value = value
  await nextTick()
  document.getElementById(`key-tab-${value}`)?.focus()
}

function dateLabel(value) {
  return value ? new Date(value).toLocaleString() : '—'
}

function callStatus(call) {
  if (call.status_code >= 400) return t(`${prefix}.rejected`)
  if (call.run_status) return t(`${prefix}.run_${call.run_status}`)
  return t(`${prefix}.accepted`)
}

const metadata = computed(() => {
  if (!detail.value) return []
  const key = detail.value
  return [
    {
      label: t('management.memberStatus'),
      value: t(
        `management.${{ active: 'apiKeyActive', disabled: 'apiKeyDisabled', expired: 'apiKeyExpired', revoked: 'apiKeyRevoked' }[key.status]}`
      )
    },
    { label: t('management.apiKeyCreator'), value: key.created_by || '—' },
    { label: t(`${prefix}.createdAt`), value: dateLabel(key.created_at) },
    {
      label: t('management.apiKeyExpiry'),
      value: key.expires_at
        ? dateLabel(key.expires_at)
        : t('management.apiKeyNever')
    },
    {
      label: t('management.apiKeyLastUsed'),
      value: key.last_used_at
        ? dateLabel(key.last_used_at)
        : t(`${prefix}.neverUsed`)
    },
    { label: t(`${prefix}.calls7d`), value: key.calls_7d ?? '—' }
  ]
})

async function load() {
  const request = ++version
  const uuid = props.selected.uuid
  loading.value = true
  error.value = ''
  detail.value = null
  try {
    const data = await getTenantApiKey(uuid)
    if (request === version) detail.value = data
  } catch {
    if (request === version) error.value = t('management.apiKeyLoadFailed')
  } finally {
    if (request === version) loading.value = false
  }
}

watch(
  () => [props.selected.uuid, props.revision],
  () => {
    load()
  },
  { immediate: true }
)
onBeforeUnmount(() => {
  version++
})
</script>
