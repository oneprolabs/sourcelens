<template>
  <Teleport to="body">
    <Transition
      enter-active-class="transition duration-200 ease-out"
      enter-from-class="opacity-0"
      enter-to-class="opacity-100"
      leave-active-class="transition duration-150 ease-in"
      leave-from-class="opacity-100"
      leave-to-class="opacity-0"
    >
      <div
        v-if="show"
        ref="dialogRef"
        class="fixed inset-0 z-[70] overflow-y-auto"
        role="dialog"
        aria-modal="true"
        :aria-label="title"
        @click="handleBackdropClick"
      >
        <div class="flex min-h-full items-center justify-center p-4">
          <div
            class="fixed inset-0 bg-ink-950/50 transition-opacity"
            aria-hidden="true"
          />

          <Transition
            enter-active-class="transition duration-200 ease-out"
            enter-from-class="opacity-0 translate-y-4 sm:translate-y-0 sm:scale-95"
            enter-to-class="opacity-100 translate-y-0 sm:scale-100"
            leave-active-class="transition duration-150 ease-in"
            leave-from-class="opacity-100 translate-y-0 sm:scale-100"
            leave-to-class="opacity-0 translate-y-4 sm:translate-y-0 sm:scale-95"
          >
            <div
              v-if="show"
              tabindex="-1"
              class="relative transform overflow-hidden rounded-lg bg-surface text-left shadow-xl transition-all w-full max-h-[90vh] sm:max-h-[90vh] flex flex-col my-4 sm:my-8"
              :class="maxWidthClass"
              @click.stop
            >
              <!-- Header -->
              <div
                class="flex-shrink-0 flex items-start justify-between gap-3 bg-surface px-4 pt-5 pb-3 sm:px-6 sm:pt-6 sm:pb-4 border-b border-line"
              >
                <h3
                  v-if="title"
                  class="text-base font-semibold leading-6 text-theme text-left flex-1 min-w-0"
                >
                  {{ title }}
                </h3>
                <button
                  type="button"
                  class="modal-close-btn inline-flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-md text-theme-subtle transition-colors hover:bg-surface-hover hover:text-theme-secondary focus:outline-none focus:ring-2 focus:ring-primary-500 focus:ring-offset-1"
                  :aria-label="t('common.close')"
                  :title="t('common.close')"
                  @click="$emit('close')"
                >
                  <svg
                    class="h-5 w-5"
                    fill="none"
                    stroke="currentColor"
                    viewBox="0 0 24 24"
                    aria-hidden="true"
                  >
                    <path
                      stroke-linecap="round"
                      stroke-linejoin="round"
                      stroke-width="2"
                      d="M6 18L18 6M6 6l12 12"
                    />
                  </svg>
                </button>
              </div>

              <!-- Scrollable Content -->
              <div
                class="flex-1 overflow-y-auto -webkit-overflow-scrolling-touch min-h-0"
              >
                <div class="bg-surface px-4 py-4 sm:px-6 sm:py-4">
                  <div v-if="icon" class="sm:flex sm:items-start">
                    <div
                      class="mx-auto flex h-12 w-12 flex-shrink-0 items-center justify-center rounded-full sm:mx-0 sm:h-10 sm:w-10"
                      :class="iconClasses"
                    >
                      <component :is="icon" class="h-6 w-6" />
                    </div>

                    <div class="mt-3 text-left sm:ml-4 sm:mt-0 w-full">
                      <slot />
                    </div>
                  </div>
                  <div v-else class="text-left w-full">
                    <slot />
                  </div>
                </div>
              </div>

              <!-- Footer (fixed at bottom) -->
              <div
                v-if="$slots.footer"
                class="bg-surface-sunken px-4 py-3 sm:flex sm:flex-row-reverse sm:px-6 flex-shrink-0 border-t border-line"
              >
                <slot name="footer" />
              </div>
            </div>
          </Transition>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'

