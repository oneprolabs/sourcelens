<template>
  <AdminLayout>
    <div class="w-full max-w-full space-y-4 p-4 md:p-6">
      <div class="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 class="text-lg font-semibold text-theme">
            {{ t('alertManagement.messages.title') }}
          </h1>
          <p class="mt-1 text-sm text-theme-muted">
            {{ t('alertManagement.messages.description') }}
          </p>
        </div>
      </div>
      <BaseCard class="[&>.card-body]:p-4 md:[&>.card-body]:p-6">
        <form
          class="mb-6 grid grid-cols-2 items-end gap-3 sm:flex sm:flex-wrap"
          @submit.prevent="load(1)"
        >
          <BaseInput
            v-model="topic"
            class="col-span-2 w-full sm:w-64"
            :label="t('alertManagement.messages.topic')"
            placeholder="release.ready"
          />
          <BaseButton type="submit" variant="outline">{{
            t('common.refresh')
          }}</BaseButton>
          <BaseButton class="sm:ml-auto" @click="openPublisher">{{
            t('alertManagement.messages.publish')
          }}</BaseButton>
        </form>

        <p
          v-if="error"
          role="alert"
          class="break-words text-sm text-danger-700"
        >
          {{ error }}
        </p>
        <p v-if="published" role="status" class="text-sm text-success-700">
          {{ t('alertManagement.messages.recorded') }}
        </p>
        <BaseLoading v-if="loading" />
        <AlertEmptyState
          v-else-if="!messages.length"
          :text="t('alertManagement.messages.empty')"
        />
        <template v-else>
          <div class="overflow-hidden rounded-lg border border-line bg-surface">
            <div class="overflow-x-auto">
              <table
                class="min-w-[48rem] w-full divide-y divide-line text-left text-sm"
              >
                <thead class="bg-surface-sunken text-theme-muted">
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
                      class="px-4 py-3 text-xs font-semibold uppercase tracking-wider text-theme-secondary"
                    >
                      {{ t(`alertManagement.messages.${key}`) }}
                    </th>
                  </tr>
                </thead>
                <tbody
                  class="divide-y divide-line bg-surface text-theme-secondary"
                >
                  <tr
                    v-for="row in messages"
                    :key="row.uuid"
                    class="transition-colors hover:bg-surface-hover"
                  >
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
                          query: {
                            subject: 'message',
                            source_uuid: row.source_uuid
                          }
                        }"
                        >{{
                          t('alertManagement.messages.deliveries')
                        }}</router-link
                      >
                    </td>
                    <td class="whitespace-nowrap px-4 py-3">
                      {{ new Date(row.created_at).toLocaleString() }}
                      <p class="text-xs text-theme-muted">
                        {{ row.publisher_name }}
                      </p>
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>
          <PaginationBar
            v-model:page-size="pageSize"
            :current-page="page"
            :total="total"
            @page-size-change="load(1)"
            @prev="load(page - 1)"
            @next="load(page + 1)"
          />
        </template>
      </BaseCard>
      <BaseModal
        :show="showPublisher"
        :title="t('alertManagement.messages.publish')"
        @close="showPublisher = false"
      >
        <form
          id="alert-message-form"
          class="space-y-4"
          @submit.prevent="publish"
        >
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
          <p class="text-xs text-theme-muted">
            {{ t('alertManagement.messages.keyHint') }}
          </p>
          <BaseInput
            v-model="form.title"
            :label="t('alertManagement.messages.messageTitle')"
            maxlength="200"
            required
          />
          <label class="block text-sm font-medium text-theme"
            >{{ t('alertManagement.messages.body')
            }}<textarea
              v-model="form.body"
              rows="6"
              maxlength="12000"
              required
              class="mt-1 block w-full rounded-lg border border-line bg-surface px-3 py-2 text-sm text-theme shadow-sm focus:border-primary-500 focus:outline-none focus:ring-2 focus:ring-primary-500/20"
            />
          </label>
          <details class="rounded-lg border border-line bg-surface-sunken p-4">
            <summary class="cursor-pointer text-sm text-theme-secondary">
              {{ t('alertManagement.diagnosis.translations') }}
            </summary>
            <p class="mt-2 text-xs text-theme-muted">
              {{ t('alertManagement.diagnosis.translationHint') }}
            </p>
            <div
              v-for="(translation, language) in form.translations"
              :key="language"
              class="mt-4 space-y-3 border-t border-line pt-4"
            >
              <p class="text-sm font-semibold text-theme">
                {{
                  { 'zh-CN': '简体中文', 'en-US': 'English', es: 'Español' }[
                    language
                  ]
                }}
              </p>
              <BaseInput
                v-model="translation.title"
                :label="t('alertManagement.messages.messageTitle')"
                maxlength="200"
              />
              <label class="block text-sm font-medium text-theme"
                >{{ t('alertManagement.messages.body') }}
                <textarea
                  v-model="translation.body"
                  rows="3"
                  maxlength="12000"
                  class="mt-1 block w-full rounded-lg border border-line bg-surface px-3 py-2 text-sm text-theme shadow-sm focus:border-primary-500 focus:outline-none focus:ring-2 focus:ring-primary-500/20"
                />
              </label>
            </div>
          </details>
          <p
            v-if="publishError"
            role="alert"
            class="break-words text-sm text-danger-700"
          >
            {{ publishError }}
          </p>
        </form>
        <template #footer>
          <div class="flex w-full justify-end gap-3">
            <BaseButton
              type="button"
              variant="outline"
              :disabled="publishing"
              @click="showPublisher = false"
              >{{ t('common.cancel') }}</BaseButton
            ><BaseButton
              type="submit"
              form="alert-message-form"
              :loading="publishing"
              >{{ t('alertManagement.messages.publish') }}</BaseButton
            >
          </div>
        </template>
      </BaseModal>
    </div>
  </AdminLayout>
</template>
<script setup>
import { onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { alertsAdminApi } from '@/admin/api'
import AdminLayout from '@/admin/layout/AdminLayout.vue'
import BaseCard from '@/components/ui/BaseCard.vue'
import AlertEmptyState from './AlertEmptyState.vue'
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
    body: '',
    translations: Object.fromEntries(
      ['zh-CN', 'en-US', 'es'].map((language) => [
        language,
        { title: '', body: '' }
      ])
    )
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
    await alertsAdminApi.publishMessage({
      ...form.value,
      translations: Object.fromEntries(
        Object.entries(form.value.translations).filter(
          ([, value]) => value.title.trim() || value.body.trim()
        )
      )
    })
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
