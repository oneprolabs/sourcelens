<template>
  <div class="space-y-4">
    <div v-for="field in fields" :key="field.key" class="space-y-1">
      <label
        :for="fieldId(field)"
        class="flex items-center justify-between gap-2 text-sm font-medium text-ink-700"
      >
        <span>
          {{ field.title || field.key }}
          <span v-if="isRequired(field)" class="text-danger-600">*</span>
        </span>
        <slot name="field-actions" :field="field" />
      </label>

      <div
        v-if="isTreeField(field) && shouldRenderTree(field)"
        class="overflow-hidden rounded-xl border border-line bg-surface"
      >
        <div class="space-y-2.5 border-b border-line bg-surface-sunken p-3">
          <div class="relative">
            <svg
              class="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-ink-400"
              viewBox="0 0 20 20"
              fill="none"
              aria-hidden="true"
            >
              <circle
                cx="9"
                cy="9"
                r="5.5"
                stroke="currentColor"
                stroke-width="1.5"
              />
              <path
                d="m13 13 4 4"
                stroke="currentColor"
                stroke-linecap="round"
                stroke-width="1.5"
              />
            </svg>
            <input
              :id="`${fieldId(field)}-search`"
              :value="treeSearchQuery(field)"
              type="search"
              class="w-full rounded-lg border border-line bg-surface py-2 pl-9 pr-3 text-sm text-ink-900 outline-none placeholder:text-ink-400 focus:border-brand-500 focus:ring-2 focus:ring-brand-500/20"
              :placeholder="treeSearchPlaceholder"
              @input="setTreeSearch(field, $event.target.value)"
            />
          </div>
          <div class="flex items-center justify-between">
            <span class="text-xs font-medium text-ink-600">
              {{ filteredTreeItemCount(field) }} /
              {{ optionsFor(field).length }}
              {{ resourceCountLabel }}
            </span>
            <span
              class="rounded-full border border-brand-200 bg-brand-50 px-2 py-0.5 text-xs font-medium text-brand-700"
            >
              {{ arrayValue(field).length }} {{ selectedCountLabel }}
            </span>
          </div>
        </div>
        <div class="max-h-72 space-y-2 overflow-y-auto p-2">
          <div
            v-for="group in filteredTreeGroups(field)"
            :key="group.owner"
            class="overflow-hidden rounded-lg border border-line bg-surface"
          >
            <div
              class="flex items-center gap-2.5 bg-surface-sunken px-3 py-2.5 text-sm font-medium hover:bg-line-soft"
            >
              <input
                type="checkbox"
                class="resource-checkbox"
                :checked="groupSelected(field, group)"
                :indeterminate="groupPartial(field, group)"
                :aria-label="group.owner"
                :disabled="readOnly"
                @change="toggleGroup(field, group, $event.target.checked)"
              />
              <button
                type="button"
                class="flex min-w-0 flex-1 items-center gap-2.5 text-left focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500/30"
                :aria-expanded="!isTreeGroupCollapsed(field, group)"
                :aria-controls="treeGroupId(field, group)"
                @click="toggleTreeGroup(field, group)"
              >
                <svg
                  class="h-4 w-4 shrink-0 text-ink-400 transition-transform"
                  :class="{ '-rotate-90': isTreeGroupCollapsed(field, group) }"
                  viewBox="0 0 20 20"
                  fill="none"
                  aria-hidden="true"
                >
                  <path
                    d="m6 8 4 4 4-4"
                    stroke="currentColor"
                    stroke-linecap="round"
                    stroke-linejoin="round"
                    stroke-width="1.5"
                  />
                </svg>
                <span
                  class="flex h-6 w-6 shrink-0 items-center justify-center rounded-md border border-line bg-surface text-[10px] font-semibold uppercase text-ink-500"
                >
                  {{ group.owner.slice(0, 2) }}
                </span>
                <span class="min-w-0 flex-1 truncate text-ink-800">{{
                  group.owner
                }}</span>
                <span
                  class="rounded-full bg-surface px-2 py-0.5 text-xs font-normal text-ink-500"
                  >{{ group.items.length }}</span
                >
              </button>
            </div>
            <div
              v-show="!isTreeGroupCollapsed(field, group)"
              :id="treeGroupId(field, group)"
              class="divide-y divide-line border-t border-line px-2"
            >
              <label
                v-for="item in group.items"
                :key="optionValue(item)"
                class="flex items-center gap-2.5 rounded px-2 py-2.5 text-sm hover:bg-surface-sunken"
                :class="readOnly ? 'cursor-default' : 'cursor-pointer'"
              >
                <input
                  type="checkbox"
                  class="resource-checkbox"
                  :checked="arrayValue(field).includes(optionValue(item))"
                  :disabled="readOnly"
                  @change="
                    toggleArrayItem(
                      field,
                      optionValue(item),
                      $event.target.checked
                    )
                  "
                />
                <span class="min-w-0 flex-1 truncate text-ink-700">{{
                  repositoryName(item)
                }}</span>
                <span
                  v-if="item.metadata?.private"
                  class="rounded border border-line bg-surface-sunken px-1.5 py-0.5 text-[11px] text-ink-500"
                  >{{ privateResourceLabel }}</span
                >
              </label>
            </div>
          </div>
          <p
            v-if="!filteredTreeGroups(field).length"
            class="px-3 py-6 text-center text-sm text-ink-500"
          >
            {{ resourceSearchEmptyText }}
          </p>
        </div>
      </div>
      <div
        v-else-if="isTreeField(field)"
        class="rounded-xl border border-dashed border-line bg-surface-sunken px-4 py-6 text-center text-sm text-ink-500"
      >
        {{ emptyResourceText }}
      </div>
      <div
        v-else-if="isArrayField(field) && !isTreeField(field)"
        class="space-y-2"
      >
        <div
          v-for="(item, index) in arrayRows(field)"
          :key="index"
          class="flex items-center gap-2"
        >
          <input
            :id="arrayItemId(field, index)"
            :value="item"
        :class="[controlClass, 'min-w-0 flex-1 font-mono']"
            :type="arrayInputType(field)"
            :aria-label="`${field.title || field.key} ${index + 1}`"
            :placeholder="field.description || ''"
            :readonly="readOnly"
            @input="updateArrayItem(field, index, $event.target.value)"
          />
          <button
            type="button"
            class="inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-md border border-line text-ink-500 transition hover:border-danger-200 hover:bg-danger-50 hover:text-danger-700 disabled:cursor-not-allowed disabled:opacity-40"
            :aria-label="`${removeArrayItemLabel} ${index + 1}`"
            :title="removeArrayItemLabel"
            :disabled="readOnly || arrayRows(field).length === 1"
            @click="removeArrayItem(field, index)"
          >
            <XIcon class="h-4 w-4" aria-hidden="true" />
          </button>
        </div>
        <button
          type="button"
          class="inline-flex items-center gap-1.5 rounded-md border border-dashed border-line px-3 py-2 text-sm font-medium text-ink-600 transition hover:border-brand-300 hover:bg-brand-50 hover:text-brand-700 disabled:cursor-not-allowed disabled:opacity-40"
          :disabled="readOnly || !canAddArrayItem(field)"
          @click="addArrayItem(field)"
        >
          <PlusIcon class="h-4 w-4" aria-hidden="true" />
          {{ addArrayItemLabel }}
        </button>
      </div>
      <BaseSelect
        v-else-if="isResourceField(field)"
        :id="fieldId(field)"
        :model-value="fieldValue(field)"
        :required="isRequired(field)"
        :disabled="readOnly || isResourceOptionDisabled(field)"
        @update:model-value="setField(field, $event)"
      >
        <option value="">{{ placeholder(field) }}</option>
        <option
          v-for="option in optionsFor(field)"
          :key="optionValue(option)"
          :value="optionValue(option)"
        >
          {{ optionLabel(option) }}
        </option>
      </BaseSelect>
      <input
        v-else-if="field.type === 'boolean'"
        :id="fieldId(field)"
        class="h-4 w-4 rounded border-line text-brand-600 focus:ring-brand-500"
        type="checkbox"
        :checked="Boolean(fieldValue(field))"
        :disabled="readOnly"
        @change="setField(field, $event.target.checked)"
      />
      <input
        v-else
        :id="fieldId(field)"
        :class="[controlClass, isInvalid(field) ? 'border-danger-500 ring-2 ring-danger-500/20' : '']"
        :type="inputType(field)"
        :value="fieldValue(field)"
        :min="field.minimum"
        :max="field.maximum"
        :step="field.type === 'integer' ? 1 : undefined"
        :readonly="readOnly || isReadOnly(field)"
        :required="isRequired(field)"
        :autocomplete="field.format === 'password' ? 'new-password' : undefined"
        :placeholder="inputPlaceholder(field)"
        @input="setField(field, normalizeInput(field, $event.target.value))"
      />
      <p v-if="isInvalid(field)" class="text-xs text-danger-600">
        {{ requiredFieldError }}
      </p>
      <p
        v-if="isResourceOptionLoading(field)"
        class="text-xs leading-5 text-ink-500"
      >
        {{ loadingOptionsLabel }}
      </p>
      <p
        v-if="
          field.description && (!isTreeField(field) || shouldRenderTree(field))
        "
        class="text-xs leading-5 text-ink-500"
      >
        {{ field.description }}
      </p>
    </div>
  </div>
