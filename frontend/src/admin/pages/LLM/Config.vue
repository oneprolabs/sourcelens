<template>
  <AdminLayout>
    <div class="w-full max-w-full p-0 md:p-6">
      <div class="mb-4">
        <h1 class="text-lg font-semibold text-gray-900">
          {{ t('llm.config.title') }}
        </h1>
        <p class="mt-1 text-sm text-gray-500">
          {{ t('llm.config.subtitleList') }}
        </p>
      </div>

      <div
        class="rounded-lg border-0 border-gray-200 bg-transparent shadow-none md:border md:bg-white md:shadow-sm"
      >
        <div class="p-0 md:p-6">
          <div
            class="mb-4 flex items-center justify-end gap-3 rounded-lg border border-gray-200 bg-white p-3 md:mb-6 md:border-0 md:p-0"
          >
            <BaseButton
              variant="outline"
              size="sm"
              :loading="loading"
              :title="t('common.refresh')"
              class="flex items-center gap-1 shadow-sm hover:shadow-md transition-shadow"
              @click="loadAll"
            >
              <svg
                v-if="!loading"
                class="w-4 h-4"
                fill="none"
                stroke="currentColor"
                viewBox="0 0 24 24"
              >
                <path
                  stroke-linecap="round"
                  stroke-linejoin="round"
                  stroke-width="2"
                  d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"
                />
              </svg>
              <span class="sr-only">{{ t('common.refresh') }}</span>
            </BaseButton>
            <BaseButton variant="primary" size="sm" @click="openAddModal">
              {{ t('llm.config.addConfig') }}
            </BaseButton>
          </div>

          <TableBulkActions
            :actions="bulkActions"
            :loading-key="bulkLoadingKey"
            :selected-count="selectedRows.length"
            @action="runBulkAction"
            @clear="clearSelection"
          />

          <BaseLoading v-if="loading" />
          <template v-else>
            <div
              v-if="configList.length === 0"
              class="py-16 text-center rounded-lg border border-gray-200 bg-gray-50"
            >
              <svg
                class="mx-auto h-12 w-12 text-gray-400 mb-4"
                fill="none"
                stroke="currentColor"
                viewBox="0 0 24 24"
              >
                <path
                  stroke-linecap="round"
                  stroke-linejoin="round"
                  stroke-width="2"
                  d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"
                />
              </svg>
              <p class="text-sm font-medium text-gray-600">
                {{ t('llm.config.noConfigs') }}
              </p>
            </div>

            <template v-else>
              <div
                data-testid="mobile-llm-config-list"
                class="space-y-3 md:hidden"
              >
                <div
                  data-testid="mobile-llm-config-select-all"
                  class="flex min-h-11 items-center rounded-lg border border-gray-200 bg-white px-3 shadow-sm"
                >
                  <label
                    class="inline-flex min-h-11 cursor-pointer items-center gap-3 text-sm font-medium text-gray-700"
                  >
                    <input
                      type="checkbox"
                      class="h-4 w-4 rounded border-gray-300 text-primary-600 focus:ring-primary-500"
                      :aria-label="t('common.selectAll')"
                      :checked="allSelected"
                      :indeterminate="someSelected"
                      @change="setAllSelected($event.target.checked)"
                    />
                    <span>{{ t('common.selectAll') }}</span>
                  </label>
                </div>

                <article
                  v-for="row in pagedConfigList"
                  :key="`mobile-${row.uuid || row.id}`"
                  class="rounded-lg border bg-white p-3 shadow-sm transition-colors"
                  :class="
                    selectedIds.has(row.uuid || row.id)
                      ? 'border-primary-300 ring-1 ring-primary-100'
                      : 'border-gray-200'
                  "
                >
                  <div class="flex items-start gap-2">
                    <label
                      class="-ml-2 inline-flex h-11 w-11 shrink-0 cursor-pointer items-center justify-center rounded-lg focus-within:ring-2 focus-within:ring-primary-500/20"
                    >
                      <input
                        type="checkbox"
                        class="h-4 w-4 rounded border-gray-300 text-primary-600 focus:ring-primary-500"
                        :aria-label="
                          t('common.selectRow', {
                            name: row.config?.model || row.uuid || row.id
                          })
                        "
                        :checked="selectedIds.has(row.uuid || row.id)"
                        @change="setRowSelected(row, $event.target.checked)"
                      />
                    </label>

                    <div class="min-w-0 flex-1 pt-1">
                      <div class="flex min-w-0 items-center gap-2">
                        <ProviderIcon :provider="row.provider" size="sm" />
                        <h2
                          class="min-w-0 break-words text-sm font-semibold text-gray-900"
                        >
                          {{ row.config?.model || '–' }}
                        </h2>
                      </div>
                      <p class="mt-1 text-xs text-gray-500">
                        {{ providerLabel(row.provider) }}
                        <span aria-hidden="true"> · </span>
                        {{
                          row.scope === 'global'
                            ? t('llm.config.scopeGlobal')
                            : t('llm.config.scopeUser')
                        }}
                        <template v-if="row.scope === 'user'">
                          <span aria-hidden="true"> · </span>
                          {{ row.username || row.user_id || '–' }}
                        </template>
                      </p>
                    </div>

                    <RowActionMenu
                      class="shrink-0"
                      :actions="rowActions(row)"
                      @select="handleRowAction($event, row)"
                    />
                  </div>

                  <div class="mt-3 flex flex-wrap gap-2 pl-9">
                    <span
                      class="inline-flex items-center gap-1 rounded-full px-2.5 py-1 text-xs font-medium"
                      :class="
                        row.is_active
                          ? 'bg-green-50 text-green-700'
                          : 'bg-gray-100 text-gray-600'
                      "
                    >
                      <span
                        class="h-1.5 w-1.5 rounded-full"
                        :class="row.is_active ? 'bg-green-500' : 'bg-gray-400'"
                      />
                      {{ t('llm.config.active') }}:
                      {{ row.is_active ? t('common.yes') : t('common.no') }}
                    </span>
                    <span
                      class="inline-flex items-center gap-1 rounded-full bg-primary-50 px-2.5 py-1 text-xs font-medium text-primary-700"
                    >
                      {{ t('llm.config.default') }}:
                      {{ row.is_default ? t('common.yes') : t('common.no') }}
                    </span>
                  </div>

                  <details class="group mt-3 border-t border-gray-100 pt-1">
                    <summary
                      class="flex min-h-11 cursor-pointer list-none items-center justify-between rounded-md px-2 text-sm font-medium text-gray-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-primary-500/20"
                    >
                      <span>{{ t('common.viewDetails') }}</span>
                      <svg
                        class="h-4 w-4 transition-transform group-open:rotate-180"
                        fill="none"
                        stroke="currentColor"
                        viewBox="0 0 24 24"
                        aria-hidden="true"
                      >
                        <path
                          stroke-linecap="round"
                          stroke-linejoin="round"
                          stroke-width="2"
                          d="M19 9l-7 7-7-7"
                        />
                      </svg>
                    </summary>

                    <dl class="space-y-3 px-2 pb-2 pt-1 text-sm">
                      <div>
                        <dt class="text-xs font-medium text-gray-500">
                          {{ t('llm.config.modelParameters') }}
                        </dt>
                        <dd
                          v-if="getRowModelParameters(row).length"
                          class="mt-1 space-y-1 text-gray-800"
                        >
                          <div
                            v-for="parameter in getRowModelParameters(row)"
                            :key="parameter.name"
                            class="flex flex-wrap gap-x-1"
                          >
                            <span class="text-gray-500"
                              >{{ parameter.name }}:</span
                            >
                            <span>{{ parameter.value }}</span>
                            <span
                              v-if="parameter.isDefault"
                              class="text-xs text-gray-400"
                            >
                              ({{ t('llm.config.defaultParameterValue') }})
                            </span>
                          </div>
                        </dd>
                        <dd v-else class="mt-1 text-gray-400">–</dd>
                      </div>
                      <div>
                        <dt class="text-xs font-medium text-gray-500">
                          {{ t('llm.config.apiBase') }}
                        </dt>
                        <dd class="mt-1 break-all text-gray-800">
                          {{ row.config?.api_base || '–' }}
                        </dd>
                      </div>
                      <div>
                        <dt class="text-xs font-medium text-gray-500">
                          {{ t('llm.config.apiKey') }}
                        </dt>
                        <dd class="mt-1 break-all text-gray-800">
                          {{
                            maskApiKey(row.config?.api_key || row.config?.key)
                          }}
                        </dd>
                      </div>
                      <div>
                        <dt class="text-xs font-medium text-gray-500">
                          {{ t('llm.config.capabilities') }}
                        </dt>
                        <dd
                          v-if="getRowCapabilities(row).length"
                          class="mt-1 flex flex-wrap gap-1"
                        >
                          <span
                            v-for="cap in getRowCapabilities(row)"
                            :key="cap"
                            class="rounded-md bg-gray-100 px-2 py-0.5 text-xs text-gray-700"
                          >
                            {{ capabilityLabel(cap) }}
                          </span>
                        </dd>
                        <dd v-else class="mt-1 text-gray-400">–</dd>
                      </div>
                    </dl>
                  </details>
                </article>
              </div>

              <div
                data-testid="desktop-llm-config-table"
                class="relative hidden overflow-x-auto rounded-lg border border-gray-200 bg-white shadow-sm md:block"
              >
                <table class="min-w-full divide-y divide-gray-200">
                  <thead class="bg-gradient-to-r from-gray-50 to-gray-100">
                    <tr>
                      <th
                        class="sticky left-0 z-20 w-12 border-b border-gray-200 bg-gray-50 px-4 py-3 text-left"
                      >
                        <input
                          type="checkbox"
                          class="h-4 w-4 rounded border-gray-300 text-primary-600 focus:ring-primary-500"
                          :aria-label="t('common.selectAll')"
                          :checked="allSelected"
                          :indeterminate="someSelected"
                          @change="setAllSelected($event.target.checked)"
                        />
                      </th>
                      <th
                        class="px-4 py-3 text-left text-xs font-semibold text-gray-700 uppercase tracking-wider border-b border-gray-200"
                      >
                        {{ t('llm.config.scopeLabel') }}
                      </th>
                      <th
                        class="px-4 py-3 text-left text-xs font-semibold text-gray-700 uppercase tracking-wider border-b border-gray-200"
                      >
                        {{ t('llm.config.user') }}
                      </th>
                      <th
                        class="px-4 py-3 text-left text-xs font-semibold text-gray-700 uppercase tracking-wider border-b border-gray-200"
                      >
                        {{ t('llm.config.provider') }}
                      </th>
                      <th
                        class="px-4 py-3 text-left text-xs font-semibold text-gray-700 uppercase tracking-wider border-b border-gray-200"
                      >
                        {{ t('llm.config.model') }}
                      </th>
                      <th
                        class="px-4 py-3 text-left text-xs font-semibold text-gray-700 uppercase tracking-wider border-b border-gray-200"
                      >
                        {{ t('llm.config.modelParameters') }}
                      </th>
                      <th
                        class="px-4 py-3 text-left text-xs font-semibold text-gray-700 uppercase tracking-wider border-b border-gray-200"
                      >
                        {{ t('llm.config.apiBase') }}
                      </th>
                      <th
                        class="px-4 py-3 text-left text-xs font-semibold text-gray-700 uppercase tracking-wider border-b border-gray-200"
                      >
                        {{ t('llm.config.apiKey') }}
                      </th>
                      <th
                        class="px-4 py-3 text-left text-xs font-semibold text-gray-700 uppercase tracking-wider border-b border-gray-200"
                      >
                        {{ t('llm.config.capabilities') }}
                      </th>
                      <th
                        class="px-4 py-3 text-left text-xs font-semibold text-gray-700 uppercase tracking-wider border-b border-gray-200"
                      >
                        {{ t('llm.config.default') }}
                      </th>
                      <th
                        class="px-4 py-3 text-left text-xs font-semibold text-gray-700 uppercase tracking-wider border-b border-gray-200"
                      >
                        {{ t('llm.config.active') }}
                      </th>
                      <th
                        class="sticky right-0 z-20 border-b border-gray-200 bg-gray-100 px-4 py-3 text-right text-xs font-semibold uppercase tracking-wider text-gray-700 shadow-[-8px_0_12px_-12px_rgba(15,23,42,0.45)]"
                      >
                        {{ t('llm.config.actions') }}
                      </th>
                    </tr>
                  </thead>
                  <tbody class="bg-white divide-y divide-gray-100">
                    <tr
                      v-for="row in pagedConfigList"
                      :key="row.uuid || row.id"
                      class="group transition-colors duration-150 hover:bg-gray-50"
                    >
                      <td
                        class="sticky left-0 z-10 bg-white px-4 py-4 group-hover:bg-gray-50"
                      >
                        <input
                          type="checkbox"
                          class="h-4 w-4 rounded border-gray-300 text-primary-600 focus:ring-primary-500"
                          :aria-label="
                            t('common.selectRow', {
                              name: row.config?.model || row.uuid || row.id
                            })
                          "
                          :checked="selectedIds.has(row.uuid || row.id)"
                          @change="setRowSelected(row, $event.target.checked)"
                        />
                      </td>
                      <td
                        class="px-4 py-4 whitespace-nowrap text-sm text-gray-900"
                      >
                        {{
                          row.scope === 'global'
                            ? t('llm.config.scopeGlobal')
                            : t('llm.config.scopeUser')
                        }}
                      </td>
                      <td
                        class="px-4 py-4 whitespace-nowrap text-sm text-gray-600"
                      >
                        {{
                          row.scope === 'user'
                            ? row.username || row.user_id || '–'
                            : '–'
                        }}
                      </td>
                      <td
                        class="px-4 py-4 whitespace-nowrap text-sm text-gray-900"
                      >
                        <ProviderIcon :provider="row.provider" size="sm">
                          <span class="text-gray-900">{{
                            providerLabel(row.provider)
                          }}</span>
                        </ProviderIcon>
                      </td>
                      <td
                        class="px-4 py-4 whitespace-nowrap text-sm text-gray-700"
                      >
                        {{ row.config?.model || '–' }}
                      </td>
                      <td class="px-4 py-4 text-sm text-gray-700">
                        <div
                          v-if="getRowModelParameters(row).length"
                          class="space-y-1 whitespace-nowrap"
                        >
                          <div
                            v-for="parameter in getRowModelParameters(row)"
                            :key="parameter.name"
                          >
                            <span class="text-gray-500"
                              >{{ parameter.name }}:</span
                            >
                            {{ parameter.value }}
                            <span
                              v-if="parameter.isDefault"
                              class="text-xs text-gray-400"
                            >
                              ({{ t('llm.config.defaultParameterValue') }})
                            </span>
                          </div>
                        </div>
                        <span v-else class="text-gray-400">–</span>
                      </td>
                      <td
                        class="px-4 py-4 whitespace-nowrap text-sm text-gray-700"
                      >
                        {{ row.config?.api_base || '–' }}
                      </td>
                      <td
                        class="px-4 py-4 whitespace-nowrap text-sm text-gray-700"
                      >
                        {{ maskApiKey(row.config?.api_key || row.config?.key) }}
                      </td>
                      <td class="px-4 py-4 text-sm">
                        <span
                          v-if="getRowCapabilities(row).length"
                          class="flex flex-wrap gap-1"
                        >
                          <span
                            v-for="cap in getRowCapabilities(row)"
                            :key="cap"
                            class="inline-flex items-center rounded-md bg-gray-100 px-2 py-0.5 text-xs text-gray-700"
                          >
                            {{ capabilityLabel(cap) }}
                          </span>
                        </span>
                        <span v-else class="text-gray-400">–</span>
                      </td>
                      <td class="px-4 py-4 whitespace-nowrap text-sm">
                        <span
                          v-if="row.is_default"
                          class="inline-flex items-center text-primary-600"
                          :title="t('llm.config.testUseDefault')"
                        >
                          <svg
                            class="h-5 w-5"
                            fill="currentColor"
                            viewBox="0 0 20 20"
                            aria-hidden="true"
                          >
                            <path
                              fill-rule="evenodd"
                              d="M10 18a8 8 0 100-16 8 8 0 000 16zm3.707-9.293a1 1 0 00-1.414-1.414L9 10.586 7.707 9.293a1 1 0 00-1.414 1.414l2 2a1 1 0 001.414 0l4-4z"
                              clip-rule="evenodd"
                            />
                          </svg>
                        </span>
                        <span v-else class="text-gray-300">–</span>
                      </td>
                      <td class="px-4 py-4 whitespace-nowrap text-sm">
                        <span
                          v-if="row.is_active"
                          class="inline-flex items-center text-green-600"
                          :title="t('common.yes')"
                        >
                          <svg
                            class="h-4 w-4"
                            fill="none"
                            stroke="currentColor"
                            viewBox="0 0 24 24"
                            aria-hidden="true"
                          >
                            <path
                              stroke-linecap="round"
                              stroke-linejoin="round"
                              stroke-width="2"
                              d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z"
                            />
                          </svg>
                        </span>
                        <span
                          v-else
                          class="inline-flex items-center text-gray-400"
                          :title="t('common.no')"
                        >
                          <svg
                            class="h-4 w-4"
                            fill="none"
                            stroke="currentColor"
                            viewBox="0 0 24 24"
                            aria-hidden="true"
                          >
                            <path
                              stroke-linecap="round"
                              stroke-linejoin="round"
                              stroke-width="2"
                              d="M15 12H9m12 0a9 9 0 11-18 0 9 9 0 0118 0z"
                            />
                          </svg>
                        </span>
                      </td>
                      <td
                        class="sticky right-0 z-10 whitespace-nowrap bg-white px-4 py-4 text-right shadow-[-8px_0_12px_-12px_rgba(15,23,42,0.45)] group-hover:bg-gray-50"
                      >
                        <RowActionMenu
                          :actions="rowActions(row)"
                          @select="handleRowAction($event, row)"
                        />
                      </td>
                    </tr>
                  </tbody>
                </table>
              </div>
            </template>
            <PaginationBar
              v-model:page-size="pageSize"
              :current-page="currentPage"
              :total="configList.length"
              @page-size-change="handlePageSizeChange"
              @prev="goPrevPage"
              @next="goNextPage"
            />
          </template>
        </div>
      </div>

      <BaseModal
        :show="!!deleteTarget"
        :title="t('common.delete')"
        @close="deleteTarget = null"
      >
        <p class="text-sm text-gray-700">
          {{ t('llm.config.confirmDeleteConfig') }}
        </p>
        <template #footer>
          <div class="flex flex-row-reverse gap-2">
            <BaseButton variant="danger" @click="confirmDeleteConfig">
              {{ t('common.delete') }}
            </BaseButton>
            <BaseButton variant="outline" @click="deleteTarget = null">
              {{ t('common.cancel') }}
            </BaseButton>
          </div>
        </template>
      </BaseModal>

      <BaseModal
        :show="showTestModal"
        :title="testModalTitle"
        @close="closeTestModal"
      >
        <div class="space-y-4">
          <div>
            <label class="block text-sm font-medium text-gray-700 mb-1">
              {{ t('llm.config.testPromptLabel') }}
            </label>
            <textarea
              v-model="testPrompt"
              rows="4"
              class="block w-full px-3 py-2 text-sm border border-gray-300 rounded-md shadow-sm focus:ring-primary-500 focus:border-primary-500"
              :placeholder="t('llm.config.testPromptPlaceholder')"
            />
          </div>
          <div>
            <label class="block text-sm font-medium text-gray-700 mb-1">
              {{ t('llm.config.maxOutputTokens') }} (max_tokens)
            </label>
            <input
              v-model.number="testMaxTokens"
              type="number"
              min="1"
              max="4096"
              class="block w-full px-3 py-2 text-sm border border-gray-300 rounded-md shadow-sm focus:ring-primary-500 focus:border-primary-500"
            />
          </div>
          <div class="flex flex-col gap-1 pt-1">
            <div class="flex items-center gap-2 min-h-[1.5rem]">
              <input
                id="test-streaming"
                v-model="testStreaming"
                type="checkbox"
                class="h-4 w-4 shrink-0 rounded border-2 border-gray-400 text-primary-600 focus:ring-2 focus:ring-primary-500 cursor-pointer"
              />
              <label
                for="test-streaming"
                class="text-sm font-medium text-gray-700 cursor-pointer select-none"
              >
                {{
                  t('llm.config.streamingOutput') ===
                  'llm.config.streamingOutput'
                    ? '流式输出'
                    : t('llm.config.streamingOutput')
                }}
              </label>
            </div>
            <p class="text-xs text-gray-500">
              {{
                t('llm.config.streamingParamHint') ===
                'llm.config.streamingParamHint'
                  ? '请求参数 stream：控制是否以 SSE 流式返回'
                  : t('llm.config.streamingParamHint')
              }}
            </p>
          </div>
          <div class="flex justify-end gap-2">
            <BaseButton
              v-if="testCallLoading && testStreaming"
              type="button"
              variant="outline"
              class="border-red-300 text-red-700 hover:bg-red-50"
              :disabled="!testCallAbortController"
              @click="stopTestCallStream"
            >
              {{
                t('llm.config.streamStop') === 'llm.config.streamStop'
                  ? '停止'
                  : t('llm.config.streamStop')
              }}
            </BaseButton>
            <BaseButton type="button" variant="outline" @click="closeTestModal">
              {{ t('common.cancel') }}
            </BaseButton>
            <BaseButton
              type="button"
              variant="primary"
              :loading="testCallLoading"
              :disabled="!testPrompt.trim()"
              @click="sendTestCall"
            >
              {{ t('llm.config.testSend') }}
            </BaseButton>
          </div>
          <div
            v-if="testCallResult !== null || (testCallLoading && testStreaming)"
            class="rounded-lg border p-4"
            :class="
              testCallLoading && testStreaming
                ? 'border-gray-200 bg-gray-50'
                : testCallOk
                  ? 'border-green-200 bg-green-50'
                  : 'border-red-200 bg-red-50'
            "
          >
            <div
              v-if="testCallLoading && testStreaming"
              class="flex items-center gap-2 mb-2"
            >
              <svg
                class="animate-spin h-4 w-4 text-gray-500"
                xmlns="http://www.w3.org/2000/svg"
                fill="none"
                viewBox="0 0 24 24"
                aria-hidden="true"
              >
                <circle
                  class="opacity-25"
                  cx="12"
                  cy="12"
                  r="10"
                  stroke="currentColor"
                  stroke-width="4"
                />
                <path
                  class="opacity-75"
                  fill="currentColor"
                  d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"
                />
              </svg>
            </div>
            <p v-else class="text-sm font-medium text-gray-700 mb-2">
              {{
                testCallOk
                  ? t('llm.config.testResponse')
                  : t('llm.config.testError')
              }}
            </p>
            <div
              v-if="
                (testCallLoading && testStreaming && streamingThinking) ||
                (testCallResult && (testCallResult.thinking || '').trim())
              "
              class="mb-3 rounded-lg border border-amber-200 bg-amber-50/80"
            >
              <div
                class="flex items-center gap-2 px-3 py-2 border-b border-amber-200 bg-amber-100/60"
              >
                <span
                  class="text-xs font-semibold uppercase tracking-wide text-amber-800"
                >
                  {{
                    t('llm.config.thinkingBlock') === 'llm.config.thinkingBlock'
                      ? '思考过程'
                      : t('llm.config.thinkingBlock')
                  }}
                </span>
              </div>
              <p
                class="px-3 py-2 text-xs text-amber-900/90 whitespace-pre-wrap break-words font-mono leading-relaxed max-h-48 overflow-y-auto"
              >
                {{
                  testCallLoading && testStreaming
                    ? streamingThinking
                    : testCallResult && testCallResult.thinking
                }}
              </p>
            </div>
            <div
              v-if="testCallOk || (testCallLoading && testStreaming)"
              class="test-call-markdown text-sm text-gray-800 overflow-x-auto"
            >
              <MarkdownRenderer
                :content="markdownContentForTest"
                :enable-highlight="true"
              />
            </div>
            <p v-else class="text-sm text-red-700">
              {{ testCallDetail }}
            </p>
            <div
              v-if="testCallOk && testCallUsage"
              class="mt-3 pt-3 border-t border-gray-200 text-xs text-gray-600"
            >
              <span class="font-medium">{{ t('llm.config.testUsage') }}:</span>
              {{ testCallUsage.prompt_tokens }} in /
              {{ testCallUsage.completion_tokens }} out /
              {{ testCallUsage.total_tokens }} total
              <span v-if="testCallUsage.cost != null">
                · {{ testCallUsage.cost_currency || 'USD' }}
                {{ testCallUsage.cost }}
              </span>
            </div>
            <div
              v-if="testCallResult && testCallResult.streaming !== undefined"
              class="mt-2 pt-2 border-t border-gray-200 text-xs text-gray-600"
            >
              <span class="font-medium"
                >{{ t('llm.config.streamingReturn') || '返回方式' }}:</span
              >
              {{
                testCallResult.streaming
                  ? t('llm.config.streamingMode') || '流式'
                  : t('llm.config.nonStreamingMode') || '非流式'
              }}
              <span v-if="testCallResult.stopped" class="ml-2 text-amber-600">
                ({{
                  t('llm.config.streamStopped') === 'llm.config.streamStopped'
                    ? '已停止'
                    : t('llm.config.streamStopped')
                }})
              </span>
            </div>
          </div>
        </div>
      </BaseModal>

      <BaseModal
        :show="showConfigModal"
        :title="editingId ? t('common.edit') : t('llm.config.addConfig')"
        @close="closeConfigModal"
      >
        <form @submit.prevent="submitConfigForm" class="space-y-4">
          <div v-if="!editingId">
            <label class="block text-sm font-medium text-gray-700 mb-1">{{
              t('llm.config.scopeLabel')
            }}</label>
            <BaseSelect v-model="form.scope">
              <option value="global">{{ t('llm.config.scopeGlobal') }}</option>
              <option value="user">{{ t('llm.config.scopeUser') }}</option>
            </BaseSelect>
          </div>
          <div v-if="!editingId && form.scope === 'user'">
            <label class="block text-sm font-medium text-gray-700 mb-1">{{
              t('llm.config.user')
            }}</label>
            <BaseSelect v-model="form.user_id" :required="userList.length > 0">
              <option value="">{{ t('llm.config.user') }}…</option>
              <option v-for="u in userList" :key="u.id" :value="u.id">
                {{ u.username || u.id }}
              </option>
            </BaseSelect>
            <p v-if="userList.length === 0" class="mt-1 text-sm text-amber-600">
              {{ t('llm.config.noUsersHint') }}
            </p>
          </div>
          <div>
            <label class="block text-sm font-medium text-gray-700 mb-1">{{
              t('llm.config.provider')
            }}</label>
            <div
              class="flex items-center gap-2 rounded-md border border-gray-300 bg-gray-50 px-3 py-2 text-sm"
            >
              <ProviderIcon :provider="form.provider" size="sm" />
              <BaseSelect
                v-model="form.provider"
                class="min-w-0 flex-1"
                :full-width="false"
                size="sm"
                variant="unstyled"
                @change="onProviderChange"
              >
                <option
                  v-for="p in providersFromModels"
                  :key="p.id"
                  :value="p.id"
                >
                  {{ p.label }}
                </option>
              </BaseSelect>
            </div>
          </div>
          <div>
            <label class="block text-sm font-medium text-gray-700 mb-1">{{
              t('llm.config.model')
            }}</label>
            <div class="relative" ref="modelDropdownRef">
              <button
                type="button"
                class="w-full flex items-center justify-between gap-2 rounded-md border border-gray-300 bg-white px-3 py-2 text-left text-sm shadow-sm focus:border-primary-500 focus:outline-none focus:ring-1 focus:ring-primary-500"
                @click="modelDropdownOpen = !modelDropdownOpen"
              >
                <span class="truncate text-gray-900">{{
                  modelSelectTriggerLabel
                }}</span>
                <svg
                  class="h-4 w-4 shrink-0 text-gray-400"
                  fill="none"
                  stroke="currentColor"
                  viewBox="0 0 24 24"
                >
                  <path
                    stroke-linecap="round"
                    stroke-linejoin="round"
                    stroke-width="2"
                    d="M19 9l-7 7-7-7"
                  />
                </svg>
              </button>
              <div
                v-show="modelDropdownOpen"
                class="absolute z-10 mt-1 max-h-64 w-full overflow-auto rounded-md border border-gray-200 bg-white py-1 shadow-lg"
              >
                <button
                  v-for="m in currentProviderModels"
                  :key="m.id"
                  type="button"
                  class="w-full px-3 py-2 text-left hover:bg-gray-50 focus:bg-gray-50 focus:outline-none"
                  :class="{ 'bg-primary-50': form.config.model === m.id }"
                  @click="selectModel(m.id)"
                >
                  <div class="font-medium text-gray-900">{{ m.label }}</div>
                  <div
                    v-if="(m.capabilities || []).length"
                    class="mt-1 flex flex-wrap gap-1"
                  >
                    <span
                      v-for="cap in m.capabilities || []"
                      :key="cap"
                      class="inline-flex rounded px-1.5 py-0.5 text-xs font-medium"
                      :class="capabilityTagClass(cap)"
                    >
                      {{ capabilityLabel(cap) }}
                    </span>
                  </div>
                  <div
                    v-if="refPriceLine(m.reference_pricing)"
                    class="mt-1 text-xs text-gray-500"
                  >
                    {{ refPriceLine(m.reference_pricing) }}
                  </div>
                </button>
                <div class="border-t border-gray-100" />
                <button
                  type="button"
                  class="w-full px-3 py-2 text-left font-medium text-gray-600 hover:bg-gray-50 focus:bg-gray-50 focus:outline-none"
                  :class="{ 'bg-primary-50': isCustomModel }"
                  @click="selectModel('__custom__')"
                >
                  {{ t('llm.config.modelCustom') }}
                </button>
              </div>
            </div>
            <div v-if="isCustomModel" class="mt-2">
              <input
                v-model="form.config.model"
                type="text"
                class="block w-full px-3 py-2 text-sm border border-gray-300 rounded-md shadow-sm focus:ring-primary-500 focus:border-primary-500"
                :placeholder="t('llm.config.modelPlaceholder')"
                @focus="modelDropdownOpen = false"
              />
            </div>
          </div>
          <div
            v-if="selectedModelInfo"
            class="rounded-lg border border-gray-200 bg-gray-50 p-3"
          >
            <p class="text-xs font-medium text-gray-600 mb-2">
              {{ t('llm.config.capabilities') }}
            </p>
            <div class="flex flex-wrap gap-1 mb-2">
              <span
                v-for="cap in selectedModelInfo.capabilities"
                :key="cap"
                class="inline-flex items-center rounded-md bg-primary-50 px-2 py-0.5 text-xs text-primary-800"
              >
                {{ capabilityLabel(cap) }}
              </span>
            </div>
            <div
              v-if="
                selectedModelInfo.max_input_tokens ||
                selectedModelInfo.max_output_tokens
              "
              class="flex flex-wrap gap-3 text-xs text-gray-600"
            >
              <span v-if="selectedModelInfo.max_input_tokens">
                {{ t('llm.config.maxInputTokens') }}:
                {{ selectedModelInfo.max_input_tokens.toLocaleString() }}
              </span>
              <span v-if="selectedModelInfo.max_output_tokens">
                {{ t('llm.config.maxOutputTokens') }}:
                {{ selectedModelInfo.max_output_tokens.toLocaleString() }}
              </span>
            </div>
            <div
              v-if="refPriceLine(selectedModelInfo.reference_pricing)"
              class="mt-2 text-xs text-gray-600"
            >
              <span class="font-medium text-gray-700">{{
                t('llm.config.referencePrice')
              }}</span>
              {{ refPriceLine(selectedModelInfo.reference_pricing) }}
            </div>
          </div>
          <div>
            <label class="block text-sm font-medium text-gray-700 mb-1">{{
              t('llm.config.apiBase')
            }}</label>
            <input
              v-model="form.config.api_base"
              type="url"
              class="block w-full px-3 py-2 text-sm border border-gray-300 rounded-md shadow-sm focus:ring-primary-500 focus:border-primary-500"
              :placeholder="defaultApiBasePlaceholder"
            />
          </div>
          <div>
            <label class="block text-sm font-medium text-gray-700 mb-1">{{
              t('llm.config.apiKey')
            }}</label>
            <input
              v-model="form.config.api_key"
              type="password"
              class="block w-full px-3 py-2 text-sm border border-gray-300 rounded-md shadow-sm focus:ring-primary-500 focus:border-primary-500"
              :placeholder="t('llm.config.apiKeyPlaceholder')"
            />
          </div>

          <div class="rounded-xl border border-gray-200 bg-gray-50/80 p-4">
            <h3 class="text-sm font-semibold text-gray-700">
              {{ t('llm.config.modelCapabilities') }}
            </h3>
            <p class="mt-1 text-xs text-gray-500">
              {{ t('llm.config.modelCapabilitiesHint') }}
            </p>
            <label
              v-if="isCustomProvider"
              class="mt-3 flex min-h-11 cursor-pointer items-center gap-3 rounded-lg p-2 hover:bg-gray-100 focus-within:ring-2 focus-within:ring-primary-500"
            >
              <input
                type="checkbox"
                :checked="form.config.vision === true"
                class="h-4 w-4 rounded border-gray-300 text-primary-600 focus:ring-primary-500"
                @change="updateParameterValue('vision', $event.target.checked)"
              />
              <span>
                <span class="block text-sm font-medium text-gray-700">
                  {{ t('llm.config.confirmVisionSupport') }}
                </span>
                <span class="block text-xs text-gray-500">
                  {{ t('llm.config.confirmVisionSupportHint') }}
                </span>
              </span>
            </label>
            <div v-else class="mt-3 text-sm text-gray-700">
              <span
                v-if="selectedModelInfo?.capabilities?.includes('vision')"
                class="inline-flex rounded-full bg-violet-100 px-2 py-1 text-xs font-medium text-violet-800"
              >
                {{ t('llm.config.visionDetected') }}
              </span>
              <span v-else class="text-xs text-gray-500">
                {{ t('llm.config.visionNotDetected') }}
              </span>
            </div>
          </div>

          <div
            v-if="advancedEditableParams.length"
            class="rounded-xl border border-gray-200 bg-gray-50/80 p-4"
          >
            <h3
              class="mb-3 flex items-center gap-2 text-sm font-semibold text-gray-700"
            >
              <svg
                class="h-4 w-4 text-gray-500"
                fill="none"
                stroke="currentColor"
                viewBox="0 0 24 24"
              >
                <path
                  stroke-linecap="round"
                  stroke-linejoin="round"
                  stroke-width="2"
                  d="M12 6V4m0 2a2 2 0 100 4m0-4a2 2 0 110 4m-6 8a2 2 0 100-4m0 4a2 2 0 110-4m0 4v2m0-6V4m6 6v10m6-2a2 2 0 100-4m0 4a2 2 0 110-4m0 4v2m0-6V4"
                />
              </svg>
              {{ t('llm.config.advancedOptions') }}
            </h3>
            <div class="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <div
                v-for="parameter in advancedEditableParams"
                :key="parameter"
                class="flex min-w-0 flex-col"
              >
                <label
                  class="mb-1 block h-5 truncate text-xs font-medium text-gray-600"
                  :title="parameter"
                >
                  {{ parameterLabel(parameter) }}
                </label>
                <input
                  :value="form.config[parameter]"
                  :type="parameterInputType(parameter)"
                  :step="parameterInputStep(parameter)"
                  :min="parameterInputMin(parameter)"
                  :max="parameterInputMax(parameter)"
                  class="block w-full rounded-md border border-gray-300 bg-white px-3 py-2 text-sm shadow-sm focus:border-primary-500 focus:ring-1 focus:ring-primary-500"
                  :placeholder="parameterPlaceholder(parameter)"
                  @input="updateParameterValue(parameter, $event.target.value)"
                />
              </div>
            </div>
          </div>

          <div
            v-if="formMessage"
            :class="
              formMessageSuccess
                ? 'text-green-600 text-sm'
                : 'text-red-600 text-sm'
            "
          >
            {{ formMessage }}
          </div>
          <div class="flex flex-wrap items-center justify-end gap-3">
            <BaseButton
              type="button"
              variant="outline"
              :loading="testLoading"
              @click="testConnection"
            >
              {{ t('llm.config.testConnection') }}
            </BaseButton>
            <BaseButton
              type="button"
              variant="outline"
              @click="closeConfigModal"
            >
              {{ t('common.cancel') }}
            </BaseButton>
            <BaseButton
              type="submit"
              variant="primary"
              :loading="formSaving"
              :disabled="submitDisabled"
            >
              {{ editingId ? t('common.save') : t('llm.config.addConfig') }}
            </BaseButton>
          </div>
        </form>
      </BaseModal>
    </div>
  </AdminLayout>
