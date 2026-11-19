<template>
  <BaseDrawer
    :show="show"
    :title="assistant?.name || t('lensAdmin.assistantDetail.title')"
    :subtitle="`${assistant?.slug || ''} · ${assistant?.status === 'archived' ? t('lensAdmin.pages.assistants.archived') : assistant?.is_enabled === false ? t('lensAdmin.enablement.disabled') : t('lensAdmin.enablement.enabled')}`"
    width="6xl"
    panel-class="assistant-drawer assistant-detail-drawer"
    @close="$emit('close')"
  >
    <template #tabs>
      <nav
        class="assistant-detail-tabs"
        :aria-label="t('lensAdmin.assistantDetail.title')"
      >
        <button
          v-for="tab in detailView?.tabs || []"
          :key="tab.id"
          type="button"
          :aria-current="detailView.activeTab === tab.id ? 'page' : undefined"
          @click="detailView.activeTab = tab.id"
        >
          {{ tab.label
          }}<small v-if="tab.count !== undefined">{{ tab.count }}</small>
        </button>
      </nav>
    </template>
    <AssistantDetailView
      v-if="assistant"
      ref="detailView"
      embedded
      :assistant="assistant"
      :lensnode-name="lensnodeName"
      :model-names="modelNames"
      :assistant-type="assistantType"
      @copy-share="$emit('copy-share', $event)"
      @edit="$emit('edit', $event)"
      @restore="$emit('restore', $event)"
    />
    <template #footer>
      <div class="assistant-detail-actions">
        <BaseButton
          v-if="assistant?.status === 'active'"
          :disabled="readonly || busy"
          @click="$emit('edit', assistant)"
          >{{ t('common.edit') }}</BaseButton
        >
        <BaseButton
          v-else
          :disabled="readonly || busy"
          :loading="busy"
          @click="$emit('restore', assistant)"
          >{{ t('lensAdmin.pages.assistants.restore') }}</BaseButton
        >
        <BaseButton variant="outline" @click="$emit('close')">{{
          t('common.close')
        }}</BaseButton>
        <BaseButton
          class="assistant-detail-copy"
          variant="ghost"
          :disabled="
            assistant?.status !== 'active' || assistant?.is_enabled === false
          "
          @click="$emit('copy-share', assistant)"
          >{{ t('lens.share.copyLink') }}</BaseButton
        >
      </div>
    </template>
  </BaseDrawer>
</template>

<script setup>
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'

import BaseButton from '@/components/ui/BaseButton.vue'
import BaseDrawer from '@/components/ui/BaseDrawer.vue'
import { useUserStore } from '@/store/user'

import AssistantDetailView from './AssistantDetailView.vue'

defineProps({
  show: { type: Boolean, default: false },
  assistant: { type: Object, default: null },
  lensnodeName: { type: String, default: '' },
  assistantType: { type: String, default: '' },
  busy: { type: Boolean, default: false },
  modelNames: { type: Object, default: () => ({}) }
})

defineEmits(['close', 'copy-share', 'edit', 'restore'])

const { t } = useI18n()
const detailView = ref(null)
const userStore = useUserStore()
const readonly = computed(() => userStore.delegation?.mode === 'readonly')
</script>
