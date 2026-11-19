<template>
  <BaseModal :show="true" :title="t(`${prefix}.title`)" @close="close">
    <div v-if="result" role="status" class="space-y-3 text-sm">
      <p>{{ t(`${prefix}.result`, result) }}</p>
      <ul v-if="result.skipped_items?.length" class="space-y-2">
        <li v-for="item in result.skipped_items" :key="item.uuid">
          <strong>{{ assistantName(item.uuid) }}</strong>
          <p class="text-ink-600">{{ t(`${prefix}.${item.reason}`) }}</p>
        </li>
      </ul>
    </div>
    <div v-else class="space-y-4 text-sm">
      <p class="text-danger-700">{{ t(`${prefix}.warning`) }}</p>
      <p v-if="busy && !review" role="status">{{ t('common.loading') }}</p>
      <ul v-if="review" class="max-h-96 space-y-3 overflow-auto">
        <li
          v-for="row in review.assistants"
          :key="row.uuid"
          class="rounded-lg border border-line p-3"
        >
          <strong>{{ row.name }}</strong>
          <p class="mt-1 break-all font-mono text-xs text-ink-600">
            {{ row.slug }}
          </p>
          <p v-if="row.skip_reason" class="mt-2 text-ink-600">
            {{ t(`${prefix}.${row.skip_reason}`) }}
          </p>
          <template v-else>
            <p class="my-2">{{ t(`${prefix}.impact`, row.impact) }}</p>
            <BaseInput
              v-model="confirmations[row.uuid]"
              :label="t(`${prefix}.confirmSlug`, { slug: row.slug })"
              :disabled="busy || readonly"
              autocomplete="off"
            />
          </template>
        </li>
      </ul>
      <p v-if="review && !eligible.length" role="status">
        {{ t(`${prefix}.noneEligible`) }}
      </p>
    </div>
    <p v-if="error" class="mt-3 text-sm text-danger-700" role="alert">
      {{ error }}
    </p>
    <template #footer>
      <BaseButton
        v-if="!result && !review"
        variant="outline"
        :loading="busy"
        @click="loadPreview(true)"
      >
        {{ t(`${prefix}.reviewAgain`) }}
      </BaseButton>
      <BaseButton
        v-if="!result && review && eligible.length"
        variant="danger"
        :disabled="!canDelete"
        :loading="busy"
        @click="save"
        >{{ t(`${prefix}.confirm`) }}</BaseButton
      >
      <BaseButton variant="outline" :disabled="busy" @click="close">
        {{ t(result ? 'common.close' : 'common.cancel') }}
      </BaseButton>
    </template>
  </BaseModal>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { bulkDeleteAssistants, listAssistants } from '@/api/lens'
import BaseModal from '@/components/ui/BaseModal.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseInput from '@/components/ui/BaseInput.vue'
import { useUserStore } from '@/store/user'
import { extractErrorMessage } from '@/utils/api'

const props = defineProps({ assistants: { type: Array, required: true } })
const emit = defineEmits(['close', 'updated'])
const { t } = useI18n()
const userStore = useUserStore()
const prefix = 'lensAdmin.deletion'
const readonly = computed(() => userStore.delegation?.mode === 'readonly')
const busy = ref(false)
const error = ref('')
const review = ref(null)
const result = ref(null)
const confirmations = ref({})
const eligible = computed(
  () => review.value?.assistants.filter((row) => !row.skip_reason) || []
)
const canDelete = computed(
  () =>
    !busy.value &&
    !readonly.value &&
    eligible.value.length > 0 &&
    eligible.value.every((row) => confirmations.value[row.uuid] === row.slug)
)

function assistantName(uuid) {
  return props.assistants.find((row) => row.uuid === uuid)?.name || uuid
}

function close() {
  if (!busy.value) emit('close')
}

async function loadPreview(refresh = false) {
  if (busy.value || result.value) return
  busy.value = true
  error.value = ''
  review.value = null
  confirmations.value = {}
  try {
    if (props.assistants.length > 100) throw new Error(t(`${prefix}.limit`))
    let rows = props.assistants
    if (refresh) {
      const current = await listAssistants({ managed: true, archived: 'all' })
      rows = props.assistants.map((row) =>
        current.find((item) => item.uuid === row.uuid)
      )
      if (rows.some((row) => !row)) throw new Error(t(`${prefix}.unavailable`))
    }
    review.value = await bulkDeleteAssistants({
      assistants: rows.map(({ uuid, updated_at }) => ({ uuid, updated_at })),
      preview: true
    })
  } catch (failure) {
    error.value = extractErrorMessage(
      failure,
      t('lensAdmin.messages.loadFailed')
    )
  } finally {
    busy.value = false
  }
}

async function save() {
  if (!canDelete.value || result.value) return
  busy.value = true
  error.value = ''
  try {
    result.value = await bulkDeleteAssistants({
      assistants: review.value.assistants.map(
        ({ uuid, updated_at, slug, skip_reason }) => ({
          uuid,
          updated_at,
          confirmation_slug: skip_reason ? slug : confirmations.value[uuid]
        })
      ),
      preview: false,
      confirmation_token: review.value.confirmation_token
    })
    emit('updated', result.value)
  } catch (failure) {
    error.value = extractErrorMessage(
      failure,
      t('lensAdmin.messages.saveFailed')
    )
    if ([400, 409].includes(failure.response?.status)) review.value = null
  } finally {
    busy.value = false
  }
}

onMounted(() => loadPreview())
</script>
