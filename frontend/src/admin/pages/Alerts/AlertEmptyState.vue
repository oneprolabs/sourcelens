<template>
  <div
    class="rounded-lg border border-line bg-surface-sunken px-4 py-16 text-center"
  >
    <svg
      class="mx-auto mb-4 h-12 w-12 text-theme-subtle"
      fill="none"
      stroke="currentColor"
      viewBox="0 0 24 24"
      aria-hidden="true"
    >
      <path
        stroke-linecap="round"
        stroke-linejoin="round"
        stroke-width="2"
        d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"
      />
    </svg>
    <p class="text-sm font-medium text-theme-secondary">{{ text }}</p>
  </div>
</template>

<script setup>
defineProps({ text: { type: String, required: true } })
</script>
