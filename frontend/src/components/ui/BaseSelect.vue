<template>
  <div ref="rootRef" :class="rootClasses">
    <label
      v-if="label"
      :for="selectId"
      class="block text-sm font-medium text-theme"
    >
      {{ label }}
      <span v-if="required" class="text-danger-600">*</span>
    </label>

    <div class="relative">
      <button
        :id="selectId"
        ref="triggerRef"
        type="button"
        role="combobox"
        aria-haspopup="listbox"
        :aria-activedescendant="activeOptionId"
        :aria-controls="listboxId"
        :aria-describedby="describedBy"
        :aria-expanded="isOpen"
        :aria-invalid="isInvalid ? 'true' : attrs['aria-invalid']"
        :aria-label="attrs['aria-label']"
        :aria-labelledby="attrs['aria-labelledby']"
        :aria-required="required ? 'true' : undefined"
        :disabled="disabled"
        :title="attrs.title"
        :class="selectClasses"
        @blur="$emit('blur', $event)"
        @click="toggleMenu"
        @focus="$emit('focus', $event)"
        @keydown="handleTriggerKeydown"
      >
        <span class="min-w-0 flex-1 truncate">
          <slot name="selected" :option="selectedOption">
            {{ selectedOption?.label || '' }}
          </slot>
        </span>

        <svg
          aria-hidden="true"
          :class="[
            'pointer-events-none absolute right-3 top-1/2 h-4 w-4',
            '-translate-y-1/2 transition-transform duration-150',
            isOpen ? 'rotate-180' : '',
            disabled ? 'text-theme-subtle' : 'text-theme-muted'
          ]"
          fill="none"
          stroke="currentColor"
          viewBox="0 0 24 24"
        >
          <path
            d="m8 10 4 4 4-4"
            stroke-linecap="round"
            stroke-linejoin="round"
            stroke-width="2"
          />
        </svg>
      </button>

      <select
        v-model="selectedValue"
        v-bind="selectAttrs"
        ref="nativeSelectRef"
        aria-hidden="true"
        class="hidden"
        tabindex="-1"
        :disabled="disabled"
        :required="required"
        @change="handleChange"
        @invalid="handleInvalid"
      >
        <slot />
      </select>
    </div>

    <p v-if="error" :id="errorId" class="text-sm text-danger-700">
      {{ error }}
    </p>

    <p v-else-if="help" :id="helpId" class="text-sm text-theme-muted">
      {{ help }}
    </p>

    <Teleport to="body">
      <ul
        v-if="isOpen"
        :id="listboxId"
        ref="menuRef"
        role="listbox"
        tabindex="-1"
        :aria-labelledby="attrs['aria-labelledby'] || selectId"
        :style="menuStyle"
        class="fixed z-[120] overflow-y-auto rounded-lg border border-line bg-surface p-1 shadow-lg ring-1 ring-black/5"
        @mousedown.prevent
      >
        <li
          v-for="(option, index) in optionItems"
          :id="optionId(index)"
          :key="option.key"
          role="option"
          :aria-disabled="option.disabled ? 'true' : undefined"
          :aria-selected="isSelected(option)"
          :class="optionClasses(option, index)"
          @click="selectOption(option, index)"
          @mouseenter="activateOption(option, index)"
        >
          <span class="min-w-0 flex-1">
            <slot name="option" :option="option">
              <span class="whitespace-nowrap">{{ option.label }}</span>
            </slot>
          </span>

          <svg
            v-if="isSelected(option)"
            aria-hidden="true"
            class="h-4 w-4 shrink-0 text-primary-600"
            fill="none"
            stroke="currentColor"
            viewBox="0 0 24 24"
          >
            <path
              d="m5 12 4 4L19 6"
              stroke-linecap="round"
              stroke-linejoin="round"
              stroke-width="2"
            />
          </svg>
        </li>
      </ul>
    </Teleport>
  </div>
</template>

<script setup>
import {
  computed,
  nextTick,
  onBeforeUnmount,
  ref,
  useAttrs,
  useSlots,
  watch
} from 'vue'

