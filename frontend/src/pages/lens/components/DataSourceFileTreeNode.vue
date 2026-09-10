<template>
  <li role="treeitem" :aria-expanded="isDirectory ? expanded : undefined">
    <div
      class="grid gap-2 px-4 py-3 sm:grid-cols-[minmax(0,1fr)_90px_110px_110px] sm:items-center sm:gap-3"
    >
      <button
        v-if="isDirectory"
        type="button"
        class="flex min-w-0 items-center gap-2 text-left text-xs font-medium text-ink-800 hover:text-primary-700"
        :aria-expanded="expanded"
        :title="node.path"
        @click="expanded = !expanded"
      >
        <ChevronRight
          class="h-3.5 w-3.5 shrink-0 text-ink-400 transition-transform"
          :class="expanded ? 'rotate-90' : ''"
          aria-hidden="true"
        />
        <FolderOpen
          v-if="expanded"
          class="h-4 w-4 shrink-0 text-warning-600"
          aria-hidden="true"
        />
        <Folder
          v-else
          class="h-4 w-4 shrink-0 text-warning-600"
          aria-hidden="true"
        />
        <span class="truncate">{{ node.name }}</span>
      </button>
      <div v-else class="min-w-0 pl-5">
        <div class="flex min-w-0 items-center gap-2">
          <FileText class="h-4 w-4 shrink-0 text-ink-400" aria-hidden="true" />
          <span
            class="truncate font-mono text-xs text-ink-800"
            :title="node.path"
          >
            {{ node.name }}
          </span>
        </div>
        <div
          class="mt-1 flex min-w-0 flex-wrap gap-x-2 gap-y-1 pl-6 text-[11px] text-ink-500 sm:hidden"
        >
          <span>{{ node.file.extension || emptyValue }}</span>
          <span>{{ node.file.sync_status }}</span>
          <span class="truncate" :title="node.file.conversion_error">
            {{ node.file.conversion_status }}
          </span>
        </div>
      </div>
      <span class="hidden text-xs text-ink-500 sm:block">
        {{ isDirectory ? emptyValue : node.file.extension || emptyValue }}
      </span>
      <span class="hidden text-xs text-ink-700 sm:block">
        {{ isDirectory ? emptyValue : node.file.sync_status }}
      </span>
      <span
        class="hidden text-xs text-ink-700 sm:block"
        :title="isDirectory ? '' : node.file.conversion_error"
      >
        {{ isDirectory ? emptyValue : node.file.conversion_status }}
      </span>
    </div>
    <ul
      v-if="isDirectory && expanded"
      role="group"
      class="ml-4 border-l border-line"
    >
      <DataSourceFileTreeNode
        v-for="child in node.children"
        :key="`${child.type}:${child.path}`"
        :node="child"
      />
    </ul>
  </li>
</template>

<script setup>
import { computed, ref } from 'vue'
import { ChevronRight, FileText, Folder, FolderOpen } from '@lucide/vue'

import { EMPTY_VALUE as emptyValue } from '../adminHelpers'

const props = defineProps({
  node: { type: Object, required: true }
})

const expanded = ref(true)
const isDirectory = computed(() => props.node.type === 'directory')
</script>
