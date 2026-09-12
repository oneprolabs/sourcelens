<template>
  <BaseDrawer
    :show="show"
    :title="t('lensAdmin.assistantDetail.title')"
    :subtitle="assistant?.name || ''"
    width="6xl"
    @close="$emit('close')"
  >
    <AssistantDetailView
      v-if="assistant"
      embedded
      :assistant="assistant"
      :lensnode-name="lensnodeName"
      :assistant-type="assistantType"
      @copy-share="$emit('copy-share', $event)"
      @edit="$emit('edit', $event)"
      @restore="$emit('restore', $event)"
    />
  </BaseDrawer>
</template>

<script setup>
import { useI18n } from 'vue-i18n'

import BaseDrawer from '@/components/ui/BaseDrawer.vue'

import AssistantDetailView from './AssistantDetailView.vue'

defineProps({
  show: { type: Boolean, default: false },
  assistant: { type: Object, default: null },
  lensnodeName: { type: String, default: '' },
  assistantType: { type: String, default: '' }
})

defineEmits(['close', 'copy-share', 'edit', 'restore'])

const { t } = useI18n()
</script>
