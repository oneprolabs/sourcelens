<template>
  <AdminLayout>
    <div class="flex max-w-full flex-col gap-4 py-4">
      <section
        class="overflow-hidden rounded-lg border border-line bg-surface shadow-sm"
      >
        <div
          class="flex flex-col gap-4 border-b border-line px-5 py-4 lg:flex-row lg:items-start lg:justify-between"
        >
          <div class="min-w-0">
            <div class="flex flex-wrap items-center gap-2">
              <h1 class="text-xl font-semibold text-ink-900">
                {{ t('lensAdmin.pages.credentials.title') }}
              </h1>
              <span
                class="rounded-md border border-line bg-surface-sunken px-2 py-1 text-xs text-ink-500"
              >
                {{
                  t('lensAdmin.total', {
                    label: t('lensAdmin.pages.credentials.label'),
                    count: credentials.length
                  })
                }}
              </span>
            </div>
          </div>
          <div class="flex flex-wrap items-center gap-2">
            <BaseButton
              variant="outline"
              size="sm"
              :loading="loading"
              @click="load"
            >
              {{ t('common.refresh') }}
            </BaseButton>
            <BaseButton variant="primary" size="sm" @click="startCreate">
              {{ t('lensAdmin.pages.credentials.action') }}
            </BaseButton>
          </div>
        </div>

        <div v-if="credentials.length" class="border-b border-line px-5 py-4">
          <div class="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
            <label class="block">
              <span class="mb-1 block text-xs font-medium text-ink-600">
                {{ t('lensAdmin.credentials.searchLabel') }}
              </span>
              <input
                v-model="searchQuery"
                type="search"
                class="form-input"
                :placeholder="t('lensAdmin.credentials.searchPlaceholder')"
              />
            </label>
            <label class="block">
              <span class="mb-1 block text-xs font-medium text-ink-600">
                {{ t('lensAdmin.credentials.providerFilter') }}
              </span>
              <BaseSelect v-model="providerFilter">
                <option value="all">
                  {{ t('lensAdmin.credentials.allProviders') }}
                </option>
                <option value="github">GitHub</option>
                <option value="gitlab">GitLab</option>
                <option value="feishu">Feishu</option>
              </BaseSelect>
            </label>
            <label class="block">
              <span class="mb-1 block text-xs font-medium text-ink-600">
                {{ t('lensAdmin.credentials.validationFilter') }}
              </span>
              <BaseSelect v-model="validationFilter">
                <option value="all">
                  {{ t('lensAdmin.credentials.allValidationStatuses') }}
                </option>
                <option value="success">
                  {{ t('lensAdmin.credentials.validationSuccess') }}
                </option>
                <option value="failed">
                  {{ t('lensAdmin.credentials.validationFailed') }}
                </option>
                <option value="unchecked">
                  {{ t('lensAdmin.credentials.validationUnchecked') }}
                </option>
              </BaseSelect>
            </label>
            <label class="block">
              <span class="mb-1 block text-xs font-medium text-ink-600">
                {{ t('lensAdmin.credentials.sortLabel') }}
              </span>
              <BaseSelect v-model="sortOption">
                <option value="default">
                  {{ t('lensAdmin.credentials.sortDefault') }}
                </option>
                <option value="name_asc">
                  {{ t('lensAdmin.credentials.sortName') }}
                </option>
                <option value="last_used_desc">
                  {{ t('lensAdmin.credentials.sortLastUsed') }}
                </option>
                <option value="validated_desc">
                  {{ t('lensAdmin.credentials.sortValidated') }}
                </option>
              </BaseSelect>
            </label>
          </div>
          <p class="mt-2 text-xs text-ink-500" role="status">
            {{
              t('lensAdmin.credentials.filteredCount', {
                filtered: filteredCredentials.length,
                total: credentials.length
              })
            }}
          </p>
        </div>

        <div class="px-5 py-4">
          <BaseLoading v-if="loading && credentials.length === 0" />

          <div
            v-else-if="credentials.length === 0"
            class="rounded-lg border border-line bg-surface-sunken py-16 text-center"
          >
            <p class="text-sm font-medium text-ink-500">
              {{ t('common.noData') }}
            </p>
          </div>

          <div
            v-else-if="filteredCredentials.length === 0"
            class="rounded-lg border border-line bg-surface-sunken py-12 text-center"
          >
            <p class="text-sm font-medium text-ink-500">
              {{ t('lensAdmin.credentials.noResults') }}
            </p>
          </div>

          <div
            v-else
            class="credentials-list relative overflow-x-auto rounded-lg border border-line bg-surface"
            data-testid="credentials-list"
          >
            <table
              class="credentials-table w-full min-w-[66rem] table-fixed divide-y divide-line"
            >
              <colgroup>
                <col class="w-44" />
                <col class="w-16" />
                <col class="w-24" />
                <col class="w-48" />
                <col class="w-36" />
                <col class="w-24" />
                <col class="w-28" />
                <col class="w-44" />
              </colgroup>
              <thead class="bg-surface-sunken">
                <tr>
                  <th
                    v-for="column in activeColumns"
                    :key="column"
                    class="table-head"
                  >
                    {{ column }}
                  </th>
                </tr>
              </thead>
              <tbody class="divide-y divide-line bg-surface">
                <tr
                  v-for="row in pagedCredentials"
                  :key="row.uuid"
                  class="transition-colors hover:bg-line-soft"
                >
                  <td class="table-cell" :data-label="activeColumns[0]">
                    <div class="font-medium text-ink-900">
                      {{ row.name }}
                    </div>
                    <div class="mt-1 text-xs text-ink-500">
                      {{
                        row.has_secret
                          ? t('lensAdmin.credentials.secretConfigured')
                          : t('lensAdmin.credentials.secretMissing')
                      }}
                    </div>
                  </td>
                  <td
                    class="table-cell text-ink-600"
                    :data-label="activeColumns[1]"
                  >
                    {{ credentialProviderLabel(row.provider) }}
                  </td>
                  <td
                    class="table-cell text-ink-600"
                    :data-label="activeColumns[2]"
                  >
                    {{ credentialAuthTypeLabel(row.auth_type) }}
                  </td>
                  <td
                    class="table-cell text-ink-600"
                    :data-label="activeColumns[3]"
                  >
                    <div class="flex min-w-0 items-center gap-2">
                      <span
                        class="min-w-0 flex-1 truncate"
                        :data-testid="`credential-url-${row.uuid}`"
                        :title="credentialUrl(row)"
                      >
                        {{ credentialUrlLabel(row) || emptyValue }}
                      </span>
                      <button
                        v-if="credentialUrl(row)"
                        type="button"
                        class="shrink-0 rounded p-1 text-ink-400 hover:bg-surface-sunken hover:text-ink-700 focus:outline-none focus:ring-2 focus:ring-brand-500/30"
                        :aria-label="
                          t('lensAdmin.credentials.copyUrl', {
                            name: row.name
                          })
                        "
                        @click="copyCredentialUrl(row)"
                      >
                        <CopyIcon class="h-4 w-4" />
                      </button>
                    </div>
                  </td>
                  <td class="table-cell" :data-label="activeColumns[4]">
                    <div class="max-w-xs">
                      <span
                        class="rounded-md border px-2 py-1 text-xs font-medium"
                        :class="credentialValidationClass(row)"
                        :title="row.validation_message || ''"
                      >
                        {{ credentialValidationLabel(row) }}
                      </span>
                      <div
                        v-if="
                          row.validation_status === 'failed' &&
                          row.validation_message
                        "
                        class="mt-1 max-w-xs truncate text-xs text-danger-700"
                        :title="row.validation_message"
                      >
                        {{ row.validation_message }}
                      </div>
                      <div
                        v-if="row.validated_at"
                        class="mt-1 text-xs text-ink-500"
                        :title="formatFullDateTime(row.validated_at)"
                      >
                        {{
                          t('lensAdmin.credentials.validatedAt', {
                            time: formatDateTime(row.validated_at)
                          })
                        }}
                      </div>
                    </div>
                  </td>
                  <td
                    class="table-cell text-ink-600"
                    :data-label="activeColumns[5]"
                  >
                    <div class="inline-flex">
                      <button
                        v-if="row.datasource_count"
                        type="button"
                        class="rounded px-1 underline decoration-dotted underline-offset-4 focus:outline-none focus:ring-2 focus:ring-brand-500/30"
                        aria-controls="credential-bindings-tooltip"
                        :aria-expanded="
                          credentialBindingTooltip.row?.uuid === row.uuid
                        "
                        :aria-label="
                          t('lensAdmin.credentials.bindingCount', {
                            count: row.datasource_count
                          })
                        "
                        @click="showCredentialBindings($event, row)"
                        @mouseenter="showCredentialBindings($event, row)"
                        @mouseleave="hideCredentialBindingsUnlessFocused"
                        @focus="showCredentialBindings($event, row)"
                        @blur="hideCredentialBindings"
                      >
                        {{ row.datasource_count }}
                      </button>
                      <span v-else>0</span>
                    </div>
                  </td>
                  <td
                    class="table-cell text-ink-600"
                    :data-label="activeColumns[6]"
                  >
                    <span
                      data-testid="credential-last-used"
                      :title="formatFullDateTime(row.last_used_at)"
                    >
                      {{ formatDateTime(row.last_used_at) }}
                    </span>
                  </td>
                  <td class="table-cell !px-2" :data-label="activeColumns[7]">
                    <div
                      class="flex flex-nowrap items-center gap-1 whitespace-nowrap"
                    >
                      <BaseButton
                        size="sm"
                        variant="outline"
                        class="!px-2"
                        :loading="validatingCredentialUuid === row.uuid"
                        @click="validateRow(row)"
                      >
                        {{ t('lensAdmin.credentials.validate') }}
                      </BaseButton>
                      <RowActions
                        :row="row"
                        :confirm-inline="false"
                        class="flex-nowrap !gap-1 [&_button]:!px-2"
                        @edit="startEdit"
                        @delete="requestDelete"
                      />
                    </div>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
          <PaginationBar
            v-if="!loading"
            v-model:page-size="pageSize"
            :current-page="currentPage"
            :total="filteredCredentials.length"
            @page-size-change="handlePageSizeChange"
            @prev="goPrevPage"
            @next="goNextPage"
          />
        </div>
      </section>

      <Teleport to="body">
        <div
          v-if="credentialBindingTooltip.row"
          id="credential-bindings-tooltip"
          class="pointer-events-none fixed z-[9999] w-64 rounded-md border border-line bg-surface p-3 text-xs shadow-lg"
          role="tooltip"
          :style="credentialBindingTooltipStyle"
        >
          <div class="mb-2 font-medium text-ink-900">
            {{ t('lensAdmin.credentials.boundDatasources') }}
          </div>
          <div class="max-h-64 space-y-2 overflow-y-auto">
            <div
              v-for="datasource in credentialBindings(
                credentialBindingTooltip.row
              )"
              :key="datasource.uuid"
              class="rounded border border-line bg-surface-sunken px-2 py-1.5"
            >
              <div class="font-medium text-ink-900">
                {{ datasource.name }}
              </div>
              <div class="mt-0.5 text-ink-500">
                {{ formatSourceType(datasource.source_type) }}
                · {{ datasource.status }}
              </div>
            </div>
          </div>
        </div>
      </Teleport>

      <BaseDrawer
        :show="showModal"
        :title="modalTitle"
        :subtitle="form.name || ''"
        @close="closeModal"
      >
        <form
          id="credential-form"
          ref="credentialFormRef"
          class="space-y-4"
          novalidate
          :aria-describedby="formError ? 'credential-form-error' : undefined"
          @input="formError = ''"
          @submit.prevent="save"
        >
          <FormRow
            :label="t('lensAdmin.fields.name')"
            for-id="credential-name"
            required
          >
            <input
              id="credential-name"
              v-model="form.name"
              name="name"
              class="form-input"
              required
            />
          </FormRow>
          <FormRow
            :label="t('lensAdmin.fields.type')"
            for-id="credential-provider"
            required
          >
            <BaseSelect
              id="credential-provider"
              v-model="form.provider"
              name="provider"
              aria-describedby="credential-provider-hint"
              required
            >
              <option value="github">GitHub</option>
              <option value="gitlab">GitLab</option>
              <option value="feishu">Feishu</option>
            </BaseSelect>
            <p id="credential-provider-hint" class="mt-1 text-xs text-ink-500">
              {{ t('lensAdmin.credentials.providerHint') }}
            </p>
          </FormRow>
          <template v-if="form.provider !== 'feishu'">
            <FormRow
              :label="t('lensAdmin.fields.url')"
              for-id="credential-url"
              required
            >
              <input
                id="credential-url"
                v-model="form.organization_url"
                name="organization_url"
                class="form-input"
                aria-describedby="credential-url-hint"
                placeholder="https://gitlab.example.com/group or https://github.com/org/repo"
                required
              />
              <p id="credential-url-hint" class="mt-1 text-xs text-ink-500">
                {{ t('lensAdmin.credentials.gitScopeHint') }}
              </p>
            </FormRow>
          </template>
          <template v-else>
            <FormRow
              :label="t('lensAdmin.fields.url')"
              for-id="credential-url"
              required
            >
              <input
                id="credential-url"
                v-model="form.folder_url"
                name="folder_url"
                class="form-input"
                aria-describedby="credential-url-hint"
                placeholder="https://xxx.feishu.cn/drive/folder/..."
                required
              />
              <p id="credential-url-hint" class="mt-1 text-xs text-ink-500">
                {{ t('lensAdmin.datasourceWizard.feishuFolderHint') }}
              </p>
            </FormRow>
            <FormRow :label="t('lensAdmin.fields.syncScope')">
              <div class="form-input bg-surface-sunken text-ink-500">
                {{ t('lensAdmin.datasourceWizard.feishuScopeDriveFolder') }}
              </div>
              <p class="mt-1 text-xs text-ink-500">
                {{ t('lensAdmin.credentials.syncScopeHint') }}
              </p>
            </FormRow>
          </template>
          <FormRow
            :label="t('lensAdmin.fields.authScheme')"
            :for-id="form.provider === 'feishu' ? '' : 'credential-auth-scheme'"
          >
            <BaseSelect
              v-if="form.provider !== 'feishu'"
              id="credential-auth-scheme"
              v-model="form.auth_type"
              name="auth_type"
              aria-describedby="credential-auth-hint"
              required
            >
              <option value="https_token">
                {{ credentialAuthTypeLabel('https_token') }}
              </option>
              <option value="none">
                {{ credentialAuthTypeLabel('none') }}
              </option>
            </BaseSelect>
            <div v-else class="form-input bg-surface-sunken text-ink-500">
              {{ credentialAuthTypeLabel(credentialFormAuthType) }}
            </div>
            <p id="credential-auth-hint" class="mt-1 text-xs text-ink-500">
              {{ credentialAuthTypeHint }}
            </p>
          </FormRow>
          <template v-if="credentialFormAuthType === 'feishu_app'">
            <FormRow
              :label="t('lensAdmin.fields.feishuAppId')"
              for-id="credential-app-id"
              :required="mode === 'create'"
            >
              <input
                id="credential-app-id"
                v-model="form.app_id"
                name="app_id"
                class="form-input"
                autocomplete="off"
                aria-describedby="credential-app-id-hint"
                :placeholder="t('lensAdmin.credentials.appIdPlaceholder')"
                :required="mode === 'create'"
              />
              <p id="credential-app-id-hint" class="mt-1 text-xs text-ink-500">
                {{ t('lensAdmin.credentials.appIdHint') }}
              </p>
            </FormRow>
            <FormRow
              :label="t('lensAdmin.fields.feishuAppSecret')"
              for-id="credential-app-secret"
              :required="mode === 'create'"
            >
              <div class="flex gap-2">
                <input
                  id="credential-app-secret"
                  v-model="form.app_secret"
                  name="app_secret"
                  class="form-input"
                  :type="credentialSecretRevealed ? 'text' : 'password'"
                  autocomplete="off"
                  aria-describedby="credential-app-secret-hint"
                  :placeholder="t('lensAdmin.credentials.appSecretPlaceholder')"
                  :required="mode === 'create'"
                />
                <button
                  v-if="canRevealCredential"
                  class="inline-flex h-10 w-10 shrink-0 items-center justify-center rounded-md border border-line text-ink-500 hover:bg-surface-sunken hover:text-ink-900"
                  type="button"
                  :title="revealButtonTitle"
                  :aria-label="revealButtonTitle"
                  @click="toggleCredentialReveal"
                >
                  <component
                    :is="credentialSecretRevealed ? EyeOffIcon : EyeIcon"
                    class="h-4 w-4"
                  />
                </button>
              </div>
              <p
                id="credential-app-secret-hint"
                class="mt-1 text-xs text-ink-500"
              >
                {{ t('lensAdmin.credentials.appSecretHint') }}
              </p>
            </FormRow>
          </template>
          <FormRow
            v-else-if="credentialFormAuthType === 'https_token'"
            :label="t('lensAdmin.fields.accessToken')"
            for-id="credential-access-token"
            :required="mode === 'create'"
          >
            <div class="flex gap-2">
              <input
                id="credential-access-token"
                v-model="form.secret"
                name="secret"
                class="form-input"
                :type="credentialSecretRevealed ? 'text' : 'password'"
                autocomplete="off"
                aria-describedby="credential-access-token-hint"
                :placeholder="t('lensAdmin.credentials.tokenPlaceholder')"
                :required="mode === 'create'"
              />
              <button
                v-if="canRevealCredential"
                class="inline-flex h-10 w-10 shrink-0 items-center justify-center rounded-md border border-line text-ink-500 hover:bg-surface-sunken hover:text-ink-900"
                type="button"
                :title="revealButtonTitle"
                :aria-label="revealButtonTitle"
                @click="toggleCredentialReveal"
              >
                <component
                  :is="credentialSecretRevealed ? EyeOffIcon : EyeIcon"
                  class="h-4 w-4"
                />
              </button>
            </div>
            <p
              id="credential-access-token-hint"
              class="mt-1 text-xs text-ink-500"
            >
              {{ t('lensAdmin.credentials.accessTokenHint') }}
            </p>
          </FormRow>
          <p
            v-if="mode === 'edit' && credentialFormAuthType !== 'none'"
            class="-mt-2 text-xs text-ink-500"
          >
            {{ t('lensAdmin.credentials.replaceHint') }}
          </p>

          <p
            v-if="formError"
            id="credential-form-error"
            class="text-sm text-danger-700"
            role="alert"
          >
            {{ formError }}
          </p>
        </form>
        <template #footer>
          <div class="flex flex-row-reverse gap-2">
            <BaseButton
              :loading="saving"
              variant="primary"
              type="submit"
              form="credential-form"
            >
              {{ t('common.save') }}
            </BaseButton>
            <BaseButton variant="outline" @click="closeModal">
              {{ t('common.cancel') }}
            </BaseButton>
          </div>
        </template>
      </BaseDrawer>

      <BaseModal
        :show="!!deleteTarget"
        :title="t('lensAdmin.credentials.deleteTitle')"
        @close="deleteTarget = null"
      >
        <p class="text-sm text-ink-700">
          {{
            t('lensAdmin.credentials.deleteMessage', {
              name: deleteTarget?.name
            })
          }}
        </p>
        <div
          v-if="deleteTarget?.datasource_count"
          class="mt-4 rounded-lg border border-warning-200 bg-warning-50 p-3"
          role="alert"
        >
          <p class="text-sm font-medium text-warning-800">
            {{
              t('lensAdmin.credentials.deleteBlocked', {
                count: deleteTarget.datasource_count
              })
            }}
          </p>
          <ul class="mt-2 list-disc space-y-1 pl-5 text-sm text-warning-800">
            <li
              v-for="datasource in credentialBindings(deleteTarget)"
              :key="datasource.uuid"
            >
              {{ datasource.name }}
            </li>
          </ul>
        </div>
        <template #footer>
          <div class="flex flex-row-reverse gap-2">
            <BaseButton
              variant="danger"
              :loading="deleting"
              :disabled="!!deleteTarget?.datasource_count"
              @click="confirmDelete"
            >
              {{ t('lensAdmin.credentials.deleteAction') }}
            </BaseButton>
            <BaseButton variant="outline" @click="deleteTarget = null">
              {{ t('common.cancel') }}
            </BaseButton>
          </div>
        </template>
      </BaseModal>
    </div>
  </AdminLayout>
