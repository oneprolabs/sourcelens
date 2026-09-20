<template>
  <ul role="tree" class="divide-y divide-line" :aria-label="ariaLabel">
    <DataSourceFileTreeNode
      v-for="node in nodes"
      :key="`${node.type}:${node.path}`"
      :node="node"
      :on-toggle="onToggle"
      :on-load-more="onLoadMore"
    />
  </ul>
</template>

<script setup>
import DataSourceFileTreeNode from './DataSourceFileTreeNode.vue'

defineProps({
  nodes: { type: Array, default: () => [] },
  ariaLabel: { type: String, default: '' },
  onToggle: { type: Function, default: null },
  onLoadMore: { type: Function, default: null }
})
</script>
