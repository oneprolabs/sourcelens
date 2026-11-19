<template>
  <AdminLayout>
    <div class="admin-list-page admin-tenant-page">
      <section class="admin-list-surface">
        <div class="admin-page-heading">
          <div class="admin-heading-copy">
            <h1>
              {{ t('management.tenantInfo') }}
            </h1>
            <p>
              {{ t('management.tenantInfoSubtitle') }}
            </p>
          </div>
        </div>

        <BaseLoading v-if="loading" />

        <div v-else class="min-w-0">
          <div
            v-if="error"
            class="mb-4 rounded-md border border-danger-200 bg-danger-50 px-4 py-3 text-sm text-danger-700"
          >
            {{ error }}
          </div>

          <form class="admin-tenant-form" @submit.prevent="save">
            <section class="admin-settings-panel">
              <div class="admin-settings-panel-heading">
                <h2 class="text-sm font-semibold text-ink-900">
                  {{ t('management.tenantBasicInfo') }}
                </h2>
                <p class="mt-1 text-xs text-ink-500">
                  {{ t('management.tenantBasicInfoHint') }}
                </p>
              </div>
              <div class="admin-settings-rows">
                <div class="admin-setting-row">
                  <div>
                    <label
                      class="text-sm font-medium text-ink-800"
                      for="tenant-name"
                    >
                      {{ t('management.tenantName') }}
                      <span class="text-danger-600">*</span>
                    </label>
                    <p class="mt-1 text-xs leading-5 text-ink-500">
                      {{ t('management.tenantNameHint') }}
                    </p>
                  </div>
                  <div>
                    <input
                      id="tenant-name"
                      v-model="form.name"
                      type="text"
                      maxlength="200"
                      class="form-input"
                      :class="nameError ? 'border-danger-400' : ''"
                    />
                    <p v-if="nameError" class="mt-1 text-xs text-danger-700">
                      {{ nameError }}
                    </p>
                  </div>
                </div>

                <div class="admin-setting-row">
                  <div>
                    <label
                      class="text-sm font-medium text-ink-800"
                      for="tenant-description"
                    >
                      {{ t('management.tenantDescription') }}
                    </label>
                    <p class="mt-1 text-xs leading-5 text-ink-500">
                      {{ t('management.tenantDescriptionHint') }}
                    </p>
                  </div>
                  <textarea
                    id="tenant-description"
                    v-model="form.description"
                    rows="1"
                    maxlength="500"
                    class="form-input"
                  />
                </div>
                <div class="admin-setting-row">
                  <div>
                    <p class="text-sm font-medium text-ink-800">
                      {{ t('management.tenantLogo') }}
                    </p>
                    <p class="mt-1 text-xs leading-5 text-ink-500">
                      {{ t('management.tenantLogoHint') }}
                    </p>
                  </div>
                  <div
                    class="admin-tenant-logo-control flex items-center gap-3"
                  >
                    <span
                      class="flex h-12 w-12 items-center justify-center overflow-hidden rounded-lg border border-line bg-surface-sunken text-base font-semibold text-ink-500"
                    >
                      <img
                        v-if="tenant?.logo_url"
                        :src="tenant.logo_url"
                        :alt="tenant.name"
                        class="h-full w-full object-cover"
                      />
                      <span v-else>{{ tenantInitial }}</span>
                    </span>
                    <input
                      ref="logoInput"
                      type="file"
                      accept="image/png,image/jpeg,image/webp,image/svg+xml"
                      class="hidden"
                      @change="onLogoChange"
                    />
                    <BaseButton
                      type="button"
                      variant="outline"
                      size="sm"
                      :loading="logoUploading"
                      @click="logoInput?.click()"
                    >
                      {{ t('management.uploadLogo') }}
                    </BaseButton>
                  </div>
                </div>
              </div>
            </section>

            <section class="admin-settings-panel">
              <div class="admin-settings-panel-heading">
                <h2 class="text-sm font-semibold text-ink-900">
                  {{ t('management.tenantRegional') }}
                </h2>
                <p class="mt-1 text-xs text-ink-500">
                  {{ t('management.tenantRegionalHint') }}
                </p>
              </div>
              <div class="admin-settings-rows">
                <div class="admin-setting-row">
                  <div>
                    <label
                      class="text-sm font-medium text-ink-800"
                      for="tenant-language"
                    >
                      {{ t('management.defaultLanguage') }}
                    </label>
                    <p class="mt-1 text-xs leading-5 text-ink-500">
                      {{ t('management.defaultLanguageHint') }}
                    </p>
                  </div>
                  <BaseSelect
                    id="tenant-language"
                    v-model="form.default_language"
                    class="form-input"
                  >
                    <option value="zh-CN">简体中文</option>
                    <option value="en-US">English</option>
                  </BaseSelect>
                </div>

                <div class="admin-setting-row">
                  <div>
                    <label
                      class="text-sm font-medium text-ink-800"
                      for="tenant-timezone"
                    >
                      {{ t('management.tenantTimezone') }}
                    </label>
                    <p class="mt-1 text-xs leading-5 text-ink-500">
                      {{ t('management.tenantTimezoneHint') }}
                    </p>
                  </div>
                  <BaseSelect
                    id="tenant-timezone"
                    v-model="form.timezone"
                    class="form-input"
                  >
                    <option value="Asia/Shanghai">
                      Asia/Shanghai（UTC+8）
                    </option>
                    <option value="UTC">UTC</option>
                  </BaseSelect>
                </div>

                <div class="admin-setting-row">
                  <div>
                    <label
                      class="text-sm font-medium text-ink-800"
                      for="tenant-contact-email"
                    >
                      {{ t('management.contactEmail') }}
                    </label>
                    <p class="mt-1 text-xs leading-5 text-ink-500">
                      {{ t('management.contactEmailHint') }}
                    </p>
                  </div>
                  <input
                    id="tenant-contact-email"
                    v-model="form.contact_email"
                    type="email"
                    maxlength="254"
                    class="form-input"
                  />
                </div>
              </div>
            </section>

            <section class="admin-settings-panel">
              <div class="admin-settings-panel-heading">
                <h2 class="text-sm font-semibold text-ink-900">
                  {{ t('management.tenantStatusSection') }}
                </h2>
                <p class="mt-1 text-xs text-ink-500">
                  {{ t('management.tenantStatusHint') }}
                </p>
              </div>
              <div class="px-5 py-4">
                <dl class="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
                  <div>
                    <dt
                      class="text-xs font-medium uppercase tracking-wide text-ink-500"
                    >
                      {{ t('management.tenantSlug') }}
                    </dt>
                    <dd class="mt-1 font-mono text-sm text-ink-900">
                      {{ tenant?.slug || '—' }}
                    </dd>
                  </div>
                  <div>
                    <dt
                      class="text-xs font-medium uppercase tracking-wide text-ink-500"
                    >
                      {{ t('management.tenantStatus') }}
                    </dt>
                    <dd class="mt-1 text-sm text-ink-900">
                      {{ statusLabel }}
                    </dd>
                  </div>
                  <div>
                    <dt
                      class="text-xs font-medium uppercase tracking-wide text-ink-500"
                    >
                      {{ t('management.tenantCreatedAt') }}
                    </dt>
                    <dd class="mt-1 text-sm text-ink-900">
                      {{ createdAtLabel }}
                    </dd>
                  </div>
                  <div>
                    <dt
                      class="text-xs font-medium uppercase tracking-wide text-ink-500"
                    >
                      {{ t('management.tenantMembers') }}
                    </dt>
                    <dd class="mt-1 text-sm text-ink-900">
                      {{ memberSummary }}
                    </dd>
                  </div>
                </dl>
                <p class="mt-4 text-xs leading-5 text-ink-500">
                  {{ t('management.tenantAdminOnlyHint') }}
                </p>
              </div>
            </section>

            <div class="admin-settings-save">
              <span class="flex-1 text-sm text-ink-500">
                <span
                  v-if="dirty"
                  class="mr-2 inline-block h-2 w-2 rounded-full bg-warning align-middle"
                />
                {{
                  dirty ? t('management.unsavedChanges') : t('management.saved')
                }}
              </span>
              <BaseButton
                type="button"
                variant="outline"
                size="sm"
                :disabled="!dirty || saving"
                @click="reset"
              >
                {{ t('management.resetChanges') }}
              </BaseButton>
              <BaseButton
                type="submit"
                variant="primary"
                size="sm"
                :loading="saving"
                :disabled="!dirty || saving"
              >
                {{ t('common.save') }}
              </BaseButton>
            </div>
          </form>
        </div>
      </section>
    </div>
  </AdminLayout>
