<template>
  <BaseDrawer
    :show="show"
    :title="drawerTitle"
    :subtitle="drawerSubtitle"
    @close="$emit('close')"
  >
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
            <span v-if="i + 1 < wizardStep">✓</span>
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

    <div v-if="activeStepKey === 'basic'" class="space-y-5">
      <p class="text-sm text-ink-500">
        {{ t('lensAdmin.datasourceWizard.step1Desc') }}
      </p>
      <FormRow :label="t('lensAdmin.fields.name')" required>
        <input v-model="form.name" class="form-input" required />
      </FormRow>
      <FormRow :label="t('lensAdmin.fields.type')" required>
        <BaseSelect v-model="form.source_type" @change="$emit('type-change')">
          <option
            v-for="type in sourceTypes"
            :key="type.value"
            :value="type.value"
          >
            {{ type.label }}
          </option>
        </BaseSelect>
        <p class="mt-1 text-xs text-ink-500">
          {{ selectedSourceTypeDescription }}
        </p>
      </FormRow>
      <FormRow :label="t('lensAdmin.fields.status')" required>
        <BaseSelect v-model="form.status">
          <option value="active">{{ t('common.status.active') }}</option>
          <option value="disabled">{{ t('common.status.disabled') }}</option>
        </BaseSelect>
      </FormRow>
    </div>

    <div v-else-if="activeStepKey === 'node'" class="space-y-5">
      <p class="text-sm text-ink-500">
        {{
          t(
            isManagedWorkspace
              ? 'lensAdmin.datasourceWizard.managedNodeDesc'
              : 'lensAdmin.datasourceWizard.step2Desc'
          )
        }}
      </p>
      <FormRow :label="t('lensAdmin.fields.lensnode')" required>
        <BaseSelect v-model="form.lensnode_uuid" required>
          <option value="">
            {{ t('lensAdmin.placeholders.selectLensNode') }}
          </option>
          <option
            v-for="node in onlineLensNodes"
            :key="node.uuid"
            :value="node.uuid"
          >
            {{ node.name }} · {{ node.workspace_path || '/workspace' }}
          </option>
        </BaseSelect>
        <p class="mt-1 text-xs text-ink-500">
          {{ t('lensAdmin.datasourceWizard.onlineNodeHint') }}
        </p>
      </FormRow>
      <div
        v-if="!onlineLensNodes.length"
        class="rounded-md border border-warning-200 bg-warning-50 p-3 text-sm text-warning-800"
      >
        {{ t('lensAdmin.datasourceWizard.noOnlineNodes') }}
      </div>
    </div>

    <div v-else-if="activeStepKey === 'connection'" class="space-y-5">
      <p class="text-sm text-ink-500">
        {{ t('lensAdmin.datasourceWizard.step3Desc') }}
      </p>
      <template v-if="isGitSourceType(form.source_type)">
        <FormRow :label="t('lensAdmin.fields.credential')" required>
          <div class="flex flex-col gap-2">
            <div class="flex gap-2">
              <BaseSelect
                :model-value="form.credential_uuid"
                @update:model-value="handleCredentialChange"
              >
                <option value="">
                  {{ t('lensAdmin.datasourceWizard.selectCredential') }}
                </option>
                <option
                  v-for="credential in filteredCredentials"
                  :key="credential.uuid"
                  :value="credential.uuid"
                >
                  {{ credentialOptionLabel(credential) }}
                </option>
              </BaseSelect>
              <BaseButton
                class="shrink-0"
                size="sm"
                variant="outline"
                :disabled="refreshingCredentials"
                :title="t('common.refresh')"
                @click="$emit('refresh-credentials')"
              >
                <RefreshCwIcon
                  class="h-4 w-4"
                  :class="{ 'animate-spin': refreshingCredentials }"
                />
                <span class="sr-only">{{ t('common.refresh') }}</span>
              </BaseButton>
            </div>
            <p class="text-xs text-ink-500">
              {{ t('lensAdmin.datasourceWizard.createCredentialHint') }}
              <a
                class="font-medium text-brand-600 hover:text-brand-700"
                href="/management/lens/resources/credentials"
                rel="noopener noreferrer"
                target="_blank"
              >
                {{ t('lensAdmin.datasourceWizard.createCredentialLink') }}
              </a>
            </p>
          </div>
        </FormRow>
        <div
          v-if="selectedCredential"
          class="grid gap-2 rounded-md border border-line bg-surface-sunken p-3 text-xs text-ink-600"
        >
          <div class="flex flex-wrap items-center gap-2">
            <span class="font-medium text-ink-900">
              {{ selectedCredential.name }}
            </span>
            <span class="rounded border border-line bg-surface px-1.5 py-0.5">
              {{ credentialProviderText(selectedCredential) }}
            </span>
            <span
              class="rounded border px-1.5 py-0.5"
              :class="credentialValidationClass(selectedCredential)"
            >
              {{ credentialValidationText(selectedCredential) }}
            </span>
          </div>
          <div class="break-all font-mono">
            {{ credentialScopeText(selectedCredential) }}
          </div>
        </div>
        <FormRow
          v-if="!testingConnection && gitBranchOptions.length"
          :label="t('lensAdmin.fields.branch')"
          required
        >
          <BaseSelect v-model="config.branch">
            <option value="">
              {{ t('lensAdmin.datasourceWizard.branchPlaceholder') }}
            </option>
            <option
              v-for="branch in gitBranchOptions"
              :key="branch"
              :value="branch"
            >
              {{ branch }}
            </option>
          </BaseSelect>
        </FormRow>
      </template>
      <template v-else>
        <FormRow :label="t('lensAdmin.fields.credential')" required>
          <div class="flex flex-col gap-2">
            <div class="flex gap-2">
              <BaseSelect
                :model-value="form.credential_uuid"
                @update:model-value="handleCredentialChange"
              >
                <option value="">
                  {{ t('lensAdmin.datasourceWizard.selectFeishuCredential') }}
                </option>
                <option
                  v-for="credential in filteredCredentials"
                  :key="credential.uuid"
                  :value="credential.uuid"
                >
                  {{ credentialOptionLabel(credential) }}
                </option>
              </BaseSelect>
              <BaseButton
                class="shrink-0"
                size="sm"
                variant="outline"
                :disabled="refreshingCredentials"
                :title="t('common.refresh')"
                @click="$emit('refresh-credentials')"
              >
                <RefreshCwIcon
                  class="h-4 w-4"
                  :class="{ 'animate-spin': refreshingCredentials }"
                />
                <span class="sr-only">{{ t('common.refresh') }}</span>
              </BaseButton>
            </div>
            <p class="text-xs text-ink-500">
              {{ t('lensAdmin.datasourceWizard.createCredentialHint') }}
              <a
                class="font-medium text-brand-600 hover:text-brand-700"
                href="/management/lens/resources/credentials"
                rel="noopener noreferrer"
                target="_blank"
              >
                {{ t('lensAdmin.datasourceWizard.createCredentialLink') }}
              </a>
            </p>
          </div>
        </FormRow>
        <div
          v-if="selectedCredential"
          class="grid gap-2 rounded-md border border-line bg-surface-sunken p-3 text-xs text-ink-600"
        >
          <div class="flex flex-wrap items-center gap-2">
            <span class="font-medium text-ink-900">
              {{ selectedCredential.name }}
            </span>
            <span class="rounded border border-line bg-surface px-1.5 py-0.5">
              {{ credentialProviderText(selectedCredential) }}
            </span>
            <span
              class="rounded border px-1.5 py-0.5"
              :class="credentialValidationClass(selectedCredential)"
            >
              {{ credentialValidationText(selectedCredential) }}
            </span>
          </div>
          <div class="break-all font-mono">
            {{ credentialScopeText(selectedCredential) }}
          </div>
        </div>
        <div
          v-if="testingConnection"
          class="flex items-center gap-2 rounded-md border border-primary-200 bg-primary-50 p-3 text-sm text-primary-700"
        >
          <LoaderCircleIcon class="h-4 w-4 animate-spin" />
          <span>{{ t('lensAdmin.datasourceWizard.loadingFeishuScope') }}</span>
        </div>
        <div v-else class="grid gap-4 md:grid-cols-2">
          <FormRow :label="t('lensAdmin.fields.recursive')">
            <label class="inline-flex items-center gap-2 text-sm text-ink-600">
              <input
                v-model="config.recursive"
                type="checkbox"
                class="h-4 w-4 rounded border-line text-brand-600 focus:ring-brand-500"
              />
              {{ t('lensAdmin.datasourceWizard.recursiveHint') }}
            </label>
          </FormRow>
          <FormRow
            v-if="config.recursive"
            :label="t('lensAdmin.fields.maxDepth')"
          >
            <input
              v-model.number="config.max_depth"
              class="form-input"
              min="1"
              type="number"
            />
          </FormRow>
        </div>
      </template>
      <div
        v-if="isGitSourceType(form.source_type) && testingConnection"
        class="flex items-center gap-2 rounded-md border border-primary-200 bg-primary-50 p-3 text-sm text-primary-700"
      >
        <LoaderCircleIcon class="h-4 w-4 animate-spin" />
        <span>{{ t('lensAdmin.datasourceWizard.loadingGitScope') }}</span>
      </div>
      <div
        v-if="
          isGitSourceType(form.source_type) &&
          !testingConnection &&
          gitBranchOptions.length
        "
        class="text-xs text-ink-500"
      >
        {{
          t('lensAdmin.datasourceWizard.branchCount', {
            count: gitBranchOptions.length
          })
        }}
      </div>
      <div class="flex items-center gap-2">
        <span
          v-if="
            isGitSourceType(form.source_type) &&
            !testingConnection &&
            !gitBranchOptions.length &&
            !gitOrganizationRepositories.length
          "
          class="text-xs text-ink-500"
        >
          {{ t('lensAdmin.datasourceWizard.branchTestHint') }}
        </span>
      </div>
      <section
        v-if="!testingConnection && gitOrganizationRepositories.length"
        class="space-y-3 rounded-md border border-line bg-surface p-3"
      >
        <div class="flex items-center justify-between gap-3">
          <div>
            <h3 class="text-sm font-semibold text-ink-900">
              {{ t('lensAdmin.datasourceWizard.gitOrganizationReposTitle') }}
            </h3>
            <p class="mt-1 text-xs text-ink-500">
              {{
                t('lensAdmin.datasourceWizard.gitOrganizationReposHint', {
                  count: gitOrganizationRepositories.length
                })
              }}
            </p>
          </div>
          <label class="flex shrink-0 items-center gap-2 text-xs text-ink-600">
            <input
              type="checkbox"
              class="h-4 w-4 rounded border-line text-brand-600 focus:ring-brand-500"
              :checked="allGitOrganizationRepositoriesSelected"
              @change="toggleAllGitOrganizationRepositories"
            />
            {{ t('common.selectAll') }}
          </label>
        </div>
        <div class="grid gap-2 md:grid-cols-[minmax(0,1fr)_220px_auto]">
          <input
            v-model="gitRepositorySearch"
            class="form-input h-9"
            :placeholder="t('common.search')"
          />
          <input
            v-model="gitBulkBranch"
            class="form-input h-9"
            :placeholder="t('lensAdmin.fields.branch')"
          />
          <BaseButton
            size="sm"
            variant="outline"
            :disabled="!gitBulkBranch.trim()"
            @click="applyGitBulkBranch"
          >
            {{ t('common.apply') }}
          </BaseButton>
        </div>
        <div class="max-h-72 overflow-y-auto rounded-md border border-line">
          <div
            v-for="repo in filteredGitOrganizationRepositories"
            :key="repo.repo_url"
            class="grid gap-3 border-b border-line px-3 py-2 last:border-b-0 md:grid-cols-[minmax(0,1fr)_180px]"
          >
            <label class="flex min-w-0 items-start gap-3">
              <input
                v-model="repo.selected"
                type="checkbox"
                class="mt-1 h-4 w-4 rounded border-line text-brand-600 focus:ring-brand-500"
              />
              <span class="min-w-0">
                <span class="block truncate text-sm font-medium text-ink-900">
                  {{ repo.name || repo.path }}
                </span>
                <span class="block truncate font-mono text-xs text-ink-500">
                  {{ repo.repo_url }}
                </span>
              </span>
            </label>
            <BaseSelect
              v-model="repo.branch"
              size="sm"
              :disabled="!repo.selected || !repo.branches?.length"
            >
              <option value="">
                {{ t('lensAdmin.datasourceWizard.branchPlaceholder') }}
              </option>
              <option
                v-for="branch in repo.branches || []"
                :key="branch"
                :value="branch"
              >
                {{ branch }}
              </option>
            </BaseSelect>
          </div>
        </div>
        <p class="text-xs text-ink-500">
          {{
            t('lensAdmin.datasourceWizard.gitOrganizationSelectedHint', {
              count: selectedGitOrganizationRepositories.length
            })
          }}
        </p>
      </section>
      <div
        v-if="connectionResult && !testingConnection"
        class="rounded-md border p-3 text-sm"
        :class="
          connectionResult.status === 'success'
            ? 'border-success-200 bg-success-50 text-success-800'
            : 'border-danger-200 bg-danger-50 text-danger-800'
        "
      >
        {{ connectionResultMessage }}
      </div>
    </div>

    <div v-else-if="activeStepKey === 'sync'" class="space-y-5">
      <p class="text-sm text-ink-500">
        {{
          t(
            isManagedWorkspace
              ? 'lensAdmin.datasourceWizard.managedPathDesc'
              : 'lensAdmin.datasourceWizard.step4Desc'
          )
        }}
      </p>
      <FormRow :label="t('lensAdmin.fields.targetPath')" required>
        <div class="space-y-3">
          <input
            v-if="isManagedWorkspace"
            v-model="form.workspace_relative_path"
            class="form-input font-mono"
            :placeholder="
              t('lensAdmin.datasourceWizard.managedPathPlaceholder')
            "
            @change="$emit('check-path')"
          />
          <div
            class="rounded-md border border-line bg-surface-sunken px-3 py-2"
          >
            <div class="text-xs text-ink-500">
              {{ t('lensAdmin.datasourceWizard.selectedTargetPath') }}
            </div>
            <div class="mt-1 flex items-center gap-2">
              <div
                class="min-w-0 flex-1 break-all font-mono text-sm text-ink-900"
              >
                {{
                  form.workspace_relative_path ? targetPath : workspacePrefix
                }}
              </div>
              <LoaderCircleIcon
                v-if="checkingPath"
                class="h-4 w-4 shrink-0 animate-spin text-primary-600"
              />
              <CheckCircleIcon
                v-else-if="pathResult && pathResult.status !== 'blocked'"
                class="h-4 w-4 shrink-0 text-success-600"
              />
              <XCircleIcon
                v-else-if="pathResult && pathResult.status === 'blocked'"
                class="h-4 w-4 shrink-0 text-danger-600"
              />
            </div>
            <p
              v-if="pathResultMessage"
              class="mt-1 text-xs"
              :class="
                pathResult?.status === 'blocked'
                  ? 'text-danger-700'
                  : 'text-success-700'
              "
            >
              {{ pathResultMessage }}
            </p>
          </div>
          <div class="rounded-md border border-line bg-surface">
            <div
              class="flex items-center justify-between border-b border-line px-3 py-2"
            >
              <div class="text-sm font-medium text-ink-900">
                {{ workspaceRoot }}
              </div>
              <button
                v-if="!isManagedWorkspace"
                class="inline-flex h-7 w-7 shrink-0 items-center justify-center rounded text-ink-500 hover:bg-surface-sunken hover:text-ink-900"
                type="button"
                :title="t('lensAdmin.datasourceWizard.createAtWorkspace')"
                @click="startCreateTargetDirectory('')"
              >
                <PlusIcon class="h-4 w-4" />
              </button>
            </div>
            <div class="max-h-64 overflow-y-auto p-2">
              <div
                v-if="!isManagedWorkspace && creatingDirectoryParent === ''"
                class="flex gap-1 px-2 py-1"
              >
                <span class="h-7 w-7 shrink-0" />
                <input
                  v-model="newDirectoryName"
                  class="directory-name-input"
                  :placeholder="
                    t('lensAdmin.datasourceWizard.newDirPlaceholder')
                  "
                  @keyup.enter="selectNewTargetDirectory"
                />
                <button
                  class="directory-action-button text-success-600 hover:bg-success-50 hover:text-success-700 disabled:cursor-not-allowed disabled:opacity-40"
                  type="button"
                  :disabled="!canCreateTargetDirectory"
                  :title="t('common.confirm')"
                  @click="selectNewTargetDirectory"
                >
                  <CheckIcon class="h-4 w-4" />
                </button>
                <button
                  class="directory-action-button text-ink-500 hover:bg-surface-sunken hover:text-ink-900"
                  type="button"
                  :title="t('common.cancel')"
                  @click="cancelCreateTargetDirectory"
                >
                  <XIcon class="h-4 w-4" />
                </button>
              </div>
              <div
                v-if="!workspaceDirectoryTree.length"
                class="px-2 py-3 text-sm text-ink-500"
              >
                {{ t('lensAdmin.datasourceWizard.noWorkspaceDirs') }}
              </div>
              <div
                v-for="dir in workspaceDirectoryTree"
                :key="dir.path"
                class="space-y-1"
              >
                <div class="flex items-center gap-1">
                  <button
                    class="inline-flex h-7 w-7 shrink-0 items-center justify-center rounded text-ink-500 hover:bg-surface-sunken hover:text-ink-900"
                    type="button"
                    @click="toggleDirectoryExpanded(dir.relative)"
                  >
                    <component
                      :is="
                        isDirectoryExpanded(dir.relative)
                          ? ChevronDownIcon
                          : ChevronRightIcon
                      "
                      class="h-4 w-4"
                    />
                  </button>
                  <button
                    class="flex min-w-0 flex-1 items-center gap-2 rounded px-2 py-1.5 text-left text-sm hover:bg-surface-sunken"
                    :class="directoryButtonClass(dir.relative)"
                    type="button"
                    @click="selectTargetDirectory(dir.relative)"
                  >
                    <component
                      :is="
                        isSelectedDirectory(dir.relative)
                          ? FolderOpenIcon
                          : FolderIcon
                      "
                      class="h-4 w-4 shrink-0"
                    />
                    <span class="truncate">{{ dir.name }}</span>
                  </button>
                  <button
                    v-if="!isManagedWorkspace"
                    class="inline-flex h-7 w-7 shrink-0 items-center justify-center rounded text-ink-500 hover:bg-surface-sunken hover:text-ink-900"
                    type="button"
                    :title="t('lensAdmin.datasourceWizard.createTargetDir')"
                    @click="startCreateTargetDirectory(dir.relative)"
                  >
                    <PlusIcon class="h-4 w-4" />
                  </button>
                </div>
                <div
                  v-if="
                    !isManagedWorkspace &&
                    creatingDirectoryParent === dir.relative
                  "
                  class="ml-5 flex gap-1 border-l border-line pl-10"
                >
                  <input
                    v-model="newDirectoryName"
                    class="directory-name-input"
                    :placeholder="
                      t('lensAdmin.datasourceWizard.newDirPlaceholder')
                    "
                    @keyup.enter="selectNewTargetDirectory"
                  />
                  <button
                    class="directory-action-button text-success-600 hover:bg-success-50 hover:text-success-700 disabled:cursor-not-allowed disabled:opacity-40"
                    type="button"
                    :disabled="!canCreateTargetDirectory"
                    :title="t('common.confirm')"
                    @click="selectNewTargetDirectory"
                  >
                    <CheckIcon class="h-4 w-4" />
                  </button>
                  <button
                    class="directory-action-button text-ink-500 hover:bg-surface-sunken hover:text-ink-900"
                    type="button"
                    :title="t('common.cancel')"
                    @click="cancelCreateTargetDirectory"
                  >
                    <XIcon class="h-4 w-4" />
                  </button>
                </div>
                <div
                  v-if="isDirectoryExpanded(dir.relative)"
                  class="ml-5 space-y-1 border-l border-line pl-2"
                >
                  <div
                    v-if="!dir.children.length"
                    class="px-2 py-1.5 text-xs text-ink-400"
                  >
                    {{ t('lensAdmin.datasourceWizard.noChildDirs') }}
                  </div>
                  <div
                    v-for="child in dir.children"
                    :key="child.path"
                    class="space-y-1"
                  >
                    <div class="flex items-center gap-1">
                      <span class="h-7 w-7 shrink-0" />
                      <button
                        class="flex min-w-0 flex-1 items-center gap-2 rounded px-2 py-1.5 text-left text-sm hover:bg-surface-sunken"
                        :class="directoryButtonClass(child.relative)"
                        type="button"
                        @click="selectTargetDirectory(child.relative)"
                      >
                        <component
                          :is="
                            isSelectedDirectory(child.relative)
                              ? FolderOpenIcon
                              : FolderIcon
                          "
                          class="h-4 w-4 shrink-0"
                        />
                        <span class="truncate">{{ child.name }}</span>
                      </button>
                      <button
                        v-if="!isManagedWorkspace"
                        class="inline-flex h-7 w-7 shrink-0 items-center justify-center rounded text-ink-500 hover:bg-surface-sunken hover:text-ink-900"
                        type="button"
                        :title="t('lensAdmin.datasourceWizard.createTargetDir')"
                        @click="startCreateTargetDirectory(child.relative)"
                      >
                        <PlusIcon class="h-4 w-4" />
                      </button>
                    </div>
                    <div
                      v-if="
                        !isManagedWorkspace &&
                        creatingDirectoryParent === child.relative
                      "
                      class="ml-8 flex gap-1"
                    >
                      <input
                        v-model="newDirectoryName"
                        class="directory-name-input"
                        :placeholder="
                          t('lensAdmin.datasourceWizard.newDirPlaceholder')
                        "
                        @keyup.enter="selectNewTargetDirectory"
                      />
                      <button
                        class="directory-action-button text-success-600 hover:bg-success-50 hover:text-success-700 disabled:cursor-not-allowed disabled:opacity-40"
                        type="button"
                        :disabled="!canCreateTargetDirectory"
                        :title="t('common.confirm')"
                        @click="selectNewTargetDirectory"
                      >
                        <CheckIcon class="h-4 w-4" />
                      </button>
                      <button
                        class="directory-action-button text-ink-500 hover:bg-surface-sunken hover:text-ink-900"
                        type="button"
                        :title="t('common.cancel')"
                        @click="cancelCreateTargetDirectory"
                      >
                        <XIcon class="h-4 w-4" />
                      </button>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
        <p class="mt-1 text-xs text-ink-500">
          {{
            isManagedWorkspace
              ? t('lensAdmin.datasourceWizard.managedPathHint')
              : isGitOrganizationMode
                ? t('lensAdmin.datasourceWizard.gitOrganizationPathHint')
                : t('lensAdmin.datasourceWizard.pathHint')
          }}
        </p>
      </FormRow>
      <FormRow
        v-if="!isManagedWorkspace"
        :label="t('lensAdmin.fields.syncPolicy')"
        required
      >
        <div class="flex items-center gap-2">
          <BaseSelect v-model="syncPolicyMode" class="min-w-0 flex-1">
            <option value="interval">
              {{ t('lensAdmin.datasourceWizard.syncPolicyInterval') }}
            </option>
            <option value="crontab">
              {{ t('lensAdmin.datasourceWizard.syncPolicyCrontab') }}
            </option>
          </BaseSelect>
          <BaseButton
            class="h-10 w-10 shrink-0"
            size="sm"
            variant="outline"
            :aria-label="t('lensAdmin.datasourceWizard.refreshDirectories')"
            :disabled="refreshingDirectories || !form.lensnode_uuid"
            @click="emit('refresh-dirs')"
          >
            <RefreshCwIcon
              class="h-4 w-4"
              :class="{ 'animate-spin': refreshingDirectories }"
            />
          </BaseButton>
        </div>
      </FormRow>
      <FormRow
        v-if="!isManagedWorkspace && syncPolicyMode === 'interval'"
        :label="t('lensAdmin.fields.syncInterval')"
        required
      >
        <input
          v-model.number="syncIntervalSeconds"
          class="form-input w-40"
          min="60"
          type="number"
        />
        <p class="mt-1 text-xs text-ink-500">
          {{ t('lensAdmin.datasourceWizard.intervalHint') }}
        </p>
      </FormRow>
      <div v-else-if="!isManagedWorkspace" class="grid gap-4 md:grid-cols-2">
        <FormRow :label="t('lensAdmin.fields.cron')" required>
          <input
            v-model="syncCron"
            class="form-input font-mono"
            placeholder="0 2 * * *"
          />
        </FormRow>
        <FormRow :label="t('lensAdmin.fields.timezone')">
          <input
            v-model="syncTimezone"
            class="form-input"
            placeholder="Asia/Shanghai"
          />
        </FormRow>
      </div>
      <section v-if="form.source_type === 'feishu'" class="space-y-3 pt-1">
        <button
          class="flex w-full items-center justify-between text-left"
          type="button"
          @click="feishuAdvancedOpen = !feishuAdvancedOpen"
        >
          <span class="text-sm font-semibold text-ink-900">
            {{ t('lensAdmin.datasourceWizard.feishuAdvancedTitle') }}
          </span>
          <component
            :is="feishuAdvancedOpen ? ChevronDownIcon : ChevronRightIcon"
            class="h-4 w-4 text-ink-500"
          />
        </button>
        <div v-if="feishuAdvancedOpen" class="space-y-3">
          <label class="flex items-start gap-3">
            <input
              v-model="config.feishu_incremental"
              type="checkbox"
              class="mt-0.5 h-4 w-4 rounded border-line text-brand-600 focus:ring-brand-500"
            />
            <span>
              <span class="block text-sm font-medium text-ink-800">
                {{ t('lensAdmin.datasourceWizard.feishuIncrementalTitle') }}
              </span>
              <span class="mt-0.5 block text-xs leading-5 text-ink-500">
                {{ t('lensAdmin.datasourceWizard.feishuIncrementalHint') }}
              </span>
            </span>
          </label>
          <label class="flex items-start gap-3">
            <input
              v-model="config.feishu_delete_missing"
              type="checkbox"
              class="mt-0.5 h-4 w-4 rounded border-line text-brand-600 focus:ring-brand-500"
            />
            <span>
              <span class="block text-sm font-medium text-ink-800">
                {{ t('lensAdmin.datasourceWizard.feishuDeleteMissingTitle') }}
              </span>
              <span class="mt-0.5 block text-xs leading-5 text-ink-500">
                {{ t('lensAdmin.datasourceWizard.feishuDeleteMissingHint') }}
              </span>
            </span>
          </label>
        </div>
      </section>
    </div>

    <div v-else-if="activeStepKey === 'conversion'" class="space-y-5">
      <p class="text-sm text-ink-500">
        {{ t('lensAdmin.datasourceWizard.step5Desc') }}
      </p>
      <section class="space-y-4 rounded-md border border-line p-3">
        <div>
          <h3 class="text-sm font-semibold text-ink-900">
            {{ t('lensAdmin.datasourceWizard.documentContentTitle') }}
          </h3>
          <p class="mt-1 text-xs text-ink-500">
            {{ t('lensAdmin.datasourceWizard.documentContentHint') }}
          </p>
        </div>
        <label class="flex items-start gap-3 text-sm text-ink-700">
          <input
            v-model="form.conversion_document"
            type="checkbox"
            class="mt-0.5 h-4 w-4 rounded border-line text-brand-600 focus:ring-brand-500"
          />
          <span>
            <span class="font-medium">
              {{ t('lensAdmin.datasourceWizard.convertDocuments') }}
            </span>
            <span class="block text-xs text-ink-500">
              {{ t('lensAdmin.datasourceWizard.convertDocumentsHint') }}
            </span>
          </span>
        </label>
        <FormRow
          v-if="form.conversion_document"
          :label="t('lensAdmin.fields.documentModel')"
          :hint="t('lensAdmin.datasourceWizard.documentModelTooltip')"
        >
          <BaseSelect v-model="form.conversion_document_model_ref">
            <option value="">
              {{ t('lensAdmin.placeholders.noModel') }}
            </option>
            <option
              v-for="config in llmConfigOptions"
              :key="config.uuid || config.id"
              :value="config.uuid || config.id"
            >
              {{ formatLLMConfigLabel(config) }}
            </option>
          </BaseSelect>
          <p class="mt-1 text-xs text-ink-500">
            {{ t('lensAdmin.datasourceWizard.documentModelHint') }}
          </p>
        </FormRow>
        <label
          class="flex items-start gap-3 text-sm"
          :class="form.conversion_document ? 'text-ink-700' : 'text-ink-400'"
        >
          <input
            v-model="form.conversion_embedded_image"
            type="checkbox"
            class="mt-0.5 h-4 w-4 rounded border-line text-brand-600 focus:ring-brand-500 disabled:opacity-50"
            :disabled="!form.conversion_document"
          />
          <span>
            <span class="font-medium">
              {{ t('lensAdmin.datasourceWizard.convertEmbeddedImages') }}
            </span>
            <span class="block text-xs text-ink-500">
              {{ t('lensAdmin.datasourceWizard.convertEmbeddedImagesHint') }}
            </span>
          </span>
        </label>
        <section
          v-if="form.conversion_document && form.conversion_embedded_image"
          class="rounded-md border border-line bg-ink-50/50"
        >
          <button
            type="button"
            class="flex w-full items-center justify-between gap-3 px-3 py-2 text-left text-sm font-medium text-ink-800"
            @click="pdfAdvancedOpen = !pdfAdvancedOpen"
          >
            <span>
              {{ t('lensAdmin.datasourceWizard.pdfAdvancedTitle') }}
            </span>
            <component
              :is="pdfAdvancedOpen ? ChevronDownIcon : ChevronRightIcon"
              class="h-4 w-4 text-ink-500"
            />
          </button>
          <div
            v-if="pdfAdvancedOpen"
            class="space-y-4 border-t border-line p-3"
          >
            <p class="text-xs leading-5 text-ink-500">
              {{ t('lensAdmin.datasourceWizard.pdfAdvancedHint') }}
            </p>
            <label class="flex items-start gap-3 text-sm text-ink-700">
              <input
                v-model="form.conversion_pdf_extract_images"
                type="checkbox"
                class="mt-0.5 h-4 w-4 rounded border-line text-brand-600 focus:ring-brand-500"
              />
              <span>
                <span class="font-medium">
                  {{ t('lensAdmin.datasourceWizard.pdfExtractImages') }}
                </span>
                <span class="block text-xs text-ink-500">
                  {{ t('lensAdmin.datasourceWizard.pdfExtractImagesHint') }}
                </span>
              </span>
            </label>
            <label class="flex items-start gap-3 text-sm text-ink-700">
              <input
                v-model="form.conversion_pdf_render_scanned_pages"
                type="checkbox"
                class="mt-0.5 h-4 w-4 rounded border-line text-brand-600 focus:ring-brand-500"
              />
              <span>
                <span class="font-medium">
                  {{ t('lensAdmin.datasourceWizard.pdfRenderScannedPages') }}
                </span>
                <span class="block text-xs text-amber-700">
                  {{
                    t('lensAdmin.datasourceWizard.pdfRenderScannedPagesHint')
                  }}
                </span>
              </span>
            </label>
            <label class="flex items-start gap-3 text-sm text-ink-700">
              <input
                v-model="form.conversion_pdf_extract_images_on_text_pages"
                type="checkbox"
                class="mt-0.5 h-4 w-4 rounded border-line text-brand-600 focus:ring-brand-500"
              />
              <span>
                <span class="font-medium">
                  {{
                    t('lensAdmin.datasourceWizard.pdfExtractImagesOnTextPages')
                  }}
                </span>
                <span class="block text-xs text-amber-700">
                  {{
                    t(
                      'lensAdmin.datasourceWizard.pdfExtractImagesOnTextPagesHint'
                    )
                  }}
                </span>
              </span>
            </label>
            <div class="grid gap-4 md:grid-cols-2">
              <FormRow
                :label="t('lensAdmin.fields.pdfMaxPages')"
                :hint="t('lensAdmin.datasourceWizard.pdfMaxPagesTooltip')"
              >
                <input
                  v-model.number="form.conversion_pdf_max_pages"
                  class="form-input"
                  min="1"
                  type="number"
                />
              </FormRow>
              <FormRow
                :label="t('lensAdmin.fields.pdfMaxImagesPerPage')"
                :hint="
                  t('lensAdmin.datasourceWizard.pdfMaxImagesPerPageTooltip')
                "
              >
                <input
                  v-model.number="form.conversion_pdf_max_images_per_page"
                  class="form-input"
                  min="1"
                  type="number"
                />
              </FormRow>
              <FormRow
                :label="t('lensAdmin.fields.pdfRenderDpi')"
                :hint="t('lensAdmin.datasourceWizard.pdfRenderDpiTooltip')"
              >
                <input
                  v-model.number="form.conversion_pdf_render_dpi"
                  class="form-input"
                  min="1"
                  type="number"
                />
              </FormRow>
              <FormRow
                :label="t('lensAdmin.fields.pdfMinTextChars')"
                :hint="t('lensAdmin.datasourceWizard.pdfMinTextCharsTooltip')"
              >
                <input
                  v-model.number="form.conversion_pdf_min_text_chars"
                  class="form-input"
                  min="1"
                  type="number"
                />
              </FormRow>
              <FormRow
                :label="t('lensAdmin.fields.pdfMinImageAreaRatio')"
                :hint="
                  t('lensAdmin.datasourceWizard.pdfMinImageAreaRatioTooltip')
                "
              >
                <input
                  v-model.number="form.conversion_pdf_min_image_area_ratio"
                  class="form-input"
                  max="1"
                  min="0.01"
                  step="0.01"
                  type="number"
                />
              </FormRow>
            </div>
          </div>
        </section>
      </section>
      <section class="space-y-4 rounded-md border border-line p-3">
        <div>
          <h3 class="text-sm font-semibold text-ink-900">
            {{ t('lensAdmin.datasourceWizard.standaloneImagesTitle') }}
          </h3>
          <p class="mt-1 text-xs text-ink-500">
            {{ t('lensAdmin.datasourceWizard.standaloneImagesHint') }}
          </p>
        </div>
        <label class="flex items-start gap-3 text-sm text-ink-700">
          <input
            v-model="form.conversion_image"
            type="checkbox"
            class="mt-0.5 h-4 w-4 rounded border-line text-brand-600 focus:ring-brand-500"
          />
          <span>
            <span class="font-medium">
              {{ t('lensAdmin.datasourceWizard.convertImages') }}
            </span>
            <span class="block text-xs text-ink-500">
              {{ t('lensAdmin.datasourceWizard.convertImagesHint') }}
            </span>
          </span>
        </label>
        <FormRow
          v-if="form.conversion_image"
          :label="t('lensAdmin.fields.visionModel')"
          :hint="t('lensAdmin.datasourceWizard.visionModelTooltip')"
        >
          <BaseSelect v-model="form.conversion_vision_model_ref">
            <option value="">
              {{ t('lensAdmin.placeholders.noModel') }}
            </option>
            <option
              v-for="config in llmConfigOptions"
              :key="config.uuid || config.id"
              :value="config.uuid || config.id"
            >
              {{ formatLLMConfigLabel(config) }}
            </option>
          </BaseSelect>
          <p class="mt-1 text-xs text-ink-500">
            {{ t('lensAdmin.datasourceWizard.visionModelHint') }}
          </p>
        </FormRow>
      </section>
      <section class="space-y-4 rounded-md border border-line p-3">
        <div>
          <h3 class="text-sm font-semibold text-ink-900">
            {{ t('lensAdmin.datasourceWizard.globalConversionLimitsTitle') }}
          </h3>
          <p class="mt-1 text-xs text-ink-500">
            {{ t('lensAdmin.datasourceWizard.globalConversionLimitsHint') }}
          </p>
        </div>
        <div class="grid gap-4 md:grid-cols-2">
          <FormRow
            :label="t('lensAdmin.fields.maxFileSizeMb')"
            :hint="t('lensAdmin.datasourceWizard.maxFileSizeTooltip')"
          >
            <input
              v-model.number="form.conversion_max_file_size_mb"
              class="form-input"
              min="1"
              type="number"
            />
          </FormRow>
          <FormRow
            :label="t('lensAdmin.fields.maxImages')"
            :hint="t('lensAdmin.datasourceWizard.maxImagesTooltip')"
          >
            <input
              v-model.number="form.conversion_max_images"
              class="form-input"
              min="1"
              type="number"
            />
          </FormRow>
        </div>
      </section>
    </div>

    <p v-if="formError" class="mt-4 text-sm text-danger-700">
      {{ formError }}
    </p>

    <template #footer>
      <div class="flex items-center justify-between">
        <div class="flex items-center gap-2">
          <BaseButton
            variant="outline"
            @click="wizardStep > 1 ? prevWizardStep() : $emit('close')"
          >
            {{
              wizardStep > 1 ? t('lensAdmin.wizard.back') : t('common.cancel')
            }}
          </BaseButton>
        </div>
        <div class="flex items-center gap-3">
          <span class="text-xs text-ink-400">
            {{ wizardStep }} / {{ wizardStepCount }}
          </span>
          <BaseButton
            v-if="wizardStep < wizardStepCount"
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
            :disabled="
              !canProceedWizard ||
              pathResult?.status === 'blocked' ||
              (!isManagedWorkspace && connectionResult?.status !== 'success')
            "
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
  CheckCircle as CheckCircleIcon,
  Check as CheckIcon,
  ChevronDown as ChevronDownIcon,
  ChevronRight as ChevronRightIcon,
  Folder as FolderIcon,
  FolderOpen as FolderOpenIcon,
  HelpCircle as HelpCircleIcon,
  LoaderCircle as LoaderCircleIcon,
  Plus as PlusIcon,
  RefreshCw as RefreshCwIcon,
  X as XIcon,
  XCircle as XCircleIcon
} from '@lucide/vue'
import { computed, defineComponent, h, nextTick, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'

import BaseButton from '@/components/ui/BaseButton.vue'
import BaseDrawer from '@/components/ui/BaseDrawer.vue'
import BaseSelect from '@/components/ui/BaseSelect.vue'

import { formatLLMConfigLabel } from './adminHelpers'

const props = defineProps({
  show: Boolean,
  mode: { type: String, default: 'create' },
  form: { type: Object, required: true },
  config: { type: Object, required: true },
  lensnodes: { type: Array, default: () => [] },
  credentials: { type: Array, default: () => [] },
  llmConfigOptions: { type: Array, default: () => [] },
  syncIntervalSeconds: { type: Number, default: 3600 },
  syncPolicyMode: { type: String, default: 'interval' },
  syncCron: { type: String, default: '0 2 * * *' },
  syncTimezone: { type: String, default: 'Asia/Shanghai' },
  pathResult: { type: Object, default: null },
  connectionResult: { type: Object, default: null },
  checkingPath: Boolean,
  testingConnection: Boolean,
  refreshingCredentials: Boolean,
  refreshingDirectories: Boolean,
  saving: Boolean,
  formError: { type: String, default: '' }
})

const emit = defineEmits([
  'close',
  'save',
  'type-change',
  'check-path',
  'test-connection',
  'connection-change',
  'refresh-credentials',
  'refresh-dirs',
  'update:syncIntervalSeconds',
  'update:syncPolicyMode',
  'update:syncCron',
  'update:syncTimezone'
])

const { t } = useI18n()
const wizardStep = ref(1)
const creatingDirectoryParent = ref(null)
const expandedDirectories = ref(new Set())
const newDirectoryName = ref('')
const feishuAdvancedOpen = ref(false)
const pdfAdvancedOpen = ref(false)
const gitRepositorySearch = ref('')
const gitBulkBranch = ref('')
const acceptedCredentialUuid = ref('')

const syncIntervalSeconds = computed({
  get() {
    return props.syncIntervalSeconds
  },
  set(value) {
    emit('update:syncIntervalSeconds', value)
  }
})

const syncPolicyMode = computed({
  get() {
    return props.syncPolicyMode
  },
  set(value) {
    emit('update:syncPolicyMode', value)
  }
})

const syncCron = computed({
  get() {
    return props.syncCron
  },
  set(value) {
    emit('update:syncCron', value)
  }
})

const syncTimezone = computed({
  get() {
    return props.syncTimezone
  },
  set(value) {
    emit('update:syncTimezone', value)
  }
})

const FormRow = defineComponent({
  props: {
    hint: {
      type: String,
      default: ''
    },
    label: {
      type: String,
      required: true
    },
    required: {
      type: Boolean,
      default: false
    }
  },
  setup(rowProps, { slots }) {
    const hintVisible = ref(false)
    const hintTrigger = ref(null)
    const hintStyle = ref({})

    function showHint() {
      const rect = hintTrigger.value?.getBoundingClientRect()
      if (!rect) return
      const width = 288
      const margin = 12
      const left = Math.min(
        window.innerWidth - width - margin,
        Math.max(margin, rect.left)
      )
      hintStyle.value = {
        left: `${left}px`,
        top: `${rect.bottom + 8}px`,
        width: `${width}px`
      }
      hintVisible.value = true
    }

    function hideHint() {
      hintVisible.value = false
    }

    return () =>
      h('div', [
        h(
          'label',
          {
            class:
              'mb-1 flex items-center gap-1 text-sm font-medium text-ink-700'
          },
          [
            h('span', rowProps.label),
            rowProps.required
              ? h('span', { class: 'text-danger-600' }, '*')
              : null,
            rowProps.hint
              ? h(
                  'span',
                  {
                    ref: hintTrigger,
                    class:
                      'relative inline-flex h-4 w-4 items-center ' +
                      'justify-center text-ink-400',
                    onBlur: hideHint,
                    onFocus: showHint,
                    onMouseenter: showHint,
                    onMouseleave: hideHint,
                    tabindex: 0
                  },
                  [
                    h(HelpCircleIcon, { class: 'h-3.5 w-3.5' }),
                    h(
                      'span',
                      {
                        'aria-hidden': !hintVisible.value,
                        class:
                          'pointer-events-none fixed z-[60] rounded-md ' +
                          'border border-line bg-surface p-2 text-xs ' +
                          'font-normal leading-5 text-ink-600 shadow-lg ' +
                          'transition-opacity ' +
                          (hintVisible.value ? 'opacity-100' : 'opacity-0'),
                        style: hintStyle.value
                      },
                      rowProps.hint
                    )
                  ]
                )
              : null
          ]
        ),
        slots.default?.()
      ])
  }
})

const drawerTitle = computed(() =>
  props.mode === 'create'
    ? t('lensAdmin.datasourceWizard.createTitle')
    : t('lensAdmin.datasourceWizard.editTitle')
)

const drawerSubtitle = computed(() =>
  props.mode === 'edit' ? props.form.name || '' : ''
)

const sourceTypes = computed(() => [
  {
    value: 'github',
    label: 'GitHub',
    description: t('lensAdmin.datasourceWizard.githubDesc')
  },
  {
    value: 'gitlab',
    label: 'GitLab',
    description: t('lensAdmin.datasourceWizard.gitlabDesc')
  },
  {
    value: 'feishu',
    label: t('lensAdmin.datasourceWizard.feishu'),
    description: t('lensAdmin.datasourceWizard.feishuDesc')
  },
  {
    value: 'managed_workspace',
    label: t('lensAdmin.datasourceWizard.managedWorkspace'),
    description: t('lensAdmin.datasourceWizard.managedWorkspaceDesc')
  }
])

const selectedSourceTypeDescription = computed(() => {
  const selected = sourceTypes.value.find(
    (type) => type.value === props.form.source_type
  )
  return selected?.description || ''
})

const isManagedWorkspace = computed(
  () => props.form.source_type === 'managed_workspace'
)

const wizardStepsMeta = computed(() => {
  const steps = [
    { key: 'basic', title: t('lensAdmin.datasourceWizard.step1Title') },
    { key: 'node', title: t('lensAdmin.datasourceWizard.step2Title') },
    { key: 'connection', title: t('lensAdmin.datasourceWizard.step3Title') },
    { key: 'sync', title: t('lensAdmin.datasourceWizard.step4Title') },
    { key: 'conversion', title: t('lensAdmin.datasourceWizard.step5Title') }
  ]
  if (isManagedWorkspace.value) {
    return steps.filter((step) => ['basic', 'node', 'sync'].includes(step.key))
  }
  return steps
})

const wizardStepCount = computed(() => wizardStepsMeta.value.length)
const activeStepKey = computed(
  () => wizardStepsMeta.value[wizardStep.value - 1]?.key || 'basic'
)

const onlineLensNodes = computed(() =>
  props.lensnodes.filter(
    (node) =>
      node.status === 'online' &&
      node.enrollment_status === 'approved' &&
      !node.token_revoked
  )
)

const selectedLensNode = computed(() =>
  props.lensnodes.find((node) => node.uuid === props.form.lensnode_uuid)
)

const workspaceRoot = computed(() =>
  String(selectedLensNode.value?.workspace_path || '/workspace').replace(
    /\/+$/,
    ''
  )
)

const workspacePrefix = computed(() => `${workspaceRoot.value}/`)

const workspaceDirectoryTree = computed(() => {
  const dirs = Array.isArray(selectedLensNode.value?.available_dirs)
    ? selectedLensNode.value.available_dirs
    : []
  return dirs
    .map((dir) => normalizeDirectoryNode(dir))
    .filter((dir) => dir.relative)
})

const targetPath = computed(() => {
  const relative = String(props.form.workspace_relative_path || '').trim()
  return relative
    ? `${workspacePrefix.value}${relative}`
    : workspacePrefix.value
})

const canCreateTargetDirectory = computed(
  () => !!normalizeRelativePathInput(newDirectoryName.value)
)

const canTestConnection = computed(() => {
  if (!props.form.lensnode_uuid) {
    return false
  }
  if (isGitSourceType(props.form.source_type)) {
    return !!(
      props.form.credential_uuid && credentialScopeUrl(selectedCredential.value)
    )
  }
  return !!(
    hasFeishuCredential() && credentialScopeUrl(selectedCredential.value)
  )
})

const pathResultMessage = computed(() => {
  if (!props.pathResult) {
    return ''
  }
  const code = props.pathResult.message_code
  if (code) {
    const key = `lensAdmin.datasourceWizard.pathStatus.${code}`
    const translated = t(key)
    if (translated !== key) {
      return translated
    }
  }
  return props.pathResult.message || ''
})

const connectionResultMessage = computed(() => {
  if (!props.connectionResult) {
    return ''
  }
  const code = props.connectionResult.message_code
  if (code) {
    const key = `lensAdmin.datasourceWizard.connectionStatus.${code}`
    const translated = t(key)
    if (translated !== key) {
      const error = props.connectionResult.details?.error
      const attempts = props.connectionResult.details?.attempts
      if (Array.isArray(attempts) && attempts.length) {
        const detail = attempts
          .map((item) =>
            [item.service, item.error || item.url].filter(Boolean).join(': ')
          )
          .join('；')
        return detail ? `${translated} ${detail}` : translated
      }
      return error ? `${translated} ${error}` : translated
    }
  }
  return props.connectionResult.message || ''
})

const canProceedWizard = computed(() => {
  if (activeStepKey.value === 'basic') {
    return !!props.form.name?.trim() && !!props.form.source_type
  }
  if (activeStepKey.value === 'node') {
    return !!props.form.lensnode_uuid
  }
  if (activeStepKey.value === 'connection') {
    if (props.connectionResult?.status !== 'success') {
      return false
    }
    if (isGitSourceType(props.form.source_type)) {
      if (isGitOrganizationMode.value) {
        return selectedGitOrganizationRepositories.value.length > 0
      }
      return (
        gitBranchOptions.value.length > 0 &&
        gitBranchOptions.value.includes(props.config.branch)
      )
    }
    return true
  }
  if (!props.form.workspace_relative_path?.trim()) {
    return false
  }
  if (props.pathResult?.status === 'blocked' || !props.pathResult) {
    return false
  }
  if (isManagedWorkspace.value) {
    return props.pathResult?.status === 'available'
  }
  if (syncPolicyMode.value === 'crontab') {
    return (
      String(syncCron.value || '')
        .trim()
        .split(/\s+/).length === 5
    )
  }
  return true
})

const gitBranchOptions = computed(() => {
  if (!isGitSourceType(props.form.source_type)) {
    return []
  }
  const branches = props.connectionResult?.details?.branches
  return Array.isArray(branches) ? branches : []
})

const isGitOrganizationMode = computed(
  () =>
    isGitSourceType(props.form.source_type) &&
    !gitBranchOptions.value.length &&
    (Array.isArray(props.config.git_repositories) ||
      Array.isArray(props.connectionResult?.details?.repositories))
)

const gitOrganizationRepositories = computed(() => {
  if (!isGitOrganizationMode.value) {
    return []
  }
  return Array.isArray(props.config.git_repositories)
    ? props.config.git_repositories
    : []
})

const selectedGitOrganizationRepositories = computed(() =>
  gitOrganizationRepositories.value.filter(
    (repo) => repo.selected && repo.branch
  )
)

const filteredGitOrganizationRepositories = computed(() => {
  const keyword = gitRepositorySearch.value.trim().toLowerCase()
  if (!keyword) {
    return gitOrganizationRepositories.value
  }
  return gitOrganizationRepositories.value.filter((repo) =>
    [repo.name, repo.path, repo.repo_url]
      .filter(Boolean)
      .some((value) => String(value).toLowerCase().includes(keyword))
  )
})

const allGitOrganizationRepositoriesSelected = computed(
  () =>
    filteredGitOrganizationRepositories.value.length > 0 &&
    filteredGitOrganizationRepositories.value.every((repo) => repo.selected)
)

const selectedCredential = computed(() =>
  props.credentials.find(
    (credential) => credential.uuid === props.form.credential_uuid
  )
)

const filteredCredentials = computed(() => {
  const selectedUuid = props.form.credential_uuid
  if (props.form.source_type === 'feishu') {
    return props.credentials.filter(
      (credential) =>
        credential.uuid === selectedUuid ||
        credential.auth_type === 'feishu_app'
    )
  }
  return props.credentials.filter(
    (credential) =>
      credential.uuid === selectedUuid ||
      (['https_token', 'none'].includes(credential.auth_type) &&
        [props.form.source_type, 'generic'].includes(credential.provider))
  )
})

function isGitSourceType(sourceType) {
  return ['git', 'github', 'gitlab'].includes(sourceType)
}

function credentialOptionLabel(credential) {
  const provider = credentialProviderText(credential)
  const scope = credentialScopeUrl(credential)
  return [credential.name || scope, provider].filter(Boolean).join(' · ')
}

function credentialProviderText(credential) {
  if (credential?.provider === 'gitlab') {
    return 'GitLab'
  }
  if (credential?.provider === 'github') {
    return 'GitHub'
  }
  if (credential?.provider === 'feishu') {
    return t('lensAdmin.datasourceWizard.feishu')
  }
  return 'Git'
}

function credentialValidationText(credential) {
  if (credential?.validation_status === 'success') {
    return t('lensAdmin.credentials.validationSuccess')
  }
  if (credential?.validation_status === 'failed') {
    return t('lensAdmin.credentials.validationFailed')
  }
  return t('lensAdmin.credentials.validationUnchecked')
}

function credentialValidationClass(credential) {
  if (credential?.validation_status === 'success') {
    return 'border-success-200 bg-success-50 text-success-700'
  }
  if (credential?.validation_status === 'failed') {
    return 'border-danger-200 bg-danger-50 text-danger-700'
  }
  return 'border-line bg-surface text-ink-500'
}

function credentialScopeText(credential) {
  return credentialScopeUrl(credential) || '-'
}

function credentialScopeUrl(credential) {
  const summary = credential?.scope_summary || {}
  return (
    summary.organization_url || summary.folder_url || summary.folder_token || ''
  )
}

function nextWizardStep() {
  if (wizardStep.value < wizardStepCount.value) wizardStep.value++
}

function prevWizardStep() {
  if (wizardStep.value > 1) wizardStep.value--
}

function normalizeDirectoryNode(raw) {
  const path = typeof raw === 'string' ? raw : raw?.path || raw?.name || ''
  const name = typeof raw === 'string' ? path.split('/').pop() : raw?.name
  const relative = pathToWorkspaceRelative(path)
  const children = Array.isArray(raw?.children)
    ? raw.children
        .map((child) => normalizeDirectoryNode(child))
        .filter((child) => child.relative)
    : []
  return {
    path,
    name: name || relative || path,
    relative,
    children
  }
}

function pathToWorkspaceRelative(path) {
  const value = String(path || '').replace(/\/+$/, '')
  const workspace = workspaceRoot.value
  if (!value || value === workspace) {
    return ''
  }
  if (value.startsWith(`${workspace}/`)) {
    return value.slice(workspace.length + 1)
  }
  return value.replace(/^\/+/, '')
}

function isSelectedDirectory(relative) {
  return props.form.workspace_relative_path === relative
}

function directoryButtonClass(relative) {
  return isSelectedDirectory(relative)
    ? 'bg-brand-50 text-brand-700'
    : 'text-ink-700'
}

function isDirectoryExpanded(relative) {
  return expandedDirectories.value.has(relative)
}

function toggleDirectoryExpanded(relative) {
  const next = new Set(expandedDirectories.value)
  if (next.has(relative)) {
    next.delete(relative)
  } else {
    next.add(relative)
  }
  expandedDirectories.value = next
}

function selectTargetDirectory(relative) {
  props.form.workspace_relative_path = relative
  emit('check-path')
}

function normalizeRelativePathInput(value) {
  const path = String(value || '')
    .trim()
    .replace(/\/+$/, '')
  if (!path || path.startsWith('/')) {
    return ''
  }
  const parts = path.split('/')
  if (parts.some((part) => !part || part === '.' || part === '..')) {
    return ''
  }
  return parts.join('/')
}

function startCreateTargetDirectory(parent) {
  creatingDirectoryParent.value = parent
  newDirectoryName.value = ''
  if (parent) {
    expandedDirectories.value = new Set([
      ...expandedDirectories.value,
      parent.split('/')[0]
    ])
  }
}

function selectNewTargetDirectory() {
  if (!canCreateTargetDirectory.value) {
    return
  }
  const parent = String(creatingDirectoryParent.value || '').replace(/\/+$/, '')
  const name = normalizeRelativePathInput(newDirectoryName.value)
  props.form.workspace_relative_path = parent ? `${parent}/${name}` : name
  newDirectoryName.value = ''
  creatingDirectoryParent.value = null
  emit('check-path')
}

function toggleAllGitOrganizationRepositories(event) {
  const checked = event.target.checked
  filteredGitOrganizationRepositories.value.forEach((repo) => {
    repo.selected = checked
  })
}

function applyGitBulkBranch() {
  const branch = gitBulkBranch.value.trim()
  if (!branch) {
    return
  }
  filteredGitOrganizationRepositories.value.forEach((repo) => {
    if (repo.selected) {
      repo.branch = branch
    }
  })
}

function cancelCreateTargetDirectory() {
  newDirectoryName.value = ''
  creatingDirectoryParent.value = null
}

function checkCurrentPathIfNeeded() {
  if (!props.show || wizardStep.value !== 4) {
    return
  }
  if (
    props.checkingPath ||
    props.pathResult ||
    !props.form.lensnode_uuid ||
    !props.form.workspace_relative_path?.trim()
  ) {
    return
  }
  emit('check-path')
}

function hasFeishuCredential() {
  return !!props.form.credential_uuid
}

function applySelectedCredentialToConfig(options = {}) {
  const credential = selectedCredential.value
  if (!credential) {
    return
  }
  const scopeUrl = credentialScopeUrl(credential)
  if (isGitSourceType(props.form.source_type)) {
    if (options.clearGitSelection) {
      clearGitSelectionConfig()
    }
    props.config.auth_scheme =
      credential.auth_type === 'none' ? 'none' : 'token'
    props.config.repo_url = scopeUrl
    props.config.organization_url = scopeUrl
    return
  }
  props.config.sync_mode = 'drive_folder'
  props.config.folder_url = scopeUrl
  props.config.recursive = props.config.recursive !== false
  props.config.max_depth = Number(props.config.max_depth) || 10
}

function clearGitSelectionConfig() {
  delete props.config.git_repositories
  delete props.config.repositories
  delete props.config.scope_type
  delete props.config.branch
}

function hasGitSelectionConfig() {
  return Boolean(
    props.config.branch ||
      props.config.scope_type ||
      (Array.isArray(props.config.git_repositories) &&
        props.config.git_repositories.length) ||
      (Array.isArray(props.config.repositories) &&
        props.config.repositories.length)
  )
}

function shouldConfirmCredentialChange(nextUuid, previousUuid) {
  return Boolean(
    props.mode === 'edit' &&
      props.show &&
      previousUuid &&
      nextUuid &&
      nextUuid !== previousUuid &&
      isGitSourceType(props.form.source_type) &&
      hasGitSelectionConfig()
  )
}

function testConnectionIfVisible() {
  if (props.show && wizardStep.value === 3 && canTestConnection.value) {
    emit('test-connection')
  }
}

async function handleCredentialChange(nextUuid) {
  const previousUuid =
    acceptedCredentialUuid.value || props.form.credential_uuid
  if (nextUuid === props.form.credential_uuid) {
    return
  }
  if (shouldConfirmCredentialChange(nextUuid, previousUuid)) {
    const confirmed = window.confirm(
      t('lensAdmin.datasourceWizard.credentialChangeWarning')
    )
    if (!confirmed) {
      props.form.credential_uuid = previousUuid
      return
    }
  }
  props.form.credential_uuid = nextUuid
  const clearGitSelection = Boolean(
    previousUuid && nextUuid && nextUuid !== previousUuid
  )
  await nextTick()
  applySelectedCredentialToConfig({ clearGitSelection })
  acceptedCredentialUuid.value = nextUuid || ''
  await nextTick()
  testConnectionIfVisible()
}

watch(
  () => props.show,
  (show) => {
    if (show) {
      wizardStep.value = 1
      creatingDirectoryParent.value = null
      expandedDirectories.value = new Set()
      newDirectoryName.value = ''
      feishuAdvancedOpen.value = false
      pdfAdvancedOpen.value = false
      gitRepositorySearch.value = ''
      gitBulkBranch.value = ''
      acceptedCredentialUuid.value = props.form.credential_uuid || ''
    }
  }
)

watch(
  () => props.form.lensnode_uuid,
  () => {
    creatingDirectoryParent.value = null
    expandedDirectories.value = new Set()
    newDirectoryName.value = ''
  }
)

watch(
  () => [
    props.show,
    wizardStep.value,
    props.form.lensnode_uuid,
    props.form.workspace_relative_path,
    props.pathResult,
    props.checkingPath
  ],
  checkCurrentPathIfNeeded,
  { flush: 'post' }
)

watch(
  () => props.form.conversion_document,
  (value) => {
    if (!value) {
      props.form.conversion_embedded_image = false
      props.form.conversion_document_model_ref = ''
      pdfAdvancedOpen.value = false
    }
  }
)

watch(
  () => props.form.conversion_embedded_image,
  (value) => {
    if (!value) {
      pdfAdvancedOpen.value = false
    }
  }
)

watch(
  () => props.form.conversion_image,
  (value) => {
    if (!value) {
      props.form.conversion_vision_model_ref = ''
    }
  }
)

watch(
  () => [
    props.form.lensnode_uuid,
    props.form.source_type,
    props.form.credential_uuid,
    datasourceConnectionConfigSignature()
  ],
  () => {
    emit('connection-change')
  }
)

watch(
  () => wizardStep.value,
  () => {
    testConnectionIfVisible()
  },
  { flush: 'post' }
)

function datasourceConnectionConfigSignature() {
  const config = { ...(props.config || {}) }
  delete config.git_repositories
  delete config.organization_url
  return JSON.stringify(config)
}
</script>

<style scoped>
.form-input {
  @apply w-full rounded-lg border border-line bg-surface px-3 py-2 text-sm text-ink-900 focus:border-brand-500 focus:outline-none focus:ring-2 focus:ring-brand-500/20;
}

.directory-action-button {
  @apply inline-flex h-7 w-7 shrink-0 items-center justify-center rounded transition-colors focus:outline-none focus:ring-2 focus:ring-brand-500/20;
}

.directory-name-input {
  @apply h-7 min-w-0 flex-1 rounded border border-line bg-surface px-2 text-sm text-ink-900 focus:border-brand-500 focus:outline-none focus:ring-2 focus:ring-brand-500/20;
}
</style>
