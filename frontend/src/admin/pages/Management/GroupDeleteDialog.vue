<template>
  <BaseModal
    :show="targets.length > 0"
    :title="t('management.groupDelete')"
    @close="close"
  >
    <div class="space-y-4">
      <p class="text-sm text-ink-600">{{ t('management.groupDeleteScope') }}</p>
      <BaseLoading v-if="loading" />
      <p v-if="error" role="alert" class="text-sm text-danger-700">
        {{ error }}
      </p>
      <template v-if="review && !result">
        <article
          v-for="group in review.groups"
          :key="group.uuid"
          class="space-y-2 border-b border-line py-3"
        >
          <h3 class="break-words text-sm font-semibold text-ink-900">
            {{ group.name }}
          </h3>
          <p v-if="group.skip_reason" class="text-sm text-warning-700">
            {{ t('management.groupForeignAssociations') }}
          </p>
          <template v-else>
            <p class="text-sm text-ink-600">
              {{
                t('management.groupDeleteHint', {
                  members: group.member_count,
                  assistants: group.assistant_count
                })
              }}
            </p>
            <ul class="space-y-1 text-sm text-ink-700">
              <li
                v-for="assistant in group.assistants"
                :key="assistant.uuid"
                class="break-words"
              >
                {{ assistant.name }}
              </li>
            </ul>
          </template>
        </article>
      </template>
      <div v-if="result" role="status" class="space-y-2 text-sm">
        <p>{{ t('management.groupDeleteResult', result) }}</p>
        <p v-for="item in result.skipped_items" :key="item.uuid">
          {{ snapshot.find((group) => group.uuid === item.uuid)?.name }} ·
          {{ t('management.groupForeignAssociations') }}
        </p>
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
          >{{ t('management.groupReviewAgain') }}</BaseButton
        >
        <BaseButton
          v-if="review && !result && !conflict"
          variant="danger"
          :disabled="!canDelete"
          :loading="saving"
          @click="submit"
          >{{ t('management.groupConfirmDelete') }}</BaseButton
        >
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
import { deleteTenantGroups, getTenantGroups } from '@/api/tenant'

const props = defineProps({ targets: { type: Array, default: () => [] } })
const emit = defineEmits(['close', 'applied'])
const { t } = useI18n()
const snapshot = ref([])
const review = ref(null)
const result = ref(null)
const loading = ref(false)
const saving = ref(false)
const conflict = ref(false)
const error = ref('')
let generation = 0
const canDelete = computed(
  () =>
    !loading.value &&
    !saving.value &&
    !conflict.value &&
    review.value?.groups.some((group) => !group.skip_reason)
)
function fail(err) {
  conflict.value = [403, 404, 409].includes(err?.response?.status)
  error.value = t(
    conflict.value
      ? 'management.groupSelectionChanged'
      : 'management.groupSaveFailed'
  )
}
async function load(refresh = false) {
  if (loading.value || saving.value || !snapshot.value.length) return
  const current = generation
  loading.value = true
  review.value = null
  error.value = ''
  try {
    let targets = snapshot.value.map((row) => ({ ...row }))
    if (refresh) {
      const response = await getTenantGroups()
      targets = targets.map((target) => {
        const group = response.results.find((row) => row.uuid === target.uuid)
        if (!group) throw { response: { status: 409 } }
        return { ...group }
      })
    }
    const response = await deleteTenantGroups({
      groups: targets.map(({ uuid, version }) => ({ uuid, version })),
      preview: true
    })
    if (current !== generation) return
    snapshot.value = targets
    review.value = response
    conflict.value = false
  } catch (err) {
    if (current === generation) fail(err)
  } finally {
    if (current === generation) loading.value = false
  }
}
async function submit() {
  if (!canDelete.value || result.value) return
  const current = generation
  const payload = {
    groups: snapshot.value.map(({ uuid, version }) => ({ uuid, version })),
    preview: false,
    confirmation: review.value.confirmation
  }
  saving.value = true
  error.value = ''
  try {
    const response = await deleteTenantGroups(payload)
    if (current !== generation) return
    result.value = response
    emit('applied', response)
  } catch (err) {
    if (current === generation) fail(err)
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
    result.value = null
    error.value = ''
    conflict.value = false
    loading.value = false
    saving.value = false
    if (snapshot.value.length) load()
  },
  { immediate: true }
)
</script>
