<template>
  <span class="plugin-icon">
    <img
      v-if="imageUrl && !failed"
      :src="imageUrl"
      :alt="label"
      @error="failed = true"
    />
    <Database v-else :size="16" :aria-label="label" role="img" />
  </span>
</template>

<script setup>
import { Database } from '@lucide/vue'
import { ref, watch } from 'vue'

import { getPluginIcon } from '@/api/lens'

const props = defineProps({
  pluginKey: { type: String, default: '' },
  src: { type: String, default: '' },
  label: { type: String, default: '' }
})

const imageUrl = ref('')
const failed = ref(false)

watch(
  () => [props.pluginKey, props.src],
  async ([key, src], previous, onCleanup) => {
    let active = true
    let objectUrl = ''
    onCleanup(() => {
      active = false
      if (objectUrl) URL.revokeObjectURL(objectUrl)
    })
    imageUrl.value = src
    failed.value = false
    if (src || !key) return
    try {
      const blob = await getPluginIcon(key)
      if (!active) return
      objectUrl = URL.createObjectURL(blob)
      imageUrl.value = objectUrl
    } catch {
      if (active) failed.value = true
    }
  },
  { immediate: true }
)
</script>

<style scoped>
.plugin-icon {
  @apply inline-flex h-5 w-5 shrink-0 items-center justify-center overflow-hidden rounded border border-line bg-surface text-ink-500;
}
.plugin-icon img {
  @apply h-full w-full object-contain;
}
</style>
