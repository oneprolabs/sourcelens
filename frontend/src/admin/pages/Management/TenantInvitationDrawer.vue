<template>
  <BaseDrawer :show="show" :title="t('invitations.title')" @close="close">
    <form
      id="tenant-invitation-form"
      ref="formElement"
      @submit.prevent="submit"
    >
      <div class="space-y-5">
        <p class="text-sm text-ink-500">
          {{
            t(reinvite ? 'invitations.bulk.reinviteHint' : 'invitations.hint')
          }}
        </p>
        <fieldset
          v-if="!review && !result"
          :disabled="saving"
          class="space-y-5"
        >
          <label
            v-if="!csvSource"
            class="block text-sm font-medium text-ink-700"
          >
            {{ t(reinvite ? 'invitations.email' : 'invitations.bulk.emails') }}
            <textarea
              v-model="form.email"
              :readonly="!!reinvite"
              @input="importError = ''"
              required
              maxlength="30000"
              rows="5"
              class="form-input mt-1"
              :placeholder="t('invitations.bulk.placeholder')"
            />
          </label>
          <p
            v-else
            class="flex flex-wrap items-center gap-2 text-sm text-ink-700"
          >
            {{ t('invitations.bulk.imported', { name: csvName }) }}
            <BaseButton variant="ghost" @click="clearCsv">{{
              t('invitations.bulk.clearImport')
            }}</BaseButton>
          </p>
          <div
            v-if="!reinvite"
            class="flex flex-wrap items-center gap-3 text-sm"
          >
            <label class="cursor-pointer text-brand-700 underline">
              {{ t('invitations.bulk.importCsv') }}
              <input
                type="file"
                accept=".csv,text/csv"
                class="sr-only"
                :disabled="groupsLoading || groupsError || saving || importing"
                @change="importCsv"
              />
            </label>
            <button
              type="button"
              class="text-brand-700 underline"
              @click="downloadTemplate"
            >
              {{ t('invitations.bulk.template') }}
            </button>
            <p class="text-xs text-ink-500">
              {{ t('invitations.bulk.csvHint') }}
            </p>
          </div>
          <label class="block text-sm font-medium text-ink-700">
            {{ t('management.roles') }}
            <BaseSelect v-model="form.role" class="form-input mt-1">
              <option value="member">{{ t('management.memberRole') }}</option>
              <option value="tenant_admin">
                {{ t('management.tenantAdminRole') }}
              </option>
            </BaseSelect>
          </label>
          <div class="space-y-2">
            <p class="text-sm font-medium text-ink-700">
              {{ t('management.memberGroups') }}
            </p>
            <p v-if="groupsLoading" class="text-xs text-ink-500">
              {{ t('common.loading') }}
            </p>
            <p v-else-if="groupsError" class="text-sm text-danger-700">
              {{ t('invitations.groupsFailed') }}
              <button
                type="button"
                class="text-brand-700 underline"
                @click="loadGroups"
              >
                {{ t('common.retry') }}
              </button>
            </p>
            <label
              v-for="group in groups"
              :key="group.id"
              class="flex items-center gap-2 text-sm text-ink-700"
            >
              <input
                v-model="form.group_ids"
                type="checkbox"
                :value="group.id"
                class="rounded border-line"
              />
              {{ group.name }}
            </label>
          </div>
        </fieldset>
        <p
          v-if="parsingError || importError"
          role="alert"
          class="text-sm text-danger-700"
        >
          {{ importError || parsingError }}
        </p>
        <p v-if="checking" class="text-sm text-ink-500" role="status">
          {{ t('invitations.bulk.checking') }}
        </p>
        <div
          v-if="!reinvite && !result && displayItems.length"
          class="space-y-3"
        >
          <p v-if="review" class="text-sm font-medium text-ink-700">
            {{ t('invitations.bulk.confirm', { count: validCount }) }}
          </p>
          <div
            v-for="status in ['valid', 'invalid', 'existing', 'duplicate']"
            :key="status"
          >
            <template
              v-if="displayItems.some((item) => item.status === status)"
            >
              <h3 class="text-sm font-medium text-ink-700">
                {{
                  t(`invitations.bulk.categories.${status}`, {
                    count: displayItems.filter((item) => item.status === status)
                      .length
                  })
                }}
              </h3>
              <ul
                class="mt-1 max-h-40 space-y-1 overflow-auto text-sm text-ink-500"
              >
                <li
                  v-for="item in displayItems.filter(
                    (item) => item.status === status
                  )"
                  :key="item.index"
                  class="break-words"
                >
                  {{ item.email || t('invitations.bulk.emptyRow') }}
                  <span v-if="item.reason">
                    · {{ reasonLabel(item.reason) }}</span
                  >
                  <span v-else> · {{ configurationLabel(item.index) }}</span>
                </li>
              </ul>
            </template>
          </div>
        </div>
        <div v-if="result" class="space-y-3" role="status">
          <p class="text-sm font-medium text-ink-900">
            {{ t('invitations.bulk.result', result) }}
          </p>
          <ul class="max-h-96 space-y-2 overflow-auto text-sm">
            <li
              v-for="item in result.items"
              :key="item.index"
              class="break-words"
            >
              <span class="text-ink-700">{{ item.email }}</span> ·
              <span
                :class="
                  item.status === 'failed' ? 'text-danger-700' : 'text-ink-500'
                "
                >{{
                  item.reason
                    ? reasonLabel(item.reason)
                    : t('invitations.bulk.sent')
                }}</span
              >
            </li>
          </ul>
          <p class="text-sm text-ink-500">
            {{ t('invitations.bulk.recovery') }}
          </p>
        </div>
        <p v-if="error" role="alert" class="text-sm text-danger-700">
          {{ error }}
        </p>
      </div>
    </form>
    <template #footer>
      <div class="flex flex-wrap justify-end gap-3">
        <BaseButton variant="outline" :disabled="saving" @click="close">{{
          t('common.close')
        }}</BaseButton>
        <BaseButton
          v-if="review && !result"
          variant="outline"
          :disabled="saving"
          @click="editDraft"
          >{{ t('invitations.bulk.edit') }}</BaseButton
        >
        <BaseButton
          v-if="!result"
          type="submit"
          form="tenant-invitation-form"
          :loading="saving"
          :disabled="!canSubmit"
        >
          {{
            reinvite
              ? t('invitations.bulk.reinvite')
              : review
                ? t('invitations.bulk.sendCount', { count: validCount })
                : t('invitations.bulk.review')
          }}
        </BaseButton>
      </div>
    </template>
  </BaseDrawer>
