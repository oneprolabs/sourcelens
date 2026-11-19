<template>
  <div
    class="min-h-screen flex items-center justify-center bg-gray-50 px-4 py-12"
  >
    <div
      class="w-full max-w-md rounded-xl border border-gray-200 bg-white p-8 shadow-sm"
    >
      <div class="mb-6 text-center">
        <h1 class="text-2xl font-semibold text-gray-900">
          {{ t(invitation ? 'invitation.title' : 'password.reset.title') }}
        </h1>
        <p class="mt-2 text-sm text-gray-500">
          {{
            t(invitation ? 'invitation.subtitle' : 'password.reset.subtitle')
          }}
        </p>
      </div>

      <div
        v-if="successMessage"
        class="mb-4 rounded-md border border-green-200 bg-green-50 p-4 text-sm text-green-700"
      >
        <p class="font-medium text-green-800">
          {{
            t(
              invitation
                ? 'invitation.successTitle'
                : 'password.reset.successTitle'
            )
          }}
        </p>
        <p class="mt-1">
          {{ successMessage }}
        </p>
      </div>

      <div
        v-if="errorMessage"
        class="mb-4 rounded-md border border-red-200 bg-red-50 p-4 text-sm text-red-700"
      >
        {{ errorMessage }}
      </div>

      <form
        v-if="!resetComplete"
        class="space-y-4"
        novalidate
        @submit.prevent="handleSubmit"
      >
        <BaseInput
          v-model="form.newPassword1"
          :label="t('password.reset.newPassword')"
          type="password"
          autocomplete="new-password"
          :placeholder="t('password.reset.newPasswordPlaceholder')"
          :error="fieldErrors.newPassword1"
          :disabled="submitting"
          required
        />

        <BaseInput
          v-model="form.newPassword2"
          :label="t('password.reset.confirmPassword')"
          type="password"
          autocomplete="new-password"
          :placeholder="t('password.reset.confirmPasswordPlaceholder')"
          :error="fieldErrors.newPassword2"
          :disabled="submitting"
          required
        />

        <BaseButton
          type="submit"
          variant="primary"
          class="w-full"
          :loading="submitting"
          :disabled="submitting"
        >
          {{
            submitting
              ? t('common.loading')
              : t(invitation ? 'invitation.submit' : 'password.reset.submit')
          }}
        </BaseButton>
      </form>

      <BaseButton
        v-else
        variant="primary"
        class="w-full"
        @click="router.push('/login')"
      >
        {{ t('password.reset.backToLogin') }}
      </BaseButton>
    </div>
  </div>
</template>

<script setup>
import { computed, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { authApi } from '@/api/auth'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseInput from '@/components/ui/BaseInput.vue'
import { getFirstApiError, getPasswordPolicyError } from '@/utils/password'

const props = defineProps({ invitation: Boolean })
const { t } = useI18n()
const route = useRoute()
const router = useRouter()
const invitationToken = computed(
  () => new URLSearchParams((route.hash || '').slice(1)).get('token') || ''
)

const form = reactive({
  newPassword1: '',
  newPassword2: ''
})

const fieldErrors = reactive({
  newPassword1: '',
  newPassword2: ''
})

const submitting = ref(false)
const errorMessage = ref('')
const successMessage = ref('')
const resetComplete = ref(false)

const validateForm = () => {
  fieldErrors.newPassword1 = ''
  fieldErrors.newPassword2 = ''
  errorMessage.value = ''

  if (!form.newPassword1) {
    fieldErrors.newPassword1 = t('password.reset.newPasswordRequired')
  }

  if (!form.newPassword2) {
    fieldErrors.newPassword2 = t('password.reset.confirmPasswordRequired')
  }

  if (fieldErrors.newPassword1 || fieldErrors.newPassword2) {
    return false
  }

  if (form.newPassword1 !== form.newPassword2) {
    fieldErrors.newPassword2 = t('password.reset.passwordMismatch')
    return false
  }

  const policyError = getPasswordPolicyError(form.newPassword1)
  if (policyError) {
    const policyKeys = {
      tooShort: 'password.reset.passwordTooShort',
      tooLong: 'password.reset.passwordTooLong',
      requirements: 'password.reset.passwordRequirements'
    }
    fieldErrors.newPassword1 = t(policyKeys[policyError])
    return false
  }

  return true
}

const handleSubmit = async () => {
  if (submitting.value || !validateForm()) {
    return
  }
  if (props.invitation && !invitationToken.value) {
    errorMessage.value = t('invitation.invalid')
    return
  }

  submitting.value = true
  errorMessage.value = ''

  try {
    const data = {
      newPassword1: form.newPassword1,
      newPassword2: form.newPassword2
    }
    const response = props.invitation
      ? await authApi.acceptTenantInvitation({
          ...data,
          token: invitationToken.value
        })
      : await authApi.confirmPasswordReset({
          ...data,
          uid: route.params.uid,
          token: route.params.token
        })

    const responseData = response?.data?.data || response?.data || response
    successMessage.value = props.invitation
      ? t('invitation.successMessage')
      : responseData?.message || t('password.reset.successMessage')
    resetComplete.value = true
    form.newPassword1 = ''
    form.newPassword2 = ''
    if (props.invitation) router.replace({ path: route.path, hash: '' })
  } catch (error) {
    const message = getFirstApiError(error)
    errorMessage.value =
      props.invitation && message === 'INVITATION_INVALID_OR_EXPIRED'
        ? t('invitation.invalid')
        : message || t('password.reset.unknownError')
  } finally {
    submitting.value = false
  }
}
</script>
