<template>
  <div class="trajectory-value">
    <template v-if="isObject && entries.length">
      <dl v-if="!Array.isArray(normalized)" class="value-fields">
        <div
          v-for="[key, item] in entries"
          :key="key"
          :class="{ 'field-block': isComplex(item) }"
        >
          <dt :title="key">{{ fieldLabel(key) }}</dt>
          <dd>
            <TrajectoryValue
              v-if="depth < 5"
              :value="item"
              :depth="depth + 1"
            />
            <pre v-else class="value-text">{{
              JSON.stringify(item, null, 2)
            }}</pre>
          </dd>
        </div>
      </dl>
      <ol v-else class="value-list">
        <li v-for="[key, item] in entries" :key="key">
          <div class="value-item-heading">
            <span>{{ Number(key) + 1 }}</span>
            <strong v-if="item?.role || item?.type">{{
              item.role || item.type
            }}</strong>
          </div>
          <TrajectoryValue v-if="depth < 5" :value="item" :depth="depth + 1" />
          <pre v-else class="value-text">{{
            JSON.stringify(item, null, 2)
          }}</pre>
        </li>
      </ol>
      <button
        v-if="allEntries.length > limit"
        class="value-more"
        type="button"
        @click="limit += 50"
      >
        {{
          t('lensRuns.trajectoryDetailShowMore', {
            n: allEntries.length - limit
          })
        }}
      </button>
    </template>
    <span v-else-if="isObject" class="value-muted">{{
      Array.isArray(normalized) ? '[]' : '{}'
    }}</span>
    <span v-else-if="normalized == null" class="value-muted">—</span>
    <span
      v-else-if="typeof normalized === 'boolean'"
      class="value-boolean"
      :data-value="normalized"
    >
      {{ normalized ? 'true' : 'false' }}
    </span>
    <span v-else-if="typeof normalized === 'number'" class="value-number">{{
      normalized.toLocaleString()
    }}</span>
    <details v-else-if="String(normalized).length > 1200" class="value-long">
      <summary>
        {{
          t('lensRuns.trajectoryDetailLongText', {
            n: String(normalized).length
          })
        }}
      </summary>
      <pre class="value-text">{{ normalized }}</pre>
    </details>
    <pre v-else class="value-text">{{
      normalized === '' ? '""' : normalized
    }}</pre>
  </div>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'

const props = defineProps({
  value: {
    type: [Object, Array, String, Number, Boolean, null],
    default: null
  },
  depth: { type: Number, default: 0 }
})
const { t, te } = useI18n()
const limit = ref(50)
watch(
  () => props.value,
  () => {
    limit.value = 50
  }
)
const normalized = computed(() => {
  if (typeof props.value !== 'string') return props.value
  const value = props.value.trim()
  if (!value.startsWith('{') && !value.startsWith('[')) return props.value
  try {
    return JSON.parse(value)
  } catch {
    return props.value
  }
})
const isObject = computed(
  () => normalized.value !== null && typeof normalized.value === 'object'
)
const allEntries = computed(() =>
  isObject.value ? Object.entries(normalized.value) : []
)
const entries = computed(() => allEntries.value.slice(0, limit.value))
function isComplex(value) {
  return (
    (value !== null && typeof value === 'object') ||
    String(value ?? '').length > 100 ||
    String(value ?? '').includes('\n')
  )
}
function fieldLabel(key) {
  const translation = `lensRuns.trajectoryFields.${key}`
  return te(translation) ? t(translation) : key.replace(/_/g, ' ')
}
</script>

<style scoped>
.trajectory-value {
  min-width: 0;
  color: var(--t-text-1);
  font-size: 12px;
  line-height: 1.7;
}
.value-fields {
  margin: 0;
}
.value-fields > div {
  display: grid;
  grid-template-columns: minmax(80px, 32%) minmax(0, 1fr);
  gap: 12px;
  padding: 8px 0;
  border-bottom: 1px solid var(--t-border-l1);
}
.value-fields > div:last-child {
  border-bottom: 0;
}
.value-fields dt {
  color: var(--t-text-3);
  overflow-wrap: anywhere;
}
.value-fields dd {
  min-width: 0;
  margin: 0;
}
.value-fields > .field-block {
  grid-template-columns: minmax(0, 1fr);
  gap: 4px;
}
.field-block > dt {
  font-size: 11px;
  font-weight: 600;
}
.value-text {
  margin: 0;
  font:
    12px/1.7 ui-monospace,
    SFMono-Regular,
    Menlo,
    monospace;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  tab-size: 2;
}
.value-number {
  font-variant-numeric: tabular-nums;
}
.value-muted {
  color: var(--t-text-3);
}
.value-boolean {
  border-radius: 4px;
  padding: 2px 6px;
  background: var(--t-bg-2);
  font-family: ui-monospace, monospace;
}
.value-boolean[data-value='true'] {
  color: var(--t-context);
}
.value-list {
  margin: 0;
  padding: 0;
  list-style: none;
}
.value-list > li {
  padding: 8px 0;
  border-bottom: 1px solid var(--t-border-l1);
}
.value-item-heading {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 4px;
  color: var(--t-text-3);
  font-size: 11px;
}
.value-item-heading > span {
  min-width: 20px;
  text-align: center;
  border-radius: 4px;
  background: var(--t-bg-2);
}
.value-long summary,
.value-more {
  color: var(--t-accent);
  cursor: pointer;
  font-size: 12px;
}
.value-long[open] summary {
  margin-bottom: 8px;
}
.value-more {
  margin-top: 8px;
  padding: 4px 0;
  border: 0;
  background: transparent;
}
.value-more:focus-visible,
summary:focus-visible {
  outline: 2px solid var(--t-accent);
  outline-offset: 2px;
}
</style>