</template>

<script setup>
import { Plus as PlusIcon, X as XIcon } from '@lucide/vue'
import { computed, nextTick, ref } from 'vue'

import BaseSelect from '@/components/ui/BaseSelect.vue'

const props = defineProps({
  schema: { type: Object, default: () => ({}) },
  modelValue: { type: Object, default: () => ({}) },
  resources: { type: Object, default: () => ({}) },
  loadingResource: { type: String, default: '' },
  emptyResourceText: { type: String, default: 'No resources loaded.' },
  passwordPlaceholder: { type: String, default: '' },
  treeSearchPlaceholder: { type: String, default: 'Search resources' },
  resourceSearchEmptyText: { type: String, default: 'No matching resources.' },
  resourceCountLabel: { type: String, default: 'resources' },
  selectedCountLabel: { type: String, default: 'selected' },
  privateResourceLabel: { type: String, default: 'Private' },
  addArrayItemLabel: { type: String, default: 'Add' },
  removeArrayItemLabel: { type: String, default: 'Remove' },
  selectOptionLabel: { type: String, default: 'Select an option' },
  loadingOptionsLabel: { type: String, default: 'Loading options…' },
  invalidFields: { type: Array, default: () => [] },
  requiredFieldError: { type: String, default: 'This field is required.' },
  controlClass: { type: String, default: 'manifest-schema-control' },
  readOnly: { type: Boolean, default: false }
})