</template>

<script setup>
import {
  Copy as CopyIcon,
  Eye as EyeIcon,
  EyeOff as EyeOffIcon
} from '@lucide/vue'
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'

import { extractErrorMessage } from '@/utils/api'
import AdminLayout from '@/admin/layout/AdminLayout.vue'
import {
  createCredential,
  deleteCredential,
  listCredentials,
  revealCredential,
  updateCredential,
  validateCredential
} from '@/api/lens'
import { useToast } from '@/composables/useToast'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseDrawer from '@/components/ui/BaseDrawer.vue'
import BaseLoading from '@/components/ui/BaseLoading.vue'
import BaseModal from '@/components/ui/BaseModal.vue'
import BaseSelect from '@/components/ui/BaseSelect.vue'
import PaginationBar from '@/components/ui/PaginationBar.vue'
import { copyToClipboard } from '@/utils/clipboard'

import FormRow from './components/FormRow.vue'
import RowActions from './components/RowActions.vue'
import { EMPTY_VALUE as emptyValue, normalizeList } from './adminHelpers'
import {
  credentialUrl,
  credentialUrlLabel,
  filterAndSortCredentials,
  formatCredentialDateTime
} from './credentialHelpers'
import { useShortDateTime } from './useShortDateTime'

const { locale, t } = useI18n()
const { showSuccess, showError } = useToast()