</template>

<script setup>
import BaseSelect from '@/components/ui/BaseSelect.vue'
import { computed, onMounted, reactive, ref } from 'vue'
import { useI18n } from 'vue-i18n'

import AdminLayout from '@/admin/layout/AdminLayout.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseLoading from '@/components/ui/BaseLoading.vue'
import {
  getCurrentTenant,
  updateCurrentTenant,
  uploadTenantLogo
} from '@/api/tenant'
import { useToast } from '@/composables/useToast'

const { t } = useI18n()
const { showSuccess, showError } = useToast()

const tenant = ref(null)
const loading = ref(true)
const saving = ref(false)
const logoUploading = ref(false)
const error = ref('')
const nameError = ref('')
const logoInput = ref(null)
const form = reactive({
  name: '',
  description: '',
  default_language: 'zh-CN',
  timezone: 'Asia/Shanghai',
  contact_email: ''
})

const tenantInitial = computed(() => {
  const name = tenant.value?.name || ''
  return name.trim().charAt(0).toUpperCase() || 'T'
})

const dirty = computed(() => {
  if (!tenant.value) return false
  return (
    form.name !== (tenant.value.name || '') ||
    form.description !== (tenant.value.description || '') ||
    form.default_language !== (tenant.value.default_language || 'zh-CN') ||
    form.timezone !== (tenant.value.timezone || 'Asia/Shanghai') ||
    form.contact_email !== (tenant.value.contact_email || '')
  )
})