import {
  acquireBodyScrollLock,
  isTopDialog,
  registerDialog,
  unregisterDialog,
  releaseBodyScrollLock
} from './dialogScrollLock'

const { t } = useI18n()

const props = defineProps({
  show: {
    type: Boolean,
    default: false
  },
  title: {
    type: String,
    default: ''
  },
  icon: {
    type: [String, Object],
    default: null
  },
  iconType: {
    type: String,
    default: 'info',
    validator: (value) =>
      ['info', 'success', 'warning', 'error'].includes(value)
  },
  closeOnBackdrop: {
    type: Boolean,
    default: true
  },
  maxWidth: {
    type: String,
    default: '2xl',
    validator: (value) =>
      ['sm', 'md', 'lg', 'xl', '2xl', '3xl', '4xl'].includes(value)
  }
})

const emit = defineEmits(['close'])
const dialogRef = ref(null)

const focusableSelector = [
  'a[href]',
  'button:not([disabled])',
  'input:not([disabled])',
  'select:not([disabled])',
  'textarea:not([disabled])',
  '[tabindex]:not([tabindex="-1"])'
].join(',')

let previousFocus = null
let dialogActive = false
const dialogToken = Symbol('base-modal')

const iconClasses = computed(() => {
  const typeClasses = {
    info: 'bg-blue-100 text-blue-600',
    success: 'bg-green-100 text-green-600',
    warning: 'bg-yellow-100 text-yellow-600',
    error: 'bg-red-100 text-red-600'
  }

  return typeClasses[props.iconType]
})

const MAX_WIDTH_CLASSES = {
  sm: 'max-w-sm',
  md: 'max-w-md',
  lg: 'max-w-lg',
  xl: 'max-w-xl',
  '2xl': 'max-w-2xl',
  '3xl': 'max-w-3xl',
  '4xl': 'max-w-4xl'
}

const maxWidthClass = computed(() => MAX_WIDTH_CLASSES[props.maxWidth])

const handleBackdropClick = () => {
  if (props.closeOnBackdrop) {
    emit('close')
  }
}

function getFocusableElements() {
  if (!dialogRef.value) return []
  return [...dialogRef.value.querySelectorAll(focusableSelector)].filter(
    (element) => element.getClientRects().length > 0
  )
}

function handleKeydown(event) {
  if (!props.show || !isTopDialog(dialogToken)) return
  if (event.key === 'Escape') {
    event.preventDefault()
    emit('close')
    return
  }
  if (event.key !== 'Tab') return

  const focusable = getFocusableElements()
  if (focusable.length === 0) {
    event.preventDefault()
    dialogRef.value?.focus()
    return
  }
  const first = focusable[0]
  const last = focusable[focusable.length - 1]
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault()
    last.focus()
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault()
    first.focus()
  }
}

function activateDialog() {
  if (dialogActive) return
  dialogActive = true
  previousFocus = document.activeElement
  acquireBodyScrollLock()
  registerDialog(dialogToken)
  window.addEventListener('keydown', handleKeydown)
  nextTick(() => {
    ;(getFocusableElements()[0] || dialogRef.value)?.focus()
  })
}

function deactivateDialog() {
  if (!dialogActive) return
  dialogActive = false
  window.removeEventListener('keydown', handleKeydown)
  unregisterDialog(dialogToken)
  releaseBodyScrollLock()
  const focusTarget = previousFocus
  previousFocus = null
  nextTick(() => {
    if (focusTarget?.isConnected) focusTarget.focus()
  })
}

watch(
  () => props.show,
  (show) => {
    if (typeof window === 'undefined') return
    if (show) {
      activateDialog()
    } else {
      deactivateDialog()
    }
  },
  { immediate: true }
)

onBeforeUnmount(() => {
  if (typeof window !== 'undefined') deactivateDialog()
})
</script>

<style scoped>
@media (max-width: 767px), (hover: none), (pointer: coarse) {
  .modal-close-btn {
    width: 44px;
    height: 44px;
    min-width: 44px;
    min-height: 44px;
  }
}
</style>