const CREDENTIAL_MASK = '********'

const loading = ref(false)
const saving = ref(false)
const deleting = ref(false)
const mode = ref('create')
const form = ref({})
const credentialFormRef = ref(null)
const formError = ref('')
const showModal = ref(false)
const deleteTarget = ref(null)

const credentials = ref([])
const currentPage = ref(1)
const pageSize = ref(20)
const searchQuery = ref('')
const providerFilter = ref('all')
const validationFilter = ref('all')
const sortOption = ref('default')
const credentialSecretRevealed = ref(false)
const revealingCredential = ref(false)
const validatingCredentialUuid = ref('')
const credentialBindingTooltip = ref({
  row: null,
  left: 0,
  top: 0
})

const formatDateTime = useShortDateTime()
const userTimeZone = Intl.DateTimeFormat().resolvedOptions().timeZone

const activeColumns = computed(() =>
  [
    'credential',
    'type',
    'authScheme',
    'url',
    'validation',
    'datasources',
    'lastUsedAt',
    'actions'
  ].map((column) => t(`lensAdmin.columns.${column}`))
)

const filteredCredentials = computed(() =>
  filterAndSortCredentials(credentials.value, {
    query: searchQuery.value,
    provider: providerFilter.value,
    validationStatus: validationFilter.value,
    sort: sortOption.value
  })
)
const totalPages = computed(() =>
  Math.max(1, Math.ceil(filteredCredentials.value.length / pageSize.value))
)
const pagedCredentials = computed(() => {
  const start = (currentPage.value - 1) * pageSize.value
  return filteredCredentials.value.slice(start, start + pageSize.value)
})

