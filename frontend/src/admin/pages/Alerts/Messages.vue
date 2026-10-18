<template>
  <AdminLayout>
    <div class="space-y-4 p-4 md:p-6">
      <div class="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 class="text-xl font-semibold text-ink-900">
            {{ t('alertManagement.messages.title') }}
          </h1>
          <p class="mt-1 text-sm text-ink-500">
            {{ t('alertManagement.messages.description') }}
          </p>
        </div>
        <BaseButton @click="openPublisher">{{
          t('alertManagement.messages.publish')
        }}</BaseButton>
      </div>
      <form class="flex flex-wrap items-end gap-3" @submit.prevent="load(1)">
        <BaseInput
          v-model="topic"
          :label="t('alertManagement.messages.topic')"
          placeholder="release.ready"
        />
        <BaseButton type="submit" variant="outline">{{
          t('common.refresh')
        }}</BaseButton>
      </form>
      <p v-if="error" role="alert" class="break-words text-sm text-danger-700">
        {{ error }}
      </p>
      <p v-if="published" role="status" class="text-sm text-success-700">
        {{ t('alertManagement.messages.recorded') }}
      </p>
      <BaseLoading v-if="loading" />
      <div
        v-else
        class="overflow-x-auto rounded-lg border border-line bg-surface"
      >
        <table
          class="min-w-[48rem] w-full divide-y divide-line text-left text-sm"
        >
          <thead class="bg-surface-sunken text-ink-500">
            <tr>
              <th
                v-for="key in [
                  'topic',
                  'messageTitle',
                  'businessKey',
                  'matching',
                  'createdAt'
                ]"
                :key="key"
                class="px-4 py-3 font-medium"
              >
                {{ t(`alertManagement.messages.${key}`) }}
              </th>
            </tr>
          </thead>
          <tbody class="divide-y divide-line text-ink-700">
            <tr v-for="row in messages" :key="row.uuid">
              <td class="px-4 py-3">{{ row.topic }}</td>
              <td class="max-w-md px-4 py-3">
                <details>
                  <summary class="cursor-pointer font-medium">
                    {{ row.title }}
                  </summary>
                  <p class="mt-2 whitespace-pre-wrap break-words">
                    {{ row.body }}
                  </p>
                </details>
              </td>
              <td class="max-w-xs break-all px-4 py-3">
                {{ row.idempotency_key }}
              </td>
              <td class="px-4 py-3">
                <p>
                  {{
                    t(
                      row.processed_at
                        ? 'alertManagement.messages.processed'
                        : 'alertManagement.messages.pending'
                    )
                  }}
                </p>
                <router-link
                  class="text-primary-600 underline"
                  :to="{
                    path: '/management/alerts/events',
                    query: { subject: 'message', source_uuid: row.source_uuid }
                  }"
                  >{{ t('alertManagement.messages.deliveries') }}</router-link
                >
              </td>
              <td class="whitespace-nowrap px-4 py-3">
                {{ new Date(row.created_at).toLocaleString() }}
                <p class="text-xs text-ink-500">{{ row.publisher_name }}</p>
              </td>
            </tr>
            <tr v-if="!messages.length">
              <td colspan="5" class="p-10 text-center text-ink-500">
                {{ t('alertManagement.messages.empty') }}
              </td>
            </tr>
          </tbody>
        </table>
        <PaginationBar
          v-model:page-size="pageSize"
          :current-page="page"
          :total="total"
          @page-size-change="load(1)"
          @prev="load(page - 1)"
          @next="load(page + 1)"
        />
      </div>
      <BaseModal
        :show="showPublisher"
        :title="t('alertManagement.messages.publish')"
        @close="showPublisher = false"
      >
        <form class="space-y-4" @submit.prevent="publish">
          <BaseInput
            v-model="form.topic"
            :label="t('alertManagement.messages.topic')"
            placeholder="release.ready"
            maxlength="120"
            required
          />
          <BaseInput
            v-model="form.idempotency_key"
            :label="t('alertManagement.messages.businessKey')"
            maxlength="160"
            required
          />
          <p class="text-xs text-ink-500">
            {{ t('alertManagement.messages.keyHint') }}
          </p>
          <BaseInput
            v-model="form.title"
            :label="t('alertManagement.messages.messageTitle')"
            maxlength="200"
            required
          />
          <label class="block text-sm text-ink-700"
            >{{ t('alertManagement.messages.body')
            }}<textarea
              v-model="form.body"
              rows="6"
              maxlength="12000"
              required
              class="mt-1 w-full rounded border border-line bg-surface p-2"
            />
          </label>
          <p
            v-if="publishError"
            role="alert"
            class="break-words text-sm text-danger-700"
          >
            {{ publishError }}
          </p>
          <div class="flex justify-end gap-3">
            <BaseButton
              type="button"
              variant="outline"
              :disabled="publishing"
              @click="showPublisher = false"
              >{{ t('common.cancel') }}</BaseButton
            ><BaseButton type="submit" :loading="publishing">{{
              t('alertManagement.messages.publish')
            }}</BaseButton>
          </div>
        </form>
      </BaseModal>
    </div>
  </AdminLayout>
</template>
<script setup>
import { onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { alertsAdminApi } from '@/admin/api'
import AdminLayout from '@/admin/layout/AdminLayout.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseInput from '@/components/ui/BaseInput.vue'
import BaseLoading from '@/components/ui/BaseLoading.vue'
import BaseModal from '@/components/ui/BaseModal.vue'
import PaginationBar from '@/components/ui/PaginationBar.vue'
import { alertApiError } from './alertOptions'
const { t } = useI18n()
const messages = ref([]),
  topic = ref(''),
  page = ref(1),
  pageSize = ref(20),
  total = ref(0)
const loading = ref(false),
  error = ref(''),
  showPublisher = ref(false),
  publishing = ref(false),
  publishError = ref(''),
  published = ref(false)
const form = ref({ topic: '', idempotency_key: '', title: '', body: '' })
async function load(value = 1) {
  page.value = Math.max(1, value)
  loading.value = true
  error.value = ''
  try {
    const data = await alertsAdminApi.getMessages({
      page: page.value,
      page_size: pageSize.value,
      topic: topic.value.trim() || undefined
    })
    messages.value = data.results || []
    total.value = data.count || 0
  } catch (err) {
    error.value = alertApiError(err)
  } finally {
    loading.value = false
  }
}
function openPublisher() {
  form.value = {
    topic: topic.value.trim(),
    idempotency_key: crypto.randomUUID(),
    title: '',
    body: ''
  }
  publishError.value = ''
  published.value = false
  showPublisher.value = true
}
async function publish() {
  if (publishing.value) return
  publishing.value = true
  publishError.value = ''
  try {
    await alertsAdminApi.publishMessage(form.value)
    showPublisher.value = false
    published.value = true
    topic.value = form.value.topic
    await load(1)
  } catch (err) {
    publishError.value = alertApiError(err)
  } finally {
    publishing.value = false
  }
}
onMounted(load)
</script>