const emit = defineEmits(['resource-options-request', 'update:modelValue'])
const treeSearch = ref({})
const collapsedTreeGroups = ref({})

const fields = computed(() => {
  const properties = props.schema?.properties
  if (!properties || typeof properties !== 'object') return []
  return Object.entries(properties)
    .map(([key, value]) => ({ key, ...(value || {}) }))
    .filter((field) =>
      ['string', 'array', 'boolean', 'integer'].includes(field.type)
    )
})

function fieldId(field) {
  return `manifest-field-${field.key}`
}

function isInvalid(field) {
  return props.invalidFields.includes(field.key)
}

function isRequired(field) {
  return (
    Array.isArray(props.schema?.required) &&
    props.schema.required.includes(field.key)
  )
}

function fieldValue(field) {
  return props.modelValue?.[field.key] ?? field.default ?? defaultValue(field)
}

function defaultValue(field) {
  if (field.type === 'boolean') return false
  if (field.type === 'array') return []
  return ''
}

function arrayValue(field) {
  const value = fieldValue(field)
  return Array.isArray(value)
    ? value
    : String(value || '')
        .split(/[\n,]+/)
        .map((item) => item.trim())
        .filter(Boolean)
}

function isArrayField(field) {
  return field.type === 'array'
}

function arrayRows(field) {
  const values = arrayValue(field)
  return values.length ? values : ['']
}

