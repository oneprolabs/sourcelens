<template>
  <BaseDrawer
    :show="show"
    :title="t('lensAdmin.datasourceDetail.title')"
    :subtitle="datasource?.name || ''"
    width="5xl"
    @close="$emit('close')"
  >
    <template v-if="datasource" #tabs>
      <div class="border-b border-line bg-surface">
        <div class="flex gap-5 px-6">
          <button
            type="button"
            class="detail-tab"
            :class="activeTab === 'basic' ? 'detail-tab-active' : ''"
            @click="activeTab = 'basic'"
          >
            {{ t('lensAdmin.datasourceDetail.tabs.basic') }}
          </button>
          <button
            type="button"
            class="detail-tab"
            :class="activeTab === 'details' ? 'detail-tab-active' : ''"
            @click="activeTab = 'details'"
          >
            {{ t('lensAdmin.datasourceDetail.tabs.details') }}
          </button>
          <button
            type="button"
            class="detail-tab"
            :class="activeTab === 'files' ? 'detail-tab-active' : ''"
            @click="activeTab = 'files'"
          >
            {{ t('lensAdmin.datasourceDetail.tabs.files') }}
          </button>
        </div>
      </div>
    </template>

    <div v-if="datasource">
      <div v-show="activeTab === 'basic'" class="space-y-4">
        <DrawerSection
          :title="t('lensAdmin.datasourceDetail.basicInfo')"
          spacing="none"
          class="datasource-overview-block rounded-xl border border-line bg-surface p-4"
        >
          <template #actions
            ><StatusBadge :status="datasource.status"
          /></template>
          <dl
            class="mt-3 grid grid-cols-2 gap-px overflow-hidden rounded-lg border border-line bg-line"
          >
            <div
              v-for="item in datasourceOverviewDetails"
              :key="item.label"
              class="min-w-0 bg-surface-sunken px-3 py-2.5"
              :class="item.wide ? 'col-span-2' : ''"
            >
              <dt class="text-[11px] font-medium text-ink-500">
                {{ item.label }}
              </dt>
              <dd
                class="mt-1 truncate text-sm font-medium text-ink-900"
                :class="item.mono ? 'font-mono text-xs' : ''"
                :title="item.value"
              >
                {{ item.value }}
              </dd>
            </div>
          </dl>
          <div
            v-if="datasource.deployments?.length"
            class="mt-4 rounded-lg border border-line bg-surface-sunken p-3"
          >
            <div class="mb-2 text-xs font-semibold text-ink-700">
              {{ t('lensAdmin.fields.lensnode') }}
            </div>
            <div class="space-y-2">
              <div
                v-for="deployment in datasource.deployments"
                :key="deployment.uuid"
                class="grid grid-cols-2 gap-3 rounded-md border border-line bg-surface px-3 py-2"
              >
                <div class="min-w-0">
                  <div class="text-[11px] text-ink-500">
                    {{ t('lensAdmin.fields.lensnode') }}
                  </div>
                  <div class="truncate text-sm font-medium text-ink-900">
                    {{ deployment.lensnode_name || deployment.lensnode_uuid }}
                  </div>
                </div>
                <div class="min-w-0">
                  <div class="text-[11px] text-ink-500">
                    {{ t('lensAdmin.fields.targetPath') }}
                  </div>
                  <div class="truncate font-mono text-xs text-ink-700">
                    {{ deployment.target_path || '-' }}
                  </div>
                </div>
              </div>
            </div>
          </div>
        </DrawerSection>

        <DrawerSection
          :title="t('lensAdmin.datasourceDetail.storage.title')"
          class="datasource-storage-block rounded-xl border border-line bg-surface p-4"
        >
          <dl v-if="datasourceStorageUsage" class="mt-3 grid grid-cols-3 gap-2">
            <div
              v-for="item in datasourceStorageUsage"
              :key="item.label"
              class="min-w-0 rounded-lg bg-surface-sunken px-3 py-2.5"
            >
              <dt class="text-[11px] font-medium text-ink-500">
                {{ item.label }}
              </dt>
              <dd class="mt-1 truncate text-sm font-medium text-ink-900">
                {{ item.value }}
              </dd>
            </div>
          </dl>
          <p v-else class="mt-3 text-sm text-ink-500">
            {{ t('lensAdmin.datasourceDetail.storage.notMeasured') }}
          </p>
        </DrawerSection>

        <section
          v-if="isUploadDatasource && originalUploadFiles.length"
          class="datasource-resource-block rounded-xl border border-line bg-surface p-4"
        >
          <h3 class="text-sm font-semibold text-ink-900">
            {{ t('lensAdmin.datasourceDetail.originalFiles') }}
          </h3>
          <div class="mt-3 overflow-hidden rounded-lg border border-line">
            <div
              class="grid grid-cols-[minmax(0,1fr)_8rem_5rem_6rem] gap-3 bg-surface-sunken px-3 py-2 text-xs font-semibold text-ink-600"
            >
              <span>{{ t('lensAdmin.datasourceDetail.fileName') }}</span>
              <span>{{ t('lensAdmin.datasourceDetail.uploadedAt') }}</span>
              <span class="text-right">{{
                t('lensAdmin.datasourceDetail.fileSize')
              }}</span>
              <span class="text-right">{{
                t('lensAdmin.datasourceDetail.uploadStatus.label')
              }}</span>
            </div>
            <div
              v-for="file in originalUploadFiles"
              :key="file.name"
              class="grid grid-cols-[minmax(0,1fr)_8rem_5rem_6rem] items-center gap-3 border-t border-line px-3 py-2 text-sm"
            >
              <span class="min-w-0 truncate text-ink-800" :title="file.name">{{
                file.name
              }}</span>
              <span class="text-xs text-ink-500">{{
                formatDate(file.uploadedAt)
              }}</span>
              <span class="text-right text-xs text-ink-500">{{
                formatFileSize(file.size)
              }}</span>
              <span class="text-right">
                <span
                  class="inline-flex rounded border px-1.5 py-0.5 text-[11px] font-medium"
                  :class="UPLOAD_STATUS_CLASS[file.status]"
                >
                  {{
                    t(`lensAdmin.datasourceDetail.uploadStatus.${file.status}`)
                  }}
                </span>
              </span>
            </div>
          </div>
        </section>

        <section
          v-if="
            !isUploadDatasource &&
            (datasourceResourceDetails.length ||
              organizationRepositories.length)
          "
          class="datasource-resource-block rounded-xl border border-line bg-surface p-4"
        >
          <h3 class="text-sm font-semibold text-ink-900">
            {{ t('lensAdmin.datasourceDetail.resourceAndTarget') }}
          </h3>
          <dl
            class="mt-3 grid grid-cols-2 gap-px overflow-hidden rounded-lg border border-line bg-line"
          >
            <div
              v-for="item in datasourceResourceDetails"
              :key="item.label"
              class="min-w-0 bg-surface-sunken px-3 py-2.5"
              :class="item.wide ? 'col-span-2' : ''"
            >
              <dt class="text-[11px] font-medium text-ink-500">
                {{ item.label }}
              </dt>
              <dd
                class="mt-1 break-words text-sm font-medium text-ink-900"
                :class="item.mono ? 'font-mono text-xs' : ''"
              >
                <a
                  v-if="item.href"
                  class="line-clamp-2 break-all text-brand-600 hover:text-brand-700"
                  :href="item.href"
                  :title="item.value"
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  {{ item.value }}
                </a>
                <template v-else>{{ item.value }}</template>
              </dd>
            </div>
          </dl>
          <div v-if="organizationRepositories.length" class="mt-3 space-y-2">
            <div
              v-for="repo in organizationRepositories"
              :key="repo.repo_url || repo.name || repo.path"
              class="rounded-lg border border-line bg-surface-sunken px-3 py-2"
            >
              <div class="flex min-w-0 items-center gap-2">
                <PluginIcon
                  :plugin-key="datasource.plugin_key"
                  :src="pluginIconUrls[datasource.plugin_key]"
                  :label="datasource.plugin_key || datasource.source_type"
                />
                <span
                  class="min-w-0 flex-1 truncate text-xs font-medium text-ink-800"
                  :title="repo.name || repo.path || repo.repo_url"
                >
                  {{ repo.name || repo.path || repo.repo_url }}
                </span>
                <span
                  v-if="repo.branch"
                  class="max-w-40 shrink-0 truncate rounded border border-line bg-surface px-1.5 py-0.5 font-mono text-[11px] text-ink-500"
                  :title="repo.branch"
                >
                  {{ repo.branch }}
                </span>
              </div>
              <a
                v-if="repo.repo_url"
                class="mt-1 block truncate font-mono text-xs text-brand-600 hover:text-brand-700"
                :href="repo.repo_url"
                :title="repo.repo_url"
                target="_blank"
                rel="noopener noreferrer"
              >
                {{ repo.repo_url }}
              </a>
            </div>
          </div>
        </section>

        <DrawerSection
          v-if="isSyncableDatasource"
          :title="t('lensAdmin.datasourceDetail.sync')"
          class="datasource-sync-block rounded-xl border border-line bg-surface p-4"
        >
          <dl class="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-3">
            <div
              v-for="item in datasourceSyncDetails"
              :key="item.label"
              class="min-w-0 rounded-lg bg-surface-sunken px-3 py-2.5"
              :class="item.wide ? 'col-span-2 sm:col-span-3' : ''"
            >
              <dt class="text-[11px] font-medium text-ink-500">
                {{ item.label }}
              </dt>
              <dd
                class="mt-1 truncate text-sm font-medium text-ink-900"
                :class="item.mono ? 'font-mono text-xs' : ''"
                :title="item.value"
              >
                {{ item.value }}
              </dd>
            </div>
          </dl>
          <div
            v-if="datasourceSyncError"
            class="mt-3 rounded-lg border border-danger-200 bg-danger-50 px-3 py-2.5"
          >
            <p class="text-[11px] font-medium text-danger-600">
              {{ t('lensAdmin.datasourceDetail.lastError') }}
            </p>
            <p class="mt-1 break-words font-mono text-xs text-danger-700">
              {{ datasourceSyncError }}
            </p>
          </div>
        </DrawerSection>

        <DrawerSection
          v-if="isSyncableDatasource"
          :title="t('lensAdmin.datasourceDetail.retrieval')"
          class="datasource-retrieval-block rounded-xl border border-line bg-surface p-4"
        >
          <div class="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2">
            <article
              v-for="group in datasourceRetrievalGroups"
              :key="group.key"
              class="rounded-lg bg-surface-sunken p-3"
              :class="group.wide ? 'sm:col-span-2' : ''"
            >
              <h4 class="text-xs font-semibold text-ink-800">
                {{ group.title }}
              </h4>
              <dl
                class="mt-3 grid grid-cols-2 gap-x-4 gap-y-3"
                :class="group.wide ? 'sm:grid-cols-4' : ''"
              >
                <div
                  v-for="item in group.items"
                  :key="item.label"
                  class="min-w-0"
                  :class="item.wide ? 'col-span-2' : ''"
                >
                  <dt class="text-[11px] leading-4 text-ink-500">
                    {{ item.label }}
                  </dt>
                  <dd
                    class="mt-1 break-words text-xs font-medium text-ink-800"
                    :class="[
                      item.mono ? 'font-mono' : '',
                      item.enabled === true ? 'text-success-700' : '',
                      item.enabled === false ? 'text-ink-500' : ''
                    ]"
                  >
                    {{ item.value }}
                  </dd>
                </div>
              </dl>
            </article>
          </div>
        </DrawerSection>
      </div>
      <div v-show="activeTab === 'details'" class="space-y-6">
        <div
          class="relative overflow-hidden rounded-lg border border-line bg-surface shadow-sm"
        >
          <ul class="divide-y divide-line bg-surface">
            <li
              class="hidden grid-cols-[110px_minmax(0,1.4fr)_150px_110px_110px_90px_24px] items-center gap-3 bg-surface-sunken px-4 py-2 text-xs font-semibold uppercase tracking-wider text-ink-600 sm:grid"
            >
              <span class="text-left">{{
                t('lensAdmin.datasourceDetail.details.colTaskType')
              }}</span>
              <span class="text-left">{{
                t('lensAdmin.datasourceDetail.details.colFileName')
              }}</span>
              <span>{{
                t('lensAdmin.datasourceDetail.details.colStartedAt')
              }}</span>
              <span>{{
                t('lensAdmin.datasourceDetail.details.colStatus')
              }}</span>
              <span>{{
                t('lensAdmin.datasourceDetail.details.colTrigger')
              }}</span>
              <span>{{
                t('lensAdmin.datasourceDetail.details.colDuration')
              }}</span>
              <span></span>
            </li>
            <li
              v-if="tasksLoading"
              class="px-4 py-6 text-center text-sm text-ink-500"
            >
              {{ t('common.loading') }}
            </li>
            <li
              v-else-if="!tasks.length"
              class="px-4 py-6 text-center text-sm text-ink-500"
            >
              {{ t('common.noData') }}
            </li>
            <template v-else>
              <li
                v-for="task in tasks"
                :key="task.id"
                class="transition-colors duration-150"
                :class="
                  expandedTaskId === task.id
                    ? 'border-primary-500 bg-primary-50'
                    : 'border-transparent hover:bg-surface-sunken'
                "
              >
                <div
                  class="px-3 py-3 sm:hidden"
                  :class="
                    tasksLoading
                      ? 'cursor-not-allowed opacity-60'
                      : 'cursor-pointer'
                  "
                  @click="toggleTaskExpand(task)"
                >
                  <div class="flex items-start justify-between gap-3">
                    <span class="min-w-0 flex-1">
                      <span
                        class="block truncate text-sm font-medium text-ink-900"
                      >
                        {{ taskTypeLabel(task) }}
                      </span>
                      <span
                        v-if="task.metadata?.filename"
                        class="block truncate text-xs text-ink-500"
                        :title="task.metadata.filename"
                        >{{ task.metadata.filename }}</span
                      >
                    </span>
                    <div class="flex shrink-0 items-center gap-2">
                      <StatusBadge :status="mapTaskStatus(task.status)" />
                      <span
                        class="text-ink-400 transition-transform duration-150"
                        :class="expandedTaskId === task.id ? 'rotate-90' : ''"
                        >▸</span
                      >
                    </div>
                  </div>
                  <dl class="mt-3 grid grid-cols-3 gap-2">
                    <div class="min-w-0">
                      <dt class="text-[11px] text-ink-500">
                        {{
                          t('lensAdmin.datasourceDetail.details.colStartedAt')
                        }}
                      </dt>
                      <dd class="mt-0.5 truncate text-xs text-ink-800">
                        {{ formatDateTime(task.started_at) }}
                      </dd>
                    </div>
                    <div class="min-w-0">
                      <dt class="text-[11px] text-ink-500">
                        {{ t('lensAdmin.datasourceDetail.details.colTrigger') }}
                      </dt>
                      <dd class="mt-0.5 truncate text-xs text-ink-800">
                        {{ formatTrigger(task) }}
                      </dd>
                    </div>
                    <div class="min-w-0">
                      <dt class="text-[11px] text-ink-500">
                        {{
                          t('lensAdmin.datasourceDetail.details.colDuration')
                        }}
                      </dt>
                      <dd class="mt-0.5 truncate text-xs text-ink-800">
                        {{ formatDuration(task.duration) }}
                      </dd>
                    </div>
                  </dl>
                </div>
                <div
                  class="hidden grid-cols-[110px_minmax(0,1.4fr)_150px_110px_110px_90px_24px] items-center gap-3 px-4 py-2 text-sm sm:grid"
                  :class="
                    tasksLoading
                      ? 'cursor-not-allowed opacity-60'
                      : 'cursor-pointer'
                  "
                  @click="toggleTaskExpand(task)"
                >
                  <span class="truncate text-left text-ink-700">
                    {{ taskTypeLabel(task) }}
                  </span>
                  <span
                    class="truncate text-left text-ink-700"
                    :title="task.metadata?.filename || ''"
                  >
                    {{ task.metadata?.filename || emptyValue }}
                  </span>
                  <span
                    class="whitespace-nowrap text-center text-sm font-medium text-ink-900"
                  >
                    {{ formatDate(task.started_at) }}
                  </span>
                  <div class="flex justify-center">
                    <StatusBadge :status="mapTaskStatus(task.status)" />
                  </div>
                  <span
                    class="whitespace-nowrap text-center text-sm text-ink-500"
                  >
                    {{ formatTrigger(task) }}
                  </span>
                  <span
                    class="whitespace-nowrap text-center text-sm text-ink-500"
                  >
                    {{ formatDuration(task.duration) }}
                  </span>
                  <span
                    class="text-center text-ink-400 transition-transform duration-150"
                    :class="expandedTaskId === task.id ? 'rotate-90' : ''"
                    >▸</span
                  >
                </div>
                <div
                  v-if="expandedTaskId === task.id"
                  class="border-t border-line bg-surface-sunken px-4 py-3"
                >
                  <div
                    v-if="expandedTaskDetailLoading"
                    class="py-2 text-center text-xs text-ink-500"
                  >
                    {{ t('common.loading') }}
                  </div>
                  <TaskSummaryCard v-else :task="expandedTask" />
                </div>
              </li>
            </template>
          </ul>
        </div>

        <div
          v-if="totalCount > 0"
          class="mt-4 flex flex-wrap items-center justify-between gap-2 border-t border-line pt-4"
        >
          <p class="text-sm text-ink-500">
            {{ t('common.pagination.showing', paginationShowing) }}
          </p>
          <div class="flex items-center gap-2">
            <BaseButton
              variant="outline"
              size="sm"
              :disabled="tasksLoading || currentPage <= 1"
              :title="t('common.pagination.previous')"
              @click="goPrevPage"
            >
              <svg
                class="h-4 w-4"
                fill="none"
                stroke="currentColor"
                viewBox="0 0 24 24"
              >
                <path
                  stroke-linecap="round"
                  stroke-linejoin="round"
                  stroke-width="2"
                  d="M15 19l-7-7 7-7"
                />
              </svg>
              <span class="sr-only">{{ t('common.pagination.previous') }}</span>
            </BaseButton>
            <BaseButton
              variant="outline"
              size="sm"
              :disabled="tasksLoading || currentPage >= totalPages"
              :title="t('common.pagination.next')"
              @click="goNextPage"
            >
              <svg
                class="h-4 w-4"
                fill="none"
                stroke="currentColor"
                viewBox="0 0 24 24"
              >
                <path
                  stroke-linecap="round"
                  stroke-linejoin="round"
                  stroke-width="2"
                  d="M9 5l7 7-7 7"
                />
              </svg>
              <span class="sr-only">{{ t('common.pagination.next') }}</span>
            </BaseButton>
          </div>
        </div>
      </div>
      <div v-show="activeTab === 'files'" class="space-y-4">
        <div class="ml-auto flex flex-wrap gap-2">
          <input
            v-model="fileQuery"
            class="min-w-48 flex-1 rounded-lg border border-line bg-surface px-3 py-2 text-sm text-ink-900 outline-none placeholder:text-ink-400 focus:border-primary-500"
            :placeholder="
              t('lensAdmin.datasourceDetail.files.searchPlaceholder')
            "
            type="search"
            @keyup.enter="searchFiles"
          />
          <BaseSelect
            v-model="fileSyncStatus"
            class="text-ink-700"
            @change="searchFiles"
          >
            <option value="">
              {{ t('lensAdmin.datasourceDetail.files.allSync') }}
            </option>
            <option value="synced">
              {{ t('lensAdmin.datasourceDetail.files.syncSynced') }}
            </option>
            <option value="missing">
              {{ t('lensAdmin.datasourceDetail.files.syncMissing') }}
            </option>
            <option value="failed">
              {{ t('lensAdmin.datasourceDetail.files.syncFailed') }}
            </option>
          </BaseSelect>
          <BaseSelect
            v-model="fileConversionStatus"
            class="text-ink-700"
            @change="searchFiles"
          >
            <option value="">
              {{ t('lensAdmin.datasourceDetail.files.allConversion') }}
            </option>
            <option value="success">
              {{ t('lensAdmin.datasourceDetail.files.conversionSuccess') }}
            </option>
            <option value="failed">
              {{ t('lensAdmin.datasourceDetail.files.conversionFailed') }}
            </option>
            <option value="skipped">
              {{ t('lensAdmin.datasourceDetail.files.conversionSkipped') }}
            </option>
            <option value="not_converted">
              {{ t('lensAdmin.datasourceDetail.files.notConverted') }}
            </option>
          </BaseSelect>
        </div>
        <div class="overflow-hidden rounded-lg border border-line bg-surface">
          <div
            class="hidden grid-cols-[minmax(0,1fr)_90px_110px_110px] gap-3 bg-surface-sunken px-4 py-2 text-xs font-semibold uppercase tracking-wider text-ink-600 sm:grid"
          >
            <span>{{ t('lensAdmin.datasourceDetail.files.path') }}</span>
            <span>{{ t('lensAdmin.datasourceDetail.files.type') }}</span>
            <span>{{ t('lensAdmin.datasourceDetail.files.syncStatus') }}</span>
            <span>{{
              t('lensAdmin.datasourceDetail.files.conversionStatus')
            }}</span>
          </div>
          <div
            v-if="filesLoading && !fileNodes.length"
            class="px-4 py-8 text-center text-sm text-ink-500"
          >
            {{ t('common.loading') }}
          </div>
          <div
            v-else-if="filesError"
            class="px-4 py-8 text-center text-sm text-danger-600"
          >
            {{ filesError }}
          </div>
          <div
            v-else-if="!fileNodes.length"
            class="px-4 py-8 text-center text-sm text-ink-500"
          >
            {{ t('lensAdmin.datasourceDetail.files.empty') }}
          </div>
          <template v-else>
            <DataSourceFileTree
              :nodes="fileNodes"
              :aria-label="t('lensAdmin.datasourceDetail.tabs.files')"
              :on-toggle="toggleFileNode"
              :on-load-more="loadMoreFileNode"
            />
            <div v-if="filesHasMore" class="px-4 py-3 text-center">
              <BaseButton
                variant="outline"
                size="sm"
                :loading="filesLoading"
                @click="loadMoreRootFiles"
              >
                {{ t('common.loadMore') }}
              </BaseButton>
            </div>
          </template>
        </div>
        <div
          v-if="fileNodes.length && !filesLoading && !filesHasMore"
          class="text-center text-xs text-ink-500"
        >
          {{ filesCount ? `${fileNodes.length} / ${filesCount}` : '' }}
        </div>
      </div>
    </div>
    <div v-else class="py-12 text-center text-sm text-ink-500">
      {{ t('lensAdmin.datasourceDetail.selectHint') }}
    </div>
    <template v-if="datasource" #footer>
      <div class="flex flex-wrap items-center justify-between gap-2">
        <BaseButton
          variant="outline"
          @click="$emit('toggle-enabled', datasource)"
        >
          {{
            datasource.status === 'active'
              ? t('lensAdmin.actions.disableDatasource')
              : t('lensAdmin.actions.enableDatasource')
          }}
        </BaseButton>
        <div class="ml-auto flex flex-wrap gap-2">
          <BaseButton
            v-if="isSyncableDatasource && isDataSourceSyncing(datasource)"
            variant="danger"
            @click="$emit('cancel-sync', datasource)"
            >{{ t('lensAdmin.actions.cancelSync') }}</BaseButton
          >
          <BaseButton
            v-else-if="isSyncableDatasource"
            variant="outline"
            :disabled="datasource.status !== 'active'"
            @click="$emit('sync', datasource)"
            >{{ t('lensAdmin.actions.sync') }}</BaseButton
          >
          <BaseButton
            variant="outline"
            :disabled="datasource.status !== 'active'"
            @click="$emit('reprocess', datasource)"
            >{{ t('lensAdmin.actions.reprocess') }}</BaseButton
          >
          <BaseButton variant="primary" @click="$emit('edit', datasource)">
            {{ t('common.edit') }}
          </BaseButton>
        </div>
      </div>
    </template>
  </BaseDrawer>