function handlePageSizeChange() {
  currentPage.value = 1
}

function goPrevPage() {
  if (currentPage.value <= 1) return
  currentPage.value -= 1
}

function goNextPage() {
  if (currentPage.value >= totalPages.value) return
  currentPage.value += 1
}

const modalTitle = computed(() => {
  return mode.value === 'create'
    ? t('lensAdmin.credentials.createTitle')
    : t('lensAdmin.credentials.editTitle')
})

const credentialFormAuthType = computed(() =>
  form.value.provider === 'feishu'
    ? 'feishu_app'
    : form.value.auth_type || 'https_token'
)

const canRevealCredential = computed(
  () => mode.value === 'edit' && !!form.value.uuid
)

const revealButtonTitle = computed(() =>
  credentialSecretRevealed.value
    ? t('lensAdmin.credentials.hideSecret')
    : t('lensAdmin.credentials.revealSecret')
)

const credentialAuthTypeHint = computed(() =>
  credentialFormAuthType.value === 'feishu_app'
    ? t('lensAdmin.credentials.feishuAuthHint')
    : credentialFormAuthType.value === 'none'
      ? t('lensAdmin.credentials.gitNoAuthHint')
      : t('lensAdmin.credentials.gitAuthHint')
)

const credentialBindingTooltipStyle = computed(() => ({
  left: `${credentialBindingTooltip.value.left}px`,
  top: `${credentialBindingTooltip.value.top}px`
}))