function arrayItemId(field, index) {
  return index === 0 ? fieldId(field) : `${fieldId(field)}-${index}`
}

function arrayInputType(field) {
  return field.items?.format === 'uri' ? 'url' : 'text'
}

function canAddArrayItem(field) {
  const maximum = Number(field.maxItems)
  return !Number.isFinite(maximum) || arrayRows(field).length < maximum
}

function updateArrayItem(field, index, value) {
  const nextValue = [...arrayRows(field)]
  nextValue[index] = value
  setField(field, nextValue)
}

function addArrayItem(field) {
  if (!canAddArrayItem(field)) return
  setField(field, [...arrayRows(field), ''])
}

async function removeArrayItem(field, index) {
  const nextValue = [...arrayRows(field)]
  if (nextValue.length === 1) return
  nextValue.splice(index, 1)
  setField(field, nextValue)
  await nextTick()
  const nextIndex = Math.min(index, nextValue.length - 1)
  document.getElementById(arrayItemId(field, nextIndex))?.focus()
}

function isTreeField(field) {
  return isArrayField(field) && field.format === 'provider-resource'
}

function shouldRenderTree(field) {
  return optionsFor(field).length > 0
}

function treeGroups(field) {
  const groups = new Map()
  optionsFor(field).forEach((item) => {
    const value = optionValue(item)
    const owner = value.includes('/') ? value.split('/')[0] : 'Resources'
    if (!groups.has(owner)) groups.set(owner, [])
    groups.get(owner).push(item)
  })
  return [...groups.entries()].map(([owner, items]) => ({ owner, items }))
}

function treeSearchQuery(field) {
  return treeSearch.value[field.key] || ''
}

function setTreeSearch(field, value) {
  treeSearch.value = { ...treeSearch.value, [field.key]: value }
}

function filteredTreeGroups(field) {
  const query = treeSearchQuery(field).trim().toLowerCase()
  if (!query) return treeGroups(field)
  return treeGroups(field).flatMap((group) => {
    if (group.owner.toLowerCase().includes(query)) return [group]
    const items = group.items.filter((item) =>
      `${optionValue(item)} ${optionLabel(item)}`.toLowerCase().includes(query)
    )
    return items.length ? [{ ...group, items }] : []
  })
}

function filteredTreeItemCount(field) {
  return filteredTreeGroups(field).reduce(
    (total, group) => total + group.items.length,
    0
  )
}

function treeGroupKey(field, group) {
  return `${field.key}:${group.owner}`
}

function treeGroupId(field, group) {
  return `${fieldId(field)}-group-${encodeURIComponent(group.owner)}`
}

function isTreeGroupCollapsed(field, group) {
  if (treeSearchQuery(field).trim()) return false
  return Boolean(collapsedTreeGroups.value[treeGroupKey(field, group)])
}

function toggleTreeGroup(field, group) {
  const key = treeGroupKey(field, group)
  collapsedTreeGroups.value = {
    ...collapsedTreeGroups.value,
    [key]: !collapsedTreeGroups.value[key]
  }
}

function groupSelected(field, group) {
  const selected = arrayValue(field)
  return group.items.every((item) => selected.includes(optionValue(item)))
}

function groupPartial(field, group) {
  const selected = arrayValue(field)
  const count = group.items.filter((item) =>
    selected.includes(optionValue(item))
  ).length
  return count > 0 && count < group.items.length
}

function toggleGroup(field, group, checked) {
  if (props.readOnly) return
  const selected = new Set(arrayValue(field))
  group.items.forEach((item) => {
    if (checked) selected.add(optionValue(item))
    else selected.delete(optionValue(item))
  })
  setField(field, [...selected])
}

