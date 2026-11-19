<template>
  <BaseModal
    :show="!!action"
    :title="t(`management.memberBulk.actions.${action || 'role'}`)"
    @close="close"
  >
    <div class="space-y-4">
      <p class="text-sm text-ink-600">
        {{ t('common.selectedCount', { count: snapshot.length }) }}
      </p>
      <p class="text-sm text-ink-500">
        {{ t('management.memberBulk.skipHint') }}
      </p>
      <ul class="max-h-32 overflow-auto text-sm text-ink-700">
        <li v-for="target in snapshot" :key="target.id">
          {{ target.label || target.id }}
        </li>
      </ul>
      <template v-if="!result">
        <label
          v-if="action === 'role'"
          class="block text-sm font-medium text-ink-700"
        >
          {{ t('management.roles') }}
          <BaseSelect
            v-model="role"
            class="form-input mt-1"
            :disabled="saving || conflict"
          >
            <option value="member">{{ t('management.memberRole') }}</option>
            <option value="tenant_admin">
              {{ t('management.tenantAdminRole') }}
            </option>
          </BaseSelect>
        </label>
        <fieldset
          v-if="action === 'groups'"
          :disabled="saving || conflict"
          class="space-y-2"
        >
          <legend class="mb-2 text-sm font-medium text-ink-700">
            {{ t('management.memberGroups') }}
          </legend>
          <p v-if="!groups.length" class="text-sm text-ink-500">
            {{ t('management.memberBulk.noGroups') }}
          </p>
          <label
            v-for="group in groups"
            :key="group.id"
            class="flex items-center gap-2 text-sm text-ink-700"
          >
            <input
              v-model="groupIds"
              type="checkbox"
              :value="group.id"
              class="rounded border-line text-brand-600"
            />
            {{ group.name }}
          </label>
        </fieldset>
        <template v-if="action === 'disable'">
          <BaseLoading v-if="previewLoading" />
          <p
            v-if="keySummary"
            class="rounded-md bg-surface-sunken p-3 text-sm text-ink-700"
          >
            {{ t('management.memberBulk.keySummary', keySummary) }}
          </p>
          <p class="text-sm text-ink-600">
            {{ t('management.disableMemberKeysHint') }}
          </p>
          <BaseButton
            v-if="!keySummary && !previewLoading && !conflict"
            variant="outline"
            @click="loadPreview()"
          >
            {{ t('management.memberBulk.retryPreview') }}
          </BaseButton>
        </template>
      </template>
      <p v-if="error" role="alert" class="text-sm text-danger-700">
        {{ error }}
      </p>
      <div v-if="result" class="space-y-3" role="status">
        <p class="text-sm font-medium text-ink-900">
          {{ t('management.memberBulk.result', result) }}
        </p>
        <ul class="max-h-64 space-y-2 overflow-auto text-sm">
          <li
            v-for="item in result.items"
            :key="item.id"
            class="flex flex-wrap justify-between gap-2"
          >
            <span class="text-ink-700">{{ targetLabel(item.id) }}</span>
            <span
              :class="
                item.status === 'failed' ? 'text-danger-700' : 'text-ink-500'
              "
              >{{ itemReason(item) }}</span
            >
          </li>
        </ul>
      </div>
    </div>
    <template #footer>
      <div class="flex w-full flex-wrap justify-end gap-2">
        <BaseButton variant="outline" :disabled="saving" @click="close">{{
          t('common.close')
        }}</BaseButton>
        <template v-if="!result">
          <template v-if="action === 'disable'">
            <BaseButton
              variant="outline"
              :disabled="!canSubmit"
              :loading="saving"
              @click="submit(false)"
            >
              {{ t('management.disableMemberOnly') }}
            </BaseButton>
            <BaseButton
              variant="danger"
              :disabled="!canSubmit"
              :loading="saving"
              @click="submit(true)"
            >
              {{ t('management.disableMemberWithKeys') }}
            </BaseButton>
          </template>
          <BaseButton
            v-else
            :disabled="!canSubmit"
            :loading="saving"
            @click="submit()"
            >{{ t('common.confirm') }}</BaseButton
          >
        </template>
      </div>
    </template>
  </BaseModal>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import BaseModal from '@/components/ui/BaseModal.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseSelect from '@/components/ui/BaseSelect.vue'
