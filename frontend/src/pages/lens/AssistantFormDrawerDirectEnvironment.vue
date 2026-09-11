<template>
  <BaseDrawer
    :show="show"
    :title="drawerTitle"
    :subtitle="drawerSubtitle"
    width="3xl"
    @close="$emit('close')"
  >
    <!-- Wizard step indicator -->
    <div class="mb-6 flex items-center">
      <template v-for="(step, i) in wizardStepsMeta" :key="step.key">
        <div class="flex flex-col items-center">
          <div
            class="flex h-8 w-8 items-center justify-center rounded-full border-2 text-sm font-medium transition-colors"
            :class="
              i + 1 < wizardStep
                ? 'border-brand-600 bg-brand-600 text-white'
                : i + 1 === wizardStep
                  ? 'border-brand-600 text-brand-600'
                  : 'border-line text-ink-400'
            "
          >
            <svg
              v-if="i + 1 < wizardStep"
              class="h-4 w-4"
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
            >
              <path
                stroke-linecap="round"
                stroke-linejoin="round"
                stroke-width="2.5"
                d="M5 13l4 4L19 7"
              />
            </svg>
            <span v-else>{{ i + 1 }}</span>
          </div>
          <span
            class="mt-1 text-xs"
            :class="
              i + 1 === wizardStep
                ? 'font-medium text-brand-600'
                : 'text-ink-400'
            "
          >
            {{ step.title }}
          </span>
        </div>
        <div
          v-if="i < wizardStepsMeta.length - 1"
          class="mb-4 mx-1 h-px flex-1 bg-line"
        />
      </template>
    </div>

    <!-- Wizard Step 1 — Basics & Models -->
    <div v-if="wizardStep === 1" class="min-w-0 space-y-5 overflow-x-hidden">
      <p class="text-sm text-ink-500">{{ t('lensAdmin.wizard.step1Desc') }}</p>
      <FormRow :label="t('lensAdmin.fields.name')">
        <input v-model="form.name" class="form-input" required />
      </FormRow>
      <FormRow :label="t('lensAdmin.fields.description')">
        <textarea
          v-model="form.description"
          class="form-input min-h-24"
          :placeholder="t('lensAdmin.placeholders.assistantDescription')"
        />
        <p class="mt-1 text-xs text-ink-500">
          {{ t('lensAdmin.wizard.assistantDescriptionHint') }}
        </p>
      </FormRow>
      <FormRow :label="t('lensAdmin.fields.routingMode')">
        <BaseSelect
          v-model="form.mode"
          :disabled="mode === 'edit' && form.status !== 'active'"
        >
          <option value="direct">
            {{ t('lensAdmin.routingModes.direct') }}
          </option>
          <option value="smart">{{ t('lensAdmin.routingModes.smart') }}</option>
        </BaseSelect>
        <p class="mt-1 text-xs text-ink-500">
          {{
            t(
              `lensAdmin.routingModes.${isSmartMode ? 'smartHint' : 'directHint'}`
            )
          }}
        </p>
      </FormRow>
      <FormRow :label="t('lensAdmin.fields.slug')">
        <input
          v-model="form.slug"
          class="form-input form-input-mono"
          :maxlength="slugMaxLength"
          pattern="[-a-zA-Z0-9_]+"
          required
        />
        <p class="mt-1 flex items-start justify-between gap-3 text-xs">
          <span class="text-ink-500">{{ t('lensAdmin.wizard.slugHint') }}</span>
          <span class="shrink-0 tabular-nums" :class="slugLengthClass">
            {{ slugLength }}/{{ slugMaxLength }}
          </span>
        </p>
      </FormRow>
      <FormRow :label="t('lensAdmin.fields.agentModel') + ' *'">
        <BaseSelect v-model="form.agent_model_ref" required>
          <option value="">
            {{ t('lensAdmin.placeholders.selectModel') }}
          </option>
          <option v-for="c in llmConfigOptions" :key="c.uuid" :value="c.uuid">
            {{ formatLLMConfigLabel(c) }}
          </option>
        </BaseSelect>
        <p class="mt-1 text-xs text-ink-500">
          {{
            t(
              `lensAdmin.wizard.${isSmartMode ? 'smartAgentModelHint' : 'agentModelHint'}`
            )
          }}
        </p>
      </FormRow>
      <FormRow
        v-if="!isSmartMode"
        :label="t('lensAdmin.fields.multimodalModel')"
      >
        <BaseSelect v-model="form.multimodal_model_ref">
          <option value="">{{ t('lensAdmin.placeholders.noModel') }}</option>
          <option
            v-for="c in visionModelOptions"
            :key="c.uuid"
            :value="c.uuid"
            :disabled="!isVisionModelEligible(c)"
          >
            {{ formatLLMConfigLabel(c) }}
            {{ isVisionModelEligible(c) ? ' · Vision' : ' · Unavailable' }}
          </option>
        </BaseSelect>
        <p
          v-if="!visionModelOptions.some(isVisionModelEligible)"
          class="mt-1 text-xs text-amber-700"
        >
          {{ t('lensAdmin.wizard.noVisionModel') }}
          <router-link
            v-if="isAdmin"
            to="/management/llm/config"
            class="ml-1 font-medium text-brand-700 underline underline-offset-2 hover:text-brand-800"
          >
            {{ t('lensAdmin.wizard.configureVisionModel') }}
          </router-link>
        </p>
        <p class="mt-1 text-xs text-ink-500">
          {{ t('lensAdmin.wizard.multimodalModelHint') }}
        </p>
      </FormRow>
      <FormRow
        v-if="mode === 'edit'"
        :label="t('lensAdmin.fields.maxConcurrency')"
      >
        <input
          v-model.number="form.max_concurrency"
          type="number"
          min="1"
          max="50"
          class="form-input w-32"
        />
        <p class="mt-1 text-xs text-ink-500">
          {{ t('lensAdmin.wizard.maxConcurrencyHint') }}
        </p>
      </FormRow>
      <FormRow :label="t('lensAdmin.fields.agentRounds')">
        <div class="grid grid-cols-5 gap-2">
          <label
            v-for="tier in agentRoundsTiers"
            :key="tier.value"
            class="flex cursor-pointer flex-col items-center rounded-lg border-2 p-2 text-center transition-colors"
            :class="
              form.agent_rounds === tier.value
                ? 'border-brand-600 bg-brand-50 text-brand-700'
                : 'border-line bg-surface text-ink-600 hover:border-brand-300'
            "
          >
            <input
              type="radio"
              :value="tier.value"
              v-model="form.agent_rounds"
              class="sr-only execution-tier-radio"
            />
            <span class="text-sm font-medium">{{ tier.label }}</span>
            <span class="mt-0.5 text-xs opacity-60">{{ tier.hint }}</span>
          </label>
        </div>
      </FormRow>
    </div>

    <!-- Wizard Step 2 — Execution -->
    <div v-else-if="wizardStep === 2" class="space-y-4">
      <p class="text-sm text-ink-500">
        {{
          t(
            `lensAdmin.wizard.${form.mode === 'smart' ? 'step2SmartDesc' : 'step2Desc'}`
          )
        }}
      </p>
      <div v-if="form.mode === 'direct'" class="space-y-4">
        <div class="grid gap-4 md:grid-cols-2">
          <FormRow :label="t('lensAdmin.fields.type')">
            <div class="grid gap-2 sm:grid-cols-3" role="radiogroup">
              <label
                v-for="type in assistantTypeOptions"
                :key="type.value"
                class="flex cursor-pointer items-center gap-2 rounded-lg border px-3 py-2.5 text-sm transition-colors"
                :class="form.capability === type.value
                  ? 'border-brand-600 bg-brand-50 text-brand-700'
                  : 'border-line bg-surface text-ink-600 hover:border-brand-300'"
              >
                <input
                  v-model="form.capability"
                  type="radio"
                  :value="type.value"
                  class="sr-only"
                />
                <span
                  class="h-3 w-3 rounded-full border-2"
                  :class="form.capability === type.value
                    ? 'border-brand-600 bg-brand-600'
                    : 'border-line'"
                />
                <span>{{ type.label }}</span>
              </label>
            </div>
          </FormRow>
          <FormRow
            v-if="requiresNodeSelection"
            :label="t('lensAdmin.fields.lensnode')"
          >
            <BaseSelect
              v-model="form.lensnode_uuid"
              :required="requiresWorkspace"
            >
              <option value="">
                {{ t('lensAdmin.placeholders.selectLensNode') }}
              </option>
              <option
                v-for="ln in compatibleLensnodes"
                :key="ln.uuid"
                :value="ln.uuid"
              >
                {{ ln.name }}
              </option>
            </BaseSelect>
          </FormRow>
        </div>
      </div>
      <div
        v-else
        class="space-y-2 rounded-md border border-primary-200 bg-primary-50/50 p-3"
        data-testid="fixed-collaboration-members"
      >
        <div class="text-sm font-medium text-ink-800">
          {{ t('lensAdmin.wizard.collaborationMembers') }}
        </div>
        <p class="text-xs text-ink-600">
          {{ t('lensAdmin.wizard.collaborationMembersHint') }}
        </p>
        <div class="grid gap-2 sm:grid-cols-2">
          <label
            v-for="assistant in collaborationMemberOptions"
            :key="assistant.uuid"
            class="flex cursor-pointer items-center gap-2 rounded-md border border-line bg-surface px-3 py-2 text-sm"
          >
            <input
              v-model="form.collaboration_member_uuids"
              type="checkbox"
              :value="assistant.uuid"
              class="h-4 w-4 rounded border-line text-brand-600"
            />
            <span class="min-w-0 truncate">{{ assistant.name }}</span>
          </label>
        </div>
        <p
          v-if="!collaborationMemberOptions.length"
          class="text-xs text-amber-700"
        >
          {{ t('lensAdmin.wizard.noCollaborationMembers') }}
        </p>
      </div>
      <template v-if="form.mode === 'direct'">
        <fieldset class="datasource-picker space-y-4 rounded-xl border border-line bg-surface p-4">
          <div class="flex items-center justify-between gap-3">
            <legend class="text-sm font-semibold text-ink-900">{{ t('lensAdmin.datasourceSelection.title') }}</legend>
            <span class="text-xs text-ink-500">{{ selectedDatasourceCount }} {{ t('lensAdmin.datasourceSelection.selected') }}</span>
          </div>
          <div class="grid grid-cols-3 gap-1 rounded-lg bg-surface-sunken p-1" role="radiogroup">
            <label v-for="value in ['auto', 'selected', 'all']" :key="value" class="cursor-pointer rounded-md px-2 py-2 text-center text-xs font-medium transition-colors" :class="form.settings.datasource_routing === value ? 'bg-surface text-brand-700 shadow-sm' : 'text-ink-500 hover:text-ink-700'">
              <input v-model="form.settings.datasource_routing" type="radio" :value="value" class="sr-only" />
              {{ t(`lensAdmin.datasourceSelection.${value}`) }}
            </label>
          </div>
          <p class="text-xs text-ink-500">{{ t('lensAdmin.datasourceSelection.scope') }}</p>
          <details v-if="datasourceOptions.length" class="relative">
            <summary class="flex cursor-pointer items-center justify-between rounded-lg border border-line bg-surface-sunken px-3 py-2.5 text-sm text-ink-700">
              <span>{{ selectedDatasourceCount ? `${selectedDatasourceCount} ${t('lensAdmin.datasourceSelection.selected')}` : t('lensAdmin.datasourceSelection.placeholder') }}</span>
              <span class="text-xs text-ink-400">⌄</span>
            </summary>
            <div class="absolute z-10 mt-1 max-h-72 w-full overflow-y-auto rounded-lg border border-line bg-surface p-2 shadow-lg">
              <label v-for="source in datasourceOptions" :key="source.uuid" class="flex cursor-pointer items-center gap-2 rounded-md px-2 py-2 text-sm hover:bg-surface-sunken">
                <input type="checkbox" :checked="hasSource(source)" :disabled="saving || source.status !== 'active'" class="h-4 w-4 rounded border-line text-brand-600" @change="selectSource(source, null, $event.target.checked)" />
                <span class="min-w-0 flex-1 truncate">{{ source.name }}</span>
                <span class="text-xs text-ink-400">{{ source.items?.length || 0 }}</span>
              </label>
              <div v-for="source in datasourceOptions" :key="`${source.uuid}-items`">
                <div v-if="hasSource(source) && source.items?.length > 1" class="ml-6 border-l border-line pl-2">
                  <label v-for="item in source.items" :key="item.uuid" class="flex cursor-pointer items-center gap-2 py-1.5 text-xs text-ink-600">
                    <input type="checkbox" :checked="hasSource(source, item)" :disabled="saving || source.status !== 'active' || item.status !== 'active'" class="h-3.5 w-3.5 rounded border-line text-brand-600" @change="selectSource(source, item, $event.target.checked)" />
                    <span class="truncate">{{ item.name }}</span>
                  </label>
                </div>
              </div>
            </div>
          </details>
          <p v-else class="rounded-lg bg-surface-sunken p-4 text-center text-sm text-ink-500">{{ t('lensAdmin.datasourceSelection.empty') }}</p>
        </fieldset>
        <div v-if="requiresWorkspace && !form.datasource_bindings?.length && (!form.settings.datasource_routing || form.settings.datasource_routing === 'selected')">
          <div class="mb-1 flex items-center justify-between">
            <span class="text-sm font-medium text-ink-700">{{
              t('lensAdmin.fields.selectedDirs')
            }}</span>
            <button
              v-if="form.lensnode_uuid"
              type="button"
              class="flex items-center gap-1 rounded-md px-2 py-1 text-xs text-ink-500 transition-colors hover:bg-surface-sunken hover:text-ink-700 disabled:opacity-40"
              :disabled="refreshingDirs"
              @click="$emit('refresh-dirs')"
            >
              <svg
                class="h-3.5 w-3.5"
                :class="{ 'animate-spin': refreshingDirs }"
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
              {{ t('common.refresh') }}
            </button>
          </div>
          <BaseSelect
            v-if="selectedLensNodeDirs.length"
            v-model="selectedDirPath"
            class="font-mono"
          >
            <option value="">
              {{ t('lensAdmin.placeholders.selectDir') }}
            </option>
            <option
              v-for="dir in selectedLensNodeDirs"
              :key="dir.path"
              :value="dir.path"
            >
              {{ dir.path }}
            </option>
          </BaseSelect>
          <div
            v-else
            class="rounded-md border border-line bg-surface-sunken p-3 text-sm text-ink-500"
          >
            {{ t('lensAdmin.placeholders.noDirs') }}
          </div>
          <div v-if="selectedDirPath" class="mt-2">
            <label class="mb-1 block text-xs font-medium text-ink-500">
              {{ t('lensAdmin.fields.includePaths') }}
            </label>
            <textarea
              class="form-input min-h-20 font-mono"
              :placeholder="t('lensAdmin.placeholders.includePaths')"
              :value="selectedDirScopeText(selectedDirPath)"
              @input="updateDirScope(selectedDirPath, $event.target.value)"
            />
          </div>
        </div>
        <div
          v-else-if="isGeneralChatTask"
          class="rounded-md border border-primary-200 bg-primary-50 p-3 text-sm text-primary-700"
        >
          {{ t('lensAdmin.wizard.generalChatExecutionHint') }}
        </div>
        <FormRow
          v-if="requiresWorkspace"
          :label="t('lensAdmin.fields.retrievalPolicy')"
        >
          <div
            class="grid gap-3 rounded-md border border-line bg-surface-sunken p-3"
          >
            <label class="block text-xs font-medium text-ink-600">
              {{ t('lensAdmin.fields.excludeExtensions') }}
              <textarea
                v-model="form.exclude_extensions_text"
                class="form-input mt-1 min-h-28 font-mono"
                :placeholder="t('lensAdmin.placeholders.extensions')"
              />
            </label>
            <label class="block text-xs font-medium text-ink-600">
              {{ t('lensAdmin.fields.excludeDirs') }}
              <textarea
                v-model="form.exclude_dirs_text"
                class="form-input mt-1 min-h-28 font-mono"
                :placeholder="t('lensAdmin.placeholders.excludeDirs')"
              />
            </label>
          </div>
        </FormRow>
        <FormRow
          v-if="isCodeAnalysisTask"
          :label="t('lensAdmin.fields.enableCodegraph')"
        >
          <label
            class="flex cursor-pointer items-center gap-3 rounded-md border border-line bg-surface-sunken p-3"
          >
            <input
              type="checkbox"
              v-model="form.enable_codegraph"
              class="h-4 w-4 flex-shrink-0 rounded border-line text-brand-600 focus:ring-brand-500"
            />
            <span class="text-sm text-ink-700">{{
              t('lensAdmin.fields.enableCodegraphHint')
            }}</span>
          </label>
        </FormRow>
      </template>
    </div>

    <!-- Wizard Step 3 — Workspace, Skills, Environment & MCP -->
    <div v-else-if="wizardStep === 3" class="space-y-5">
      <p class="text-sm text-ink-500">
        {{
          t(`lensAdmin.wizard.${isSmartMode ? 'step3SmartDesc' : 'step3Desc'}`)
        }}
      </p>
      <div
        v-if="isSmartMode"
        class="rounded-md border border-primary-200 bg-primary-50 p-3 text-sm text-primary-700"
      >
        {{ t('lensAdmin.wizard.smartResourcesHint') }}
      </div>
      <div>
        <span class="text-sm font-medium text-ink-700">{{
          t('lensAdmin.wizard.contextLabel')
        }}</span>
        <p class="mb-2 text-xs text-ink-500">
          {{
            t(
              `lensAdmin.wizard.${
                isSmartMode ? 'smartContextHint' : 'contextHint'
              }`
            )
          }}
        </p>
        <textarea
          v-model="form.workspace_guide_overview"
          class="form-input min-h-60"
          :placeholder="t('lensAdmin.wizard.contextPlaceholder')"
        />
      </div>
      <template v-if="!isSmartMode">
        <div>
          <div class="mb-2 text-sm font-medium text-ink-700">
            {{ t('lensAdmin.wizard.skillsSection') }}
          </div>
          <p v-if="isGeneralChatTask" class="mb-2 text-xs text-ink-500">
            {{ t('lensAdmin.wizard.generalChatSkillsHint') }}
          </p>
          <div v-if="selectableSkills.length" class="space-y-2">
            <div class="relative">
              <Search
                :size="16"
                class="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-ink-400"
                aria-hidden="true"
              />
              <input
                v-model="skillSearch"
                class="form-input skill-search-input"
                type="search"
                :placeholder="t('lensAdmin.wizard.searchSkills')"
                :aria-label="t('lensAdmin.wizard.searchSkills')"
                autocomplete="off"
              />
            </div>
            <p class="text-xs text-ink-500" aria-live="polite">
              {{
                t('lensAdmin.wizard.skillSearchResults', {
                  count: filteredSelectableSkills.length,
                  total: selectableSkills.length
                })
              }}
            </p>
            <div
              v-if="filteredSelectableSkills.length"
              class="max-h-96 space-y-2 overflow-y-auto rounded-md border border-line bg-surface-sunken p-2"
              data-testid="assistant-skill-options"
            >
              <div
                v-for="skill in filteredSelectableSkills"
                :key="skill.uuid"
                data-testid="assistant-skill-option"
                class="overflow-hidden rounded-md border bg-surface transition-colors"
                :class="
                  isSkillSelected(skill.uuid)
                    ? 'border-primary-300 bg-primary-50/60'
                    : 'border-line hover:border-primary-200'
                "
              >
                <label
                  class="group flex cursor-pointer items-start gap-3 px-3 py-2.5 transition-colors hover:bg-primary-50/40"
                >
                  <input
                    type="checkbox"
                    :value="skill.uuid"
                    v-model="form.skill_uuids"
                    class="sr-only"
                  />
                  <span
                    class="mt-0.5 flex h-5 w-5 flex-shrink-0 items-center justify-center rounded border transition-colors"
                    :class="
                      isSkillSelected(skill.uuid)
                        ? 'border-primary-600 bg-primary-600 text-white'
                        : 'border-line bg-surface text-transparent group-hover:border-primary-300'
                    "
                  >
                    <svg
                      class="h-3.5 w-3.5"
                      fill="none"
                      stroke="currentColor"
                      viewBox="0 0 24 24"
                    >
                      <path
                        stroke-linecap="round"
                        stroke-linejoin="round"
                        stroke-width="3"
                        d="M5 13l4 4L19 7"
                      />
                    </svg>
                  </span>
                  <div class="min-w-0 flex-1 space-y-1">
                    <div class="flex min-w-0 items-start justify-between gap-2">
                      <div
                        class="min-w-0 truncate text-sm font-semibold text-ink-900"
                      >
                        {{ skill.name }}
                      </div>
                    </div>
                    <div class="truncate text-xs text-ink-400">
                      {{ skill.package_name || skill.uuid }}
                    </div>
                    <p
                      v-if="skillDescription(skill)"
                      class="line-clamp-2 text-xs leading-5 text-ink-500"
                    >
                      {{ skillDescription(skill) }}
                    </p>
                  </div>
                </label>
                <div
                  v-if="isSkillSelected(skill.uuid) && skillEnvironment(skill).length"
                  class="px-3 pb-3"
                  data-testid="assistant-skill-environments"
                >
                  <div class="ml-8 border-l border-primary-200 pl-3">
                    <div class="space-y-2.5">
                      <div
                        class="space-y-2.5 rounded-md bg-surface-sunken p-2.5"
                      >
                        <div
                          v-for="item in skillEnvironment(skill)"
                          :key="item.name"
                          class="flex items-center gap-2"
                        >
                          <label
                            class="w-40 flex-shrink-0 truncate font-mono text-[11px] font-medium text-ink-700"
                            :for="`skill-environment-${skill.uuid}-${item.name}`"
                          >
                            {{ item.name
                            }}<span v-if="item.required" class="text-danger-600"
                              >*</span
                            >
                          </label>
                          <div class="relative min-w-0 flex-1">
                            <input
                              :id="`skill-environment-${skill.uuid}-${item.name}`"
                              v-model="
                                environmentDraft(skill.uuid).values[item.name]
                              "
                              class="form-input w-full pr-10 skill-environment-input font-mono"
                              :type="
                                isEnvironmentRevealed(skill.uuid, item.name)
                                  ? 'text'
                                  : 'password'
                              "
                              :placeholder="environmentInputPlaceholder(item)"
                              :aria-label="item.name"
                              autocomplete="off"
                            />
                            <button
                              type="button"
                              class="absolute right-1 top-1/2 inline-flex h-7 w-7 -translate-y-1/2 items-center justify-center rounded-md text-ink-400 transition-colors hover:bg-surface-sunken hover:text-ink-700"
                              :aria-label="
                                environmentRevealLabel(skill.uuid, item.name)
                              "
                              :title="
                                environmentRevealLabel(skill.uuid, item.name)
                              "
                              @click="
                                toggleEnvironmentReveal(skill.uuid, item.name)
                              "
                            >
                              <component
                                :is="
                                  isEnvironmentRevealed(skill.uuid, item.name)
                                    ? EyeOffIcon
                                    : EyeIcon
                                "
                                class="h-4 w-4"
                                aria-hidden="true"
                              />
                            </button>
                          </div>
                        </div>
                        <p
                          v-if="
                            hasRequiredEnvironment(skill) &&
                            !skillEnvironmentConfigured(skill)
                          "
                          class="rounded-md border border-primary-200 bg-primary-50 px-3 py-2 text-[11px] leading-4 text-primary-700"
                          role="status"
                        >
                          {{
                            t('lensAdmin.wizard.skillEnvironmentRequiredHint')
                          }}
                        </p>
                      </div>
                    </div>
                  </div>
                </div>
              </div>
            </div>
            <div
              v-else
              class="rounded-md border border-line bg-surface-sunken p-3 text-sm text-ink-500"
              role="status"
            >
              {{ t('lensAdmin.wizard.noMatchingSkills') }}
            </div>
          </div>
          <div
            v-else
            class="rounded-md border border-line bg-surface-sunken p-3 text-sm text-ink-500"
          >
            {{ t('lensAdmin.wizard.noSkills') }}
          </div>
        </div>
        <div>
          <div class="mb-2 text-sm font-medium text-ink-700">
            {{ t('lensAdmin.wizard.mcpSection') }}
          </div>
          <div
            v-if="mcps.length"
            class="space-y-2 rounded-md border border-line bg-surface-sunken p-2"
          >
            <div
              v-for="mcp in orderedMcps"
              :key="mcp.uuid"
              class="overflow-hidden rounded-md border bg-surface transition-colors"
              :class="
                isMcpSelected(mcp.uuid)
                  ? 'border-primary-300 bg-primary-50/60'
                  : 'border-line hover:border-primary-200'
              "
            >
              <label
                class="flex cursor-pointer items-center gap-3 px-3 py-2.5 transition-colors hover:bg-primary-50/40"
              >
                <input
                  type="checkbox"
                  :value="mcp.uuid"
                  v-model="form.mcp_uuids"
                  class="h-4 w-4 flex-shrink-0 rounded border-line text-brand-600 focus:ring-brand-500"
                />
                <div class="min-w-0 flex-1">
                  <div class="text-sm font-medium text-ink-900">
                    {{ mcp.name }}
                  </div>
                  <div class="truncate text-xs text-ink-400">
                    {{ mcp.transport }} · {{ mcp.endpoint || emptyValue }}
                  </div>
                </div>
                <StatusBadge :status="mcp.enabled ? 'enabled' : 'disabled'" />
              </label>
              <div
                v-if="isMcpSelected(mcp.uuid) && mcpEnvironment(mcp).length"
                class="px-3 pb-3"
                data-testid="assistant-mcp-environments"
              >
                <div class="ml-7 border-l border-primary-200 pl-3">
                  <div class="flex items-center justify-between gap-3">
                    <h3 class="text-xs font-semibold text-ink-700">
                      {{ t('lensAdmin.wizard.environmentSection') }}
                    </h3>
                    <span
                      v-if="hasRequiredMcpEnvironment(mcp)"
                      class="flex-shrink-0 text-[11px] font-medium text-danger-600"
                    >
                      {{ t('lensAdmin.wizard.environmentRequired') }}
                    </span>
                  </div>
                  <p class="mt-1 text-[11px] leading-4 text-ink-500">
                    {{ t('lensAdmin.wizard.mcpEnvironmentSectionHint') }}
                  </p>
                  <div class="mt-2 space-y-2.5">
                    <p class="text-[11px] leading-4 text-ink-500">
                      {{
                        t('lensAdmin.wizard.mcpEnvironmentConfigurationHint', {
                          count: mcpEnvironment(mcp).length
                        })
                      }}
                    </p>
                    <BaseSelect
                      v-model="form.mcp_environment_set_uuids[mcp.uuid]"
                      class="text-xs"
                      :aria-label="t('lensAdmin.wizard.selectEnvironmentSet')"
                    >
                      <option value="">
                        {{ t('lensAdmin.wizard.selectEnvironmentSet') }}
                      </option>
                      <option value="__new__">
                        {{ t('lensAdmin.wizard.createEnvironmentSet') }}
                      </option>
                      <option
                        v-for="variableSet in enabledEnvironmentVariableSets"
                        :key="variableSet.uuid"
                        :value="variableSet.uuid"
                      >
                        {{ variableSet.name }}
                      </option>
                    </BaseSelect>
                    <EnvironmentSetValues
                      v-if="
                        form.mcp_environment_set_uuids[mcp.uuid] &&
                        form.mcp_environment_set_uuids[mcp.uuid] !== '__new__'
                      "
                      :variable-set="selectedMcpEnvironmentSet(mcp.uuid)"
                      :allowed-keys="
                        mcpEnvironment(mcp).map((item) => item.name)
                      "
                    />
                    <p
                      v-if="!form.mcp_environment_set_uuids[mcp.uuid]"
                      class="rounded-md border border-primary-200 bg-primary-50 px-3 py-2 text-[11px] leading-4 text-primary-700"
                      role="status"
                    >
                      {{ t('lensAdmin.wizard.environmentSetRequiredHint') }}
                    </p>
                    <div
                      v-if="
                        form.mcp_environment_set_uuids[mcp.uuid] === '__new__'
                      "
                      class="space-y-1"
                    >
                      <label class="text-[11px] font-medium text-ink-700">
                        {{ t('lensAdmin.wizard.environmentSetNameLabel') }}
                      </label>
                      <input
                        v-model="mcpEnvironmentDraft(mcp.uuid).name"
                        class="form-input skill-environment-input"
                        type="text"
                        :placeholder="t('lensAdmin.wizard.environmentSetName')"
                        :aria-label="
                          t('lensAdmin.wizard.environmentSetNameLabel')
                        "
                        maxlength="160"
                        autocomplete="off"
                      />
                      <p class="text-[11px] leading-4 text-ink-500">
                        {{ t('lensAdmin.wizard.environmentSetNameHint') }}
                      </p>
                    </div>
                    <div
                      v-if="form.mcp_environment_set_uuids[mcp.uuid]"
                      class="space-y-2.5 rounded-md bg-surface-sunken p-2.5"
                    >
                      <div
                        v-for="item in mcpEnvironment(mcp)"
                        :key="item.name"
                        class="space-y-1"
                      >
                        <div class="flex items-center justify-between gap-3">
                          <label
                            class="font-mono text-[11px] font-medium text-ink-700"
                          >
                            {{ item.name
                            }}<span
                              v-if="isMcpEnvironmentRequired(mcp, item)"
                              class="text-danger-600"
                            >
                              *</span
                            >
                          </label>
                          <span
                            v-if="mcpEnvironmentItemSaved(mcp, item)"
                            class="text-[11px] text-success-700"
                          >
                            {{ t('lensAdmin.wizard.environmentConfigured') }}
                          </span>
                        </div>
                        <input
                          v-model="
                            mcpEnvironmentDraft(mcp.uuid).values[item.name]
                          "
                          class="form-input skill-environment-input font-mono"
                          :type="item.secret ? 'password' : 'text'"
                          :placeholder="environmentInputPlaceholder(item)"
                          :aria-label="item.name"
                          autocomplete="off"
                        />
                      </div>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </div>
          <div
            v-else
            class="rounded-md border border-line bg-surface-sunken p-3 text-sm text-ink-500"
          >
            {{ t('lensAdmin.wizard.noMcp') }}
          </div>
        </div>
        <div>
          <div class="mb-2 text-sm font-medium text-ink-700">
            {{ t('lensAdmin.wizard.pluginSection') }}
          </div>
          <div
            class="space-y-3 rounded-md border border-line bg-surface-sunken p-3"
          >
            <p
              v-if="missingSkillPluginRequirements.length"
              class="text-xs leading-5 text-warning-700"
            >
              {{
                t('lensAdmin.wizard.pluginRequirementsMissing', {
                  requirements: missingSkillPluginRequirements.join('; ')
                })
              }}
            </p>
            <div
              v-for="connection in activePluginConnections"
              :key="connection.uuid"
              class="rounded-md border border-line bg-surface p-3"
            >
              <label class="flex cursor-pointer items-center gap-3">
                <input
                  type="checkbox"
                  class="h-4 w-4 flex-shrink-0 rounded border-line text-brand-600 focus:ring-brand-500"
                  :checked="Boolean(pluginBinding(connection.uuid))"
                  @change="
                    togglePluginConnection(connection, $event.target.checked)
                  "
                />
                <span
                  class="flex h-9 w-9 flex-shrink-0 items-center justify-center overflow-hidden rounded-lg border border-line bg-surface-sunken"
                >
                  <img
                    v-if="pluginIconUrl(connection.plugin_key)"
                    :src="pluginIconUrl(connection.plugin_key)"
                    :alt="pluginDisplayName(connection.plugin_key)"
                    class="h-full w-full object-cover"
                  />
                  <span
                    v-else
                    class="text-xs font-semibold uppercase text-brand-700"
                  >
                    {{ connection.plugin_key.slice(0, 2) }}
                  </span>
                </span>
                <span class="min-w-0 flex-1">
                  <span class="block text-sm font-medium text-ink-900">
                    {{ connection.name }}
                  </span>
                  <span class="mt-0.5 block truncate text-xs text-ink-500">
                    {{ pluginDisplayName(connection.plugin_key) }}
                  </span>
                </span>
              </label>
            </div>
            <p
              v-if="!activePluginConnections.length"
              class="text-xs text-ink-500"
            >
              {{ t('lensAdmin.wizard.pluginConnectionOptional') }}
            </p>
          </div>
        </div>
      </template>
    </div>

    <!-- Wizard Step 4 — Authorization -->
    <div v-else-if="wizardStep === 4" class="space-y-5">
      <p class="text-sm text-ink-500">{{ t('lensAdmin.wizard.step4Desc') }}</p>
      <FormRow :label="t('lensAdmin.fields.visibility')">
        <div class="grid grid-cols-2 gap-3">
          <label
            v-for="opt in ['public', 'private']"
            :key="opt"
            class="flex cursor-pointer items-start gap-3 rounded-lg border-2 p-3 transition-colors"
            :class="
              form.visibility === opt
                ? opt === 'private'
                  ? 'border-amber-400 bg-amber-50'
                  : 'border-emerald-400 bg-emerald-50'
                : 'border-line bg-surface hover:border-brand-300'
            "
          >
            <input
              type="radio"
              :value="opt"
              v-model="form.visibility"
              class="sr-only"
            />
            <component
              :is="opt === 'private' ? LockIcon : GlobeIcon"
              class="mt-0.5 h-5 w-5 flex-shrink-0"
              :class="
                form.visibility === opt
                  ? opt === 'private'
                    ? 'text-amber-600'
                    : 'text-emerald-600'
                  : 'text-ink-400'
              "
            />
            <div class="min-w-0">
              <div
                class="text-sm font-semibold"
                :class="
                  form.visibility === opt ? 'text-ink-900' : 'text-ink-600'
                "
              >
                {{ t(`lensAdmin.visibility.${opt}`) }}
              </div>
              <div class="mt-0.5 text-xs leading-5 text-ink-500">
                {{ t(`lensAdmin.visibility.${opt}Desc`) }}
              </div>
            </div>
          </label>
        </div>
        <p class="mt-2 text-xs text-ink-500">
          {{ t('lensAdmin.wizard.visibilityHint') }}
        </p>
      </FormRow>

      <div v-if="form.visibility === 'private'" class="space-y-4">
        <div
          class="flex items-start gap-2 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-700"
        >
          <LockIcon class="mt-0.5 h-4 w-4 flex-shrink-0" />
          <span>{{ t('lensAdmin.access.hint') }}</span>
        </div>
        <div class="grid gap-4 md:grid-cols-2">
          <div
            class="overflow-hidden rounded-lg border border-line"
            data-testid="authorized-groups-selector"
          >
            <div
              class="flex items-center justify-between border-b border-line bg-surface-sunken px-3 py-2"
            >
              <div
                class="flex items-center gap-2 text-sm font-medium text-ink-700"
              >
                <UsersIcon class="h-4 w-4 text-ink-400" />
                {{ t('lensAdmin.access.groups') }}
              </div>
              <span
                data-testid="authorized-groups-count"
                class="rounded-full bg-surface px-2 py-0.5 text-xs font-medium text-ink-500"
              >
                {{ form.access_group_ids.length }}
              </span>
            </div>
            <div class="border-b border-line p-2">
              <input
                v-model="groupSearch"
                type="search"
                class="form-input"
                data-testid="authorized-group-search"
                :aria-label="t('lensAdmin.access.searchGroups')"
                :placeholder="t('lensAdmin.access.searchGroupsPlaceholder')"
              />
            </div>
            <div
              v-if="orderedGroups.length"
              class="max-h-52 space-y-1 overflow-y-auto p-2"
              @scroll.passive="maybeLoadMoreGroups"
            >
              <label
                v-for="g in orderedGroups"
                :key="g.id"
                data-testid="authorized-group-option"
                class="flex cursor-pointer items-center gap-2.5 rounded-md border px-2.5 py-2 text-sm transition-colors"
                :class="
                  form.access_group_ids.includes(g.id)
                    ? 'border-brand-300 bg-brand-50 text-ink-900'
                    : 'border-transparent text-ink-700 hover:bg-surface-sunken'
                "
              >
                <input
                  type="checkbox"
                  :value="g.id"
                  v-model="form.access_group_ids"
                  class="h-4 w-4 flex-shrink-0 rounded border-line text-brand-600 focus:ring-brand-500"
                />
                <UsersIcon class="h-4 w-4 flex-shrink-0 text-ink-400" />
                <span class="truncate">{{ g.name }}</span>
              </label>
            </div>
            <p
              v-if="groupLoading"
              class="px-3 py-3 text-center text-xs text-ink-400"
            >
              {{ t('lensAdmin.access.loadingGroups') }}
            </p>
            <p
              v-else-if="groupFailed"
              class="px-3 py-3 text-center text-xs text-danger-700"
            >
              {{ t('lensAdmin.access.groupsFailed') }}
              <button
                type="button"
                class="ml-1 font-medium underline"
                @click="loadGroups()"
              >
                {{ t('lensAdmin.access.retry') }}
              </button>
            </p>
            <p
              v-else-if="!orderedGroups.length"
              class="px-3 py-8 text-center text-xs text-ink-400"
            >
              {{
                groupSearch.trim()
                  ? t('lensAdmin.access.noGroupResults')
                  : t('lensAdmin.access.noGroups')
              }}
            </p>
          </div>

          <div
            class="overflow-hidden rounded-lg border border-line"
            data-testid="authorized-users-selector"
          >
            <div
              class="flex items-center justify-between border-b border-line bg-surface-sunken px-3 py-2"
            >
              <div
                class="flex items-center gap-2 text-sm font-medium text-ink-700"
              >
                <UserIcon class="h-4 w-4 text-ink-400" />
                {{ t('lensAdmin.access.users') }}
              </div>
              <span
                data-testid="authorized-users-count"
                class="rounded-full bg-surface px-2 py-0.5 text-xs font-medium text-ink-500"
              >
                {{ form.access_user_ids.length }}
              </span>
            </div>
            <div class="border-b border-line p-2">
              <input
                v-model="userSearch"
                type="search"
                class="form-input"
                data-testid="authorized-user-search"
                :aria-label="t('lensAdmin.access.searchUsers')"
                :placeholder="t('lensAdmin.access.searchUsersPlaceholder')"
              />
            </div>
            <div
              v-if="orderedUsers.length"
              class="max-h-52 space-y-1 overflow-y-auto p-2"
              @scroll.passive="maybeLoadMoreUsers"
            >
              <label
                v-for="u in orderedUsers"
                :key="u.id"
                data-testid="authorized-user-option"
                class="flex cursor-pointer items-center gap-2.5 rounded-md border px-2.5 py-2 text-sm transition-colors"
                :class="
                  form.access_user_ids.includes(u.id)
                    ? 'border-brand-300 bg-brand-50'
                    : 'border-transparent hover:bg-surface-sunken'
                "
              >
                <input
                  type="checkbox"
                  :value="u.id"
                  v-model="form.access_user_ids"
                  class="h-4 w-4 flex-shrink-0 rounded border-line text-brand-600 focus:ring-brand-500"
                />
                <span
                  class="flex h-7 w-7 flex-shrink-0 items-center justify-center rounded-full bg-brand-100 text-xs font-semibold text-brand-700"
                >
                  {{ userInitial(u) }}
                </span>
                <div class="min-w-0 flex-1">
                  <div class="truncate text-ink-900">{{ userLabel(u) }}</div>
                  <div v-if="u.email" class="truncate text-xs text-ink-400">
                    {{ u.email }}
                  </div>
                </div>
              </label>
            </div>
            <p
              v-if="userLoading"
              class="px-3 py-3 text-center text-xs text-ink-400"
            >
              {{ t('lensAdmin.access.loadingUsers') }}
            </p>
            <p
              v-else-if="userFailed"
              class="px-3 py-3 text-center text-xs text-danger-700"
            >
              {{ t('lensAdmin.access.usersFailed') }}
              <button
                type="button"
                class="ml-1 font-medium underline"
                @click="loadUsers()"
              >
                {{ t('lensAdmin.access.retry') }}
              </button>
            </p>
            <p
              v-else-if="!orderedUsers.length"
              class="px-3 py-8 text-center text-xs text-ink-400"
            >
              {{
                userSearch.trim()
                  ? t('lensAdmin.access.noUserResults')
                  : t('lensAdmin.access.noUsers')
              }}
            </p>
          </div>
        </div>
      </div>
    </div>

    <p v-if="formError" class="mt-4 text-sm text-danger-700">{{ formError }}</p>

    <template #footer>
      <div class="flex items-center justify-between">
        <BaseButton
          variant="outline"
          @click="wizardStep > 1 ? prevWizardStep() : $emit('close')"
        >
          {{ wizardStep > 1 ? t('lensAdmin.wizard.back') : t('common.cancel') }}
        </BaseButton>
        <div class="flex items-center gap-3">
          <span class="text-xs text-ink-400"
            >{{ wizardStep }} / {{ WIZARD_STEP_COUNT }}</span
          >
          <BaseButton
            v-if="wizardStep < WIZARD_STEP_COUNT"
            variant="primary"
            :disabled="!canProceedWizard"
            @click="nextWizardStep"
          >
            {{ t('lensAdmin.wizard.next') }}
          </BaseButton>
          <BaseButton
            v-else
            variant="primary"
            :loading="saving"
            @click="$emit('save')"
          >
            {{
              mode === 'create'
                ? t('lensAdmin.wizard.finish')
                : t('common.save')
            }}
          </BaseButton>
        </div>
      </div>
    </template>
  </BaseDrawer>
