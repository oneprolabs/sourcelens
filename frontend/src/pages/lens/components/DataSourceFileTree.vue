<template>
  <ul role="tree" class="divide-y divide-line" :aria-label="ariaLabel">
    <DataSourceFileTreeNode
      v-for="node in nodes"
      :key="`${node.type}:${node.path}`"
      :node="node"
    />
  </ul>
</template>

<script setup>
import { computed } from 'vue'

import { buildDataSourceFileTree } from '../dataSourceFileTree'

import DataSourceFileTreeNode from './DataSourceFileTreeNode.vue'

const props = defineProps({
  files: { type: Array, default: () => [] },
  ariaLabel: { type: String, default: '' }
})

const nodes = computed(() => buildDataSourceFileTree(props.files))
</script>
