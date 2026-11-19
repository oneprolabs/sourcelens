<template>
  <span class="catalog-tile" :style="tone" aria-hidden="true"
    ><slot>{{ name[0]?.toUpperCase() }}</slot></span
  >
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({ name: { type: String, default: '' } })
const tones = [
  ['#e8efff', '#4a68d8'],
  ['#e6f7ef', '#1fa56f'],
  ['#fff4de', '#c27a0a'],
  ['#fdeceb', '#d4503f'],
  ['#f1eafd', '#6a55c8'],
  ['#e3f4f7', '#1c8e9e']
]
const tone = computed(() => {
  const index =
    ((props.name.charCodeAt(0) || 0) + props.name.length) % tones.length
  return { background: tones[index][0], color: tones[index][1] }
})
</script>