const statusLabel = computed(() => {
  const value = tenant.value?.status
  if (value === 'suspended') return t('management.tenantSuspended')
  if (value === 'pending') return t('management.tenantPending')
  return t('management.tenantActive')
})

const createdAtLabel = computed(() => {
  if (!tenant.value?.created_at) return '—'
  const date = new Date(tenant.value.created_at)
  if (Number.isNaN(date.getTime())) return tenant.value.created_at
  return date.toLocaleDateString()
})

const memberSummary = computed(() => {
  if (!tenant.value) return '—'
  return t('management.tenantMembersValue', {
    members: tenant.value.member_count ?? 0,
    groups: tenant.value.group_count ?? 0
  })
})

function applyTenant(data) {
  tenant.value = data
  form.name = data?.name || ''
  form.description = data?.description || ''
  form.default_language = data?.default_language || 'zh-CN'
  form.timezone = data?.timezone || 'Asia/Shanghai'
  form.contact_email = data?.contact_email || ''
}

async function fetchTenant() {
  loading.value = true
  error.value = ''
  try {
    applyTenant(await getCurrentTenant())
  } catch (err) {
    error.value =
      err?.response?.data?.detail || t('management.tenantLoadFailed')
  } finally {
    loading.value = false
  }
}

async function save() {
  nameError.value = ''
  if (!form.name.trim()) {
    nameError.value = t('management.tenantNameRequired')
    return
  }
  saving.value = true
  error.value = ''
  try {
    const data = await updateCurrentTenant({
      name: form.name.trim(),
      description: form.description,
      default_language: form.default_language,
      timezone: form.timezone,
      contact_email: form.contact_email
    })
    applyTenant(data)
    showSuccess(t('management.tenantSaved'))
  } catch (err) {
    const payload = err?.response?.data
    if (payload?.name) {
      nameError.value = t('management.tenantNameRequired')
    } else {
      error.value = payload?.detail || t('management.tenantSaveFailed')
    }
    showError(t('management.tenantSaveFailed'))
  } finally {
    saving.value = false
  }
}

function reset() {
  applyTenant(tenant.value)
  nameError.value = ''
  error.value = ''
}

async function onLogoChange(event) {
  const file = event.target.files?.[0]
  event.target.value = ''
  if (!file) return
  logoUploading.value = true
  try {
    applyTenant(await uploadTenantLogo(file))
    showSuccess(t('management.logoUploaded'))
  } catch (err) {
    const code = err?.response?.data?.logo
    const message =
      {
        LOGO_TOO_LARGE: t('management.logoTooLarge'),
        LOGO_UNSUPPORTED_TYPE: t('management.logoUnsupported')
      }[code] || t('management.logoUploadFailed')
    showError(message)
  } finally {
    logoUploading.value = false
  }
}

onMounted(fetchTenant)
</script>
