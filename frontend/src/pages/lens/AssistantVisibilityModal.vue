<template>
  <BaseModal :show="true" :title="t(`${prefix}.title`)" @close="close">
    <p class="mb-4 text-sm text-ink-600">
      {{ t(`${prefix}.selection`, { count: assistants.length }) }}
    </p>
    <fieldset :disabled="busy || readonly" class="min-w-0 space-y-4">
      <label
        v-for="value in ['tenant', 'private', 'public']"
        :key="value"
        class="flex items-start gap-3 rounded-lg border border-line p-3"
      >
        <input
          v-model="visibility"
          type="radio"
          name="batch-visibility"
          :value="value"
          class="mt-1"
        />
        <span
          ><strong class="block text-sm">{{
            t(`lensAdmin.visibility.${value}`)
          }}</strong
          ><span class="text-sm text-ink-600">{{
            t(`lensAdmin.visibility.${value}Desc`)
          }}</span></span
        >
      </label>
      <p
        v-if="visibility === 'public'"
        class="rounded-lg bg-amber-50 p-3 text-sm text-amber-800"
      >
        {{ t('lensAdmin.visibility.publicWarning') }}
      </p>
      <p
        v-if="visibility === 'private' && !canManageAccessGrants"
        class="text-sm text-ink-600"
      >
        {{ t('lensAdmin.access.delegatedHint') }}
      </p>
      <div
        v-if="visibility === 'private' && canManageAccessGrants"
        class="space-y-3"
      >
        <p class="text-sm text-ink-600">{{ t(`${prefix}.replaceGrants`) }}</p>
        <label class="block text-sm">
          {{ t(`${prefix}.searchSubjects`) }}
          <input
            v-model="search"
            type="search"
            maxlength="160"
            class="form-input mt-1 w-full"
            @input="scheduleSearch"
          />
        </label>
        <p v-if="selectedSubjects.length" class="text-sm" aria-live="polite">
          {{
            t(`${prefix}.selectedSubjects`, { count: selectedSubjects.length })
          }}
        </p>
        <div
          class="max-h-64 space-y-2 overflow-auto rounded-lg border border-line p-3"
        >
          <label
            v-for="subject in subjectOptions"
            :key="subject.key"
            class="flex items-center gap-2 text-sm"
          >
            <input
              v-model="selectedKeys"
              type="checkbox"
              :value="subject.key"
            />
            <span
              >{{ subject.name }} ·
              {{
                t(
                  `lensAdmin.access.${subject.type === 'user' ? 'users' : 'groups'}`
                )
              }}</span
            >
          </label>
          <p v-if="subjectsLoading" role="status" class="text-sm text-ink-600">
            {{ t('common.loading') }}
          </p>
          <p
            v-else-if="!subjectOptions.length && !subjectsError"
            class="text-sm text-ink-600"
          >
            {{ t(`${prefix}.noSubjects`) }}
          </p>
          <div v-if="subjectsError" role="alert" class="text-sm text-red-700">
            {{ subjectsError }}
            <BaseButton
              variant="outline"
              size="sm"
              @click="loadSubjects(subjectPage)"
              >{{ t('lensAdmin.access.retry') }}</BaseButton
            >
          </div>
          <BaseButton
            v-else-if="hasMoreSubjects && !subjectsLoading"
            variant="outline"
            size="sm"
            @click="loadSubjects(subjectPage + 1)"
            >{{ t(`${prefix}.loadMore`) }}</BaseButton
          >
        </div>
      </div>
    </fieldset>
    <p v-if="error" role="alert" class="mt-4 text-sm text-red-700">
      {{ error }}
    </p>
    <template #footer>
      <BaseButton
        :loading="busy"
        :disabled="
          readonly ||
          !assistants.length ||
          (visibility === 'private' &&
            canManageAccessGrants &&
            !selectedSubjects.length)
        "
        @click="save"
        >{{ t(`${prefix}.apply`, { count: assistants.length }) }}</BaseButton
      >
      <BaseButton
        variant="outline"
        class="mr-3"
        :disabled="busy"
        @click="close"
        >{{ t('common.cancel') }}</BaseButton
      >
    </template>
  </BaseModal>
</template>