</template>

<script setup>
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { format } from 'date-fns'

import api from '@/api'
import { listDataSourceSyncTasks } from '@/api/lens'
import { llmAdminApi } from '@/admin/api/llmAdmin'
import { taskManagementApi } from '@/admin/api/taskManagement'
import { extractErrorMessage, extractResponseData } from '@/utils/api'
import { lensNodeErrorMessage } from '@/utils/lensNodeErrors'
import { formatDuration } from '@/utils/formatting'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseSelect from '@/components/ui/BaseSelect.vue'
import PluginIcon from '@/components/ui/PluginIcon.vue'
import BaseDrawer from '@/components/ui/BaseDrawer.vue'
import DrawerSection from '@/components/ui/DrawerSection.vue'
import StatusBadge from '@/components/ui/StatusBadge.vue'
import TaskSummaryCard from '@/components/task-management/TaskSummaryCard.vue'

import {
  EMPTY_VALUE as emptyValue,
  compactUuid,
  formatLLMConfigLabel,
  normalizeList
} from './adminHelpers'
import {
  dataSourceBranch,
  dataSourceRepositories,
  dataSourceRepositoryUrl,
  isOrganizationDataSource,
  isDataSourceSyncing,
  latestUploadTasksByFilename
} from './datasourceHelpers'
import DataSourceFileTree from './components/DataSourceFileTree.vue'
import { buildDataSourceFileTree } from './dataSourceFileTree'
import { useShortDateTime } from './useShortDateTime'

