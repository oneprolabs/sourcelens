<template>
  <div class="space-y-4">
    <EmailCodeLogin v-if="mode === 'code'" @success="$emit('success')" />

    <form
      v-else-if="mode === 'password'"
      class="space-y-4"
      @submit.prevent="handleLogin"
    >
      <BaseInput
        v-model="formData.email"
        :label="t('auth.email')"
        type="email"
        name="email"
        autocomplete="email"
        :placeholder="t('auth.emailPlaceholder')"
        required
        :error="errors.email"
        :disabled="loading"
      />

      <BaseInput
        v-model="formData.password"
        :label="t('auth.password')"
        type="password"
        name="password"
        autocomplete="current-password"
        :placeholder="t('auth.password')"
        required
        :error="errors.password"
        :disabled="loading"
      />

      <div class="text-right">
        <button
          type="button"
          class="text-sm text-primary-600 hover:underline"
          @click="mode = 'forgot'"
        >
          {{ passwordText.forgot.link }}
        </button>
      </div>

      <TurnstileWidget
        ref="pwTurnstileRef"
        @verified="onPwVerified"
        @expired="pwTurnstilePassed = false"
        @error="pwTurnstilePassed = false"
      />

      <div
        v-if="errorMessage"
        class="rounded-lg border border-danger-100 bg-danger-50 p-4"
      >
        <p class="text-sm text-danger-700">{{ errorMessage }}</p>
      </div>

      <BaseButton
        type="submit"
        variant="primary"
        class="w-full"
        :loading="loading"
        :disabled="loading"
      >
        {{ loading ? t('auth.signingIn') : t('auth.signIn') }}
      </BaseButton>
    </form>

    <ForgotPasswordForm v-else @back="mode = 'password'" />

    <button
      v-if="mode !== 'forgot'"
      type="button"
      class="block w-full text-center text-sm text-ink-500 hover:underline"
      @click="toggleMode"
    >
      {{
        mode === 'code'
          ? t('auth.codeLogin.switchToPassword')
          : t('auth.codeLogin.switchToCode')
      }}
    </button>
  </div>
</template>

<script setup>
import { computed, reactive, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useUserStore } from '@/store/user'
import BaseInput from '@/components/ui/BaseInput.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import EmailCodeLogin from '@/components/auth/EmailCodeLogin.vue'
import ForgotPasswordForm from '@/components/auth/ForgotPasswordForm.vue'
import TurnstileWidget from '@/components/TurnstileWidget.vue'
import { getPasswordManagementText } from '@/locales/passwordManagement'

const emit = defineEmits(['success'])

const { t, locale } = useI18n()
const userStore = useUserStore()
const passwordText = computed(() => getPasswordManagementText(locale.value))

const mode = ref('password')
const loading = ref(false)
const errorMessage = ref('')
const formData = reactive({ email: '', password: '' })
const errors = reactive({ email: '', password: '' })

const pwTurnstilePassed = ref(false)
const pwTurnstileToken = ref('')
const pwTurnstileRef = ref(null)

const onPwVerified = (token) => {
  pwTurnstileToken.value = token || ''
  pwTurnstilePassed.value = true
}

const toggleMode = () => {
  mode.value = mode.value === 'code' ? 'password' : 'code'
  errorMessage.value = ''
}

const validateLogin = () => {
  errors.email = ''
  errors.password = ''
  if (!formData.email.trim()) {
    errors.email = t('auth.required.email')
    return false
  }
  if (!formData.password) {
    errors.password = t('auth.required.password')
    return false
  }
  return true
}

const handleLogin = async () => {
  if (!validateLogin()) {
    return
  }
  if (!pwTurnstilePassed.value) {
    errorMessage.value = t('auth.codeLogin.turnstileRequired')
    return
  }

  loading.value = true
  errorMessage.value = ''
  try {
    await userStore.login({
      email: formData.email,
      password: formData.password,
      turnstileToken: pwTurnstileToken.value
    })
    emit('success')
  } catch (error) {
    console.error('Login error:', error)
    errorMessage.value = t('auth.loginError')
    pwTurnstilePassed.value = false
    pwTurnstileRef.value?.reset?.()
    loading.value = false
  }
}
</script>