function credentialProviderLabel(provider) {
  if (provider === 'github') {
    return 'GitHub'
  }
  if (provider === 'gitlab') {
    return 'GitLab'
  }
  if (provider === 'feishu') {
    return 'Feishu'
  }
  if (provider) {
    return 'Git'
  }
  return emptyValue
}

function credentialAuthTypeLabel(authType) {
  const labels = {
    none: t('lensAdmin.credentials.noAuth'),
    https_token: 'HTTPS Token',
    feishu_app: 'Feishu App'
  }
  return labels[authType] || authType || emptyValue
}

function credentialValidationLabel(row) {
  if (validatingCredentialUuid.value === row?.uuid) {
    return t('lensAdmin.credentials.validationRunning')
  }
  if (row?.validation_status === 'success') {
    return t('lensAdmin.credentials.validationSuccess')
  }
  if (row?.validation_status === 'failed') {
    return t('lensAdmin.credentials.validationFailed')
  }
  return t('lensAdmin.credentials.validationUnchecked')
}

function credentialValidationClass(row) {
  if (row?.validation_status === 'success') {
    return 'border-success-200 bg-success-50 text-success-700'
  }
  if (row?.validation_status === 'failed') {
    return 'border-danger-200 bg-danger-50 text-danger-700'
  }
  return 'border-line bg-surface-sunken text-ink-500'
}