const props = defineProps({
  show: { type: Boolean, default: false },
  datasource: { type: Object, default: null },
  pluginIconUrls: { type: Object, default: () => ({}) },
  lensnodes: { type: Array, default: () => [] }
})

defineEmits([
  'cancel-sync',
  'close',
  'edit',
  'reprocess',
  'sync',
  'toggle-enabled',
  'upload'
])

const { t } = useI18n()
const formatDateTime = useShortDateTime()
const activeTab = ref('basic')
const isSyncableDatasource = computed(
  () => !['managed_workspace', 'upload'].includes(props.datasource?.source_type)
)
const isUploadDatasource = computed(
  () =>
    props.datasource?.source_type === 'upload' ||
    props.datasource?.plugin_key === 'file_upload'
)
const UPLOAD_STATUS_CLASS = {
  uploading: 'border-warning-200 bg-warning-50 text-warning-700',
  processed: 'border-success-200 bg-success-50 text-success-700'
}

function uploadFileStatus(task) {
  const status = String(task?.status || '').toUpperCase()
  if (PROCESSING_STATUSES.has(status)) return 'uploading'
  return 'processed'
}

const originalUploadFiles = computed(() =>
  [...latestUploadTasksByFilename(uploadTasks.value).values()].map((task) => {
    const metadata = task.metadata || {}
    return {
      name: metadata.filename,
      size: Number(metadata.byte_size) || 0,
      uploadedAt: task.created_at,
      status: uploadFileStatus(task)
    }
  })
)

