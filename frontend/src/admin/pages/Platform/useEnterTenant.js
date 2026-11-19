import { onScopeDispose, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRouter } from 'vue-router'

import { startTenantDelegation } from '@/api/platform'
import { useToast } from '@/composables/useToast'
import { useUserStore } from '@/store/user'

export function useEnterTenant() {
  const { t } = useI18n()
  const { showError, showSuccess } = useToast()
  const router = useRouter()
  const userStore = useUserStore()
  const startingTenantId = ref(null)
  let disposed = false

  onScopeDispose(() => {
    disposed = true
  })

  async function enterTenant(tenantId) {
    if (!tenantId || startingTenantId.value) return
    const userId = userStore.userInfo?.id
    const isCurrent = () => !disposed && userStore.userInfo?.id === userId
    startingTenantId.value = tenantId
    try {
      const delegation = await startTenantDelegation(tenantId)
      if (!isCurrent()) return
      userStore.setDelegation(delegation)
      await router.push('/management/overview')
      showSuccess(t('management.delegationStarted'))
    } catch {
      if (isCurrent()) showError(t('management.delegationFailed'))
    } finally {
      startingTenantId.value = null
    }
  }

  return { enterTenant, startingTenantId }
}