function defaultEndpoint(provider) {
  if (provider === 'gitlab') {
    return 'https://gitlab.com'
  }
  if (provider === 'feishu') {
    return 'https://open.feishu.cn'
  }
  return 'https://github.com'
}

function endpointFromCredentialUrl(provider, url) {
  if (provider === 'feishu') {
    return 'https://open.feishu.cn'
  }
  const parsed = parseUrl(url)
  if (parsed?.origin) {
    return parsed.origin
  }
  return defaultEndpoint(provider)
}

function parseUrl(value) {
  try {
    return new URL(String(value || '').trim())
  } catch {
    return null
  }
}

function defaultSyncScope(provider) {
  return provider === 'feishu' ? 'feishu_folder' : 'service'
}

function formatSourceType(sourceType) {
  if (sourceType === 'git') {
    return 'Git'
  }
  if (sourceType === 'feishu') {
    return t('lensAdmin.datasourceWizard.feishu')
  }
  return sourceType || emptyValue
}

function credentialBindings(row) {
  return Array.isArray(row?.datasource_bindings) ? row.datasource_bindings : []
}

function showCredentialBindings(event, row) {
  if (!credentialBindings(row).length) {
    return
  }
  const rect = event.currentTarget.getBoundingClientRect()
  const tooltipWidth = 256
  const tooltipGap = 8
  const estimatedHeight = Math.min(
    280,
    46 + credentialBindings(row).length * 48
  )
  const left = Math.min(
    Math.max(8, rect.left),
    window.innerWidth - tooltipWidth - 8
  )
  const top =
    rect.top > estimatedHeight + tooltipGap
      ? rect.top - estimatedHeight - tooltipGap
      : rect.bottom + tooltipGap
  credentialBindingTooltip.value = {
    row,
    left,
    top
  }
}

function hideCredentialBindings() {
  credentialBindingTooltip.value = {
    row: null,
    left: 0,
    top: 0
  }
}

function hideCredentialBindingsUnlessFocused(event) {
  if (document.activeElement === event.currentTarget) {
    return
  }
  hideCredentialBindings()
}

function formatFullDateTime(value) {
  if (!value) return ''
  return formatCredentialDateTime(value, locale.value, userTimeZone)
}

async function copyCredentialUrl(row) {
  if (await copyToClipboard(credentialUrl(row))) {
    showSuccess(t('common.copied'))
    return
  }
  showError(t('lensAdmin.credentials.copyUrlFailed'))
}