<script setup>
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import {
  bulkUpdateAssistantVisibility,
  listAssistantAccessSubjects
} from '@/api/lens'
import { extractErrorMessage } from '@/utils/api'
import { useUserStore } from '@/store/user'
import { useToast } from '@/composables/useToast'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseModal from '@/components/ui/BaseModal.vue'
import { createLatestRequestRunner } from './assistantAccessSelectors'

const props = defineProps({ assistants: { type: Array, required: true } })
const emit = defineEmits(['close', 'updated'])
const { t } = useI18n()
const { showSuccess } = useToast()
const userStore = useUserStore()
const canManageAccessGrants = computed(() => !userStore.delegation)
const prefix = 'lensAdmin.bulkVisibility'
const readonly = computed(() => userStore.delegation?.mode === 'readonly')
const visibility = ref('tenant')
const busy = ref(false)
const error = ref('')
const search = ref('')
const selectedKeys = ref([])
const subjects = ref([])
const subjectCache = ref({})
const subjectsLoading = ref(false)
const subjectsError = ref('')
const subjectPage = ref(1)
const hasMoreSubjects = ref(false)
const requests = createLatestRequestRunner()
let searchTimer
const selectedSubjects = computed(() =>
  selectedKeys.value.map((key) => subjectCache.value[key]).filter(Boolean)
)
const subjectOptions = computed(() => [
  ...new Map(
    [...selectedSubjects.value, ...subjects.value].map((subject) => [
      subject.key,
      subject
    ])
  ).values()
])

function close() {
  if (!busy.value) emit('close')
}

function scheduleSearch() {
  if (!canManageAccessGrants.value) return
  clearTimeout(searchTimer)
  requests.invalidate()
  subjects.value = []
  subjectsLoading.value = true
  searchTimer = setTimeout(() => loadSubjects(1), 250)
}

async function loadSubjects(page = 1) {
  if (readonly.value || !canManageAccessGrants.value) return
  subjectPage.value = page
  subjectsLoading.value = true
  subjectsError.value = ''
  const params = { page, page_size: 30, search: search.value.trim() }
  const result = await requests.run(() => listAssistantAccessSubjects(params))
  if (!result.current || !canManageAccessGrants.value) return
  subjectsLoading.value = false
  if (result.error) {
    subjectsError.value = extractErrorMessage(
      result.error,
      t('lensAdmin.messages.loadFailed')
    )
    return
  }
  const pages = [result.value.users, result.value.groups]
  const rows = pages.flatMap((response, index) =>
    (response.results || response || [])
      .filter((row) => index || row.is_active !== false)
      .map((row) => ({
        type: index ? 'group' : 'user',
        id: row.id,
        key: `${index ? 'group' : 'user'}:${row.id}`,
        name: row.name || row.username
      }))
  )
  for (const row of rows) subjectCache.value[row.key] = row
  subjects.value = page === 1 ? rows : [...subjects.value, ...rows]
  hasMoreSubjects.value = pages.some(
    (response) => Number(response.count || 0) > page * 30
  )
}

async function save() {
  if (busy.value || readonly.value || !props.assistants.length) return
  if (
    visibility.value === 'private' &&
    canManageAccessGrants.value &&
    !selectedSubjects.value.length
  )
    return
  busy.value = true
  error.value = ''
  try {
    const result = await bulkUpdateAssistantVisibility({
      assistants: props.assistants.map(({ uuid, updated_at }) => ({
        uuid,
        updated_at
      })),
      visibility: visibility.value,
      ...(visibility.value === 'private' && canManageAccessGrants.value
        ? {
            access_grants: selectedSubjects.value.map(({ type, id }) => ({
              type,
              id
            }))
          }
        : {})
    })
    showSuccess(t(`${prefix}.result`, result))
    emit('updated', result)
  } catch (failure) {
    error.value = extractErrorMessage(
      failure,
      t('lensAdmin.messages.saveFailed')
    )
  } finally {
    busy.value = false
  }
}

watch(visibility, (value) => {
  if (value === 'private' && !subjects.value.length) loadSubjects()
})
watch(canManageAccessGrants, (allowed) => {
  if (allowed) return
  clearTimeout(searchTimer)
  requests.invalidate()
  subjects.value = []
  subjectCache.value = {}
  selectedKeys.value = []
  subjectsLoading.value = false
  subjectsError.value = ''
})
onBeforeUnmount(() => {
  clearTimeout(searchTimer)
  requests.invalidate()
})
</script>