import {
  applyModelModifiers,
  extractSelectOptions,
  findNextEnabledOption,
  findTypeaheadOption
} from './baseSelect'

defineOptions({ inheritAttrs: false })

const props = defineProps({
  modelValue: {
    default: ''
  },
  modelModifiers: {
    type: Object,
    default: () => ({})
  },
  label: {
    type: String,
    default: ''
  },
  help: {
    type: String,
    default: ''
  },
  error: {
    type: String,
    default: ''
  },
  invalid: {
    type: Boolean,
    default: false
  },
  required: {
    type: Boolean,
    default: false
  },
  disabled: {
    type: Boolean,
    default: false
  },
  size: {
    type: String,
    default: 'md',
    validator: (value) => ['sm', 'md', 'lg'].includes(value)
  },
  variant: {
    type: String,
    default: 'default',
    validator: (value) => ['default', 'unstyled'].includes(value)
  },
  fullWidth: {
    type: Boolean,
    default: true
  },
  mobileTouch: {
    type: Boolean,
    default: false
  },
  menuMinWidth: {
    type: Number,
    default: 96
  }
})

const emit = defineEmits(['update:modelValue', 'change', 'focus', 'blur'])
const attrs = useAttrs()
const slots = useSlots()
const rootRef = ref(null)
const triggerRef = ref(null)
const nativeSelectRef = ref(null)
const menuRef = ref(null)
const isOpen = ref(false)
const activeIndex = ref(-1)
const menuStyle = ref({})
const generatedId = ref(`select-${Math.random().toString(36).substring(2, 11)}`)
let typeaheadQuery = ''
let typeaheadTimer

const selectId = computed(() => attrs.id || generatedId.value)
const nativeSelectId = computed(() => `${selectId.value}-native`)
const listboxId = computed(() => `${selectId.value}-listbox`)
const errorId = computed(() => `${selectId.value}-error`)
const helpId = computed(() => `${selectId.value}-help`)
const isInvalid = computed(() => props.invalid || Boolean(props.error))
const optionItems = computed(() =>
  extractSelectOptions(slots.default?.() || [])
)
const selectedIndex = computed(() =>
  optionItems.value.findIndex((option) => isSelected(option))
)
const selectedOption = computed(() =>
  selectedIndex.value >= 0 ? optionItems.value[selectedIndex.value] : null
)
const activeOptionId = computed(() =>
  isOpen.value && activeIndex.value >= 0
    ? optionId(activeIndex.value)
    : undefined
)
const describedBy = computed(() => {
  let feedbackId = ''
  if (props.error) feedbackId = errorId.value
  else if (props.help) feedbackId = helpId.value

  return (
    [attrs['aria-describedby'], feedbackId].filter(Boolean).join(' ') ||
    undefined
  )
})

const selectAttrs = computed(() => {
  const forwardedAttrs = { ...attrs }
  delete forwardedAttrs.class
  delete forwardedAttrs.id
  delete forwardedAttrs.title
  delete forwardedAttrs['aria-describedby']
  delete forwardedAttrs['aria-invalid']
  delete forwardedAttrs['aria-label']
  delete forwardedAttrs['aria-labelledby']

  return {
    ...forwardedAttrs,
    id: nativeSelectId.value
  }
})

const rootClasses = computed(() => [
  'space-y-1',
  props.fullWidth ? 'w-full' : 'inline-block',
  attrs.class
])

const selectedValue = computed({
  get: () => props.modelValue,
  set: (value) => {
    emit('update:modelValue', applyModelModifiers(value, props.modelModifiers))
  }
})