async function toggleCredentialReveal() {
  if (!form.value.uuid || revealingCredential.value) {
    return
  }
  if (credentialSecretRevealed.value) {
    credentialSecretRevealed.value = false
    form.value.secret = form.value.secret ? CREDENTIAL_MASK : ''
    form.value.app_secret = form.value.app_secret ? CREDENTIAL_MASK : ''
    return
  }
  revealingCredential.value = true
  try {
    const payload = await revealCredential(form.value.uuid)
    if (credentialFormAuthType.value === 'feishu_app') {
      form.value.app_id = payload?.app_id || ''
      form.value.app_secret = payload?.app_secret || ''
    } else {
      form.value.secret = payload?.secret || ''
    }
    credentialSecretRevealed.value = true
  } catch (error) {
    showError(extractErrorMessage(error, t('lensAdmin.messages.loadFailed')))
  } finally {
    revealingCredential.value = false
  }
}

async function load() {
  loading.value = true
  formError.value = ''
  try {
    const credentialRows = await listCredentials()
    credentials.value = normalizeList(credentialRows)
  } catch (error) {
    showError(extractErrorMessage(error, t('lensAdmin.messages.loadFailed')))
  } finally {
    loading.value = false
  }
}

function startCreate() {
  mode.value = 'create'
  formError.value = ''
  credentialSecretRevealed.value = false
  revealingCredential.value = false
  form.value = defaultForm()
  showModal.value = true
}

function startEdit(row) {
  mode.value = 'edit'
  formError.value = ''
  credentialSecretRevealed.value = false
  revealingCredential.value = false
  form.value = formFromRow(row)
  showModal.value = true
}

function closeModal() {
  showModal.value = false
  form.value = {}
  formError.value = ''
  credentialSecretRevealed.value = false
  revealingCredential.value = false
}

function defaultForm() {
  return {
    name: '',
    provider: 'github',
    auth_type: 'https_token',
    sync_scope: 'service',
    organization_url: '',
    folder_url: '',
    folder_token: '',
    secret: '',
    app_id: '',
    app_secret: ''
  }
}

function formFromRow(row) {
  return {
    uuid: row.uuid,
    name: row.name || '',
    provider:
      row.auth_type === 'feishu_app'
        ? 'feishu'
        : row.provider === 'gitlab'
          ? 'gitlab'
          : 'github',
    auth_type: row.auth_type || 'https_token',
    sync_scope: row.sync_scope || defaultSyncScope(row.provider),
    organization_url: row.scope_summary?.organization_url || '',
    folder_url: row.scope_summary?.folder_url || '',
    folder_token: row.scope_summary?.folder_token || '',
    secret: row.masked_secret || '',
    app_id: row.masked_app_id || '',
    app_secret: row.masked_secret || ''
  }
}

async function save() {
  formError.value = ''
  const firstInvalidField = credentialFormRef.value?.querySelector(':invalid')
  if (firstInvalidField) {
    formError.value = t('lensAdmin.credentials.formInvalid')
    await nextTick()
    firstInvalidField.focus()
    return
  }

  saving.value = true
  try {
    const payload = buildPayload()
    const uuid = form.value.uuid
    let saved = null
    if (mode.value === 'create') {
      saved = await createCredential(payload)
    } else {
      saved = await updateCredential(uuid, payload)
    }
    if (saved?.uuid) {
      try {
        await validateCredential(saved.uuid)
      } catch (validationError) {
        showError(credentialValidationErrorMessage(validationError))
      }
    }
    showSuccess(t('lensAdmin.messages.saveSuccess'))
    closeModal()
    await load()
  } catch (error) {
    formError.value = extractErrorMessage(
      error,
      t('lensAdmin.messages.saveFailed')
    )
    showError(formError.value)
  } finally {
    saving.value = false
  }
}

async function validateRow(row) {
  if (!row?.uuid || validatingCredentialUuid.value) {
    return
  }
  validatingCredentialUuid.value = row.uuid
  try {
    const result = await validateCredential(row.uuid)
    showSuccess(result?.message || t('lensAdmin.credentials.validationSuccess'))
    await load()
  } catch (error) {
    showError(credentialValidationErrorMessage(error))
    await load()
  } finally {
    validatingCredentialUuid.value = ''
  }
}

function credentialValidationErrorMessage(error) {
  const message =
    error?.response?.data?.data?.message ||
    error?.response?.data?.message ||
    extractErrorMessage(error, t('lensAdmin.messages.saveFailed'))
  const parsed = parseEmbeddedJson(message)
  if (parsed?.error === 'insufficient_scope') {
    return t('lensAdmin.credentials.insufficientScope', {
      scope: parsed.scope || '-'
    })
  }
  return message
}

function parseEmbeddedJson(value) {
  const text = String(value || '')
  const start = text.indexOf('{')
  const end = text.lastIndexOf('}')
  if (start < 0 || end <= start) {
    return null
  }
  try {
    return JSON.parse(text.slice(start, end + 1))
  } catch {
    return null
  }
}