import BaseLoading from '@/components/ui/BaseLoading.vue'
import { bulkUpdateTenantMembers } from '@/api/tenant'

const props = defineProps({
  action: { type: String, default: '' },
  targets: { type: Array, default: () => [] },
  groups: { type: Array, default: () => [] }
})
const emit = defineEmits(['close', 'applied'])
const { t } = useI18n()
const snapshot = ref([])
const role = ref('member')
const groupIds = ref([])
const saving = ref(false)
const previewLoading = ref(false)
const keySummary = ref(null)
const error = ref('')
const conflict = ref(false)
const result = ref(null)
let generation = 0

const canSubmit = computed(
  () =>
    !!props.action &&
    snapshot.value.length > 0 &&
    !saving.value &&
    !conflict.value &&
    !previewLoading.value &&
    (props.action !== 'groups' || groupIds.value.length > 0) &&
    (props.action !== 'disable' || keySummary.value !== null)
)

function payload() {
  return {
    action: props.action,
    members: snapshot.value.map(({ id, version }) => ({ id, version })),
    ...(props.action === 'role' ? { role: role.value } : {}),
    ...(props.action === 'groups' ? { group_ids: [...groupIds.value] } : {})
  }
}

function reportError(err) {
  const data = err?.response?.data
  const first = (value) => (Array.isArray(value) ? value[0] : value)
  if ([404, 409].includes(err?.response?.status)) {
    conflict.value = true
    error.value = t('management.memberBulk.selectionChanged')
  } else if (first(data?.role) === 'LAST_TENANT_ADMIN_REQUIRED') {
    error.value = t('management.lastAdminRequired')
  } else if (first(data?.status) === 'CANNOT_DISABLE_SELF') {
    error.value = t('management.cannotDisableSelf')
  } else if (err?.response?.status === 403) {
    conflict.value = true
    error.value = t('management.memberBulk.authorityChanged')
  } else {
    error.value = t('management.memberSaveFailed')
  }
}

async function loadPreview() {
  if (saving.value || previewLoading.value || props.action !== 'disable') return
  const current = generation
  previewLoading.value = true
  error.value = ''
  try {
    const data = await bulkUpdateTenantMembers({ ...payload(), preview: true })
    if (current === generation) keySummary.value = data.key_summary
  } catch (err) {
    if (current === generation) reportError(err)
  } finally {
    if (current === generation) previewLoading.value = false
  }
}

async function submit(disableKeys = false) {
  if (!canSubmit.value || result.value) return
  const current = generation
  const data = {
    ...payload(),
    ...(props.action === 'disable' ? { disable_api_keys: disableKeys } : {})
  }
  saving.value = true
  error.value = ''
  try {
    const response = await bulkUpdateTenantMembers(data)
    if (current !== generation) return
    result.value = response
    emit('applied', {
      action: data.action,
      role: data.role,
      targets: snapshot.value,
      result: response
    })
  } catch (err) {
    if (current === generation) reportError(err)
  } finally {
    if (current === generation) saving.value = false
  }
}

function targetLabel(id) {
  return snapshot.value.find((target) => target.id === id)?.label || String(id)
}

function itemReason(item) {
  const reasons = [
    'unchanged',
    'inactive_member',
    'invitation_required',
    'not_invited',
    'rate_limited',
    'delivery_failed',
    'selection_changed'
  ]
  return t(
    item.reason && reasons.includes(item.reason)
      ? `management.memberBulk.reasons.${item.reason}`
      : `management.memberBulk.statuses.${item.status}`
  )
}

function close() {
  if (!saving.value) emit('close', conflict.value)
}

watch(
  () => [props.action, props.targets],
  () => {
    generation++
    snapshot.value = props.targets.map((target) => ({ ...target }))
    role.value = 'member'
    groupIds.value = []
    saving.value = false
    previewLoading.value = false
    keySummary.value = null
    error.value = ''
    conflict.value = false
    result.value = null
    if (props.action === 'disable') loadPreview()
  },
  { immediate: true }
)
</script>