const selectClasses = computed(() => {
  const sizeClasses = {
    sm: 'px-2 py-1 pr-8 text-xs',
    md: 'px-3 py-2 pr-10 text-sm',
    lg: 'px-4 py-3 pr-12 text-base'
  }
  const variantClasses = {
    default: [
      'rounded-lg border bg-surface text-theme shadow-sm',
      'transition-colors hover:border-line-strong',
      'focus:border-primary-500 focus:outline-none focus:ring-2',
      'focus:ring-primary-500/20 disabled:bg-surface-sunken',
      'disabled:text-theme-subtle disabled:opacity-50'
    ].join(' '),
    unstyled: [
      'border-0 bg-transparent text-theme shadow-none',
      'focus:outline-none focus:ring-0',
      'disabled:text-theme-subtle disabled:opacity-50'
    ].join(' ')
  }
  const invalidClasses = isInvalid.value
    ? 'border-danger-600 focus:border-danger-600 focus:ring-danger-600/20'
    : 'border-line'
  const spacingClasses =
    props.variant === 'unstyled'
      ? 'py-0 pl-0 pr-8 text-sm'
      : sizeClasses[props.size]

  return [
    'relative flex w-full appearance-none items-center text-left',
    'disabled:cursor-not-allowed',
    spacingClasses,
    variantClasses[props.variant],
    props.variant === 'default' ? invalidClasses : '',
    props.mobileTouch ? 'min-h-11 md:min-h-0' : ''
  ]
    .filter(Boolean)
    .join(' ')
})

const optionSizeClasses = computed(() => {
  const sizeClasses = {
    sm: 'px-2 py-1.5 text-xs',
    md: 'px-3 py-2 text-sm',
    lg: 'px-4 py-2.5 text-base'
  }
  return sizeClasses[props.size]
})

function isSelected(option) {
  return Object.is(option.value, props.modelValue)
}

function optionId(index) {
  return `${listboxId.value}-option-${index}`
}

function optionClasses(option, index) {
  return [
    'relative flex select-none items-center gap-2 rounded-md outline-none',
    optionSizeClasses.value,
    props.mobileTouch ? 'min-h-11 md:min-h-0' : '',
    option.disabled
      ? 'cursor-not-allowed text-theme-subtle opacity-60'
      : 'cursor-pointer text-theme',
    !option.disabled && activeIndex.value === index
      ? 'bg-surface-selected text-theme'
      : '',
    !option.disabled && activeIndex.value !== index
      ? 'hover:bg-surface-sunken'
      : '',
    isSelected(option) ? 'font-medium' : ''
  ]
}

function activateOption(option, index) {
  if (!option.disabled) activeIndex.value = index
}

function toggleMenu() {
  if (isOpen.value) closeMenu()
  else openMenu(1)
}

function openMenu(direction) {
  if (props.disabled || !optionItems.value.length) return

  isOpen.value = true
  if (selectedIndex.value >= 0) {
    activeIndex.value = selectedIndex.value
  } else {
    const startIndex = direction < 0 ? 0 : -1
    activeIndex.value = findNextEnabledOption(
      optionItems.value,
      startIndex,
      direction
    )
  }

  nextTick(() => {
    updateMenuPosition()
    scrollActiveOptionIntoView()
  })
}

function closeMenu() {
  isOpen.value = false
  activeIndex.value = -1
  clearTypeahead()
}

function moveActiveOption(direction) {
  const nextIndex = findNextEnabledOption(
    optionItems.value,
    activeIndex.value,
    direction
  )
  if (nextIndex < 0) return

  activeIndex.value = nextIndex
  nextTick(scrollActiveOptionIntoView)
}

function selectOption(option, index) {
  if (option.disabled) return

  activeIndex.value = index
  const changed = !isSelected(option)
  if (changed) selectedValue.value = option.value
  closeMenu()

  if (changed) {
    nextTick(() => {
      const event = new Event('change', { bubbles: true })
      Object.defineProperty(event, 'target', {
        configurable: true,
        value: nativeSelectRef.value
      })
      handleChange(event)
    })
  }
}