function formatFileSize(bytes) {
  if (!bytes) return '-'
  return `${new Intl.NumberFormat().format(Math.ceil(bytes / 1024))} KB`
}

function formatBytes(value) {
  const bytes = Number(value)
  if (!Number.isFinite(bytes) || bytes < 0) return emptyValue
  if (bytes < 1024) return `${bytes} B`
  const units = ['KB', 'MB', 'GB', 'TB']
  let size = bytes
  let unit = 'B'
  for (const candidate of units) {
    size /= 1024
    unit = candidate
    if (size < 1024 || candidate === 'TB') break
  }
  return `${size.toFixed(size >= 10 ? 0 : 1)} ${unit}`
}

const tasks = ref([])
const tasksLoading = ref(false)
const currentPage = ref(1)
const totalCount = ref(0)
const totalPages = ref(1)
const pageSize = 10
const processingRefreshTimer = ref(null)
const processingRefreshInFlight = ref(false)
const tasksLoadInFlight = ref(false)
const taskRequestSeq = ref(0)
const taskListContextKey = ref('')

const uploadTasks = ref([])
const uploadTasksRequestSeq = ref(0)

const fileNodes = ref([])
const searchFileEntries = ref([])
const filesLoading = ref(false)
const filesError = ref('')
const filesCount = ref(0)
const filePage = ref(1)
const filesHasMore = ref(false)
const filesRootDirectory = ref('')
const fileQuery = ref('')
const fileSyncStatus = ref('')
const fileConversionStatus = ref('')
const fileListContextKey = ref('')
const fileRequestSeq = ref(0)