function toggleArrayItem(field, value, checked) {
  if (props.readOnly) return
  const selected = new Set(arrayValue(field))
  if (checked) selected.add(value)
  else selected.delete(value)
  setField(field, [...selected])
}

function isResourceOptionField(field) {
  return field.format === 'provider-resource-option'
}

function isResourceOptionLoading(field) {
  return (
    field.format === 'provider-resource-option' &&
    props.loadingResource === field.resource
  )
}

function isResourceOptionDisabled(field) {
  if (isResourceOptionLoading(field)) return true
  if (field.format !== 'provider-resource-option') return false
  const dependency = fields.value.find(
    (candidate) => candidate.key === field.depends_on
  )
  return !dependency || !fieldValue(dependency)
}

function isResourceField(field) {
  return (
    (field.format === 'provider-resource' || isResourceOptionField(field)) &&
    optionsFor(field).length > 0
  )
}

function optionsFor(field) {
  const options =
    field.format === 'provider-resource-option'
      ? dependentOptions(field)
      : props.resources?.[field.resource]?.items
  const normalized = Array.isArray(options) ? [...options] : []
  if (isTreeField(field)) return normalized
  const currentValue = fieldValue(field)
  const currentValues = Array.isArray(currentValue)
    ? currentValue
    : [currentValue]
  currentValues
    .filter((value) => value !== '')
    .forEach((value) => {
      if (!normalized.some((option) => optionValue(option) === value)) {
        normalized.push({ value, label: value })
      }
    })
  return normalized
}

function repositoryName(option) {
  const label = optionLabel(option)
  const parts = String(label).split('/')
  return parts.length > 1 ? parts.slice(1).join('/') : label
}

function dependentOptions(field) {
  return props.resources?.[field.resource]?.items || []
}

function optionValue(option) {
  return typeof option === 'object' ? (option.value ?? '') : option
}

function optionLabel(option) {
  return typeof option === 'object'
    ? (option.label ?? option.value ?? '')
    : option
}

function placeholder(field) {
  return field.placeholder || props.selectOptionLabel
}

function inputType(field) {
  if (field.format === 'password') return 'password'
  if (field.type === 'integer') return 'number'
  if (field.format === 'uri') return 'url'
  return 'text'
}

function inputPlaceholder(field) {
  if (field.format === 'password' && props.passwordPlaceholder) {
    return props.passwordPlaceholder
  }
  return field.description || ''
}

function isReadOnly(field) {
  return field.readOnly === true || field.readonly === true
}

function normalizeInput(field, value) {
  if (field.type === 'integer') {
    const number = Number(value)
    return Number.isFinite(number) ? number : ''
  }
  return value
}

function setField(field, value) {
  if (props.readOnly) return
  const nextValue = { ...props.modelValue, [field.key]: value }
  fields.value.forEach((candidate) => {
    if (candidate.depends_on !== field.key) return
    nextValue[candidate.key] = ''
    if (value) {
      emit('resource-options-request', {
        resource: candidate.resource,
        selectedValues: { [field.key]: value }
      })
    }
  })
  emit('update:modelValue', nextValue)
}
</script>

<style scoped>
.manifest-schema-control {
  @apply w-full rounded-lg border border-line bg-surface px-3 py-2 text-sm
    text-ink-900 placeholder:text-ink-400 focus:border-brand-500
    focus:outline-none focus:ring-2 focus:ring-brand-500/20;
}

.resource-checkbox {
  @apply h-4 w-4 shrink-0 appearance-none rounded-[3px] border-2
    border-ink-300 bg-surface transition checked:border-brand-600
    checked:bg-brand-600 focus:outline-none focus:ring-2
    focus:ring-brand-500/20;
}

.resource-checkbox:checked {
  background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16'%3E%3Cpath fill='none' stroke='white' stroke-linecap='round' stroke-linejoin='round' stroke-width='2' d='m4 8 2.5 2.5L12 5'/%3E%3C/svg%3E");
}
</style>
