<template>
  <form
    class="space-y-4"
    @submit.prevent="codeSent ? handleVerifyCode() : handleSendCode()"
  >
    <BaseInput
      v-model="codeForm.email"
      :label="t('auth.codeLogin.emailLabel')"
      type="email"
      name="email"
      autocomplete="email"
      :placeholder="t('auth.codeLogin.emailPlaceholder')"
      required
      :disabled="loading || codeSent"
    />

    <TurnstileWidget
      v-if="!codeSent"
      ref="turnstileRef"
      @verified="onTurnstileVerified"
      @expired="turnstilePassed = false"
      @error="turnstilePassed = false"
    />

    <div v-if="codeSent" class="space-y-2">
      <BaseInput
        v-model="codeForm.code"
        :label="t('auth.codeLogin.codeLabel')"
        type="text"
        name="one-time-code"
        inputmode="numeric"
        autocomplete="one-time-code"
        :placeholder="t('auth.codeLogin.codePlaceholder')"
        required
        :disabled="loading"
      />
      <button
        type="button"
        class="text-sm text-primary-600 hover:underline disabled:opacity-50"
        :disabled="cooldown > 0 || loading"
        @click="handleSendCode"
      >
        {{
          cooldown > 0
            ? t('auth.codeLogin.resendIn', { seconds: cooldown })
            : t('auth.codeLogin.resend')
        }}
      </button>
    </div>

    <div
      v-if="errorMessage"
      class="rounded-lg border border-danger-100 bg-danger-50 p-4"
    >
      <p class="text-sm text-danger-700">{{ errorMessage }}</p>
    </div>
    <div
      v-if="infoMessage"
      class="rounded-lg border border-primary-100 bg-primary-50 p-4"
    >
      <p class="text-sm text-primary-700">{{ t(infoMessage) }}</p>
    </div>

    <BaseButton
      type="submit"
      variant="primary"
      class="w-full"
      :loading="loading"
      :disabled="loading"
    >
      <template v-if="codeSent">
        {{
          loading
            ? t('auth.codeLogin.verifying')
            : t('auth.codeLogin.verifyLogin')
        }}
      </template>
      <template v-else>
        {{
          loading ? t('auth.codeLogin.sending') : t('auth.codeLogin.sendCode')
        }}
      </template>
    </BaseButton>
  </form>
</template>

<script setup>
import { onBeforeUnmount, reactive, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useUserStore } from '@/store/user'
import { authApi } from '@/api/auth'
import BaseInput from '@/components/ui/BaseInput.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import TurnstileWidget from '@/components/TurnstileWidget.vue'
import { getVerificationErrorMessage } from '@/utils/verificationError'

const emit = defineEmits(['success'])

const { t, locale } = useI18n()
const userStore = useUserStore()

const loading = ref(false)
const errorMessage = ref('')
const infoMessage = ref('')

const codeForm = reactive({ email: '', code: '' })
const codeSent = ref(false)
const turnstilePassed = ref(false)
const turnstileToken = ref('')
const turnstileRef = ref(null)
const cooldown = ref(0)
let cooldownTimer = null

const isValidEmail = (value) => /\S+@\S+\.\S+/.test(value)

const onTurnstileVerified = (token) => {
  turnstileToken.value = token || ''
  turnstilePassed.value = true
}

const startCooldown = (seconds) => {
  cooldown.value = seconds
  if (cooldownTimer) {
    clearInterval(cooldownTimer)
  }
  cooldownTimer = setInterval(() => {
    cooldown.value -= 1
    if (cooldown.value <= 0) {
      clearInterval(cooldownTimer)
      cooldownTimer = null
    }
  }, 1000)
}

const handleSendCode = async () => {
  errorMessage.value = ''
  infoMessage.value = ''

  if (!isValidEmail(codeForm.email)) {
    errorMessage.value = t('auth.codeLogin.invalidEmail')
    return
  }
  if (!turnstilePassed.value) {
    errorMessage.value = t('auth.codeLogin.turnstileRequired')
    return
  }

  loading.value = true
  try {
    await authApi.sendLoginCode({
      email: codeForm.email,
      turnstileToken: turnstileToken.value,
      language: locale.value
    })
    codeSent.value = true
    infoMessage.value = 'auth.codeLogin.codeSent'
    startCooldown(60)
  } catch (error) {
    errorMessage.value =
      error.response?.data?.message || t('auth.codeLogin.sendFailed')
    turnstilePassed.value = false
    turnstileRef.value?.reset?.()
  } finally {
    loading.value = false
  }
}

const handleVerifyCode = async () => {
  errorMessage.value = ''
  if (!codeForm.code.trim()) {
    return
  }

  loading.value = true
  try {
    await userStore.loginWithCode({
      email: codeForm.email,
      code: codeForm.code.trim(),
      language: locale.value
    })
    emit('success')
  } catch (error) {
    errorMessage.value = getVerificationErrorMessage(error, t)
  } finally {
    loading.value = false
  }
}

onBeforeUnmount(() => {
  if (cooldownTimer) {
    clearInterval(cooldownTimer)
  }
})
</script>