</template>

<script setup>
/**
 * LLM Configuration: Provider -> Model selection with capability tags.
 * API Base URL below model selection; defaults to official URL for the provider.
 */
import { CirclePlay, Pencil, Power, PowerOff, Star, Trash2 } from '@lucide/vue'
import { computed, nextTick, onMounted, reactive, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'

import { llmAdminApi } from '@/admin/api'
import { DEFAULT_LLM_REQUEST_TIMEOUT_SECONDS } from '@/admin/api/llmTimeout'
import AdminLayout from '@/admin/layout/AdminLayout.vue'
import ProviderIcon from '@/components/llm/ProviderIcon.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseLoading from '@/components/ui/BaseLoading.vue'
import BaseModal from '@/components/ui/BaseModal.vue'
import BaseSelect from '@/components/ui/BaseSelect.vue'
import MarkdownRenderer from '@/components/ui/MarkdownRenderer.vue'
import PaginationBar from '@/components/ui/PaginationBar.vue'
import RowActionMenu from '@/components/ui/RowActionMenu.vue'
import TableBulkActions from '@/components/ui/TableBulkActions.vue'
import { useTableSelection } from '@/composables/useTableSelection'
import { useToast } from '@/composables/useToast'

const PROVIDER_LABELS = {
  openai: 'OpenAI',
  openai_compatible: 'OpenAI Compatible',
  azure_openai: 'Azure OpenAI',
  gemini: 'Google Gemini',
  anthropic: 'Anthropic',
  mistral: 'Mistral',
  dashscope: 'Dashscope (Qwen)',
  deepseek: 'DeepSeek',
  xai: 'xAI (Grok)',
  minimax: 'MiniMax',
  moonshot: 'Moonshot (Kimi)',
  zai: 'Z.AI (GLM)',
  volcengine: 'Volcengine (Doubao)',
  meta_llama: 'Meta Llama',
  amazon_nova: 'Amazon Nova',
  nvidia_nim: 'NVIDIA NIM',
  openrouter: 'OpenRouter'
}

const CORE_CONFIG_PARAMS = new Set(['api_base', 'api_key', 'model'])
const COMMON_EDITABLE_PARAMS = [
  'api_base',
  'api_key',
  'model',
  'max_tokens',
  'temperature',
  'top_p',
  'request_timeout_seconds'
]
const CUSTOM_EDITABLE_PARAMS = [...COMMON_EDITABLE_PARAMS, 'vision']
const GLOBAL_PARAM_DEFAULTS = {
  max_tokens: 16384,
  temperature: 0.7,
  top_p: 1,
  request_timeout_seconds: DEFAULT_LLM_REQUEST_TIMEOUT_SECONDS
}
const PARAM_LABEL_KEYS = {
  api_version: 'apiVersion',
  deployment: 'deployment',
  max_tokens: 'maxOutputTokens',
  request_timeout_seconds: 'requestTimeoutSeconds',
  vision: 'visionCapability',
  temperature: 'temperature',
  top_p: 'topP'
}
const NUMERIC_PARAM_RULES = {
  max_tokens: { min: 1, step: 1 },
  request_timeout_seconds: { min: 1, step: 1 },
  temperature: { min: 0, max: 2, step: 0.1 },
  top_p: { min: 0, max: 1, step: 0.1 }
}

function providerLabel(provider) {
  return PROVIDER_LABELS[provider] || provider || '–'
}

function maskApiKey(value) {
  if (!value || typeof value !== 'string') return '–'
  const key = value.trim()
  if (!key) return '–'
  if (key.includes('***')) return key
  if (key.length <= 8) return '***'
  return `${key.slice(0, 4)}***${key.slice(-4)}`
}

const { t } = useI18n()
const { showError, showSuccess } = useToast()

const loading = ref(true)
const configList = ref([])
const currentPage = ref(1)
const pageSize = ref(20)
const bulkLoadingKey = ref('')
const deleteTarget = ref(null)
const userList = ref([])
const modelsData = ref({ providers: [], capability_labels: {} })
const providerSchemas = ref({})

const showConfigModal = ref(false)
const showTestModal = ref(false)
const testConfigRow = ref(null)
const testPrompt = ref('')
const testMaxTokens = ref(2048)
const testCallLoading = ref(false)
const testCallResult = ref(null)
const testStreaming = ref(true)
const streamingContent = ref('')
const streamingThinking = ref('')
const testCallAbortController = ref(null)
const editingId = ref(null)
const formSaving = ref(false)
const testLoading = ref(false)
const formMessage = ref('')
const formMessageSuccess = ref(false)
const connectionTestedSuccess = ref(false)
const modelDropdownOpen = ref(false)
const modelDropdownRef = ref(null)

const form = reactive({
  scope: 'global',
  user_id: null,
  provider: 'openai',
  config: {
    api_key: '',
    api_base: '',
    model: '',
    deployment: '',
    api_version: '2024-02-15-preview',
    max_tokens: null,
    temperature: null,
    top_p: null,
    request_timeout_seconds: null
  },
  is_active: true
})

const providersFromModels = computed(() => {
  return (modelsData.value?.providers || []).map((p) => ({
    id: p.id,
    label: p.label
  }))
})

const currentProviderSchema = computed(
  () => providerSchemas.value[form.provider] || null
)

const advancedEditableParams = computed(() =>
  getProviderEditableParams(form.provider).filter(
    (parameter) => !CORE_CONFIG_PARAMS.has(parameter) && parameter !== 'vision'
  )
)

const isCustomProvider = computed(() => form.provider === 'openai_compatible')

const currentProviderModels = computed(() => {
  const list = (modelsData.value?.providers || []).find(
    (p) => (p.id || '').toLowerCase() === (form.provider || '').toLowerCase()
  )
  return list?.models || []
})

const defaultApiBaseForProvider = computed(() => {
  if (currentProviderSchema.value?.default_api_base) {
    return currentProviderSchema.value.default_api_base
  }
  const p = (modelsData.value?.providers || []).find(
    (x) => (x.id || '').toLowerCase() === (form.provider || '').toLowerCase()
  )
  return p?.default_api_base || ''
})

const defaultApiBasePlaceholder = computed(() => {
  return defaultApiBaseForProvider.value || t('llm.config.apiBasePlaceholder')
})

const isCurrentModelInList = computed(() => {
  const id = (form.config.model || '').trim()
  if (!id) return false
  return currentProviderModels.value.some((m) => (m.id || '').trim() === id)
})

const isCustomModel = computed(() => {
  const list = currentProviderModels.value
  if (list.length === 0) return true
  return !isCurrentModelInList.value
})

const modelSelectTriggerLabel = computed(() => {
  if (isCustomModel.value) {
    return form.config.model
      ? `${t('llm.config.modelCustom')} ${form.config.model}`
      : t('llm.config.modelCustom')
  }
  const m = currentProviderModels.value.find(
    (x) => (x.id || '').trim() === (form.config.model || '').trim()
  )
  return m ? m.label : t('llm.config.modelSelect')
})

const submitDisabled = computed(() => {
  if (
    !editingId.value &&
    form.scope === 'user' &&
    userList.value.length === 0
  ) {
    return true
  }
  if (!editingId.value && !connectionTestedSuccess.value) {
    return true
  }
  return false
})

const testModalTitle = computed(() => {
  const row = testConfigRow.value
  if (!row) return t('llm.config.testCall')
  const prov = providerLabel(row.provider)
  const model = row.config?.model || '–'
  return `${t('llm.config.testCall')} · ${prov} / ${model}`
})

const testCallOk = computed(() => testCallResult.value?.ok === true)
const testCallContent = computed(() => testCallResult.value?.content ?? '')
const testCallDetail = computed(() => testCallResult.value?.detail ?? '')
const testCallUsage = computed(() => testCallResult.value?.usage ?? null)

const totalPages = computed(() =>
  Math.max(1, Math.ceil(configList.value.length / pageSize.value))
)

const pagedConfigList = computed(() => {
  const start = (currentPage.value - 1) * pageSize.value
  return configList.value.slice(start, start + pageSize.value)
})

const {
  allSelected,
  clearSelection,
  selectedIds,
  selectedRows,
  setAllSelected,
  setRowSelected,
  someSelected
} = useTableSelection(pagedConfigList, (row) => row.uuid || row.id)

const bulkActions = computed(() => [
  {
    key: 'enable',
    label: t('llm.config.enable'),
    icon: Power
  },
  {
    key: 'disable',
    label: t('llm.config.disable'),
    icon: PowerOff,
    variant: 'danger',
    confirm: true
  },
  {
    key: 'delete',
    label: t('common.delete'),
    icon: Trash2,
    variant: 'danger',
    confirm: true
  }
])

function handlePageSizeChange() {
  currentPage.value = 1
}

function goPrevPage() {
  if (currentPage.value <= 1) return
  currentPage.value -= 1
}

function goNextPage() {
  if (currentPage.value >= totalPages.value) return
  currentPage.value += 1
}

function unwrapMarkdownIfCodeBlock(raw) {
  if (typeof raw !== 'string' || !raw.trim()) return raw
  const trimmed = raw.trim()
  const openMatch = trimmed.match(/^```(?:markdown|md)?\s*\n?/i)
  const closeMatch = trimmed.match(/\n?```\s*$/)
  if (
    openMatch &&
    closeMatch &&
    trimmed.length > openMatch[0].length + closeMatch[0].length
  ) {
    return trimmed
      .slice(openMatch[0].length, trimmed.length - closeMatch[0].length)
      .trim()
  }
  return raw
}

const markdownContentForTest = computed(() => {
  const src =
    testCallLoading.value && testStreaming.value
      ? streamingContent.value
      : testCallContent.value
  return unwrapMarkdownIfCodeBlock(src || '')
})

const selectedModelInfo = computed(() => {
  const modelId = (form.config.model || '').trim()
  if (!modelId) return null
  const m = currentProviderModels.value.find(
    (x) => (x.id || '').trim() === modelId
  )
  if (!m) return null
  return {
    capabilities: m.capabilities || [],
    max_input_tokens: m.max_input_tokens,
    max_output_tokens: m.max_output_tokens,
    reference_pricing: m.reference_pricing || null
  }
})

function capabilityLabel(capKey) {
  const labels = modelsData.value?.capability_labels || {}
  return labels[capKey] || capKey
}

const CAP_TAG_CLASSES = {
  'text-to-text': 'bg-sky-100 text-sky-800',
  code: 'bg-emerald-100 text-emerald-800',
  vision: 'bg-violet-100 text-violet-800',
  multimodal: 'bg-indigo-100 text-indigo-800',
  'text-to-image': 'bg-amber-100 text-amber-800',
  'long-context': 'bg-orange-100 text-orange-800',
  'low-cost': 'bg-slate-100 text-slate-600',
  embedding: 'bg-teal-100 text-teal-800',
  reasoning: 'bg-rose-100 text-rose-800'
}

function capabilityTagClass(capKey) {
  return CAP_TAG_CLASSES[capKey] || 'bg-gray-100 text-gray-700'
}

function formatRefPrice(num) {
  if (num == null || typeof num !== 'number') return '–'
  return num % 1 === 0 ? String(num) : num.toFixed(2)
}

function refPriceLine(rp) {
  if (!rp || (rp.input_usd_per_1m == null && rp.output_usd_per_1m == null))
    return ''
  const inStr =
    rp.input_usd_per_1m != null
      ? `$${formatRefPrice(rp.input_usd_per_1m)}/1M in`
      : ''
  const outStr =
    rp.output_usd_per_1m != null
      ? `$${formatRefPrice(rp.output_usd_per_1m)}/1M out`
      : ''
  return [inStr, outStr].filter(Boolean).join(' · ')
}

function selectModel(modelId) {
  if (modelId === '__custom__') {
    form.config.model = ''
  } else {
    form.config.model = modelId
  }
  modelDropdownOpen.value = false
}

function closeModelDropdown(e) {
  const el = modelDropdownRef.value
  if (el && e.target && !el.contains(e.target)) {
    modelDropdownOpen.value = false
  }
}

watch(modelDropdownOpen, (open) => {
  if (open) {
    nextTick(() => document.addEventListener('click', closeModelDropdown))
  } else {
    document.removeEventListener('click', closeModelDropdown)
  }
})

function getRowCapabilities(row) {
  const provider = (row.provider || '').toLowerCase()
  const modelId = (row.config?.model || '').trim()
  if (!modelId) return []
  const prov = (modelsData.value?.providers || []).find(
    (p) => (p.id || '').toLowerCase() === provider
  )
  const model = prov?.models?.find((m) => (m.id || '').trim() === modelId)
  return model?.capabilities || []
}

function getProviderEditableParams(provider) {
  if (provider === 'openai_compatible') {
    return CUSTOM_EDITABLE_PARAMS
  }
  const editableParams = providerSchemas.value[provider]?.editable_params
  if (Array.isArray(editableParams) && editableParams.length) {
    return editableParams
  }
  if (provider === 'azure_openai') {
    return [
      'api_base',
      'api_key',
      'deployment',
      'model',
      'api_version',
      'max_tokens',
      'temperature',
      'top_p',
      'request_timeout_seconds'
    ]
  }
  return COMMON_EDITABLE_PARAMS
}

function getParameterDefault(provider, parameter) {
  const schema = providerSchemas.value[provider] || {}
  const providerDefault = schema[`default_${parameter}`]
  if (providerDefault !== null && providerDefault !== undefined) {
    return providerDefault
  }
  return GLOBAL_PARAM_DEFAULTS[parameter]
}

function getRowModelParameters(row) {
  const config = row.config || {}
  return getProviderEditableParams(row.provider)
    .filter((parameter) => !CORE_CONFIG_PARAMS.has(parameter))
    .map((parameter) => {
      const configuredValue = config[parameter]
      const hasConfiguredValue =
        configuredValue !== null &&
        configuredValue !== undefined &&
        configuredValue !== ''
      return {
        name: parameter,
        value: hasConfiguredValue
          ? configuredValue
          : getParameterDefault(row.provider, parameter),
        isDefault: !hasConfiguredValue
      }
    })
    .filter(
      (parameter) => parameter.value !== null && parameter.value !== undefined
    )
}

function parameterLabel(parameter) {
  const labelKey = PARAM_LABEL_KEYS[parameter]
  return labelKey ? t(`llm.config.${labelKey}`) : parameter
}

function parameterInputType(parameter) {
  return NUMERIC_PARAM_RULES[parameter] ? 'number' : 'text'
}

function parameterInputStep(parameter) {
  return NUMERIC_PARAM_RULES[parameter]?.step
}

function parameterInputMin(parameter) {
  return NUMERIC_PARAM_RULES[parameter]?.min
}

function parameterInputMax(parameter) {
  return NUMERIC_PARAM_RULES[parameter]?.max
}

function parameterPlaceholder(parameter) {
  const defaultValue = getParameterDefault(form.provider, parameter)
  if (defaultValue !== null && defaultValue !== undefined) {
    return t('llm.config.defaultParameterPlaceholder', {
      value: defaultValue
    })
  }
  return t('llm.config.optionalParameterPlaceholder')
}

function updateParameterValue(parameter, value) {
  if (parameter === 'vision') {
    form.config[parameter] = value === true
    return
  }
  if (value === '') {
    form.config[parameter] = null
    return
  }
  form.config[parameter] = NUMERIC_PARAM_RULES[parameter]
    ? Number(value)
    : value
}

function onProviderChange() {
  form.config.model = ''
  form.config.api_base = defaultApiBaseForProvider.value || ''
  const editableParams = new Set(getProviderEditableParams(form.provider))
  for (const parameter of Object.keys(form.config)) {
    if (!editableParams.has(parameter)) delete form.config[parameter]
  }
  connectionTestedSuccess.value = false
}

function resetForm() {
  form.scope = 'global'
  form.user_id = null
  form.provider = 'openai'
  form.config = {
    api_key: '',
    api_base: '',
    model: '',
    deployment: '',
    api_version: '2024-02-15-preview',
    max_tokens: null,
    temperature: null,
    top_p: null,
    request_timeout_seconds: null
  }
  form.is_active = true
  formMessage.value = ''
  connectionTestedSuccess.value = false
}

async function loadModels() {
  try {
    const data = await llmAdminApi.getLLMConfigModels()
    modelsData.value = {
      providers: data?.providers || [],
      capability_labels: data?.capability_labels || {}
    }
  } catch (e) {
    if (e?.response?.status !== 404) console.error(e)
    modelsData.value = { providers: [], capability_labels: {} }
  }
}

async function loadProviderSchemas() {
  try {
    const data = await llmAdminApi.getLLMConfigProviders()
    providerSchemas.value = data?.providers || {}
  } catch (e) {
    if (e?.response?.status !== 404) console.error(e)
    providerSchemas.value = {}
  }
}

async function loadAll() {
  loading.value = true
  try {
    const data = await llmAdminApi.getLLMConfigAll()
    configList.value = Array.isArray(data) ? data : []
  } catch (e) {
    if (e?.response?.status !== 404) console.error(e)
    configList.value = []
  } finally {
    loading.value = false
  }
}

async function loadUsers() {
  try {
    const data = await llmAdminApi.getUsers()
    userList.value = Array.isArray(data) ? data : []
  } catch {
    userList.value = []
  }
}

function openAddModal() {
  editingId.value = null
  resetForm()
  form.config.api_base = defaultApiBaseForProvider.value || ''
  loadUsers()
  showConfigModal.value = true
}

async function editConfig(row) {
  editingId.value = row.uuid || row.id
  try {
    const data = await llmAdminApi.getLLMConfigDetail(row.uuid || row.id)
    form.provider = (data?.provider || 'openai').toLowerCase()
    const c = data?.config || {}
    form.config = {
      ...c,
      api_key: c.api_key ?? '',
      api_base: c.api_base ?? '',
      model: c.model ?? '',
      deployment: c.deployment ?? '',
      api_version: c.api_version ?? '2024-02-15-preview',
      max_tokens: c.max_tokens ?? null,
      temperature: c.temperature ?? null,
      top_p: c.top_p ?? null,
      request_timeout_seconds: c.request_timeout_seconds ?? null
    }
    form.is_active = data?.is_active !== false
  } catch (e) {
    formMessage.value =
      e?.response?.data?.detail || e?.message || 'Failed to load'
    formMessageSuccess.value = false
  }
  showConfigModal.value = true
}

function closeConfigModal() {
  showConfigModal.value = false
  editingId.value = null
  resetForm()
}

function openTestModal(row) {
  testConfigRow.value = row
  testPrompt.value = ''
  testMaxTokens.value = 2048
  testCallResult.value = null
  streamingContent.value = ''
  showTestModal.value = true
}

function closeTestModal() {
  showTestModal.value = false
  testConfigRow.value = null
  testPrompt.value = ''
  testMaxTokens.value = 2048
  testCallResult.value = null
  streamingContent.value = ''
}

async function sendTestCall() {
  const row = testConfigRow.value
  if (!(row?.uuid || row?.id) || !testPrompt.value.trim()) return
  const boundedMaxTokens = Math.min(
    4096,
    Math.max(1, Number(testMaxTokens.value) || 2048)
  )
  testCallLoading.value = true
  testCallResult.value = null
  streamingContent.value = ''
  streamingThinking.value = ''
  testCallAbortController.value = null
  const body = {
    prompt: testPrompt.value.trim(),
    max_tokens: boundedMaxTokens,
    config_uuid: row.uuid || row.id
  }
  try {
    if (testStreaming.value) {
      const controller = new AbortController()
      testCallAbortController.value = controller
      await llmAdminApi.postLLMConfigTestCallStream(
        body,
        {
          onChunk(content) {
            streamingContent.value += content
          },
          onReasoning(content) {
            streamingThinking.value += content
          },
          onDone(usage) {
            testCallResult.value = {
              ok: true,
              content: streamingContent.value,
              usage,
              streaming: true,
              thinking: streamingThinking.value
            }
            testCallLoading.value = false
            testCallAbortController.value = null
          },
          onError(detail) {
            if (detail === 'aborted') {
              testCallResult.value = {
                ok: true,
                content: streamingContent.value,
                streaming: true,
                stopped: true,
                thinking: streamingThinking.value
              }
            } else {
              testCallResult.value = {
                ok: false,
                detail: detail || t('llm.config.testFailed'),
                streaming: true
              }
            }
            testCallLoading.value = false
            testCallAbortController.value = null
          }
        },
        controller.signal
      )
      if (testCallResult.value === null) {
        testCallLoading.value = false
        testCallAbortController.value = null
      }
    } else {
      const res = await llmAdminApi.postLLMConfigTestCall(body)
      testCallResult.value =
        res && typeof res === 'object' && !Array.isArray(res)
          ? { ...res, streaming: false }
          : res
    }
  } catch (e) {
    if (e?.name === 'AbortError') {
      testCallResult.value = {
        ok: true,
        content: streamingContent.value,
        streaming: true,
        stopped: true,
        thinking: streamingThinking.value
      }
    } else {
      testCallResult.value = {
        ok: false,
        detail:
          e?.response?.data?.detail ||
          e?.detail ||
          e?.message ||
          t('llm.config.testFailed'),
        streaming: testStreaming.value
      }
    }
  } finally {
    testCallLoading.value = false
    testCallAbortController.value = null
  }
}

function stopTestCallStream() {
  if (testCallAbortController.value) {
    testCallAbortController.value.abort()
  }
}

function buildFormConfigPayload(includeEmpty = false) {
  const payload = {}
  for (const parameter of getProviderEditableParams(form.provider)) {
    const value = form.config[parameter]
    if (value !== null && value !== undefined && value !== '') {
      payload[parameter] = value
    } else if (
      includeEmpty &&
      parameter !== 'api_key' &&
      !CORE_CONFIG_PARAMS.has(parameter)
    ) {
      payload[parameter] = null
    }
  }
  return payload
}

async function testConnection() {
  testLoading.value = true
  formMessage.value = ''
  try {
    const payload = {
      provider: form.provider,
      config: buildFormConfigPayload()
    }
    const res = await llmAdminApi.postLLMConfigTest(payload)
    if (res?.ok) {
      formMessage.value = t('llm.config.testSuccess')
      formMessageSuccess.value = true
      connectionTestedSuccess.value = true
    } else {
      formMessage.value = res?.detail || t('llm.config.testFailed')
      formMessageSuccess.value = false
    }
  } catch (e) {
    formMessage.value =
      e?.response?.data?.detail || e?.message || t('llm.config.testFailed')
    formMessageSuccess.value = false
  } finally {
    testLoading.value = false
  }
}

async function submitConfigForm() {
  if (!editingId.value && form.scope === 'user' && !form.user_id) {
    formMessage.value = t('llm.config.user') + '?'
    formMessageSuccess.value = false
    return
  }
  formSaving.value = true
  formMessage.value = ''
  try {
    const body = {
      provider: form.provider,
      config: buildFormConfigPayload(Boolean(editingId.value)),
      is_active: form.is_active
    }
    if (editingId.value) {
      await llmAdminApi.putLLMConfigDetail(editingId.value, body)
    } else {
      if (form.scope === 'user' && form.user_id) {
        body.scope = 'user'
        body.user_id = form.user_id
      }
      await llmAdminApi.postLLMConfig(body)
    }
    formMessage.value = t('llm.config.saveSuccess')
    formMessageSuccess.value = true
    closeConfigModal()
    await loadAll()
  } catch (e) {
    formMessage.value =
      e?.response?.data?.detail || e?.message || t('llm.config.saveError')
    formMessageSuccess.value = false
  } finally {
    formSaving.value = false
  }
}

async function setActive(row, value) {
  if (!(row?.uuid || row?.id)) return
  try {
    await updateActive(row, value)
    await loadAll()
  } catch (e) {
    showError(e?.response?.data?.detail || e?.message || t('common.error'))
  }
}

async function updateActive(row, value) {
  const id = row.uuid || row.id
  const data = await llmAdminApi.getLLMConfigDetail(id)
  const body = {
    provider: (data?.provider || row.provider || 'openai').toLowerCase(),
    config: { ...(data?.config || {}) },
    is_active: value
  }
  return llmAdminApi.putLLMConfigDetail(id, body)
}

function rowActions(row) {
  return [
    {
      key: 'test',
      label: t('llm.config.testCall'),
      icon: CirclePlay
    },
    ...(row.scope === 'global' && !row.is_default
      ? [
          {
            key: 'default',
            label: t('llm.config.setAsDefault'),
            icon: Star
          }
        ]
      : []),
    {
      key: 'toggle',
      label: row.is_active ? t('llm.config.disable') : t('llm.config.enable'),
      icon: row.is_active ? PowerOff : Power,
      variant: row.is_active ? 'danger' : undefined
    },
    {
      key: 'edit',
      label: t('common.edit'),
      icon: Pencil
    },
    {
      key: 'delete',
      label: t('common.delete'),
      icon: Trash2,
      variant: 'danger',
      divider: true
    }
  ]
}

function handleRowAction(action, row) {
  if (action === 'test') openTestModal(row)
  else if (action === 'default') setAsDefault(row)
  else if (action === 'toggle') setActive(row, !row.is_active)
  else if (action === 'edit') editConfig(row)
  else deleteTarget.value = row
}

async function setAsDefault(row) {
  if (!(row?.uuid || row?.id) || row.scope !== 'global') return
  try {
    const data = await llmAdminApi.getLLMConfigDetail(row.uuid || row.id)
    const c = data?.config || {}
    const body = {
      provider: (data?.provider || row.provider || 'openai').toLowerCase(),
      config: { ...c },
      is_active: data?.is_active !== false,
      is_default: true
    }
    await llmAdminApi.putLLMConfigDetail(row.uuid || row.id, body)
    await loadAll()
  } catch (e) {
    console.error(e)
  }
}

async function deleteConfig(row) {
  if (!(row?.uuid || row?.id)) return
  await llmAdminApi.deleteLLMConfigDetail(row.uuid || row.id)
}

async function confirmDeleteConfig() {
  const row = deleteTarget.value
  if (!row) return
  try {
    await deleteConfig(row)
    deleteTarget.value = null
    showSuccess(t('management.bulkDeleted', { count: 1 }))
    await loadAll()
  } catch (e) {
    showError(e?.response?.data?.detail || e?.message || t('common.error'))
  }
}

async function runBulkAction(action) {
  if (bulkLoadingKey.value) return
  const targets = selectedRows.value.filter(
    (row) => action === 'delete' || row.is_active !== (action === 'enable')
  )
  if (!targets.length) {
    showError(t('management.noEligibleRows'))
    return
  }

  bulkLoadingKey.value = action
  try {
    await llmAdminApi.bulkMutateLLMConfigs(
      targets.map((row) => row.uuid || row.id),
      action
    )
    const messageKey =
      action === 'delete' ? 'management.bulkDeleted' : 'management.bulkUpdated'
    showSuccess(t(messageKey, { count: targets.length }))
    await loadAll()
  } catch (e) {
    showError(e?.response?.data?.detail || e?.message || t('common.error'))
  } finally {
    bulkLoadingKey.value = ''
  }
}

onMounted(() => {
  loadProviderSchemas()
  loadModels()
  loadAll()
})
</script>

<style scoped>
.test-call-markdown {
  max-height: 28rem;
  overflow-y: auto;
}
.test-call-markdown :deep(.markdown-content) {
  @apply text-gray-800;
}
.test-call-markdown :deep(.markdown-content pre) {
  @apply text-xs;
}
</style>
