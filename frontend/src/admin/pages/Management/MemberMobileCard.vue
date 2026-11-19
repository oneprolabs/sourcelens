<template>
  <article class="admin-group-card min-w-0">
    <label class="admin-member-card-selection">
      <input
        type="checkbox"
        :checked="selected"
        :disabled="disabled"
        :aria-label="
          t('common.selectRow', {
            name: member.display_name || member.username || member.email
          })
        "
        @change="select"
      />
    </label>
    <button
      type="button"
      class="admin-group-card-heading"
      @click="$emit('detail')"
    >
      <span class="admin-avatar">{{
        (member.display_name || member.username || member.email).charAt(0)
      }}</span>
      <span class="min-w-0">
        <span class="block break-words text-sm font-semibold text-ink-900">{{
          member.display_name || member.username || member.email
        }}</span>
        <span class="member-email block break-all">{{ member.email }}</span>
      </span>
    </button>
    <dl>
      <div class="admin-group-card-meta">
        <dt>{{ t('management.roles') }}</dt>
        <dd>
          <span
            class="admin-member-role"
            :class="{ 'is-admin': member.role === 'tenant_admin' }"
            >{{ roleLabel }}</span
          >
        </dd>
      </div>
      <div class="admin-group-card-meta">
        <dt>{{ t('management.memberStatus') }}</dt>
        <dd class="admin-member-status">
          <span
            class="h-1.5 w-1.5 rounded-full"
            :class="
              member.status === 'active'
                ? 'admin-status-active'
                : member.status === 'locked'
                  ? 'admin-status-locked'
                  : member.status === 'disabled'
                    ? 'admin-status-disabled'
                    : 'admin-status-invited'
            "
          />{{ statusLabel }}
        </dd>
      </div>
      <div class="admin-group-card-meta admin-member-extra">
        <dt>{{ t('management.memberGroups') }}</dt>
        <dd class="break-words">
          {{
            member.groups?.length
              ? member.groups.map((group) => group.name).join(' / ')
              : '—'
          }}
        </dd>
      </div>
      <div class="admin-group-card-meta">
        <dt>{{ t('management.memberLastLogin') }}</dt>
        <dd>
          <time v-if="member.last_login" :datetime="member.last_login">{{
            lastLogin
          }}</time
          ><span v-else>{{ lastLogin }}</span>
        </dd>
      </div>
      <div class="admin-group-card-meta admin-member-extra">
        <dt>{{ t('management.memberRuns') }}</dt>
        <dd class="font-mono">{{ member.runs_30d ?? 0 }}</dd>
      </div>
    </dl>
    <div class="admin-group-card-actions">
      <RowActionMenu
        :actions="actions"
        :label="`${t('common.moreActions')} · ${member.display_name || member.username || member.email}`"
        @select="$emit('action', $event)"
      />
    </div>
  </article>
</template>

<script setup>
import { useI18n } from 'vue-i18n'
import RowActionMenu from '@/components/ui/RowActionMenu.vue'

const props = defineProps({
  member: { type: Object, required: true },
  selected: Boolean,
  disabled: Boolean,
  roleLabel: { type: String, required: true },
  statusLabel: { type: String, required: true },
  lastLogin: { type: String, required: true },
  actions: { type: Array, required: true }
})
const emit = defineEmits(['select', 'detail', 'action'])
const { t } = useI18n()

function select() {
  if (!props.disabled) emit('select')
}
</script>
