<template>
  <section class="knowledge-detail-section knowledge-inline-settings">
    <h3>{{ t('lensAdmin.datasourceDetail.settings.title') }}</h3>
    <form :id="formId" @submit.prevent="saveSettings">
      <fieldset class="knowledge-settings-grid" :disabled="saving || blocked">
        <label v-if="syncable" class="knowledge-settings-field">
          <span>{{ t('lensAdmin.datasourceDetail.settings.frequency') }}</span>
          <BaseSelect
            v-model="preset"
            :disabled="saving || blocked"
            :aria-label="t('lensAdmin.datasourceDetail.settings.frequency')"
          >
            <option v-if="currentPreset === 'custom'" value="custom">
              {{ currentPolicyLabel }}
            </option>
            <option
              v-for="key in ['hourly', 'sixHourly', 'daily', 'manual']"
              :key="key"
              :value="key"
            >
              {{ t(`lensAdmin.knowledgeBulk.presets.${key}`) }}
            </option>
          </BaseSelect>
          <small v-if="preset === 'daily'">{{
            t('lensAdmin.knowledgeBulk.dailyTimezone')
          }}</small>
        </label>
        <label class="knowledge-settings-field">
          <span>{{ t('lensAdmin.datasourceDetail.settings.fileLimit') }}</span>
          <input
            v-model="fileLimit"
            type="number"
            min="1"
            step="1"
            :placeholder="t('lensAdmin.datasourceDetail.settings.inherit')"
          />
          <small>{{
            t('lensAdmin.datasourceDetail.settings.fileLimitHint')
          }}</small>
        </label>
      </fieldset>
      <p v-if="error" role="alert" class="knowledge-settings-error">
        {{ error }}
      </p>
      <p v-else-if="saved" role="status" class="knowledge-settings-saved">
        {{ t('lensAdmin.datasourceDetail.settings.saved') }}
      </p>
    </form>
  </section>
</template>

<script setup>
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useUserStore } from '@/store/user'
import { saveDataSourceSettings } from '@/api/lens'
import { extractErrorMessage } from '@/utils/api'
import { dataSourcePolicyLabel } from './datasourceHelpers'
import BaseSelect from '@/components/ui/BaseSelect.vue'

const props = defineProps({
  show: Boolean,
  datasource: { type: Object, default: null },
  blocked: Boolean,
  formId: { type: String, default: 'datasource-detail-settings' }
})
const emit = defineEmits(['saved', 'busy', 'dirty'])
const { t } = useI18n()
const userStore = useUserStore()
const preset = ref('custom')
const currentPreset = ref('custom')
const fileLimit = ref('')
const originalLimit = ref('')
const saving = ref(false)
const error = ref('')
const saved = ref(false)
let generation = 0
const syncable = computed(
  () => !['upload', 'managed_workspace'].includes(props.datasource?.source_type)
)
const currentPolicyLabel = computed(() =>
  dataSourcePolicyLabel(props.datasource, t)
)
const dirty = computed(
  () =>
    preset.value !== currentPreset.value ||
    String(fileLimit.value) !== String(originalLimit.value)
)
function identity() {
  return JSON.stringify([
    props.show,
    props.datasource?.uuid,
    userStore.userInfo?.id,
    userStore.userInfo?.tenant?.uuid,
    userStore.userInfo?.tenant_role,
    userStore.delegation || null
  ])
}
function hydrate() {
  const policy = props.datasource?.sync_policy || {}
  let selected = 'custom'
  if (policy.mode === 'manual') selected = 'manual'
  else if (
    policy.mode === 'crontab' &&
    policy.cron === '0 2 * * *' &&
    policy.timezone === 'Asia/Shanghai'
  )
    selected = 'daily'
  else if (policy.mode !== 'crontab') {
    if (policy.interval_seconds === 3600) selected = 'hourly'
    if (policy.interval_seconds === 21600) selected = 'sixHourly'
  }
  currentPreset.value = selected
  preset.value = selected
  originalLimit.value = policy.conversion?.max_file_size_mb ?? ''
  fileLimit.value = originalLimit.value
}
watch(
  identity,
  () => {
    generation++
    saving.value = false
    error.value = ''
    saved.value = false
    hydrate()
    emit('busy', false)
  },
  { immediate: true, flush: 'sync' }
)
watch(
  () => props.datasource?.sync_policy,
  () => {
    if (!dirty.value && !saving.value) hydrate()
  },
  { deep: true }
)
watch(
  dirty,
  (value) => {
    saved.value = false
    emit('dirty', value)
  },
  { immediate: true, flush: 'sync' }
)
async function saveSettings() {
  if (
    !props.show ||
    !props.datasource?.uuid ||
    props.blocked ||
    userStore.delegation?.mode === 'readonly' ||
    saving.value ||
    !dirty.value
  )
    return
  error.value = ''
  saved.value = false
  const payload = {}
  if (syncable.value && preset.value !== currentPreset.value) {
    if (preset.value === 'manual') payload.schedule = { mode: 'manual' }
    else if (preset.value === 'daily')
      payload.schedule = {
        mode: 'crontab',
        cron: '0 2 * * *',
        timezone: 'Asia/Shanghai'
      }
    else if (['hourly', 'sixHourly'].includes(preset.value))
      payload.schedule = {
        mode: 'interval',
        interval_seconds: preset.value === 'hourly' ? 3600 : 21600
      }
  }
  if (String(fileLimit.value) !== String(originalLimit.value)) {
    const value =
      String(fileLimit.value).trim() === '' ? null : Number(fileLimit.value)
    if (value !== null && (!Number.isInteger(value) || value < 1)) {
      error.value = t('lensAdmin.datasourceDetail.settings.invalidLimit')
      return
    }
    payload.max_file_size_mb = value
  }
  if (!Object.keys(payload).length) return
  const openedBy = identity()
  const request = ++generation
  const uuid = props.datasource.uuid
  saving.value = true
  emit('busy', true)
  try {
    const row = await saveDataSourceSettings(uuid, payload)
    if (request !== generation || openedBy !== identity()) return
    currentPreset.value = preset.value
    originalLimit.value = fileLimit.value
    saved.value = true
    emit('saved', row)
  } catch (failure) {
    if (request === generation && openedBy === identity())
      error.value = extractErrorMessage(failure, t('common.error'))
  } finally {
    if (request === generation) {
      saving.value = false
      emit('busy', false)
    }
  }
}
defineExpose({ saveSettings })

onBeforeUnmount(() => {
  generation++
})
</script>