</template>

<script setup>
import {
  Eye as EyeIcon,
  EyeOff as EyeOffIcon,
  Globe as GlobeIcon,
  Lock as LockIcon,
  Search,
  User as UserIcon,
  Users as UsersIcon
} from '@lucide/vue'
import { computed, defineComponent, h, onBeforeUnmount, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'

import { managementApi } from '@/admin/api/management'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseDrawer from '@/components/ui/BaseDrawer.vue'
import BaseSelect from '@/components/ui/BaseSelect.vue'
import StatusBadge from '@/components/ui/StatusBadge.vue'
import { useUserStore } from '@/store/user'
import {
  pluginDisplayName as translatedPluginDisplayName
} from '@/utils/pluginI18n'

import EnvironmentSetValues from './components/EnvironmentSetValues.vue'

import { EMPTY_VALUE, formatLLMConfigLabel } from './adminHelpers'
import {
  appendUniqueOptions,
  assignmentFirstOptions,
  createLatestRequestRunner
} from './assistantAccessSelectors'
import {
  environmentConfigurationComplete,
  mcpRequiredEnvironmentNames
} from './assistantEnvironment'
import {
  filterSelectableSkills,
  skillDescription,
  sortSkillsBySelection
} from './assistantSkills'

import { toggleBinding } from './assistantDatasources'

const props = defineProps({
  show: Boolean,
  mode: { type: String, default: 'create' },
  // Shared reactive form object owned by the parent; this drawer writes into
  // it directly so the parent's save() can read the result unchanged.
  form: { type: Object, required: true },
  lensnodes: { type: Array, default: () => [] },
  assistants: { type: Array, default: () => [] },
  skills: { type: Array, default: () => [] },
  environmentVariableSets: { type: Array, default: () => [] },
  mcps: { type: Array, default: () => [] },
  pluginConnections: { type: Array, default: () => [] },
  pluginManifests: { type: Object, default: () => ({}) },
  pluginIconUrls: { type: Object, default: () => ({}) },
  llmConfigOptions: { type: Array, default: () => [] },
  datasourceOptions: { type: Array, default: () => [] },
  saving: Boolean,
  formError: { type: String, default: '' },
  refreshingDirs: Boolean
})

defineEmits(['close', 'save', 'refresh-dirs'])

const { t, te } = useI18n()
const userStore = useUserStore()
const isAdmin = computed(() => userStore.userHasFeature('admin_console'))
const isSmartMode = computed(() => props.form.mode === 'smart')
const collaborationMemberOptions = computed(() =>
  props.assistants.filter(
    (assistant) =>
      assistant.status === 'active' &&
      (assistant.mode || assistant.routing_mode || 'direct') === 'direct' &&
      assistant.uuid !== props.form.uuid
  )
)

const activePluginConnections = computed(() =>
  props.pluginConnections.filter((connection) => {
    if (connection.status !== 'active') return false
    const manifest = props.pluginManifests?.[connection.plugin_key]
    return Array.isArray(manifest?.tools) && manifest.tools.length > 0
  })
)

const selectedPluginBindings = computed(() =>
  Array.isArray(props.form.plugin_bindings) ? props.form.plugin_bindings : []
)

const missingSkillPluginRequirements = computed(() => {
  const selectedSkills = props.skills.filter((skill) =>
    (props.form.skill_uuids || []).includes(skill.uuid)
  )
  const missing = []
  selectedSkills.forEach((skill) => {
    const requirements = skill.definition?.required_plugins
    if (!Array.isArray(requirements)) return
    requirements.forEach((requirement) => {
      const granted = selectedPluginCapabilities(requirement.plugin)
      const missingCapabilities = (requirement.capabilities || []).filter(
        (capability) => !granted.has(capability)
      )
      if (missingCapabilities.length) {
        missing.push(`${requirement.plugin}: ${missingCapabilities.join(', ')}`)
      }
    })
  })
  return [...new Set(missing)]
})

const emptyValue = EMPTY_VALUE
const WIZARD_STEP_COUNT = 4
const ACCESS_PAGE_SIZE = 20
const SEARCH_DELAY_MS = 250
const LOAD_MORE_THRESHOLD_PX = 96
const wizardStep = ref(1)
const initialSkillSelection = ref([])
const initialMcpSelection = ref([])

const visionModelOptions = computed(() => {
  const selected = props.form.multimodal_model_ref
  const eligible = props.llmConfigOptions.filter((config) =>
    isVisionModelEligible(config)
  )
  const historical = props.llmConfigOptions.find(
    (config) => config.uuid === selected
  )
  if (historical && !eligible.some((config) => config.uuid === selected)) {
    return [historical, ...eligible]
  }
  return eligible
})

function isVisionModelEligible(config) {
  const declared = config.config?.supports_vision ?? config.config?.vision
  return (
    config.is_active !== false &&
    (config.vision_capability === 'supported' ||
      config.supports_vision === true ||
      declared === true ||
      config.capabilities?.includes?.('vision'))
  )
}
const groupPage = ref(1)
const groupTotal = ref(0)
const groupResults = ref([])
const groupOptions = ref(new Map())
const groupLoading = ref(false)
const groupFailed = ref(false)
const groupSearch = ref('')
const userPage = ref(1)
const userTotal = ref(0)
const userResults = ref([])
const userOptions = ref(new Map())
const userLoading = ref(false)
const userFailed = ref(false)
const userSearch = ref('')
const skillSearch = ref('')
const groupRequest = createLatestRequestRunner()
const userRequest = createLatestRequestRunner()
let groupSearchTimer = null
let userSearchTimer = null
let groupAbortController = null
let userAbortController = null
let resettingAccessSelectors = false

watch(
  () => props.show,
  (show) => {
    if (show) {
      wizardStep.value = 1
      skillSearch.value = ''
      initialSkillSelection.value =
        props.mode === 'edit' ? [...(props.form.skill_uuids || [])] : []
      initialMcpSelection.value =
        props.mode === 'edit' ? [...(props.form.mcp_uuids || [])] : []
      initializeAccessSelectors()
    } else {
      resetPendingAccessRequests()
    }
  }
)

watch(
  groupSearch,
  () => {
    if (!props.show || resettingAccessSelectors) return
    if (groupSearchTimer) clearTimeout(groupSearchTimer)
    groupAbortController?.abort()
    groupRequest.invalidate()
    groupPage.value = 1
    groupTotal.value = 0
    groupResults.value = []
    groupFailed.value = false
    groupLoading.value = true
    const delay = groupSearch.value.trim() ? SEARCH_DELAY_MS : 0
    groupSearchTimer = setTimeout(() => {
      groupSearchTimer = null
      loadGroups(1)
    }, delay)
  },
  { flush: 'sync' }
)

watch(
  userSearch,
  () => {
    if (!props.show || resettingAccessSelectors) return
    if (userSearchTimer) clearTimeout(userSearchTimer)
    userAbortController?.abort()
    userRequest.invalidate()
    userPage.value = 1
    userTotal.value = 0
    userResults.value = []
    userFailed.value = false
    userLoading.value = true
    const delay = userSearch.value.trim() ? SEARCH_DELAY_MS : 0
    userSearchTimer = setTimeout(() => {
      userSearchTimer = null
      loadUsers(1)
    }, delay)
  },
  { flush: 'sync' }
)

onBeforeUnmount(resetPendingAccessRequests)

const FormRow = defineComponent({
  props: {
    label: {
      type: String,
      required: true
    }
  },
  setup(rowProps, { slots }) {
    return () =>
      h('div', [
        h(
          'label',
          { class: 'mb-1 block text-sm font-medium text-ink-700' },
          rowProps.label
        ),
        slots.default?.()
      ])
  }
})

const drawerTitle = computed(() =>
  props.mode === 'create'
    ? t('lensAdmin.drawer.createTitle')
    : t('lensAdmin.drawer.editTitle')
)

const drawerSubtitle = computed(() =>
  props.mode === 'edit' ? props.form.name || '' : ''
)

const slugMaxLength = 180
const slugPattern = /^[-a-zA-Z0-9_]+$/

const canonicalSlug = computed(() => String(props.form.slug ?? '').trim())

const slugLength = computed(() => canonicalSlug.value.length)

const slugLengthClass = computed(() => {
  if (slugLength.value >= slugMaxLength) {
    return 'text-danger-600'
  }
  if (slugLength.value >= slugMaxLength * 0.9) {
    return 'text-amber-600'
  }
  return 'text-ink-400'
})

const agentRoundsTiers = computed(() => [
  {
    value: 'flash',
    label: t('lensAdmin.agentRounds.flash'),
    hint: t('lensAdmin.agentRounds.flashHint')
  },
  {
    value: 'fast',
    label: t('lensAdmin.agentRounds.fast'),
    hint: t('lensAdmin.agentRounds.fastHint')
  },
  {
    value: 'balanced',
    label: t('lensAdmin.agentRounds.balanced'),
    hint: t('lensAdmin.agentRounds.balancedHint')
  },
  {
    value: 'deep',
    label: t('lensAdmin.agentRounds.deep'),
    hint: t('lensAdmin.agentRounds.deepHint')
  },
  {
    value: 'max',
    label: t('lensAdmin.agentRounds.max'),
    hint: t('lensAdmin.agentRounds.maxHint')
  }
])

const wizardStepsMeta = computed(() => [
  { key: 'basic', title: t('lensAdmin.wizard.step1Title') },
  { key: 'execution', title: t('lensAdmin.wizard.step2Title') },
  { key: 'tools', title: t('lensAdmin.wizard.step3Title') },
  { key: 'access', title: t('lensAdmin.wizard.step4Title') }
])

const canProceedWizard = computed(() => {
  if (wizardStep.value === 1) {
    return (
      !!props.form.name?.trim() &&
      slugPattern.test(canonicalSlug.value) &&
      canonicalSlug.value.length <= slugMaxLength &&
      !!props.form.agent_model_ref
    )
  }
  if (wizardStep.value === 2) {
    if (isSmartMode.value) {
      return (props.form.collaboration_member_uuids || []).length > 0
    }
    if (!props.form.capability) return false
    if (isGeneralChatTask.value) return true
    return (['auto', 'all'].includes(props.form.settings?.datasource_routing) || selectedDirs().length > 0 || props.form.datasource_bindings?.length > 0)
  }
  if (wizardStep.value === 3) {
    if (isSmartMode.value) return true
    const hasGeneralChatExecutionTool =
      !isGeneralChatTask.value ||
      (props.form.skill_uuids || []).length > 0 ||
      selectedPluginBindings.value.some(
        (binding) => binding.enabled !== false
      )
    return (
      hasGeneralChatExecutionTool &&
      selectedSkillEnvironmentsConfigured() &&
      selectedMcpEnvironmentsConfigured() &&
      missingSkillPluginRequirements.value.length === 0
    )
  }
  return true
})

const assistantTypeOptions = computed(() => [
  {
    value: 'general_chat',
    label: t('lensAdmin.assistantTypes.generalChat')
  },
  {
    value: 'code_analysis',
    label: t('lensAdmin.assistantTypes.codeAnalysis')
  },
  {
    value: 'knowledge_qa',
    label: t('lensAdmin.assistantTypes.knowledgeQa')
  }
])

const selectedDatasourceCount = computed(() => (props.form.datasource_bindings || []).length)

const isGeneralChatTask = computed(
  () => props.form.capability === 'general_chat'
)

const isCodeAnalysisTask = computed(
  () => props.form.capability === 'code_analysis'
)

const requiresWorkspace = computed(() =>
  ['code_analysis', 'knowledge_qa'].includes(props.form.capability)
)

const requiresNodeSelection = computed(() => false)

watch(
  () => props.form.capability,
  (capability, previousCapability) => {
    if (capability === previousCapability) return
    if (!requiresWorkspace.value) props.form.selected_dirs = []
  }
)

watch(
  () => props.form.mode,
  (assistantMode) => {
    if (assistantMode !== 'smart') return
    props.form.capability = 'general_chat'
    props.form.lensnode_uuid = ''
    props.form.selected_dirs = []
    props.form.skill_uuids = []
    props.form.mcp_uuids = []
    props.form.plugin_bindings = []
    props.form.multimodal_model_ref = ''
  }
)

const compatibleLensnodes = computed(() =>
  props.lensnodes.filter((lensnode) =>
    (lensnode.tasks || []).some((task) => task.name === props.form.capability)
  )
)

function pluginBinding(connectionUuid) {
  return selectedPluginBindings.value.find(
    (binding) => binding.connection_uuid === connectionUuid
  )
}

function pluginDisplayName(pluginKey) {
  return (
    translatedPluginDisplayName(props.pluginManifests?.[pluginKey], t, te) ||
    pluginKey
  )
}

function pluginIconUrl(pluginKey) {
  return props.pluginIconUrls?.[pluginKey] || ''
}

function selectedPluginCapabilities(pluginKey) {
  const capabilities = new Set()
  selectedPluginBindings.value.forEach((binding) => {
    if (binding.enabled === false) return
    const connection = props.pluginConnections.find(
      (item) => item.uuid === binding.connection_uuid
    )
    if (connection?.plugin_key !== pluginKey) return
    const tools = props.pluginManifests?.[pluginKey]?.tools || []
    tools.forEach((tool) => capabilities.add(tool.capability))
  })
  return capabilities
}

function togglePluginConnection(connection, checked) {
  const bindings = [...selectedPluginBindings.value]
  if (!checked) {
    props.form.plugin_bindings = bindings.filter(
      (binding) => binding.connection_uuid !== connection.uuid
    )
    return
  }
  if (pluginBinding(connection.uuid)) return
  props.form.plugin_bindings = [
    ...bindings,
    { connection_uuid: connection.uuid, enabled: true }
  ]
}

const selectedLensNodeDirs = computed(() => {
  const selected = props.lensnodes.find(
    (lensnode) => lensnode.uuid === props.form.lensnode_uuid
  )
  const dirs = Array.isArray(selected?.available_dirs)
    ? selected.available_dirs
    : []
  return dirs
    .map((dir) => {
      if (typeof dir === 'string') {
        return { path: dir }
      }
      return { ...dir, path: dir.path || dir.name || '' }
    })
    .filter((dir) => dir.path)
})

function nextWizardStep() {
  if (wizardStep.value < WIZARD_STEP_COUNT) wizardStep.value++
}

function prevWizardStep() {
  if (wizardStep.value > 1) wizardStep.value--
}

function userLabel(user) {
  return user.display_name || user.username || user.email || `#${user.id}`
}

function userInitial(user) {
  return (userLabel(user).trim()[0] || '?').toUpperCase()
}

const orderedGroups = computed(() =>
  assignmentFirstOptions(
    props.form.access_group_ids,
    groupOptions.value,
    groupResults.value
  )
)

const orderedUsers = computed(() =>
  assignmentFirstOptions(
    props.form.access_user_ids,
    userOptions.value,
    userResults.value
  )
)

function cacheOptions(cache, options) {
  const next = new Map(cache.value)
  options.forEach((option) => next.set(option.id, option))
  cache.value = next
}

function seedAssignedOptions() {
  const groups = new Map()
  const users = new Map()
  for (const grant of props.form.access_grant_options || []) {
    if (grant.type === 'group') {
      groups.set(grant.id, { id: grant.id, name: grant.name })
    } else if (grant.type === 'user') {
      users.set(grant.id, {
        id: grant.id,
        name: grant.name,
        username: grant.username || grant.name,
        display_name: grant.username || grant.name,
        email: grant.email || ''
      })
    }
  }
  groupOptions.value = groups
  userOptions.value = users
}

function resetPendingAccessRequests() {
  if (groupSearchTimer) {
    clearTimeout(groupSearchTimer)
    groupSearchTimer = null
  }
  if (userSearchTimer) {
    clearTimeout(userSearchTimer)
    userSearchTimer = null
  }
  groupAbortController?.abort()
  groupAbortController = null
  groupRequest.invalidate()
  userAbortController?.abort()
  userAbortController = null
  userRequest.invalidate()
  groupLoading.value = false
  userLoading.value = false
}

function initializeAccessSelectors() {
  resetPendingAccessRequests()
  resettingAccessSelectors = true
  groupPage.value = 1
  groupTotal.value = 0
  groupResults.value = []
  groupFailed.value = false
  groupSearch.value = ''
  userPage.value = 1
  userTotal.value = 0
  userResults.value = []
  userFailed.value = false
  userSearch.value = ''
  resettingAccessSelectors = false
  seedAssignedOptions()
  loadGroups(1)
  loadUsers(1)
}

async function loadGroups(page = groupPage.value) {
  groupPage.value = page
  groupLoading.value = true
  groupFailed.value = false
  if (page === 1) groupResults.value = []
  const search = groupSearch.value.trim()
  groupAbortController?.abort()
  const controller = new AbortController()
  groupAbortController = controller
  const result = await groupRequest.run(() =>
    managementApi.getGroups(
      {
        page,
        page_size: ACCESS_PAGE_SIZE,
        compact: true,
        ...(search ? { search } : {})
      },
      { signal: controller.signal }
    )
  )
  if (!result.current) return
  if (groupAbortController === controller) groupAbortController = null
  groupLoading.value = false
  if (result.error) {
    groupFailed.value = true
    return
  }
  const rows = Array.isArray(result.value)
    ? result.value
    : (result.value?.results ?? [])
  groupPage.value = Number(result.value?.page) || page
  groupTotal.value = Number(result.value?.count) || rows.length
  groupResults.value =
    page === 1 ? rows : appendUniqueOptions(groupResults.value, rows)
  cacheOptions(groupOptions, rows)
}

async function loadUsers(page = userPage.value) {
  userPage.value = page
  userLoading.value = true
  userFailed.value = false
  if (page === 1) userResults.value = []
  const search = userSearch.value.trim()
  userAbortController?.abort()
  const controller = new AbortController()
  userAbortController = controller
  const result = await userRequest.run(() =>
    managementApi.getUsers(
      {
        page,
        page_size: ACCESS_PAGE_SIZE,
        assignable: true,
        compact: true,
        ...(search ? { search } : {})
      },
      { signal: controller.signal }
    )
  )
  if (!result.current) return
  if (userAbortController === controller) userAbortController = null
  userLoading.value = false
  if (result.error) {
    userFailed.value = true
    return
  }
  const rows = Array.isArray(result.value)
    ? result.value
    : (result.value?.results ?? [])
  userPage.value = Number(result.value?.page) || page
  userTotal.value = Number(result.value?.count) || rows.length
  userResults.value =
    page === 1 ? rows : appendUniqueOptions(userResults.value, rows)
  cacheOptions(userOptions, rows)
}

function isNearListEnd(event) {
  const list = event.currentTarget
  return (
    list.scrollHeight - list.scrollTop - list.clientHeight <=
    LOAD_MORE_THRESHOLD_PX
  )
}

function maybeLoadMoreGroups(event) {
  if (
    !groupLoading.value &&
    !groupFailed.value &&
    groupResults.value.length < groupTotal.value &&
    isNearListEnd(event)
  ) {
    loadGroups(groupPage.value + 1)
  }
}

function maybeLoadMoreUsers(event) {
  if (
    !userLoading.value &&
    !userFailed.value &&
    userResults.value.length < userTotal.value &&
    isNearListEnd(event)
  ) {
    loadUsers(userPage.value + 1)
  }
}

const selectableSkills = computed(() =>
  filterSelectableSkills(props.skills, '')
)

const orderedSkills = computed(() =>
  sortSkillsBySelection(selectableSkills.value, initialSkillSelection.value)
)

const filteredSelectableSkills = computed(() =>
  filterSelectableSkills(orderedSkills.value, skillSearch.value)
)

const orderedMcps = computed(() => {
  const selected = new Set(initialMcpSelection.value)
  return [...props.mcps].sort(
    (left, right) =>
      Number(selected.has(right.uuid)) - Number(selected.has(left.uuid))
  )
})

function isSkillSelected(uuid) {
  return (props.form.skill_uuids || []).includes(uuid)
}

function skillEnvironment(skill) {
  const environment = skill?.definition?.environment
  return Array.isArray(environment) ? environment : []
}

function hasRequiredEnvironment(skill) {
  return skillEnvironment(skill).some((item) => item.required)
}

function environmentDraft(skillUuid) {
  props.form.skill_environment_drafts ||= {}
  props.form.skill_environment_drafts[skillUuid] ||= {
    name: '',
    values: {},
    revealed: {}
  }
  const draft = props.form.skill_environment_drafts[skillUuid]
  draft.revealed ||= {}
  return draft
}

function selectedEnvironmentSet(skillUuid) {
  const selectedUuid = props.form.skill_environment_set_uuids?.[skillUuid]
  return props.environmentVariableSets.find(
    (variableSet) => variableSet.uuid === selectedUuid
  )
}

function environmentInputPlaceholder(item) {
  return item.description || item.name
}

function isEnvironmentRevealed(skillUuid, name) {
  return !!environmentDraft(skillUuid).revealed?.[name]
}

function environmentRevealLabel(skillUuid, name) {
  return isEnvironmentRevealed(skillUuid, name)
    ? t('lensAdmin.wizard.maskEnvironmentValue')
    : t('lensAdmin.wizard.revealEnvironmentValue')
}

function toggleEnvironmentReveal(skillUuid, name) {
  const draft = environmentDraft(skillUuid)
  draft.revealed[name] = !draft.revealed[name]
}

function skillEnvironmentConfigured(skill) {
  return environmentConfigurationComplete({
    selectedUuid: props.form.skill_environment_set_uuids?.[skill.uuid] || '',
    requiredNames: skillEnvironment(skill)
      .filter((item) => item.required)
      .map((item) => item.name),
    draftValues: environmentDraft(skill.uuid).values,
    savedKeys: selectedEnvironmentSet(skill.uuid)?.keys
  })
}

function selectedSkillEnvironmentsConfigured() {
  return (props.form.skill_uuids || []).every((skillUuid) => {
    const skill = props.skills.find((item) => item.uuid === skillUuid)
    if (!skill) return true
    return environmentConfigurationComplete({
      selectedUuid: props.form.skill_environment_set_uuids?.[skillUuid] || '',
      requiredNames: skillEnvironment(skill)
        .filter((item) => item.required)
        .map((item) => item.name),
      draftValues: environmentDraft(skillUuid).values,
      savedKeys: selectedEnvironmentSet(skillUuid)?.keys
    })
  })
}

function isMcpSelected(uuid) {
  return (props.form.mcp_uuids || []).includes(uuid)
}

function mcpEnvironment(mcp) {
  return Array.isArray(mcp?.environment) ? mcp.environment : []
}

function hasRequiredMcpEnvironment(mcp) {
  return mcpRequiredEnvironmentNames(mcp).length > 0
}

function isMcpEnvironmentRequired(mcp, item) {
  return mcpRequiredEnvironmentNames(mcp).includes(item.name)
}

function mcpEnvironmentDraft(mcpUuid) {
  props.form.mcp_environment_drafts ||= {}
  props.form.mcp_environment_drafts[mcpUuid] ||= {
    name: '',
    values: {}
  }
  return props.form.mcp_environment_drafts[mcpUuid]
}

function selectedMcpEnvironmentSet(mcpUuid) {
  const selectedUuid = props.form.mcp_environment_set_uuids?.[mcpUuid]
  return props.environmentVariableSets.find(
    (variableSet) => variableSet.uuid === selectedUuid
  )
}

function mcpEnvironmentItemSaved(mcp, item) {
  return (selectedMcpEnvironmentSet(mcp.uuid)?.keys || []).includes(item.name)
}

function selectedMcpEnvironmentsConfigured() {
  return (props.form.mcp_uuids || []).every((mcpUuid) => {
    const mcp = props.mcps.find((item) => item.uuid === mcpUuid)
    if (!mcp) return true
    return environmentConfigurationComplete({
      selectedUuid: props.form.mcp_environment_set_uuids?.[mcpUuid] || '',
      requiredNames: mcpRequiredEnvironmentNames(mcp),
      draftValues: mcpEnvironmentDraft(mcpUuid).values,
      savedKeys: selectedMcpEnvironmentSet(mcpUuid)?.keys
    })
  })
}

function hasSource(source, item = null) {
  return (props.form.datasource_bindings || []).some((binding) =>
    binding.datasource_uuid === source.uuid &&
    (binding.item_uuid || null) === (item?.uuid || null))
}

function selectSource(source, item, checked) {
  props.form.datasource_bindings = toggleBinding(
    props.form.datasource_bindings || [], source, item, checked
  )
}

function selectedDirs() {
  return Array.isArray(props.form.selected_dirs) ? props.form.selected_dirs : []
}

const selectedDirPath = computed({
  get() {
    return selectedDirs()[0]?.path || ''
  },
  set(path) {
    if (!path) {
      props.form.selected_dirs = []
      return
    }
    const existing = selectedDirs().find((dir) => dir.path === path)
    props.form.selected_dirs = [existing || { path, include_paths_text: '' }]
  }
})

function selectedDirScopeText(path) {
  const dir = selectedDirs().find((item) => item.path === path)
  return dir?.include_paths_text || ''
}

function updateDirScope(path, value) {
  props.form.selected_dirs = selectedDirs().map((dir) =>
    dir.path === path ? { ...dir, include_paths_text: value } : dir
  )
}
</script>

<style scoped>
.form-input {
  @apply w-full min-w-0 max-w-full rounded-lg border border-line bg-surface px-3 py-2 text-sm text-ink-900 focus:border-brand-500 focus:outline-none focus:ring-2 focus:ring-brand-500/20;
}

.form-input-mono {
  @apply overflow-x-auto font-mono;
}

.skill-search-input {
  @apply pl-9;
}

.skill-environment-input {
  @apply py-1.5 text-xs;
}

.execution-tier-radio {
  position: absolute !important;
  width: 1px !important;
  height: 1px !important;
  margin: -1px !important;
  padding: 0 !important;
  overflow: hidden !important;
  border: 0 !important;
  clip: rect(0, 0, 0, 0) !important;
  white-space: nowrap !important;
}
</style>