const FILE_PAGE_SIZE = 100

const expandedTaskId = ref(null)
const expandedTask = ref(null)
const expandedTaskDetailLoading = ref(false)
const llmConfigOptions = ref([])
const llmConfigLoaded = ref(false)

const PROCESSING_STATUSES = new Set(['PENDING', 'STARTED', 'RETRY'])
const TASK_METADATA_FIELDS = [
  'datasource_uuid',
  'trigger',
  'progress_percent',
  'phase',
  'overall_progress_percent',
  'phase_progress',
  'progress_counts',
  'last_substantive_progress_at',
  'progress_step',
  'progress_message',
  'sync_summary',
  'source_type',
  'target_path',
  'filename',
  'byte_size',
  'is_latest_version',
  'deleted',
  'duplicate',
  'error'
].join(',')

const paginationShowing = computed(() => ({
  from: (currentPage.value - 1) * pageSize + 1,
  to: Math.min(currentPage.value * pageSize, totalCount.value),
  total: totalCount.value
}))

function mapTaskStatus(status) {
  const m = {
    PENDING: 'pending',
    STARTED: 'processing',
    SUCCESS: 'success',
    FAILURE: 'failed',
    RETRY: 'processing',
    REVOKED: 'cancelled'
  }
  return m[status] || (status && status.toLowerCase()) || 'pending'
}

function isProcessingStatus(status) {
  return PROCESSING_STATUSES.has(String(status || '').toUpperCase())
}

function formatDate(val) {
  if (!val) return '-'
  try {
    return format(new Date(val), 'yyyy-MM-dd HH:mm')
  } catch {
    return val
  }
}

function resetTaskList() {
  tasks.value = []
  totalCount.value = 0
  totalPages.value = 1
  expandedTaskId.value = null
  expandedTask.value = null
}

function resetFileList() {
  fileNodes.value = []
  searchFileEntries.value = []
  filesCount.value = 0
  filePage.value = 1
  filesHasMore.value = false
  filesRootDirectory.value = ''
  filesError.value = ''
}

const fileSearchActive = computed(() =>
  Boolean(
    fileQuery.value.trim() || fileSyncStatus.value || fileConversionStatus.value
  )
)

function mapFileNode(entry) {
  if (entry?.type === 'directory') {
    return {
      type: 'directory',
      name: entry.name || '',
      path: entry.path || '',
      children: null,
      loaded: false,
      loading: false,
      hasMore: false,
      page: 0,
      error: ''
    }
  }
  return {
    type: 'file',
    name: entry?.name || '',
    path: entry?.path || '',
    file: entry
  }
}

async function fetchFileEntries({ directory = '', page = 1 } = {}) {
  const uuid = props.datasource?.uuid
  const res = await api.get(`/lens/admin/datasources/${uuid}/files/`, {
    params: {
      page,
      page_size: FILE_PAGE_SIZE,
      directory,
      query: fileQuery.value,
      sync_status: fileSyncStatus.value,
      conversion_status: fileConversionStatus.value
    }
  })
  const data = extractResponseData(res) || {}
  return {
    results: Array.isArray(data.results) ? data.results : [],
    count: Number(data.count) || 0
  }
}

async function loadFiles() {
  const requestSeq = fileRequestSeq.value + 1
  fileRequestSeq.value = requestSeq
  const uuid = props.datasource?.uuid
  if (!uuid) {
    resetFileList()
    return
  }
  filesLoading.value = true
  filesError.value = ''
  try {
    const rootPage = await fetchFileEntries({ directory: '', page: 1 })
    if (
      requestSeq !== fileRequestSeq.value ||
      uuid !== props.datasource?.uuid
    ) {
      return
    }
    let directory = ''
    let results = rootPage.results
    let count = rootPage.count
    if (
      !fileSearchActive.value &&
      count === 1 &&
      results.length === 1 &&
      results[0]?.type === 'directory'
    ) {
      const nestedPage = await fetchFileEntries({
        directory: results[0].path,
        page: 1
      })
      if (
        requestSeq !== fileRequestSeq.value ||
        uuid !== props.datasource?.uuid
      ) {
        return
      }
      directory = results[0].path
      results = nestedPage.results
      count = nestedPage.count
    }
    filesRootDirectory.value = directory
    if (fileSearchActive.value) {
      searchFileEntries.value = results
      fileNodes.value = buildDataSourceFileTree(results)
    } else {
      fileNodes.value = results.map(mapFileNode)
    }
    filesCount.value = count
    filePage.value = 1
    filesHasMore.value = count > results.length
  } catch (error) {
    if (
      requestSeq !== fileRequestSeq.value ||
      uuid !== props.datasource?.uuid
    ) {
      return
    }
    resetFileList()
    filesError.value = extractErrorMessage(error, t('common.error'))
  } finally {
    if (requestSeq === fileRequestSeq.value) {
      filesLoading.value = false
    }
  }
}

function searchFiles() {
  fileRequestSeq.value += 1
  resetFileList()
  loadFiles()
}

async function loadMoreRootFiles() {
  if (filesLoading.value || !filesHasMore.value) return
  const requestSeq = fileRequestSeq.value
  filesLoading.value = true
  filesError.value = ''
  const nextPage = filePage.value + 1
  try {
    const { results, count } = await fetchFileEntries({
      directory: filesRootDirectory.value,
      page: nextPage
    })
    if (requestSeq !== fileRequestSeq.value) return
    filePage.value = nextPage
    filesCount.value = count
    if (fileSearchActive.value) {
      searchFileEntries.value = [...searchFileEntries.value, ...results]
      fileNodes.value = buildDataSourceFileTree(searchFileEntries.value)
      filesHasMore.value = searchFileEntries.value.length < count
      return
    }
    fileNodes.value = [...fileNodes.value, ...results.map(mapFileNode)]
    filesHasMore.value = fileNodes.value.length < count
  } catch (error) {
    filesError.value = extractErrorMessage(error, t('common.error'))
  } finally {
    if (requestSeq === fileRequestSeq.value) {
      filesLoading.value = false
    }
  }
}

