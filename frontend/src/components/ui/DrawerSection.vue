<template>
  <section class="drawer-section" :class="sectionClass">
    <div v-if="title || $slots.actions" class="drawer-section-header">
      <h3 v-if="title" class="drawer-section-title">{{ title }}</h3>
      <div v-if="$slots.actions" class="drawer-section-actions">
        <slot name="actions" />
      </div>
    </div>
    <div class="drawer-section-body">
      <slot />
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed } from 'vue'

interface Props {
  title?: string
  spacing?: 'none' | 'sm' | 'md'
}

const props = withDefaults(defineProps<Props>(), {
  title: '',
  spacing: 'md'
})

const sectionClass = computed(() => `drawer-section-${props.spacing}`)
</script>

<style scoped>
.drawer-section { @apply min-w-0; }
.drawer-section-header { @apply mb-3 flex items-center justify-between gap-3; }
.drawer-section-title { @apply text-sm font-semibold text-ink-900; }
.drawer-section-actions { @apply shrink-0; }
.drawer-section-body { @apply min-w-0; }
.drawer-section-sm .drawer-section-header { @apply mb-2; }
.drawer-section-none .drawer-section-header { @apply mb-0; }
</style>