function buildPayload() {
  const payload = {
    name: form.value.name,
    provider: form.value.provider,
    auth_type: credentialFormAuthType.value,
    sync_scope: form.value.sync_scope || defaultSyncScope(form.value.provider)
  }
  if (credentialFormAuthType.value === 'feishu_app') {
    if (!isFeishuDriveFolderUrl(form.value.folder_url)) {
      throw new Error(t('lensAdmin.credentials.feishuFolderUrlInvalid'))
    }
    payload.folder_url = form.value.folder_url?.trim()
    payload.folder_token = form.value.folder_token?.trim()
    payload.endpoint_url = endpointFromCredentialUrl(
      form.value.provider,
      form.value.folder_url
    )
    if (form.value.app_id?.trim() && form.value.app_id !== CREDENTIAL_MASK) {
      payload.app_id = form.value.app_id.trim()
    }
    if (
      form.value.app_secret?.trim() &&
      form.value.app_secret !== CREDENTIAL_MASK
    ) {
      payload.app_secret = form.value.app_secret.trim()
    }
  } else if (
    form.value.secret?.trim() &&
    form.value.secret !== CREDENTIAL_MASK
  ) {
    payload.secret = form.value.secret.trim()
  }
  if (['https_token', 'none'].includes(credentialFormAuthType.value)) {
    payload.organization_url = form.value.organization_url?.trim()
    payload.endpoint_url = endpointFromCredentialUrl(
      form.value.provider,
      form.value.organization_url
    )
  }
  return payload
}

function isFeishuDriveFolderUrl(value) {
  try {
    const url = new URL(String(value || '').trim())
    const parts = url.pathname.split('/').filter(Boolean)
    return (
      ['http:', 'https:'].includes(url.protocol) &&
      url.hostname.endsWith('.feishu.cn') &&
      parts.length >= 3 &&
      parts[0] === 'drive' &&
      parts[1] === 'folder'
    )
  } catch {
    return false
  }
}

watch(
  () => form.value.provider,
  (provider, previous) => {
    if (!provider || provider === previous) {
      return
    }
    form.value.sync_scope = defaultSyncScope(provider)
    if (provider === 'feishu') {
      form.value.auth_type = 'feishu_app'
      return
    }
    if (!['https_token', 'none'].includes(form.value.auth_type)) {
      form.value.auth_type = 'https_token'
    }
  }
)

watch([searchQuery, providerFilter, validationFilter, sortOption], () => {
  currentPage.value = 1
})

watch(
  () => form.value.auth_type,
  (authType) => {
    if (authType === 'none') {
      form.value.secret = ''
      credentialSecretRevealed.value = false
    }
  }
)

function requestDelete(row) {
  deleteTarget.value = row
}

async function confirmDelete() {
  const row = deleteTarget.value
  if (!row?.uuid || row.datasource_count || deleting.value) {
    return
  }
  deleting.value = true
  try {
    await deleteCredential(row.uuid)
    showSuccess(t('lensAdmin.messages.deleteSuccess'))
    deleteTarget.value = null
    await load()
  } catch (error) {
    showError(extractErrorMessage(error, t('lensAdmin.messages.deleteFailed')))
  } finally {
    deleting.value = false
  }
}

onMounted(load)
</script>

<style scoped>
.form-input {
  @apply w-full rounded-lg border border-line bg-surface px-3 py-2 text-sm text-ink-900 focus:border-brand-500 focus:outline-none focus:ring-2 focus:ring-brand-500/20;
}

.table-head {
  @apply border-b border-line px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-ink-500;
}

.table-cell {
  @apply px-4 py-3 align-top text-sm text-ink-700;
}

@media (max-width: 767px) {
  .credentials-list {
    @apply overflow-x-hidden border-0 bg-transparent;
  }

  .credentials-table,
  .credentials-table tbody {
    display: block;
    width: 100%;
  }

  .credentials-table {
    min-width: 100% !important;
    table-layout: auto;
  }

  .credentials-table colgroup {
    display: none;
  }

  .credentials-table thead {
    position: absolute;
    width: 1px;
    height: 1px;
    padding: 0;
    margin: -1px;
    overflow: hidden;
    clip: rect(0, 0, 0, 0);
    white-space: nowrap;
    border: 0;
  }

  .credentials-table tbody {
    @apply space-y-3;
  }

  .credentials-table tbody tr {
    @apply block overflow-hidden rounded-lg border border-line bg-surface;
  }

  .credentials-table .table-cell {
    display: grid;
    grid-template-columns: minmax(6rem, 36%) minmax(0, 1fr);
    @apply gap-3 border-b border-line px-3 py-3;
  }

  .credentials-table .table-cell::before {
    content: attr(data-label);
    @apply text-xs font-semibold uppercase tracking-wide text-ink-500;
  }

  .credentials-table .table-cell:last-child {
    @apply border-b-0;
  }
}
</style>