async function toggleFileNode(node) {
  if (!node || node.loaded || node.loading) return
  node.loading = true
  node.error = ''
  try {
    const { results, count } = await fetchFileEntries({
      directory: node.path,
      page: 1
    })
    node.children = results.map(mapFileNode)
    node.page = 1
    node.loaded = true
    node.hasMore = node.children.length < count
  } catch (error) {
    node.error = extractErrorMessage(error, t('common.error'))
  } finally {
    node.loading = false
  }
}

async function loadMoreFileNode(node) {
  if (!node || node.loading || !node.hasMore) return
  node.loading = true
  node.error = ''
  const nextPage = (node.page || 1) + 1
  try {
    const { results, count } = await fetchFileEntries({
      directory: node.path,
      page: nextPage
    })
    node.page = nextPage
    node.children = [...(node.children || []), ...results.map(mapFileNode)]
    node.hasMore = node.children.length < count
  } catch (error) {
    node.error = extractErrorMessage(error, t('common.error'))
  } finally {
    node.loading = false
  }
}

function hasProcessingTasks() {
  return tasks.value.some((task) => isProcessingStatus(task.status))
}

function resetUploadTasks() {
  uploadTasksRequestSeq.value += 1
  uploadTasks.value = []
}

async function loadUploadTasks() {
  const requestSeq = uploadTasksRequestSeq.value + 1
  uploadTasksRequestSeq.value = requestSeq
  const uuid = props.datasource?.uuid
  if (!uuid) {
    uploadTasks.value = []
    return
  }
  try {
    const list = await listDataSourceSyncTasks(uuid)
    if (requestSeq !== uploadTasksRequestSeq.value) return
    uploadTasks.value = list
  } catch {
    if (requestSeq !== uploadTasksRequestSeq.value) return
    uploadTasks.value = []
  }
}

async function loadTasks(options = {}) {
  if (tasksLoadInFlight.value) return false
  const requestSeq = taskRequestSeq.value + 1
  taskRequestSeq.value = requestSeq
  const uuid = props.datasource?.uuid
  if (!uuid) {
    resetTaskList()
    return false
  }
  tasksLoadInFlight.value = true
  if (!options.silent) {
    tasksLoading.value = true
  }
  try {
    const params = {
      page: currentPage.value,
      page_size: pageSize,
      task_type: 'lens_datasource_all',
      metadata_fields: TASK_METADATA_FIELDS
    }
    const res = await api.get(`/lens/admin/datasources/${uuid}/sync-tasks/`, {
      params
    })
    const data = extractResponseData(res)
    const list =
      data?.results ?? data?.list ?? (Array.isArray(data) ? data : [])
    const serverTotal = data?.count ?? data?.pagination?.total
    const total = Number.isFinite(Number(serverTotal))
      ? Number(serverTotal)
      : list.length
    if (requestSeq !== taskRequestSeq.value) {
      return
    }
    tasks.value = list
    totalCount.value = total
    totalPages.value = total > 0 ? Math.ceil(total / pageSize) : 1
    return true
  } catch (e) {
    if (requestSeq !== taskRequestSeq.value) {
      return
    }
    if (!options.silent) {
      resetTaskList()
    }
    // eslint-disable-next-line no-console
    console.error(extractErrorMessage(e, t('common.error')))
    return false
  } finally {
    if (requestSeq === taskRequestSeq.value) {
      tasksLoadInFlight.value = false
      tasksLoading.value = false
    }
  }
}

async function refreshProcessingTasks() {
  if (processingRefreshInFlight.value) return
  const processingTasks = tasks.value.filter((task) =>
    isProcessingStatus(task.status)
  )
  if (!processingTasks.length) {
    stopProcessingRefresh()
    return
  }
  processingRefreshInFlight.value = true
  try {
    const results = await Promise.allSettled(
      processingTasks.map((task) =>
        taskManagementApi.getExecution(task.id, {
          metadata_fields: TASK_METADATA_FIELDS
        })
      )
    )
    const refreshedById = new Map()
    results.forEach((result) => {
      if (result.status !== 'fulfilled') return
      const row = extractResponseData(result.value)
      if (row?.id == null) return
      refreshedById.set(String(row.id), row)
    })
    if (!refreshedById.size) return
    tasks.value = tasks.value.map((task) => {
      if (!refreshedById.has(String(task.id))) return task
      const updated = { ...task, ...refreshedById.get(String(task.id)) }
      if (expandedTaskId.value === task.id && expandedTask.value) {
        expandedTask.value = { ...expandedTask.value, ...updated }
      }
      return updated
    })
    if (!hasProcessingTasks()) {
      stopProcessingRefresh()
      await loadTasks({ silent: true })
    }
  } finally {
    processingRefreshInFlight.value = false
  }
}

function startProcessingRefresh() {
  if (processingRefreshTimer.value || !hasProcessingTasks()) return
  stopProcessingRefresh()
  processingRefreshTimer.value = window.setInterval(
    refreshProcessingTasks,
    3000
  )
}

function stopProcessingRefresh() {
  if (!processingRefreshTimer.value) return
  window.clearInterval(processingRefreshTimer.value)
  processingRefreshTimer.value = null
}

function goPrevPage() {
  if (tasksLoading.value || currentPage.value <= 1) return
  currentPage.value -= 1
  loadTasks()
}

function goNextPage() {
  if (tasksLoading.value || currentPage.value >= totalPages.value) return
  currentPage.value += 1
  loadTasks()
}

async function toggleTaskExpand(task) {
  if (tasksLoading.value) return
  if (expandedTaskId.value === task.id) {
    expandedTaskId.value = null
    expandedTask.value = null
    return
  }
  expandedTaskId.value = task.id
  expandedTask.value = task
  await loadExpandedTask(task.id)
}

async function loadExpandedTask(id) {
  expandedTaskDetailLoading.value = true
  try {
    const res = await taskManagementApi.getExecution(id, {
      metadata_fields: TASK_METADATA_FIELDS
    })
    const data = extractResponseData(res)
    if (expandedTaskId.value === id) {
      expandedTask.value = data
    }
  } catch (e) {
    // eslint-disable-next-line no-console
    console.error(extractErrorMessage(e, t('common.error')))
  } finally {
    expandedTaskDetailLoading.value = false
  }
}

function taskTypeLabel(task) {
  if (task?.module === 'lens_datasource_conversion') {
    return t('lensAdmin.datasourceDetail.details.taskTypeConversion')
  }
  if (task?.module === 'lens_datasource_upload') {
    return t('lensAdmin.datasourceDetail.details.taskTypeUpload')
  }
  return t('lensAdmin.datasourceDetail.details.taskTypeSync')
}

function formatTrigger(task) {
  const trigger = task?.trigger || task?.metadata?.trigger
  if (trigger === 'manual') {
    return t('lensAdmin.datasourceDetail.details.triggerManual')
  }
  if (trigger === 'periodic' || trigger === 'scheduled') {
    return t('lensAdmin.datasourceDetail.details.triggerScheduled')
  }
  return trigger || t('lensAdmin.datasourceDetail.details.triggerSystem')
}

