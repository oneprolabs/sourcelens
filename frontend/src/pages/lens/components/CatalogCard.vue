<template>
  <article class="catalog-row">
    <button
      type="button"
      class="catalog-row-open"
      :aria-label="`${t('common.viewDetails')}: ${row.name}`"
      @click="$emit('detail', row)"
    />
    <CatalogTile :name="row.name" />
    <div class="catalog-row-copy">
      <h3>{{ row.name }}</h3>
      <p v-if="row.description && (!compact || row.kind === 'connector')">
        {{ row.description }}
      </p>
      <p v-if="row.kind !== 'connector'">
        {{ t(row.is_official ? 'catalog.official' : 'catalog.tenantOwned') }}
        <span v-if="row.version"> · {{ row.version }}</span>
        · {{ t('catalog.boundCount', { count: row.bound_count || 0 }) }}
      </p>
    </div>
    <button
      v-if="row.kind === 'connector' || !compact"
      type="button"
      class="catalog-row-action"
      :class="{ 'is-enabled': row.tenant_enabled }"
      :disabled="disabled || loading"
      :aria-busy="loading"
      :aria-label="`${actionLabel}: ${row.name}`"
      :title="actionLabel"
      @click="
        $emit(
          row.kind === 'connector'
            ? 'connect'
            : row.is_official
              ? 'toggle'
              : 'manage',
          row
        )
      "
    >
      <component
        :is="
          loading
            ? LoaderCircle
            : row.kind === 'connector'
              ? Plus
              : !row.is_official
                ? Pencil
                : row.tenant_enabled
                  ? Check
                  : Plus
        "
        :size="16"
        :class="{ 'animate-spin': loading }"
        aria-hidden="true"
      />
    </button>
  </article>
</template>

<script setup>
import { Check, LoaderCircle, Pencil, Plus } from '@lucide/vue'
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import CatalogTile from './CatalogTile.vue'

const props = defineProps({
  row: { type: Object, required: true },
  loading: Boolean,
  disabled: Boolean,
  compact: Boolean
})
defineEmits(['detail', 'toggle', 'connect', 'manage'])
const { t } = useI18n()
const actionLabel = computed(() =>
  t(
    props.row.kind === 'connector'
      ? 'catalog.configureConnection'
      : !props.row.is_official
        ? 'common.edit'
        : props.row.tenant_enabled
          ? 'catalog.disable'
          : 'catalog.enable'
  )
)
</script>
