<template>
  <BaseModal
    :show="targets.length > 0"
    :title="t('management.memberDelete.title')"
    @close="close"
  >
    <div class="space-y-4">
      <p class="text-sm text-danger-700">
        {{ t('management.memberDelete.warning') }}
      </p>
      <BaseLoading v-if="loading" />
      <p v-if="error" role="alert" class="text-sm text-danger-700">
        {{ error }}
      </p>
      <template v-if="review && !result">
        <article
          v-for="member in review.members"
          :key="member.id"
          class="space-y-2 rounded-md border border-line p-3"
        >
          <h3 class="break-all text-sm font-semibold text-ink-900">
            {{ label(member.id) }}
          </h3>
          <p v-if="member.skip_reason" class="text-sm text-ink-600">
            {{ reason(member.skip_reason) }}
          </p>
          <template v-else>
            <p class="text-sm text-ink-600">
              {{ t('management.memberDelete.impact', member.impact) }}
            </p>
            <p class="text-sm text-ink-600">
              {{ t('management.memberDelete.files', member.impact) }}
            </p>
            <ul v-if="member.assistants.length" class="text-sm text-ink-600">
              <li
                v-for="assistant in member.assistants"
                :key="assistant.uuid"
                class="break-words"
              >
                {{ assistant.name }}
              </li>
            </ul>
            <p
              v-if="member.impact.assistants > member.assistants.length"
              class="text-xs text-ink-500"
            >
              {{
                t('management.memberDelete.assistantLimit', {
                  count: member.assistants.length
                })
              }}
            </p>
            <label class="block text-sm text-ink-700">
              {{
                t('management.memberDelete.confirmEmail', {
                  email: member.email
                })
              }}
              <input
                v-model="confirmations[member.id]"
                type="text"
                autocomplete="off"
                class="form-input mt-1 w-full"
                :disabled="saving || conflict"
              />
            </label>
          </template>
        </article>
      </template>
      <div v-if="result" role="status" class="space-y-3 text-sm">
        <p class="font-semibold text-ink-900">
          {{ t('management.memberDelete.result', result) }}
        </p>
        <ul class="space-y-2 text-ink-600">
          <li v-for="id in result.deleted_ids" :key="id">
            {{ label(id) }} · {{ t('management.memberDelete.deleted') }}
          </li>
          <li v-for="item in result.skipped_items" :key="item.id">
            {{ label(item.id) }} · {{ reason(item.reason) }}
          </li>
        </ul>
      </div>
    </div>
    <template #footer>
      <div class="flex flex-wrap justify-end gap-2">
        <BaseButton variant="outline" :disabled="saving" @click="close">{{
          t(result ? 'common.close' : 'common.cancel')
        }}</BaseButton>
        <BaseButton
          v-if="!result && (!review || conflict)"
          :disabled="loading || saving"
          @click="load(true)"
        >
          {{ t('management.memberDelete.review') }}
        </BaseButton>
        <BaseButton
          v-if="review && !result && !conflict"
          variant="danger"
          :disabled="!canDelete"
          :loading="saving"
          @click="submit"
        >
          {{ t('management.memberDelete.confirm') }}
        </BaseButton>
      </div>
    </template>
  </BaseModal>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseLoading from '@/components/ui/BaseLoading.vue'
import BaseModal from '@/components/ui/BaseModal.vue'
import { deleteTenantMembers, getTenantMembers } from '@/api/tenant'

const props = defineProps({ targets: { type: Array, default: () => [] } })
const emit = defineEmits(['close', 'applied'])
const { t } = useI18n()
const snapshot = ref([])
const review = ref(null)
const confirmations = ref({})
const loading = ref(false)
const saving = ref(false)
const conflict = ref(false)
const error = ref('')
const result = ref(null)
let generation = 0
const canDelete = computed(() => {
  const eligible =
    review.value?.members.filter((member) => !member.skip_reason) || []
  return (
    eligible.length > 0 &&
    !saving.value &&
    !loading.value &&
    !conflict.value &&
    eligible.every((member) => confirmations.value[member.id] === member.email)
  )
})

function label(id) {
  return snapshot.value.find((member) => member.id === id)?.label || String(id)
}

function reason(value) {
  return t(`management.memberDelete.reasons.${value}`)
}

function failure(err) {
  conflict.value = [403, 404, 409].includes(err?.response?.status)
  error.value = t(
    `management.memberDelete.${conflict.value ? 'changed' : 'failed'}`
  )
}

async function load(refresh = false) {
  if (loading.value || saving.value || !snapshot.value.length) return
  const current = generation
  loading.value = true
  error.value = ''
  confirmations.value = {}
  review.value = null
  try {
    let targets = snapshot.value.map((row) => ({ ...row }))
    if (refresh) {
      targets = await Promise.all(
        targets.map(async (target) => {
          const data = await getTenantMembers({
            search: target.email,
            page_size: 100
          })
          const member = data.results.find((row) => row.id === target.id)
          if (!member) throw { response: { status: 409 } }
          return { ...target, version: member.version, email: member.email }
        })
      )
    }
    const response = await deleteTenantMembers({
      members: targets.map(({ id, version }) => ({ id, version })),
      preview: true
    })
    if (current !== generation) return
    snapshot.value = targets
    review.value = response
    conflict.value = false
  } catch (err) {
    if (current === generation) failure(err)
  } finally {
    if (current === generation) loading.value = false
  }
}

async function submit() {
  if (!canDelete.value || result.value) return
  const current = generation
  const payload = {
    members: review.value.members.map(
      ({ id, version, email, skip_reason }) => ({
        id,
        version,
        confirmation_email: skip_reason ? email : confirmations.value[id]
      })
    ),
    preview: false,
    confirmation: review.value.confirmation
  }
  saving.value = true
  error.value = ''
  try {
    const response = await deleteTenantMembers(payload)
    if (current !== generation) return
    result.value = response
    emit('applied', response)
  } catch (err) {
    if (current === generation) failure(err)
  } finally {
    if (current === generation) saving.value = false
  }
}

function close() {
  if (!saving.value) emit('close')
}

watch(
  () => props.targets,
  () => {
    generation++
    snapshot.value = props.targets.map((row) => ({ ...row }))
    review.value = null
    confirmations.value = {}
    loading.value = false
    saving.value = false
    conflict.value = false
    error.value = ''
    result.value = null
    if (snapshot.value.length) load()
  },
  { immediate: true }
)
</script>