watch(
  () => [props.datasource?.uuid, props.show, activeTab.value],
  ([uuid, visible, tab]) => {
    if (!visible || !uuid) {
      stopProcessingRefresh()
      taskRequestSeq.value += 1
      taskListContextKey.value = ''
      tasksLoadInFlight.value = false
      tasksLoading.value = false
      resetTaskList()
      resetUploadTasks()
      return
    }
    if (tab !== 'details') {
      stopProcessingRefresh()
      // The basic tab lists the datasource's current upload targets, derived
      // from the same upload task history the editor uses.
      if (tab === 'basic' && isUploadDatasource.value) {
        const uploadContextKey = `${uuid}:upload`
        if (taskListContextKey.value !== uploadContextKey) {
          taskListContextKey.value = uploadContextKey
          loadUploadTasks()
        }
        return
      }
      taskRequestSeq.value += 1
      taskListContextKey.value = ''
      tasksLoadInFlight.value = false
      tasksLoading.value = false
      resetTaskList()
      return
    }
    const contextKey = `${uuid}:${tab}`
    if (taskListContextKey.value === contextKey) {
      if (
        isDataSourceSyncing(props.datasource) &&
        !hasProcessingTasks() &&
        !tasksLoadInFlight.value
      ) {
        loadTasks({ silent: true }).then((loaded) => {
          if (loaded && hasProcessingTasks()) {
            startProcessingRefresh()
          }
        })
      }
      return
    }
    taskListContextKey.value = contextKey
    stopProcessingRefresh()
    taskRequestSeq.value += 1
    currentPage.value = 1
    resetTaskList()
    loadTasks().then((loaded) => {
      if (!loaded) return
      if (hasProcessingTasks()) {
        startProcessingRefresh()
      } else {
        stopProcessingRefresh()
      }
    })
  },
  { immediate: true }
)

watch(
  () => [props.datasource?.uuid, props.show, activeTab.value],
  ([uuid, visible, tab]) => {
    if (!visible || !uuid || tab !== 'files') {
      fileRequestSeq.value += 1
      fileListContextKey.value = ''
      resetFileList()
      return
    }
    const contextKey = `${uuid}:${tab}`
    if (fileListContextKey.value === contextKey) return
    fileListContextKey.value = contextKey
    loadFiles()
  },
  { immediate: true }
)

watch(
  () => props.show,
  (visible) => {
    if (visible) {
      loadLLMConfigOptions()
    }
  },
  { immediate: true }
)

onBeforeUnmount(() => {
  stopProcessingRefresh()
})

function formatSourceType(rowOrType) {
  const sourceType =
    typeof rowOrType === 'string' ? rowOrType : rowOrType?.source_type
  if (
    typeof rowOrType !== 'string' &&
    rowOrType?.plugin_key === 'file_upload'
  ) {
    return t('lensAdmin.datasourceWizard.fileUploadTitle')
  }
  if (sourceType === 'git') {
    return 'Git'
  }
  if (sourceType === 'feishu') {
    return t('lensAdmin.datasourceWizard.feishu')
  }
  if (sourceType === 'upload') {
    return t('lensAdmin.datasourceWizard.fileUploadTitle')
  }
  if (sourceType === 'managed_workspace') {
    return t('lensAdmin.datasourceWizard.managedWorkspace')
  }
  return sourceType || emptyValue
}

async function loadLLMConfigOptions() {
  if (llmConfigLoaded.value) return
  llmConfigLoaded.value = true
  try {
    const rows = await llmAdminApi
      .getLLMConfigAll({ scope: 'global' })
      .catch(() => [])
    llmConfigOptions.value = normalizeList(rows)
  } catch {
    llmConfigOptions.value = []
  }
}

function authSchemeLabel(authScheme) {
  if (authScheme === 'token') {
    return t('lensAdmin.datasourceWizard.authToken')
  }
  return t('lensAdmin.datasourceWizard.authNone')
}

function feishuScopeLabel() {
  return t('lensAdmin.datasourceWizard.feishuScopeDriveFolder')
}

function formatSyncPolicy(syncPolicy) {
  if (syncPolicy?.mode === 'crontab') {
    const cron = syncPolicy.cron || emptyValue
    const timezone = syncPolicy.timezone || 'UTC'
    return `${cron} · ${timezone}`
  }
  const interval = syncPolicy?.interval_seconds
  return interval
    ? t('lensAdmin.table.intervalSeconds', { seconds: interval })
    : emptyValue
}

function detailItem(label, value, mono = false, options = {}) {
  const normalized = Array.isArray(value) ? value.join(', ') : value
  return {
    label,
    value: normalized || emptyValue,
    mono,
    ...options
  }
}

function settingItem(label, value, enabled = null, options = {}) {
  return detailItem(label, value, false, { enabled, ...options })
}

function booleanLabel(value) {
  return value ? t('common.status.enabled') : t('common.status.disabled')
}

function modelLabel(modelRef) {
  if (!modelRef) return emptyValue
  const found = llmConfigOptions.value.find(
    (config) => String(config.uuid || config.id) === String(modelRef)
  )
  if (found) return formatLLMConfigLabel(found)
  return t('lensAdmin.datasourceDetail.unknownModel', {
    id: compactUuid(modelRef)
  })
}

const isManagedWorkspaceDatasource = computed(
  () => props.datasource?.source_type === 'managed_workspace'
)

const datasourceOverviewDetails = computed(() => {
  const row = props.datasource
  if (!row) return []
  const items = [
    detailItem(t('lensAdmin.fields.name'), row.name),
    detailItem(t('lensAdmin.fields.type'), formatSourceType(row))
  ]
  if (isUploadDatasource.value || isManagedWorkspaceDatasource.value) {
    items.push(
      detailItem(
        t('lensAdmin.datasourceDetail.nodeInfo'),
        datasourceLensNodeName(row)
      )
    )
  } else {
    items.push(
      detailItem(
        t('lensAdmin.datasourceDetail.connection'),
        row.connection_name ||
          (row.connection
            ? compactUuid(
                typeof row.connection === 'object'
                  ? row.connection.uuid
                  : row.connection
              )
            : t('lensAdmin.datasourceDetail.legacyConnection'))
      )
    )
  }
  items.push(detailItem('UUID', row.uuid, true))
  return items
})

const datasourceStorageUsage = computed(() => {
  const usage = props.datasource?.storage_usage
  if (!usage || usage.status !== 'complete') return null
  return [
    {
      label: t('lensAdmin.datasourceDetail.storage.raw'),
      value: formatBytes(usage.raw_bytes)
    },
    {
      label: t('lensAdmin.datasourceDetail.storage.derived'),
      value: formatBytes(usage.derived_bytes)
    },
    {
      label: t('lensAdmin.datasourceDetail.storage.total'),
      value: formatBytes(usage.total_bytes)
    }
  ]
})

function datasourceLensNodeName(row) {
  if (row.lensnode_name) return row.lensnode_name
  const uuid =
    (typeof row.lensnode === 'object' ? row.lensnode?.uuid : row.lensnode) ||
    row.lensnode_uuid
  const found = props.lensnodes.find((node) => node.uuid === uuid)
  return found?.name || uuid || emptyValue
}

