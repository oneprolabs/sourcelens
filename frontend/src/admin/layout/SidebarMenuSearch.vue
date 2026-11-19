<template>
  <div
    :class="[
      'admin-menu-search min-h-0 px-3 pt-2',
      searching ? 'flex flex-1 flex-col' : 'shrink-0'
    ]"
  >
    <label class="relative block">
      <Search
        class="pointer-events-none absolute left-3 top-3 h-4 w-4 text-ink-400"
        aria-hidden="true"
      />
      <kbd class="admin-search-shortcut" aria-hidden="true">/</kbd>
      <input
        ref="input"
        v-model="query"
        type="search"
        autocomplete="off"
        :aria-label="t('management.searchMenu')"
        :placeholder="t('management.searchMenu')"
        class="min-h-11 w-full rounded-lg border border-ink-700 bg-ink-800 py-2 pl-9 pr-3 text-sm text-white placeholder:text-ink-400 focus:border-brand-400 focus:outline-none focus:ring-1 focus:ring-brand-400"
        @keydown="handleInputKeydown"
      />
    </label>
    <nav
      v-if="searching"
      ref="results"
      :aria-label="t('management.menuSearchResults')"
      class="min-h-0 flex-1 overflow-y-auto py-3"
      @keydown="handleResultKeydown"
    >
      <router-link
        v-for="item in matches"
        :key="item.path"
        :to="item.path"
        class="mb-1 block break-words rounded-lg px-3 py-2 text-sm text-ink-200 hover:bg-white/10 focus:bg-white/10 focus:outline-none"
        @click="select"
      >
        <span class="block">{{ item.label }}</span>
        <span v-if="item.group" class="mt-1 block text-xs text-ink-400">{{
          item.group
        }}</span>
      </router-link>
      <p
        v-if="!matches.length"
        role="status"
        class="px-3 py-4 text-sm text-ink-400"
      >
        {{ t('management.noMenuResults') }}
      </p>
    </nav>
  </div>
</template>

<script setup>
import { Search } from '@lucide/vue'
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'

const props = defineProps({
  items: { type: Array, default: () => [] },
  active: { type: Boolean, default: true }
})
const emit = defineEmits(['update:searching', 'navigate'])
const { t } = useI18n()
const query = ref('')
const input = ref(null)
const results = ref(null)
const searching = computed(() => !!query.value.trim())
const matches = computed(() => {
  const terms = query.value.trim().toLocaleLowerCase().split(/\s+/)
  return props.items.filter((item) => {
    const text =
      `${item.label} ${item.group || ''} ${item.path}`.toLocaleLowerCase()
    return terms.every((term) => text.includes(term))
  })
})
function select() {
  query.value = ''
  emit('navigate')
}
function focusSearch(event) {
  if (
    !props.active ||
    event.defaultPrevented ||
    event.key !== '/' ||
    event.ctrlKey ||
    event.metaKey ||
    event.altKey
  )
    return
  if (
    event.target?.closest?.(
      'input, textarea, select, [contenteditable]:not([contenteditable="false"]), [role="textbox"], [role="dialog"], [aria-modal="true"]'
    )
  )
    return
  event.preventDefault()
  input.value?.focus()
}
function clearSearch(event) {
  event.preventDefault()
  event.stopPropagation()
  query.value = ''
  input.value?.focus()
}
function handleInputKeydown(event) {
  if (event.key === 'Escape') clearSearch(event)
  else if (
    searching.value &&
    (event.key === 'ArrowDown' || event.key === 'Enter')
  ) {
    const first = results.value?.querySelector('a')
    if (!first || event.isComposing) return
    event.preventDefault()
    if (event.key === 'Enter') first.click()
    else first.focus()
  }
}
function handleResultKeydown(event) {
  if (event.key === 'Escape') return clearSearch(event)
  if (!['ArrowDown', 'ArrowUp'].includes(event.key)) return
  const links = [...results.value.querySelectorAll('a')]
  const current = links.indexOf(event.target.closest('a'))
  if (current < 0) return
  event.preventDefault()
  const next = current + (event.key === 'ArrowDown' ? 1 : -1)
  if (next < 0) input.value?.focus()
  else links[next % links.length]?.focus()
}
watch(searching, (value) => emit('update:searching', value))
watch(
  () => props.active,
  (active) => {
    if (!active) query.value = ''
  }
)
onMounted(() => window.addEventListener('keydown', focusSearch))
onBeforeUnmount(() => window.removeEventListener('keydown', focusSearch))
</script>