</template>

<script setup>
import { computed, onBeforeUnmount, reactive, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import {
  getTenantGroups,
  previewTenantInvitations,
  bulkInviteTenantMembers,
  inviteTenantMember
} from '@/api/tenant'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseDrawer from '@/components/ui/BaseDrawer.vue'
import BaseSelect from '@/components/ui/BaseSelect.vue'
import {
  classifyInvitationRows,
  invitationCsvTemplate,
  parseInvitationCsv,
  parseInvitationText
} from '@/utils/invitations'

const props = defineProps({
  show: Boolean,
  reinvite: { type: Object, default: null }
})
const emit = defineEmits(['close', 'saved'])
const { t } = useI18n()
const form = reactive({ email: '', role: 'member', group_ids: [] })
const formElement = ref(null)
const groups = ref([])
const groupsLoading = ref(false)
const groupsError = ref(false)
const saving = ref(false)
const checking = ref(false)
const error = ref('')
const conflict = ref(false)
const review = ref(null)
const result = ref(null)
const validation = ref(null)
const csvSource = ref('')
const csvName = ref('')
const importError = ref('')
const importing = ref(false)
let groupRequest = 0
let generation = 0
let drawerRequest = 0
let fileRequest = 0
let validationTimer = null

const parsed = computed(() => {
  try {
    const entries = csvSource.value
      ? parseInvitationCsv(csvSource.value, groups.value, form)
      : parseInvitationText(form.email, form)
    return {
      entries,
      error: entries.length > 100 ? t('invitations.bulk.tooMany') : ''
    }
  } catch (failure) {
    return { entries: [], error: t(`invitations.bulk.${failure.message}`) }
  }
})
const parsingError = computed(() => parsed.value.error)
const displayItems = computed(
  () =>
    review.value?.items ||
    validation.value?.items ||
    classifyInvitationRows(parsed.value.entries)
)
const validCount = computed(
  () => displayItems.value.filter((item) => item.status === 'valid').length
)
const canSubmit = computed(
  () =>
    !saving.value &&
    !conflict.value &&
    (!props.reinvite || (!groupsLoading.value && !groupsError.value)) &&
    !importing.value &&
    !parsingError.value &&
    !importError.value &&
    (props.reinvite
      ? parsed.value.entries.length === 1
      : review.value
        ? validCount.value > 0
        : parsed.value.entries.length > 0 && validCount.value > 0)
)

function invalidateValidation() {
  generation++
  clearTimeout(validationTimer)
  validation.value = null
  checking.value = false
}

async function loadGroups() {
  const request = ++groupRequest
  groupsLoading.value = true
  groupsError.value = false
  try {
    const data = await getTenantGroups()
    if (request === groupRequest && props.show)
      groups.value = data.results || []
  } catch {
    if (request === groupRequest && props.show) groupsError.value = true
  } finally {
    if (request === groupRequest) groupsLoading.value = false
  }
}

async function validateDraft() {
  if (
    !props.show ||
    review.value ||
    result.value ||
    parsingError.value ||
    !parsed.value.entries.length ||
    props.reinvite
  )
    return null
  const current = ++generation
  const entries = JSON.parse(JSON.stringify(parsed.value.entries))
  checking.value = true
  try {
    const data = await previewTenantInvitations({ entries })
    if (current !== generation || !props.show) return null
    validation.value = { ...data, entries }
    return validation.value
  } catch (failure) {
    if (current === generation)
      error.value = t(
        failure?.response?.status === 403
          ? 'invitations.bulk.authorityChanged'
          : 'invitations.bulk.validationFailed'
      )
    return null
  } finally {
    if (current === generation) checking.value = false
  }
}

async function submit() {
  if (!canSubmit.value || result.value || !formElement.value?.reportValidity())
    return
  const session = drawerRequest
  saving.value = true
  error.value = ''
  clearTimeout(validationTimer)
  try {
    if (props.reinvite) {
      const member = props.reinvite
      await inviteTenantMember({
        email: member.email,
        role: form.role,
        group_ids: [...form.group_ids],
        target_id: member.id,
        version: member.version
      })
      if (session !== drawerRequest || !props.show) return
      result.value = {
        sent: 1,
        skipped: 0,
        failed: 0,
        items: [{ index: 0, email: member.email, status: 'sent', reason: '' }]
      }
      emit('saved', { keepOpen: true })
      return
    }
    if (!review.value) {
      const data = await validateDraft()
      if (data?.counts.valid) review.value = data
      return
    }
    const current = generation
    const data = await bulkInviteTenantMembers({
      entries: review.value.entries,
      confirmation: review.value.confirmation
    })
    if (current !== generation || session !== drawerRequest || !props.show)
      return
    result.value = data
    emit('saved', { keepOpen: true })
  } catch (failure) {
    if (session !== drawerRequest || !props.show) return
    const data = failure?.response?.data?.data || failure?.response?.data || {}
    if (
      props.reinvite &&
      data.detail === 'INVITATION_DELIVERY_FAILED' &&
      data.member_id
    ) {
      result.value = {
        sent: 0,
        skipped: 0,
        failed: 1,
        items: [
          {
            index: 0,
            email: props.reinvite.email,
            status: 'failed',
            reason: 'delivery_failed'
          }
        ]
      }
      emit('saved', { keepOpen: true })
      return
    }
    conflict.value = [403, 409].includes(failure?.response?.status)
    error.value = t(
      failure?.response?.status === 409
        ? 'invitations.bulk.reviewExpired'
        : failure?.response?.status === 403
          ? 'invitations.bulk.authorityChanged'
          : 'invitations.failed'
    )
  } finally {
    if (session === drawerRequest) saving.value = false
  }
}

function editDraft() {
  conflict.value = false
  review.value = null
  error.value = ''
  invalidateValidation()
  validateDraft()
}

function close() {
  if (saving.value) return
  invalidateValidation()
  fileRequest++
  emit('close', conflict.value)
}

function clearCsv() {
  csvSource.value = ''
  csvName.value = ''
  importError.value = ''
  fileRequest++
  importing.value = false
}

async function importCsv(event) {
  const file = event.target.files?.[0]
  event.target.value = ''
  if (!file || saving.value || importing.value) return
  const current = ++fileRequest
  importing.value = true
  importError.value = ''
  try {
    if (file.size > 128 * 1024) throw new Error('fileTooLarge')
    const text = await file.text()
    if (current !== fileRequest || !props.show) return
    parseInvitationCsv(text, groups.value, form)
    csvSource.value = text
    csvName.value = file.name
  } catch (failure) {
    if (current === fileRequest && props.show) {
      const known = ['invalidCsv', 'emptyCsv', 'tooMany', 'fileTooLarge']
      importError.value = t(
        `invitations.bulk.${known.includes(failure.message) ? failure.message : 'importFailed'}`
      )
    }
  } finally {
    if (current === fileRequest) importing.value = false
  }
}

function downloadTemplate() {
  const url = URL.createObjectURL(
    new Blob([invitationCsvTemplate()], { type: 'text/csv;charset=utf-8' })
  )
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = 'member-invitations.csv'
  anchor.click()
  URL.revokeObjectURL(url)
}

function reasonLabel(reason) {
  const known = [
    'review_changed',
    'invalid_email',
    'invalid_role',
    'invalid_groups',
    'duplicate',
    'email_unavailable',
    'rate_limited',
    'delivery_failed',
    'authority_changed'
  ]
  return t(
    known.includes(reason)
      ? `invitations.bulk.reasons.${reason}`
      : 'invitations.failed'
  )
}

function configurationLabel(index) {
  const entry = (review.value?.entries || parsed.value.entries)[index]
  if (!entry) return ''
  const role = t(
    entry.role === 'tenant_admin'
      ? 'management.tenantAdminRole'
      : 'management.memberRole'
  )
  const names = entry.group_ids
    .map((id) => groups.value.find((group) => group.id === id)?.name)
    .filter(Boolean)
  return names.length ? `${role} · ${names.join(' / ')}` : role
}

watch(
  () => [JSON.stringify(parsed.value.entries), props.show],
  () => {
    if (review.value || result.value) return
    invalidateValidation()
    error.value = ''
    if (
      props.show &&
      !props.reinvite &&
      parsed.value.entries.length &&
      !parsingError.value
    )
      validationTimer = setTimeout(validateDraft, 400)
  }
)

watch(
  () => [props.show, props.reinvite],
  ([show]) => {
    drawerRequest++
    saving.value = false
    invalidateValidation()
    fileRequest++
    importing.value = false
    if (!show) {
      groupRequest++
      return
    }
    Object.assign(form, {
      email: props.reinvite?.email || '',
      role: props.reinvite?.role || 'member',
      group_ids: props.reinvite?.groups?.map((group) => group.id) || []
    })
    review.value = null
    result.value = null
    error.value = ''
    conflict.value = false
    importError.value = ''
    csvSource.value = ''
    csvName.value = ''
    groups.value = []
    loadGroups()
  },
  { immediate: true }
)

onBeforeUnmount(() => {
  drawerRequest++
  invalidateValidation()
  groupRequest++
  fileRequest++
})
</script>