const datasourceResourceDetails = computed(() => {
  const row = props.datasource
  if (!row) return []
  const config = row.config || {}
  if (isManagedWorkspaceDatasource.value) {
    return [
      detailItem(
        t('lensAdmin.datasourceDetail.managedDirectory'),
        row.target_path,
        true,
        { wide: true }
      )
    ]
  }
  if (row.source_type === 'git') {
    const repositoryUrl = dataSourceRepositoryUrl(row, row.connection_endpoint)
    const items = [
      detailItem(t('lensAdmin.fields.repoUrl'), repositoryUrl, true, {
        href: isHttpUrl(repositoryUrl) ? repositoryUrl : '',
        wide: true
      }),
      detailItem(
        t('lensAdmin.fields.authScheme'),
        row.connection
          ? t('lensAdmin.datasourceDetail.connectionManaged')
          : authSchemeLabel(config.auth_scheme)
      )
    ]
    if (!isOrganizationDataSource(row)) {
      items.splice(
        1,
        0,
        detailItem(t('lensAdmin.fields.branch'), dataSourceBranch(row), true)
      )
    }
    return items
  }
  const resourceUrls = Array.isArray(row.datasource_config?.resource_urls)
    ? row.datasource_config.resource_urls
    : [config.folder_url, config.document_url].filter(Boolean)
  return [
    ...resourceUrls.map((url, index) =>
      detailItem(
        resourceUrls.length > 1
          ? `${t('lensAdmin.fields.url')} ${index + 1}`
          : t('lensAdmin.fields.url'),
        url,
        true,
        { href: isHttpUrl(url) ? url : '', wide: true }
      )
    ),
    detailItem(t('lensAdmin.fields.syncScope'), feishuScopeLabel())
  ].filter((item) => item.value !== emptyValue)
})

const organizationRepositories = computed(() =>
  dataSourceRepositories(props.datasource)
)
const datasourceSyncDetails = computed(() => {
  const row = props.datasource
  if (!row) return []
  return [
    detailItem(
      t('lensAdmin.fields.syncInterval'),
      formatSyncPolicy(row.sync_policy)
    ),
    detailItem(
      t('lensAdmin.datasourceDetail.lastSyncedAt'),
      formatDateTime(row.last_synced_at)
    ),
    detailItem(
      t('lensAdmin.table.nextSync'),
      formatDateTime(row.sync_state?.next_run_at)
    ),
    detailItem(
      t('lensAdmin.datasourceDetail.lastStatus'),
      syncStatusLabel(row.sync_state?.last_status)
    ),
    detailItem(
      t('lensAdmin.datasourceDetail.createdAt'),
      formatDateTime(row.created_at)
    ),
    detailItem(
      t('lensAdmin.datasourceDetail.updatedAt'),
      formatDateTime(row.updated_at)
    )
  ]
})

const datasourceSyncError = computed(() => {
  const row = props.datasource
  return lensNodeErrorMessage(row?.last_error, t) || row?.last_error || ''
})

function syncStatusLabel(status) {
  const normalized = String(status || '').toLowerCase()
  const statusKey = {
    running: 'processing',
    success: 'success',
    failed: 'failed',
    processing: 'processing',
    pending: 'pending',
    cancelled: 'cancelled'
  }[normalized]
  if (statusKey) {
    return t(`common.status.${statusKey}`)
  }
  return status || emptyValue
}

const datasourceRetrievalGroups = computed(() => {
  const conversion = props.datasource?.sync_policy?.conversion || {}
  return [
    {
      key: 'document',
      title: t('lensAdmin.datasourceDetail.processing.document'),
      items: [
        settingItem(
          t('lensAdmin.datasourceWizard.convertDocuments'),
          booleanLabel(conversion.document),
          Boolean(conversion.document),
          { wide: true }
        ),
        settingItem(
          t('lensAdmin.fields.documentModel'),
          modelLabel(conversion.document_model_ref),
          null,
          { wide: true }
        ),
        settingItem(
          t('lensAdmin.fields.maxFileSizeMb'),
          conversion.max_file_size_mb || 100
        ),
        settingItem(t('lensAdmin.fields.maxPages'), conversion.max_pages || 500)
      ]
    },
    {
      key: 'image',
      title: t('lensAdmin.datasourceDetail.processing.image'),
      items: [
        settingItem(
          t('lensAdmin.datasourceWizard.convertImages'),
          booleanLabel(conversion.image),
          Boolean(conversion.image)
        ),
        settingItem(
          t('lensAdmin.datasourceWizard.convertEmbeddedImages'),
          booleanLabel(conversion.embedded_image),
          Boolean(conversion.embedded_image)
        ),
        settingItem(
          t('lensAdmin.fields.visionModel'),
          modelLabel(conversion.vision_model_ref),
          null,
          { wide: true }
        ),
        settingItem(
          t('lensAdmin.fields.maxImages'),
          conversion.max_images || 100
        )
      ]
    },
    {
      key: 'pdf',
      title: t('lensAdmin.datasourceDetail.processing.pdf'),
      wide: true,
      items: [
        settingItem(
          t('lensAdmin.datasourceWizard.pdfExtractImages'),
          booleanLabel(conversion.pdf_extract_images !== false),
          conversion.pdf_extract_images !== false
        ),
        settingItem(
          t('lensAdmin.datasourceWizard.pdfExtractImagesOnTextPages'),
          booleanLabel(conversion.pdf_extract_images_on_text_pages),
          Boolean(conversion.pdf_extract_images_on_text_pages)
        ),
        settingItem(
          t('lensAdmin.datasourceWizard.pdfRenderScannedPages'),
          booleanLabel(conversion.pdf_render_scanned_pages),
          Boolean(conversion.pdf_render_scanned_pages),
          { wide: true }
        ),
        settingItem(
          t('lensAdmin.fields.pdfMaxPages'),
          conversion.pdf_max_pages || 30
        ),
        settingItem(
          t('lensAdmin.fields.pdfMaxImagesPerPage'),
          conversion.pdf_max_images_per_page || 3
        ),
        settingItem(
          t('lensAdmin.fields.pdfRenderDpi'),
          conversion.pdf_render_dpi || 144
        ),
        settingItem(
          t('lensAdmin.fields.pdfMinTextChars'),
          conversion.pdf_min_text_chars || 30
        ),
        settingItem(
          t('lensAdmin.fields.pdfMinImageAreaRatio'),
          conversion.pdf_min_image_area_ratio || 0.08,
          null,
          { wide: true }
        )
      ]
    }
  ]
})

function isHttpUrl(value) {
  return /^https?:\/\//i.test(String(value || ''))
}
</script>

<style scoped>
.detail-tab {
  @apply border-b-2 border-transparent py-3 text-sm font-medium text-ink-500 transition-colors;
}

.detail-tab:hover {
  @apply text-ink-700;
}

.detail-tab-active {
  @apply border-primary-500 text-primary-600;
}
</style>