function handleTriggerKeydown(event) {
  if (props.disabled) return

  if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
    event.preventDefault()
    const direction = event.key === 'ArrowDown' ? 1 : -1
    if (!isOpen.value) openMenu(direction)
    else moveActiveOption(direction)
    return
  }

  if (event.key === 'Home' || event.key === 'End') {
    event.preventDefault()
    if (!isOpen.value) openMenu(event.key === 'Home' ? 1 : -1)
    const startIndex = event.key === 'Home' ? -1 : 0
    const direction = event.key === 'Home' ? 1 : -1
    activeIndex.value = findNextEnabledOption(
      optionItems.value,
      startIndex,
      direction
    )
    nextTick(scrollActiveOptionIntoView)
    return
  }

  if (event.key === 'Enter' || event.key === ' ') {
    event.preventDefault()
    if (!isOpen.value) openMenu(1)
    else if (activeIndex.value >= 0) {
      selectOption(optionItems.value[activeIndex.value], activeIndex.value)
    }
    return
  }

  if (event.key === 'Escape' && isOpen.value) {
    event.preventDefault()
    event.stopPropagation()
    closeMenu()
    return
  }

  if (event.key === 'Tab') {
    closeMenu()
    return
  }

  if (
    event.key.length === 1 &&
    !event.altKey &&
    !event.ctrlKey &&
    !event.metaKey
  ) {
    handleTypeahead(event.key)
  }
}

function handleTypeahead(character) {
  typeaheadQuery += character.toLocaleLowerCase()
  window.clearTimeout(typeaheadTimer)
  typeaheadTimer = window.setTimeout(clearTypeahead, 500)

  const startIndex = isOpen.value ? activeIndex.value : selectedIndex.value
  const matchingIndex = findTypeaheadOption(
    optionItems.value,
    typeaheadQuery,
    startIndex
  )
  if (matchingIndex < 0) return

  if (isOpen.value) {
    activeIndex.value = matchingIndex
    nextTick(scrollActiveOptionIntoView)
  } else {
    selectOption(optionItems.value[matchingIndex], matchingIndex)
  }
}

function clearTypeahead() {
  typeaheadQuery = ''
  window.clearTimeout(typeaheadTimer)
  typeaheadTimer = undefined
}

function scrollActiveOptionIntoView() {
  if (!menuRef.value || activeIndex.value < 0) return
  const option = document.getElementById(optionId(activeIndex.value))
  option?.scrollIntoView({ block: 'nearest' })
}

function updateMenuPosition() {
  const trigger = triggerRef.value
  if (!trigger) return

  const rect = trigger.getBoundingClientRect()
  const viewportMargin = 8
  const menuGap = 4
  const maximumHeight = 256
  const availableBelow = window.innerHeight - rect.bottom - menuGap
  const availableAbove = rect.top - menuGap
  const opensAbove = availableBelow < 160 && availableAbove > availableBelow
  const availableHeight = opensAbove ? availableAbove : availableBelow
  const width = Math.min(
    Math.max(props.menuMinWidth, rect.width),
    window.innerWidth - viewportMargin * 2
  )
  const left = Math.max(
    viewportMargin,
    Math.min(rect.left, window.innerWidth - width - viewportMargin)
  )

  menuStyle.value = {
    bottom: opensAbove
      ? `${window.innerHeight - rect.top + menuGap}px`
      : 'auto',
    left: `${left}px`,
    maxHeight: `${Math.max(96, Math.min(maximumHeight, availableHeight))}px`,
    top: opensAbove ? 'auto' : `${rect.bottom + menuGap}px`,
    width: `${width}px`
  }
}

function handleOutsidePointer(event) {
  if (
    rootRef.value?.contains(event.target) ||
    menuRef.value?.contains(event.target)
  ) {
    return
  }
  closeMenu()
}

function handleInvalid(event) {
  event.preventDefault()
  triggerRef.value?.focus()
}

function handleChange(event) {
  emit('change', event)
}

function addOpenListeners() {
  document.addEventListener('pointerdown', handleOutsidePointer)
  window.addEventListener('resize', updateMenuPosition)
  window.addEventListener('scroll', updateMenuPosition, true)
}

function removeOpenListeners() {
  document.removeEventListener('pointerdown', handleOutsidePointer)
  window.removeEventListener('resize', updateMenuPosition)
  window.removeEventListener('scroll', updateMenuPosition, true)
}

watch(isOpen, (open) => {
  if (open) addOpenListeners()
  else removeOpenListeners()
})

watch(
  () => props.disabled,
  (disabled) => {
    if (disabled) closeMenu()
  }
)

onBeforeUnmount(() => {
  removeOpenListeners()
  clearTypeahead()
})
</script>
