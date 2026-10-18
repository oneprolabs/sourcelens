<template>
  <section class="space-y-3 border-t border-line pt-4">
    <div class="flex items-center justify-between gap-2">
      <h2 class="text-sm font-semibold text-theme">
        {{ t('settings.alertSubscriptions.title') }}
      </h2>
      <BaseButton type="button" variant="ghost" size="sm" @click="load">
        {{ t('common.refresh') }}
      </BaseButton>
    </div>
    <p class="text-xs text-theme-secondary">
      {{ t('settings.alertSubscriptions.description') }}
    </p>
    <p v-if="error" role="alert" class="break-words text-sm text-danger-700">
      {{ error }}
    </p>
    <p v-if="loading" role="status" class="text-sm text-theme-secondary">
      {{ t('common.loading') }}
    </p>
    <div
      v-else
      class="divide-y divide-line overflow-hidden rounded-xl border border-line"
    >
      <div
        v-for="rule in rows"
        :key="rule.uuid"
        class="flex items-center justify-between gap-3 px-4 py-4"
      >
        <span class="min-w-0 break-words text-sm font-medium text-theme"
          >{{ rule.name
          }}<span
            v-if="rule.topic"
            class="mt-1 block text-xs text-theme-secondary"
            >{{ rule.topic }}</span
          ></span
        >
        <BaseButton
          type="button"
          :variant="subscribed(rule) ? 'outline' : 'primary'"
          size="sm"
          class="shrink-0"
          :disabled="busy === rule.uuid"
          @click="toggle(rule)"
        >
          {{
            t(
              subscribed(rule)
                ? 'settings.alertSubscriptions.unsubscribe'
                : 'settings.alertSubscriptions.subscribe'
            )
          }}
        </BaseButton>
      </div>
      <p
        v-if="!rows.length"
        class="px-4 py-8 text-center text-sm text-theme-secondary"
      >
        {{ t('settings.alertSubscriptions.empty') }}
      </p>
    </div>
  </section>
</template>
<script setup>
import BaseButton from '@/components/ui/BaseButton.vue'
import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { personalAlertsApi } from '@/api/alerts'
const { t } = useI18n()
const rules = ref([]),
  subscriptions = ref([]),
  loading = ref(false),
  error = ref(''),
  busy = ref('')
const rows = computed(() => {
  const result = new Map(rules.value.map((rule) => [rule.uuid, rule]))
  for (const item of subscriptions.value) {
    if (!result.has(item.rule_uuid))
      result.set(item.rule_uuid, { uuid: item.rule_uuid, name: item.rule_name })
  }
  return [...result.values()]
})
const subscribed = (rule) =>
  subscriptions.value.find((item) => item.rule_uuid === rule.uuid)
async function load() {
  loading.value = true
  error.value = ''
  try {
    ;[rules.value, subscriptions.value] = await Promise.all([
      personalAlertsApi.rules(),
      personalAlertsApi.subscriptions()
    ])
  } catch (err) {
    error.value = err?.message || t('settings.alertSubscriptions.failed')
  } finally {
    loading.value = false
  }
}
async function toggle(rule) {
  busy.value = rule.uuid
  error.value = ''
  try {
    const existing = subscribed(rule)
    if (existing) {
      await personalAlertsApi.unsubscribe(existing.uuid)
      subscriptions.value = subscriptions.value.filter(
        (item) => item.uuid !== existing.uuid
      )
    } else
      subscriptions.value.push(await personalAlertsApi.subscribe(rule.uuid))
  } catch {
    error.value = t('settings.alertSubscriptions.failed')
  } finally {
    busy.value = ''
  }
}
onMounted(load)
</script>
