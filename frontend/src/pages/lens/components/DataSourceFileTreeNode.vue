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
        @click="toggle"
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
          <span>{{ node.file?.extension || emptyValue }}</span>
          <span>{{ node.file?.sync_status }}</span>
          <span class="truncate" :title="node.file?.conversion_error">
            {{ node.file?.conversion_status }}
          </span>
        </div>
      </div>
      <span class="hidden text-xs text-ink-500 sm:block">
        {{ isDirectory ? emptyValue : node.file?.extension || emptyValue }}
      </span>
      <span class="hidden text-xs text-ink-700 sm:block">
        {{ isDirectory ? emptyValue : node.file?.sync_status }}
      </span>
      <span
        class="hidden text-xs text-ink-700 sm:block"
        :title="isDirectory ? '' : node.file?.conversion_error"
      >
        {{ isDirectory ? emptyValue : node.file?.conversion_status }}
      </span>
    </div>
    <ul
      v-if="isDirectory && expanded"
      role="group"
      class="ml-4 border-l border-line"
    >
      <DataSourceFileTreeNode
        v-for="child in node.children || []"
        :key="`${child.type}:${child.path}`"
        :node="child"
        :on-toggle="onToggle"
        :on-load-more="onLoadMore"
      />
      <li v-if="node.loading" class="px-4 py-2 text-xs text-ink-500">
        {{ t('common.loading') }}
      </li>
      <li v-else-if="node.error" class="px-4 py-2 text-xs text-danger-600">
        {{ node.error }}
      </li>
      <li v-else-if="node.hasMore" class="px-4 py-2">
        <button
          type="button"
          class="text-xs font-medium text-primary-700 hover:underline"
          @click="onLoadMore?.(node)"
        >
          {{ t('common.loadMore') }}
        </button>
      </li>
      <li
        v-else-if="node.loaded && !(node.children || []).length"
        class="px-4 py-2 text-xs text-ink-500"
      >
        {{ t('lensAdmin.datasourceDetail.files.empty') }}
      </li>
    </ul>
  </li>
</template>

<script setup>
import { computed, ref } from 'vue'
import { ChevronRight, FileText, Folder, FolderOpen } from '@lucide/vue'
import { useI18n } from 'vue-i18n'

import { EMPTY_VALUE as emptyValue } from '../adminHelpers'

const props = defineProps({
  node: { type: Object, required: true },
  onToggle: { type: Function, default: null },
  onLoadMore: { type: Function, default: null }
})

const { t } = useI18n()

const expanded = ref(false)
const isDirectory = computed(() => props.node.type === 'directory')

function toggle() {
  const next = !expanded.value
  expanded.value = next
  if (next && !props.node.loaded && !props.node.loading) {
    props.onToggle?.(props.node)
  }
}
</script>
