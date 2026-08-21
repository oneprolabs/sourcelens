<template>
  <div
    class="lens-chat-page qa-screen-view"
    :class="{
      'visual-viewport-constrained': visualViewportConstrained
    }"
    :style="mobileViewportStyle"
  >
    <Transition
      enter-active-class="transition-opacity duration-200"
      enter-from-class="opacity-0"
      enter-to-class="opacity-100"
      leave-active-class="transition-opacity duration-150"
      leave-from-class="opacity-100"
      leave-to-class="opacity-0"
    >
      <div
        v-if="sidebarOpen && isMobile"
        class="fixed inset-0 z-20 bg-[#2a2722]/35"
        @click="sidebarOpen = false"
      />
    </Transition>

    <aside
      class="sidebar"
      :inert="isMobile && !sidebarOpen"
      :aria-hidden="isMobile && !sidebarOpen"
      :class="[
        sidebarOpen && isMobile ? 'sidebar-open' : '',
        sidebarCollapsedActive ? 'sidebar-collapsed' : 'sidebar-expanded'
      ]"
    >
      <div class="side-head">
        <div
          class="sidebar-brand"
          :class="sidebarCollapsedActive ? 'sidebar-brand-collapsed' : ''"
        >
          <button
            type="button"
            class="sidebar-brand-link"
            :aria-label="
              sidebarCollapsedActive
                ? t('common.expand')
                : t('management.logoTitle')
            "
            @click="handleSidebarLogoClick"
          >
            <span
              class="sidebar-logo-stage"
              :class="
                sidebarCollapsedActive ? 'sidebar-logo-stage-collapsed' : ''
              "
            >
              <BrandLogo
                variant="wordmark"
                wrapperClass="sidebar-logo-layer sidebar-wordmark-layer"
              />
              <BrandLogo
                variant="mark"
                wrapperClass="sidebar-logo-layer sidebar-mark-layer"
              />
            </span>
          </button>
          <button
            v-if="!isMobile"
            class="sidebar-collapse-btn"
            type="button"
            :aria-label="
              sidebarCollapsedActive ? t('common.expand') : t('common.collapse')
            "
            @click="sidebarCollapsed = !sidebarCollapsed"
          >
            <PanelLeftClose
              v-if="!sidebarCollapsedActive"
              :size="20"
              :stroke-width="2.1"
              aria-hidden="true"
            />
            <PanelLeftOpen
              v-else
              :size="20"
              :stroke-width="2.1"
              aria-hidden="true"
            />
          </button>
          <button
            v-else
            class="sidebar-collapse-btn"
            type="button"
            :aria-label="t('common.close')"
            @click="sidebarOpen = false"
          >
            <svg
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              stroke-width="2.25"
              aria-hidden="true"
            >
              <path
                d="M6 18L18 6M6 6l12 12"
                stroke-linecap="round"
                stroke-linejoin="round"
              />
            </svg>
          </button>
        </div>

        <button
          class="new-chat-btn"
          :class="sidebarCollapsedActive ? 'new-chat-btn-collapsed' : ''"
          type="button"
          @click="createNewSession"
        >
          <Plus :size="18" :stroke-width="2.25" aria-hidden="true" />
          <span v-if="!sidebarCollapsedActive || isMobile">
            {{ t('lens.chat.newSession') }}
          </span>
        </button>
      </div>

      <div class="side-scroll">
        <section
          v-if="!sidebarCollapsedActive || isMobile"
          class="sessions-section"
        >
          <div class="sessions-head">
            <h2 class="sr-only">{{ t('lens.chat.sessionLists') }}</h2>
            <div
              class="session-filters"
              role="group"
              :aria-label="t('lens.chat.sessionLists')"
            >
              <button
                type="button"
                :class="{ 'session-filter-active': !showArchivedSessions }"
                :aria-pressed="!showArchivedSessions"
                @click="switchSessionView(false)"
              >
                {{ t('lens.chat.sessions') }}
              </button>
              <button
                type="button"
                :class="{ 'session-filter-active': showArchivedSessions }"
                :aria-pressed="showArchivedSessions"
                @click="switchSessionView(true)"
              >
                {{ t('lens.chat.archivedSessions') }}
              </button>
            </div>
          </div>
          <div class="sessions-list">
            <div
              v-for="session in sessions"
              :key="session.uuid"
              class="session-item"
              :class="{
                'session-item-active': selectedSessionUuid === session.uuid
              }"
            >
              <input
                v-if="renamingSessionUuid === session.uuid"
                v-model="renameDraft"
                class="session-rename-input"
                :placeholder="t('lens.chat.untitledSession')"
                @click.stop
                @keydown.enter.stop.prevent="saveRename(session)"
                @keydown.esc.stop="cancelRename"
                @blur="saveRename(session)"
              />
              <template v-else>
                <div
                  class="min-w-0 flex-1 cursor-pointer"
                  :title="session.title || t('lens.chat.untitledSession')"
                  @click="selectSession(session)"
                >
                  <div class="session-title-row">
                    <Pin
                      v-if="session.pinned_at"
                      class="session-pinned-icon"
                      :size="13"
                      :stroke-width="2.2"
                      :aria-label="t('lens.chat.pinned')"
                    />
                    <div class="session-title">
                      {{ session.title || t('lens.chat.untitledSession') }}
                    </div>
                  </div>
                </div>

                <div class="flex shrink-0 items-center gap-1">
                  <span
                    v-if="sessionActivity.hasActivity(session.uuid)"
                    class="session-activity-indicator"
                    :title="t('lens.chat.sessionActive')"
                  >
                    <LoaderCircle
                      :size="15"
                      :stroke-width="2.2"
                      aria-hidden="true"
                    />
                    <span class="sr-only">
                      {{ t('lens.chat.sessionActive') }}
                    </span>
                  </span>
                  <span
                    v-else-if="sessionHasUnreadAnswer(session.uuid)"
                    class="session-unread-indicator"
                    :title="t('lens.chat.unreadAnswer')"
                  >
                    <span class="sr-only">
                      {{ t('lens.chat.unreadAnswer') }}
                    </span>
                  </span>
                  <RowActionMenu
                    class="session-overflow"
                    :actions="sessionActions(session)"
                    :label="
                      t('lens.chat.sessionActions', {
                        title: session.title || t('lens.chat.untitledSession')
                      })
                    "
                    @click.stop
                    @select="handleSessionAction(session, $event)"
                  />
                </div>
              </template>
            </div>
            <p v-if="!sessions.length" class="session-list-empty">
              {{
                showArchivedSessions
                  ? t('lens.chat.noArchivedSessions')
                  : t('lens.chat.noRecentSessions')
              }}
            </p>
          </div>
        </section>
      </div>

      <div class="sidebar-footer">
        <UserDock
          :collapsed="sidebarCollapsedActive"
          :is-mobile="isMobile"
          @require-login="requireLogin"
          @open-my-shares="openMyShares"
        />
      </div>
    </aside>

    <main class="main-shell">
      <div v-if="isMobile" class="mobile-topbar">
        <button
          v-if="mySharesOpen"
          type="button"
          class="sidebar-collapse-btn"
          :aria-label="t('common.back')"
          @click="mySharesOpen = false"
        >
          <ArrowLeft :size="20" :stroke-width="2.1" aria-hidden="true" />
        </button>
        <button
          v-else
          type="button"
          class="sidebar-collapse-btn"
          :aria-label="t('lens.chat.sessions')"
          @click="sidebarOpen = true"
        >
          <PanelLeftOpen :size="20" :stroke-width="2.1" aria-hidden="true" />
        </button>
        <div class="mobile-topbar-title">
          <span v-if="mySharesOpen" class="mobile-topbar-title-text">
            {{ t('lens.qa.mineTitle') }}
          </span>
          <AssistantSwitcher v-else-if="switchable" mode="header" />
          <span v-else class="mobile-topbar-title-text">
            {{ assistantName }}
          </span>
        </div>
        <button
          type="button"
          class="sidebar-collapse-btn"
          :aria-label="t('lens.chat.newSession')"
          @click="createNewSession"
        >
          <Plus :size="20" :stroke-width="2.1" aria-hidden="true" />
        </button>
      </div>
      <header
        v-if="!isMobile && (mySharesOpen || assistantName)"
        class="chat-header"
      >
        <template v-if="mySharesOpen">
          <button
            type="button"
            class="chat-header-back"
            :aria-label="t('common.back')"
            @click="mySharesOpen = false"
          >
            <ArrowLeft :size="18" :stroke-width="2.1" aria-hidden="true" />
          </button>
          <span class="chat-header-title">{{ t('lens.qa.mineTitle') }}</span>
        </template>
        <template v-else>
          <div class="chat-header-assistant">
            <AssistantSwitcher v-if="switchable" mode="header" />
            <div v-else class="chat-header-title">{{ assistantName }}</div>
            <p
              v-if="assistantDescription"
              class="chat-header-description"
              :class="{ 'pl-2': switchable }"
              :title="assistantDescription"
            >
              {{ assistantDescription }}
            </p>
          </div>
          <router-link
            v-if="assistantSlug"
            :to="`/lens/assistants/${assistantSlug}/qa`"
            class="chat-header-link ml-auto"
          >
            <MessagesSquare :size="15" :stroke-width="2" aria-hidden="true" />
            {{ t('lens.qa.publicListLink') }}
          </router-link>
        </template>
      </header>
      <MySharesPanel v-if="mySharesOpen" />
      <template v-else>
        <div
          ref="scrollRef"
          class="thread-scroll"
          @scroll.passive="handleThreadScroll"
        >
          <div v-if="!booted" class="thread-loading">
            <BaseLoading />
          </div>
          <div v-else-if="!hasAssistant" class="thread-loading">
            <AssistantEmptyState :variant="emptyVariant" />
          </div>
          <div v-else class="thread">
            <div
              v-if="isMobile && !decoratedMessages.length && !showLiveAnswer"
              class="chat-welcome"
            >
              <p class="chat-welcome-assistant">{{ assistantName }}</p>
              <h1 class="chat-welcome-title">
                {{ t('lens.chat.startTitle') }}
              </h1>
              <p v-if="assistantDescription" class="chat-welcome-description">
                {{ assistantDescription }}
              </p>
            </div>
            <div
              v-for="message in decoratedMessages"
              :key="message.uuid"
              class="message-row"
              :class="
                message.role === 'user'
                  ? 'message-row-user'
                  : 'message-row-assistant'
              "
            >
              <div class="message-body">
                <details
                  v-if="structuredProgress(message._runtimeState).items.length"
                  class="runtime-progress-card"
                >
                  <summary class="runtime-progress-summary">
                    <span class="runtime-card-title">
                      {{
                        progressTitle(
                          structuredProgress(message._runtimeState).kind,
                          structuredProgress(message._runtimeState).hasPlan
                        )
                      }}
                    </span>
                    <span class="runtime-progress-summary-text">
                      {{
                        structuredProgressText(
                          message._runtimeState,
                          message.thinking.duration_seconds,
                          true
                        )
                      }}
                    </span>
                    <span class="runtime-progress-chevron" aria-hidden="true">
                      ⌄
                    </span>
                  </summary>
                  <div
                    v-if="
                      structuredProgress(message._runtimeState).kind ===
                      'workflow'
                    "
                    class="runtime-workflow"
                  >
                    <div
                      v-for="task in structuredProgress(message._runtimeState)
                        .tasks"
                      :key="task.id"
                      class="runtime-workflow-task"
                      :class="{
                        'is-direct': !structuredProgress(message._runtimeState)
                          .hasPlan
                      }"
                    >
                      <div
                        v-if="structuredProgress(message._runtimeState).hasPlan"
                        class="runtime-plan-step runtime-task-row"
                        :class="{
                          'is-active-ancestor': isActiveProgressAncestor(
                            task,
                            task.stages
                          )
                        }"
                      >
                        <span
                          class="runtime-plan-status"
                          :class="[
                            `is-${task.status}`,
                            {
                              'is-active-ancestor': isActiveProgressAncestor(
                                task,
                                task.stages
                              )
                            }
                          ]"
                          aria-hidden="true"
                        >
                          {{ progressStatusIcon(task.status) }}
                        </span>
                        <span>{{ workflowTaskTitle(task) }}</span>
                      </div>
                      <div
                        v-for="stage in task.stages"
                        :key="stage.id"
                        class="runtime-workflow-stage"
                      >
                        <div
                          class="runtime-plan-step runtime-stage-row"
                          :class="{
                            'is-active-ancestor': isActiveProgressAncestor(
                              stage,
                              stage.steps
                            )
                          }"
                        >
                          <span
                            class="runtime-plan-status"
                            :class="[
                              `is-${stage.status}`,
                              {
                                'is-active-ancestor': isActiveProgressAncestor(
                                  stage,
                                  stage.steps
                                )
                              }
                            ]"
                            aria-hidden="true"
                          >
                            {{ progressStatusIcon(stage.status) }}
                          </span>
                          <span>{{ workflowStageTitle(stage.kind) }}</span>
                        </div>
                        <div class="runtime-workflow-steps">
                          <div
                            v-for="step in stage.steps"
                            :key="step.id"
                            class="runtime-plan-step runtime-step-row"
                          >
                            <span
                              class="runtime-plan-status"
                              :class="`is-${step.status}`"
                              aria-hidden="true"
                            >
                              {{ progressStatusIcon(step.status) }}
                            </span>
                            <span>{{ workflowStepTitle(step) }}</span>
                          </div>
                        </div>
                      </div>
                    </div>
                  </div>
                  <div
                    v-else-if="
                      structuredProgress(message._runtimeState).kind ===
                      'activity'
                    "
                    class="runtime-node-activities runtime-standalone-activities"
                  >
                    <div
                      v-for="activity in structuredProgress(
                        message._runtimeState
                      ).items"
                      :key="activity.id"
                      class="runtime-node-activity"
                    >
                      <span class="runtime-activity-indicator">✓</span>
                      <span>{{ activityLabel(activity.kind) }}</span>
                      <span
                        v-if="activity.count > 1"
                        class="runtime-activity-count"
                      >
                        ×{{ activity.count }}
                      </span>
                    </div>
                  </div>
                  <div
                    v-else
                    v-for="item in structuredProgress(message._runtimeState)
                      .items"
                    :key="item.id"
                    class="runtime-plan-node"
                  >
                    <div class="runtime-plan-step">
                      <span
                        class="runtime-plan-status"
                        :class="`is-${item.status}`"
                        aria-hidden="true"
                      >
                        {{ progressStatusIcon(item.status) }}
                      </span>
                      <span class="runtime-step-content">
                        <span>{{ item.title }}</span>
                        <span v-if="item.summary" class="runtime-step-summary">
                          {{ item.summary }}
                        </span>
                      </span>
                    </div>
                    <div
                      v-if="
                        nodeActivities(message._runtimeState, item.id).length
                      "
                      class="runtime-node-activities"
                    >
                      <div
                        v-for="activity in nodeActivities(
                          message._runtimeState,
                          item.id
                        )"
                        :key="activity.id"
                        class="runtime-node-activity"
                      >
                        <span class="runtime-activity-indicator">✓</span>
                        <span>{{ activityLabel(activity.kind) }}</span>
                        <span
                          v-if="activity.count > 1"
                          class="runtime-activity-count"
                        >
                          ×{{ activity.count }}
                        </span>
                      </div>
                    </div>
                  </div>
                </details>

                <div
                  v-if="message._runtimeState?.capabilityBlock"
                  class="runtime-block-card"
                  role="status"
                >
                  <div class="runtime-card-title">
                    {{ t('lens.chat.runtime.blockedTitle') }}
                  </div>
                  <div>
                    {{
                      capabilityRecovery(message._runtimeState.capabilityBlock)
                    }}
                  </div>
                </div>

                <div
                  v-if="message._runtimeState?.executionFailure"
                  class="runtime-block-card"
                  role="status"
                >
                  <div class="runtime-card-title">
                    {{ t('lens.chat.runtime.executionFailedTitle') }}
                  </div>
                  <div>
                    {{ executionFailureRecovery(message._runtimeState) }}
                  </div>
                </div>

                <div
                  v-if="message._runtimeState?.verificationFailure"
                  class="runtime-outcome-card"
                  role="status"
                >
                  <div class="runtime-card-title">
                    {{ t('lens.chat.runtime.verificationFailedTitle') }}
                  </div>
                  <div>
                    {{ verificationFailureRecovery(message._runtimeState) }}
                  </div>
                </div>

                <div
                  v-if="
                    message._runtimeState?.outcome === 'partial' ||
                    (message._runtimeState?.outcome === 'blocked' &&
                      !message._runtimeState?.clarificationRequest &&
                      !message._runtimeState?.capabilityBlock &&
                      !message._runtimeState?.executionFailure &&
                      !message._runtimeState?.verificationFailure)
                  "
                  class="runtime-outcome-card"
                  role="status"
                >
                  {{
                    message._runtimeState.outcome === 'blocked'
                      ? t('lens.chat.runtime.outcomeBlocked')
                      : t('lens.chat.runtime.outcomePartial')
                  }}
                </div>

                <div
                  v-if="clarificationRequestFor(message)"
                  class="clarification-card"
                  role="status"
                >
                  <div class="runtime-card-title">
                    {{ t('lens.chat.runtime.clarificationTitle') }}
                  </div>
                  <p class="clarification-question">
                    {{ clarificationRequestFor(message).question }}
                  </p>
                  <label
                    class="clarification-label"
                    :for="`clarification-${message.run}`"
                  >
                    {{ t('lens.chat.runtime.clarificationHint') }}
                  </label>
                  <textarea
                    :id="`clarification-${message.run}`"
                    class="clarification-input"
                    rows="2"
                    :value="clarificationAnswerFor(message)"
                    :placeholder="
                      t('lens.chat.runtime.clarificationPlaceholder')
                    "
                    :disabled="
                      isClarificationAnswered(message) ||
                      isClarificationSubmitting(message)
                    "
                    @input="setClarificationAnswer(message, $event)"
                  />
                  <div class="clarification-actions">
                    <span
                      v-if="isClarificationAnswered(message)"
                      class="clarification-submitted"
                    >
                      {{ t('lens.chat.runtime.clarificationAnswered') }}
                    </span>
                    <span
                      v-if="clarificationErrorFor(message)"
                      class="clarification-error"
                      role="alert"
                    >
                      {{ clarificationErrorFor(message) }}
                    </span>
                    <button
                      v-if="!isClarificationAnswered(message)"
                      type="button"
                      class="clarification-submit"
                      :disabled="isClarificationSubmitting(message)"
                      @click="submitClarification(message)"
                    >
                      {{
                        isClarificationSubmitting(message)
                          ? t('lens.chat.runtime.clarificationSubmitting')
                          : t('lens.chat.runtime.clarificationSubmit')
                      }}
                    </button>
                  </div>
                </div>

                <div class="message-card" :class="message.role">
                  <div
                    v-if="message.role === 'assistant' && message.content"
                    class="message-markdown"
                  >
                    <MarkdownRenderer :content="message.content" />
                  </div>
                  <template v-else>
                    <div
                      v-if="message.attachments && message.attachments.length"
                      class="message-attachments"
                    >
                      <AuthImage
                        v-for="img in message.attachments.filter(
                          (item) => item.kind !== 'document'
                        )"
                        :key="img.uuid || img.localUrl"
                        :src="img.localUrl || img.url"
                        :alt="img.original_name || 'image'"
                        zoomable
                      />
                      <button
                        v-for="file in message.attachments.filter(
                          (item) => item.kind === 'document'
                        )"
                        :key="file.uuid"
                        type="button"
                        class="message-document-card"
                        :title="file.original_name"
                        @click="downloadAttachmentFile(file)"
                      >
                        <FileText :size="20" aria-hidden="true" />
                        <span>
                          <strong>{{
                            compactFilename(file.original_name)
                          }}</strong>
                          <small>{{ formatBytes(file.byte_size) }}</small>
                        </span>
                        <Download :size="16" aria-hidden="true" />
                      </button>
                    </div>
                    <div v-if="message.content" class="message-text">
                      {{ message.content }}
                    </div>
                  </template>
                  <MessageCitations
                    v-if="
                      message.role === 'assistant' &&
                      message.citations?.length &&
                      !isAnonymous
                    "
                    :citations="message.citations"
                    @open="openCodeCitation(message, $event)"
                  />
                  <div
                    v-if="message.output_files && message.output_files.length"
                    class="message-deliverables"
                  >
                    <div
                      v-for="file in message.output_files"
                      :key="file.uuid"
                      class="deliverable-card"
                    >
                      <button
                        type="button"
                        class="deliverable-open"
                        @click="handleCardClick(file)"
                      >
                        <span class="deliverable-thumb">
                          <FileText :size="20" />
                        </span>
                        <span class="deliverable-meta">
                          <span class="deliverable-name">{{
                            file.filename
                          }}</span>
                          <span class="deliverable-sub">{{
                            fileTypeLabel(file)
                          }}</span>
                        </span>
                      </button>
                      <span class="deliverable-actions">
                        <button
                          v-if="isPreviewable(file)"
                          type="button"
                          class="deliverable-action"
                          :title="t('lens.chat.preview')"
                          :aria-label="t('lens.chat.preview')"
                          @click="openPreview(file)"
                        >
                          <Eye :size="18" />
                        </button>
                        <button
                          type="button"
                          class="deliverable-action"
                          :title="t('lens.chat.download')"
                          :aria-label="t('lens.chat.download')"
                          @click="downloadOutputFile(file)"
                        >
                          <Download :size="18" />
                        </button>
                      </span>
                    </div>
                  </div>
                </div>

                <div class="message-time" :class="message.role">
                  {{ formatTime(getMessageTimestamp(message)) }}
                </div>

                <div
                  v-if="message.role === 'assistant'"
                  class="message-actions"
                >
                  <button
                    type="button"
                    class="icon-btn"
                    :title="t('common.copy')"
                    :aria-label="t('common.copy')"
                    @click="copyMessage(message)"
                  >
                    <svg
                      viewBox="0 0 24 24"
                      fill="none"
                      stroke="currentColor"
                      stroke-width="2"
                      aria-hidden="true"
                    >
                      <rect x="9" y="9" width="13" height="13" rx="2" />
                      <path
                        d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"
                      />
                    </svg>
                  </button>
                  <button
                    v-if="isMobile && canRetryLastQuestion(message)"
                    type="button"
                    class="icon-btn"
                    :title="t('lens.chat.retryAction')"
                    :aria-label="t('lens.chat.retryAction')"
                    @click="retryLastQuestion(message)"
                  >
                    <svg
                      viewBox="0 0 24 24"
                      fill="none"
                      stroke="currentColor"
                      stroke-width="2"
                      aria-hidden="true"
                    >
                      <path
                        d="M3 12a9 9 0 1 0 3-6.7L3 8"
                        stroke-linecap="round"
                        stroke-linejoin="round"
                      />
                      <path
                        d="M3 3v5h5"
                        stroke-linecap="round"
                        stroke-linejoin="round"
                      />
                    </svg>
                  </button>
                  <button
                    v-if="isMobile && !isAnonymous && message.run"
                    type="button"
                    class="icon-btn mobile-share-btn"
                    :class="{ 'icon-btn-shared': isMessageShared(message) }"
                    :title="
                      isMessageShared(message)
                        ? t('lens.qa.sharedButton')
                        : t('lens.qa.shareButton')
                    "
                    :aria-label="
                      isMessageShared(message)
                        ? t('lens.qa.sharedButton')
                        : t('lens.qa.shareButton')
                    "
                    @click="openShare(message)"
                  >
                    <svg
                      viewBox="0 0 24 24"
                      fill="none"
                      stroke="currentColor"
                      stroke-width="2"
                      aria-hidden="true"
                    >
                      <circle cx="18" cy="5" r="3" />
                      <circle cx="6" cy="12" r="3" />
                      <circle cx="18" cy="19" r="3" />
                      <path
                        d="M8.59 13.51l6.83 3.98M15.41 6.51l-6.82 3.98"
                        stroke-linecap="round"
                        stroke-linejoin="round"
                      />
                    </svg>
                  </button>
                  <template v-if="!isMobile">
                    <button
                      v-if="message.content"
                      type="button"
                      class="icon-btn"
                      :title="t('lens.qa.exportPdf')"
                      :aria-label="t('lens.qa.exportPdf')"
                      @click="exportQa(message)"
                    >
                      <Download :size="16" aria-hidden="true" />
                    </button>
                    <button
                      v-if="!isAnonymous && message.run && message.content"
                      type="button"
                      class="icon-btn"
                      :class="{
                        'icon-btn-feedback-positive':
                          message.feedback === 'positive'
                      }"
                      :title="t('lens.chat.feedbackHelpful')"
                      :aria-label="t('lens.chat.feedbackHelpful')"
                      :aria-pressed="message.feedback === 'positive'"
                      :disabled="isFeedbackUpdating(message.run)"
                      @click="setFeedback(message, 'positive')"
                    >
                      <ThumbsUp :size="16" />
                    </button>
                    <button
                      v-if="!isAnonymous && message.run && message.content"
                      type="button"
                      class="icon-btn"
                      :class="{
                        'icon-btn-feedback-negative':
                          message.feedback === 'negative'
                      }"
                      :title="t('lens.chat.feedbackUnhelpful')"
                      :aria-label="t('lens.chat.feedbackUnhelpful')"
                      :aria-pressed="message.feedback === 'negative'"
                      :disabled="isFeedbackUpdating(message.run)"
                      @click="setFeedback(message, 'negative')"
                    >
                      <ThumbsDown :size="16" />
                    </button>
                    <button
                      v-if="!isAnonymous && message.run"
                      type="button"
                      class="icon-btn"
                      :class="{ 'icon-btn-shared': isMessageShared(message) }"
                      :title="
                        isMessageShared(message)
                          ? t('lens.qa.sharedButton')
                          : t('lens.qa.shareButton')
                      "
                      :aria-label="
                        isMessageShared(message)
                          ? t('lens.qa.sharedButton')
                          : t('lens.qa.shareButton')
                      "
                      @click="openShare(message)"
                    >
                      <svg
                        viewBox="0 0 24 24"
                        fill="none"
                        stroke="currentColor"
                        stroke-width="2"
                        aria-hidden="true"
                      >
                        <circle cx="18" cy="5" r="3" />
                        <circle cx="6" cy="12" r="3" />
                        <circle cx="18" cy="19" r="3" />
                        <path
                          d="M8.59 13.51l6.83 3.98M15.41 6.51l-6.82 3.98"
                          stroke-linecap="round"
                          stroke-linejoin="round"
                        />
                      </svg>
                    </button>
                  </template>
                  <button
                    v-if="!isMobile && canRetryLastQuestion(message)"
                    type="button"
                    class="icon-btn"
                    :title="t('lens.chat.retryAction')"
                    :aria-label="t('lens.chat.retryAction')"
                    @click="retryLastQuestion(message)"
                  >
                    <svg
                      viewBox="0 0 24 24"
                      fill="none"
                      stroke="currentColor"
                      stroke-width="2"
                      aria-hidden="true"
                    >
                      <path
                        d="M3 12a9 9 0 1 0 3-6.7L3 8"
                        stroke-linecap="round"
                        stroke-linejoin="round"
                      />
                      <path
                        d="M3 3v5h5"
                        stroke-linecap="round"
                        stroke-linejoin="round"
                      />
                    </svg>
                  </button>
                </div>
              </div>
            </div>

            <!-- Empty-answer hint: a finished turn returned no text -->
            <div v-if="showRetryHint" class="message-row message-row-assistant">
              <div class="message-body">
                <div class="retry-hint">
                  <span class="retry-hint-text">
                    {{ retryHintMessage }}
                  </span>
                  <button
                    v-if="canRetryLastQuestion()"
                    type="button"
                    class="retry-hint-btn"
                    @click="retryLastQuestion()"
                  >
                    {{ t('lens.chat.retryAction') }}
                  </button>
                </div>
              </div>
            </div>

            <!-- Live answer: one row for progress and streaming markdown -->
            <div
              v-if="showLiveAnswer"
              class="message-row message-row-assistant live-progress-row"
            >
              <div class="message-body">
                <details
                  v-if="isRunActive && liveStructuredProgress.items.length"
                  class="runtime-progress-card runtime-progress-live"
                  :open="!isMobile"
                  role="status"
                  aria-live="polite"
                >
                  <summary
                    class="runtime-progress-summary runtime-progress-live-summary"
                  >
                    <span class="runtime-card-title">
                      {{
                        progressTitle(
                          liveStructuredProgress.kind,
                          liveStructuredProgress.hasPlan
                        )
                      }}
                    </span>
                    <span class="runtime-progress-summary-text">
                      {{ liveProgressText }}
                      <span v-if="elapsedText"> · {{ elapsedText }}</span>
                    </span>
                    <span class="runtime-progress-chevron" aria-hidden="true">
                      ⌄
                    </span>
                  </summary>
                  <div class="runtime-progress-content">
                    <div
                      class="runtime-card-title runtime-progress-desktop-title"
                    >
                      {{
                        progressTitle(
                          liveStructuredProgress.kind,
                          liveStructuredProgress.hasPlan
                        )
                      }}
                    </div>
                    <div
                      v-if="liveStructuredProgress.kind === 'workflow'"
                      class="runtime-workflow"
                    >
                      <div
                        v-for="task in liveStructuredProgress.tasks"
                        :key="task.id"
                        class="runtime-workflow-task"
                        :class="{
                          'is-direct': !liveStructuredProgress.hasPlan
                        }"
                      >
                        <div
                          v-if="liveStructuredProgress.hasPlan"
                          class="runtime-plan-step runtime-task-row"
                          :class="{
                            'is-active-ancestor': isActiveProgressAncestor(
                              task,
                              task.stages
                            )
                          }"
                        >
                          <span
                            class="runtime-plan-status"
                            :class="[
                              `is-${livePlanStatus(
                                task,
                                liveStructuredProgress.tasks
                              )}`,
                              {
                                'is-active-ancestor': isActiveProgressAncestor(
                                  task,
                                  task.stages
                                )
                              }
                            ]"
                            aria-hidden="true"
                          >
                            {{
                              progressStatusIcon(
                                livePlanStatus(
                                  task,
                                  liveStructuredProgress.tasks
                                )
                              )
                            }}
                          </span>
                          <span>{{ workflowTaskTitle(task) }}</span>
                        </div>
                        <div
                          v-for="stage in task.stages"
                          :key="stage.id"
                          class="runtime-workflow-stage"
                        >
                          <div
                            class="runtime-plan-step runtime-stage-row"
                            :class="{
                              'is-active-ancestor': isActiveProgressAncestor(
                                stage,
                                stage.steps
                              )
                            }"
                          >
                            <span
                              class="runtime-plan-status"
                              :class="[
                                `is-${stage.status}`,
                                {
                                  'is-active-ancestor':
                                    isActiveProgressAncestor(stage, stage.steps)
                                }
                              ]"
                              aria-hidden="true"
                            >
                              {{ progressStatusIcon(stage.status) }}
                            </span>
                            <span>{{ workflowStageTitle(stage.kind) }}</span>
                          </div>
                          <div
                            :ref="
                              stage.status === 'in_progress'
                                ? 'liveActivityScrollRef'
                                : undefined
                            "
                            class="runtime-workflow-steps"
                          >
                            <div
                              v-for="step in stage.steps"
                              :key="step.id"
                              class="runtime-plan-step runtime-step-row"
                            >
                              <span
                                class="runtime-plan-status"
                                :class="`is-${step.status}`"
                                aria-hidden="true"
                              >
                                {{ progressStatusIcon(step.status) }}
                              </span>
                              <span>{{ workflowStepTitle(step) }}</span>
                            </div>
                          </div>
                        </div>
                      </div>
                    </div>
                    <div
                      v-else-if="liveStructuredProgress.kind === 'activity'"
                      ref="liveActivityScrollRef"
                      class="runtime-node-activities runtime-standalone-activities"
                    >
                      <div
                        v-for="activity in liveStructuredProgress.items"
                        :key="activity.id"
                        class="runtime-node-activity"
                      >
                        <span
                          class="runtime-activity-indicator"
                          :class="{
                            'is-current': isCurrentStandaloneActivity(
                              activity,
                              liveStructuredProgress.items
                            )
                          }"
                          aria-hidden="true"
                        >
                          {{
                            isCurrentStandaloneActivity(
                              activity,
                              liveStructuredProgress.items
                            )
                              ? ''
                              : '✓'
                          }}
                        </span>
                        <span>{{ activityLabel(activity.kind) }}</span>
                        <span
                          v-if="activity.count > 1"
                          class="runtime-activity-count"
                        >
                          ×{{ activity.count }}
                        </span>
                      </div>
                    </div>
                    <div
                      v-else
                      v-for="item in liveStructuredProgress.items"
                      :key="item.id"
                      class="runtime-plan-node"
                    >
                      <div class="runtime-plan-step">
                        <span
                          class="runtime-plan-status"
                          :class="`is-${livePlanStatus(
                            item,
                            liveStructuredProgress.items
                          )}`"
                          aria-hidden="true"
                        >
                          {{
                            progressStatusIcon(
                              livePlanStatus(item, liveStructuredProgress.items)
                            )
                          }}
                        </span>
                        <span class="runtime-step-content">
                          <span>{{ item.title }}</span>
                          <span
                            v-if="item.summary"
                            class="runtime-step-summary"
                          >
                            {{ item.summary }}
                          </span>
                        </span>
                      </div>
                      <div
                        v-if="nodeActivities(runtimeState, item.id).length"
                        :ref="
                          item.status === 'in_progress'
                            ? 'liveActivityScrollRef'
                            : undefined
                        "
                        class="runtime-node-activities"
                      >
                        <div
                          v-for="activity in nodeActivities(
                            runtimeState,
                            item.id
                          )"
                          :key="activity.id"
                          class="runtime-node-activity"
                        >
                          <span
                            class="runtime-activity-indicator"
                            :class="{
                              'is-current': isCurrentActivity(activity, item)
                            }"
                            aria-hidden="true"
                          >
                            {{ isCurrentActivity(activity, item) ? '' : '✓' }}
                          </span>
                          <span>{{ activityLabel(activity.kind) }}</span>
                          <span
                            v-if="activity.count > 1"
                            class="runtime-activity-count"
                          >
                            ×{{ activity.count }}
                          </span>
                        </div>
                      </div>
                    </div>
                    <div class="runtime-progress-footer">
                      <span>{{ liveProgressText }}</span>
                      <span v-if="elapsedText"> · {{ elapsedText }}</span>
                    </div>
                  </div>
                </details>

                <div
                  v-else-if="isRunActive"
                  class="live-status-card"
                  role="status"
                  aria-live="polite"
                >
                  <span class="live-progress-dot" />
                  <span class="live-status-text">
                    {{ liveProgressText }}
                  </span>
                  <span v-if="elapsedText" class="thinking-elapsed">
                    {{ elapsedText }}
                  </span>
                </div>

                <div
                  v-if="runtimeState.capabilityBlock"
                  class="runtime-block-card"
                  role="status"
                >
                  <div class="runtime-card-title">
                    {{ t('lens.chat.runtime.blockedTitle') }}
                  </div>
                  <div>
                    {{ capabilityRecovery(runtimeState.capabilityBlock) }}
                  </div>
                </div>

                <div
                  v-if="runtimeState.executionFailure"
                  class="runtime-block-card"
                  role="status"
                >
                  <div class="runtime-card-title">
                    {{ t('lens.chat.runtime.executionFailedTitle') }}
                  </div>
                  <div>
                    {{ executionFailureRecovery(runtimeState) }}
                  </div>
                </div>

                <div
                  v-if="runtimeState.verificationFailure"
                  class="runtime-outcome-card"
                  role="status"
                >
                  <div class="runtime-card-title">
                    {{ t('lens.chat.runtime.verificationFailedTitle') }}
                  </div>
                  <div>
                    {{ verificationFailureRecovery(runtimeState) }}
                  </div>
                </div>

                <div
                  v-if="runtimeState.artifacts.length"
                  class="runtime-artifact-card"
                  role="status"
                >
                  <div class="runtime-card-title">
                    {{ t('lens.chat.runtime.artifactTitle') }}
                  </div>
                  <div
                    v-for="artifact in runtimeState.artifacts"
                    :key="artifact.filename"
                  >
                    {{ artifact.filename }}
                  </div>
                </div>

                <div
                  v-if="
                    runtimeState.outcome === 'partial' ||
                    (runtimeState.outcome === 'blocked' &&
                      !runtimeState.clarificationRequest &&
                      !runtimeState.capabilityBlock &&
                      !runtimeState.executionFailure &&
                      !runtimeState.verificationFailure)
                  "
                  class="runtime-outcome-card"
                  role="status"
                >
                  {{
                    runtimeState.outcome === 'blocked'
                      ? t('lens.chat.runtime.outcomeBlocked')
                      : t('lens.chat.runtime.outcomePartial')
                  }}
                </div>

                <div
                  v-if="partialAnswer || streamError"
                  class="message-card assistant"
                >
                  <div v-if="streamError" class="live-text">
                    {{ streamError }}
                  </div>
                  <div
                    v-else
                    class="message-markdown live-markdown"
                    :class="{ 'is-streaming': showCursor }"
                  >
                    <MarkdownRenderer :content="partialAnswer" />
                  </div>
                </div>
              </div>
            </div>

            <p
              v-if="canCompose && isMobile"
              class="disclaimer mobile-disclaimer"
            >
              {{
                t('lens.chat.disclaimer') ||
                '回答由 AI 生成，请自行核实关键信息。'
              }}
            </p>
          </div>
        </div>

        <div
          v-if="selectedSessionArchived"
          class="archived-session-notice"
          role="status"
        >
          <Archive :size="18" :stroke-width="2" aria-hidden="true" />
          <span>{{ t('lens.chat.archivedReadOnly') }}</span>
          <button type="button" @click="restoreManagedSession(selectedSession)">
            {{ t('lens.chat.restoreSession') }}
          </button>
        </div>

        <div
          v-else-if="canCompose"
          class="composer-wrap"
          :class="{ 'composer-wrap-empty': isEmptyConversation }"
        >
          <div class="composer-inner">
            <div class="composer-shell">
              <div v-if="attachments.length" class="composer-attachments">
                <div
                  v-for="item in attachments"
                  :key="item.key"
                  class="composer-thumb"
                  :class="{
                    'is-uploading': item.status === 'uploading',
                    'is-document': item.kind === 'document'
                  }"
                >
                  <img
                    v-if="item.kind === 'image'"
                    :src="item.localUrl"
                    :alt="item.name"
                  />
                  <span v-else class="composer-document">
                    <FileText :size="22" aria-hidden="true" />
                    <span>{{ item.name }}</span>
                  </span>
                  <span
                    v-if="item.status === 'uploading'"
                    class="composer-thumb-spinner"
                  />
                  <button
                    type="button"
                    class="composer-thumb-remove"
                    :aria-label="t('lens.chat.removeAttachment')"
                    :title="t('lens.chat.removeAttachment')"
                    @click="removeAttachment(item)"
                  >
                    <span aria-hidden="true">×</span>
                  </button>
                </div>
              </div>
              <div
                v-if="isEmptyConversation"
                class="prompt-suggestions"
                :aria-label="t('lens.chat.suggestions.label')"
              >
                <button
                  v-for="suggestion in promptSuggestions"
                  :key="suggestion"
                  type="button"
                  class="prompt-suggestion"
                  @click="applyPromptSuggestion(suggestion)"
                >
                  {{ suggestion }}
                </button>
              </div>
              <div class="composer">
                <input
                  ref="fileInput"
                  type="file"
                  :accept="attachmentAccept"
                  multiple
                  class="composer-file-input"
                  @change="onFileInputChange"
                />
                <button
                  v-if="acceptsAttachments"
                  class="composer-attach-btn"
                  type="button"
                  :disabled="
                    !selectedSessionUuid ||
                    attachments.length >= MAX_ATTACHMENTS
                  "
                  :aria-label="t('lens.chat.attachFile')"
                  :title="t('lens.chat.attachFile')"
                  @click="triggerFilePick"
                >
                  <svg
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    stroke-width="2.2"
                    aria-hidden="true"
                  >
                    <path d="M12 5v14M5 12h14" stroke-linecap="round" />
                  </svg>
                </button>
                <textarea
                  ref="composerRef"
                  v-model="question"
                  class="composer-input"
                  rows="1"
                  :placeholder="t('lens.chat.questionPlaceholder')"
                  @keydown.enter.exact.prevent="insertNewline"
                  @keydown.ctrl.enter.exact.prevent="handlePrimaryAction"
                  @paste="onComposerPaste"
                  @input="autoResizeTextarea"
                />
                <button
                  class="composer-action-btn"
                  :class="isRunActive ? 'composer-action-btn-stop' : ''"
                  type="button"
                  :disabled="!canSubmit && !isRunActive"
                  :aria-label="
                    isRunActive ? t('common.stop') : t('common.submit')
                  "
                  @click="handlePrimaryAction"
                >
                  <svg
                    v-if="!isRunActive"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    stroke-width="2.5"
                    aria-hidden="true"
                  >
                    <path
                      d="M12 19V5M5 12l7-7 7 7"
                      stroke-linecap="round"
                      stroke-linejoin="round"
                    />
                  </svg>
                  <svg
                    v-else
                    viewBox="0 0 24 24"
                    fill="currentColor"
                    aria-hidden="true"
                  >
                    <rect x="5" y="5" width="14" height="14" rx="2.5" />
                  </svg>
                </button>
              </div>
            </div>

            <p v-if="!isMobile" class="disclaimer">
              {{
                t('lens.chat.disclaimer') ||
                '回答由 AI 生成，请自行核实关键信息。'
              }}
            </p>
          </div>
        </div>
      </template>
    </main>

    <LoginModal
      :show="showLoginModal"
      @close="showLoginModal = false"
      @success="onLoginSuccess"
    />

    <QaShareModal
      :open="shareOpen"
      :run-uuid="shareRunUuid"
      :existing-share="shareExisting"
      :assistant-name="assistantName"
      :question="shareQuestion"
      :answer-preview="shareAnswer"
      @close="shareOpen = false"
      @shared="handleShareUpdated"
      @unshared="handleShareRemoved"
    />

    <BaseModal
      :show="Boolean(deleteSessionTarget)"
      :title="t('lens.chat.deleteSessionTitle')"
      :close-on-backdrop="!deletingSession"
      @close="closeDeleteSession"
    >
      <p class="text-sm text-ink-600">
        {{
          t('lens.chat.deleteSessionConfirm', {
            title: deleteSessionTarget?.title || t('lens.chat.untitledSession')
          })
        }}
      </p>
      <template #footer>
        <div class="flex w-full justify-end gap-2">
          <BaseButton
            variant="outline"
            :disabled="deletingSession"
            @click="closeDeleteSession"
          >
            {{ t('common.cancel') }}
          </BaseButton>
          <BaseButton
            variant="danger"
            :loading="deletingSession"
            @click="doDeleteSession"
          >
            {{ t('common.delete') }}
          </BaseButton>
        </div>
      </template>
    </BaseModal>

    <FilePreviewModal
      :file="previewFile"
      @close="closePreview"
      @download="downloadOutputFile"
    />

    <CodeCitationDrawer
      :show="citationDrawerOpen"
      :citation="citationSource"
      :loading="citationSourceLoading"
      :error="citationSourceError"
      @close="closeCodeCitation"
      @retry="loadCodeCitation"
    />
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch, nextTick } from 'vue'
import {
  Archive,
  ArchiveRestore,
  ArrowLeft,
  MessagesSquare,
  PanelLeftClose,
  PanelLeftOpen,
  Plus,
  Download,
  Eye,
  FileText,
  LoaderCircle,
  Pencil,
  Pin,
  PinOff,
  Share2,
  ThumbsDown,
  ThumbsUp,
  Trash2
} from '@lucide/vue'
import { useI18n } from 'vue-i18n'
import { useRoute, useRouter } from 'vue-router'

import MarkdownRenderer from '@/components/ui/MarkdownRenderer.vue'
import AuthImage from '@/components/ui/AuthImage.vue'
import BaseLoading from '@/components/ui/BaseLoading.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseModal from '@/components/ui/BaseModal.vue'
import RowActionMenu from '@/components/ui/RowActionMenu.vue'
import BrandLogo from '@/components/layout/BrandLogo.vue'
import AssistantSwitcher from '@/components/lens/AssistantSwitcher.vue'
import UserDock from '@/components/lens/UserDock.vue'
import MySharesPanel from '@/components/lens/MySharesPanel.vue'
import AssistantEmptyState from '@/components/lens/AssistantEmptyState.vue'
import LoginModal from '@/components/auth/LoginModal.vue'
import QaShareModal from '@/components/lens/QaShareModal.vue'
import FilePreviewModal from '@/components/lens/FilePreviewModal.vue'
import CodeCitationDrawer from '@/pages/lens/components/CodeCitationDrawer.vue'
import MessageCitations from '@/pages/lens/components/MessageCitations.vue'
import {
  extensionOf,
  fetchDeliverableBlob,
  isPreviewable
} from '@/utils/filePreview'
import { downloadQaPdf } from '@/utils/qaPdf'
import { lensNodeErrorMessage } from '@/utils/lensNodeErrors'
import { qaShareUrl } from '@/utils/lens'
import { shareWithNative, supportsNativeShare } from '@/utils/nativeShare'
import { useToast } from '@/composables/useToast'
import { useSessionActivity } from '@/composables/useSessionActivity'
import { useIsMobile } from '@/composables/useIsMobile'
import apiConfig from '@/config/api'
import { useLensStore } from '@/store/lens'
import { usePreferencesStore } from '@/store/preferences'
import { useUserStore } from '@/store/user'
import {
  answerSummary,
  clearUnreadSession,
  handleTerminalRun,
  readUnreadSessions,
  shouldReviewUnreadSession,
  UNREAD_STORAGE_KEY
} from '@/utils/answerCompletionNotifications'
import { startRunCompletionTracking } from '@/utils/runCompletionTracking'
import {
  precedingUserMessage,
  retryRunUuid,
  retryableUserMessage
} from '@/pages/lens/chatMessageContext'
import { prepareRunSubmission } from '@/pages/lens/chatSubmission'
import { promptSuggestionKeys } from '@/pages/lens/chatPromptSuggestions'
import { resolveChatViewport } from '@/pages/lens/chatViewport'
import { compactFilename } from '@/pages/lens/filenameDisplay'
import {
  DOCUMENT_EXTENSIONS,
  IMAGE_MIME,
  attachmentUploadError,
  hasAttachmentErrorCode,
  MAX_ATTACHMENTS,
  readImageDimensions,
  validateImageDimensions,
  validateAttachment
} from '@/pages/lens/chatAttachments'
import {
  resolveRunStatus,
  shouldShowRetryHint
} from '@/pages/lens/chatRetryHint'
import {
  activitiesForNode,
  applyRuntimeEvent,
  calculateRunElapsedSeconds,
  createConversationAutoScroller,
  createRuntimeState,
  formatActivityProgressText,
  formatDocumentProgressText,
  formatDuration,
  getMessageTimestamp,
  isActiveProgressAncestor,
  planStepDisplayStatus,
  scrollConversationToBottomAfterRender,
  selectLiveProgressText,
  selectStructuredProgress,
  summarizePlanProgress,
  summarizeStageProgress,
  terminalSyncEvent,
  workflowProgressSource
} from '@/pages/lens/runtimeEvents'
import {
  archiveSession,
  answerRunClarification,
  cancelRun,
  createRun,
  createSession,
  deleteAttachment,
  deleteSession,
  getPublicAssistant,
  getRun,
  getRunCitationSource,
  getRunPdf,
  listMyShares,
  listAssistants,
  listMessages,
  listSessions,
  pinSession,
  restoreSession,
  unpinSession,
  updateSession,
  updateRunFeedback,
  uploadAttachment
} from '@/api/lens'

const route = useRoute()
const router = useRouter()
const { t } = useI18n()
const { showError, showSuccess, showWarning } = useToast()
const userStore = useUserStore()
const lensStore = useLensStore()
const preferencesStore = usePreferencesStore()
const sessionActivity = useSessionActivity()

const assistants = ref([])
const sessions = ref([])
const messages = ref([])
const feedbackUpdatingRuns = ref(new Set())
const selectedAssistantUuid = ref('')
const selectedSessionUuid = ref('')
const question = ref('')
const attachments = ref([])
const fileInput = ref(null)
const partialAnswer = ref('')
const citationDrawerOpen = ref(false)
const activeCitation = ref(null)
const citationSource = ref(null)
const citationSourceLoading = ref(false)
const citationSourceError = ref('')
let citationRequestId = 0

const RUN_POLL_INTERVAL_MS = 3000
const RUN_POLL_MAX_ATTEMPTS = 160
const TITLE_POLL_INTERVAL_MS = 2000
const TITLE_POLL_MAX_ATTEMPTS = 15
const streamError = ref('')
const failedRunError = ref(null)
const queuePosition = ref(null)
const currentRun = ref(null)
const pendingRunSubmission = ref(null)
const retryDraft = ref(null)
const runStatusResolvingSessionUuid = ref('')
const loading = ref({ run: false })
const streamController = ref(null)
const sidebarOpen = ref(false)
const sidebarCollapsed = ref(false)
const showArchivedSessions = ref(false)
const deleteSessionTarget = ref(null)
const deletingSession = ref(false)
const renamingSessionUuid = ref('')
const renameDraft = ref('')
const composerRef = ref(null)
const scrollRef = ref(null)
const mobileViewportStyle = ref({})
const visualViewportConstrained = ref(false)
const seenStepEventCounts = new Map()
let sessionLoadGeneration = 0
const runtimeState = ref(createRuntimeState())
const clarificationAnswers = ref({})
const clarificationSubmitting = ref(new Set())
const clarificationErrors = ref({})
const liveActivityScrollRef = ref(null)
const elapsedSeconds = ref(0)
let elapsedTimer = null
let reviewingUnreadSession = false
const answerAutoScroller = createConversationAutoScroller({
  getElement: () => scrollRef.value,
  waitForRender: nextTick,
  schedule: (callback) => window.setTimeout(callback, 100),
  cancel: (timerId) => window.clearTimeout(timerId)
})

const publicAssistant = ref(null)
const showLoginModal = ref(false)
const shareOpen = ref(false)
const shareRunUuid = ref('')
const shareAnswer = ref('')
const shareQuestion = ref('')
const shareExisting = ref(null)
const sharesByRun = ref({})
const mySharesOpen = ref(false)
// False until the current bootstrap settles, so the view can distinguish
// "still loading" from "loaded, but no assistant to show".
const booted = ref(false)

const selectedAssistant = computed(
  () =>
    assistants.value.find(
      (item) => item.uuid === selectedAssistantUuid.value
    ) || null
)

const selectedSession = computed(
  () =>
    sessions.value.find((item) => item.uuid === selectedSessionUuid.value) ||
    null
)

const selectedSessionArchived = computed(
  () => selectedSession.value?.status === 'archived'
)

const isAnonymous = computed(() => !userStore.isAuthenticated)

// Image upload requires both a multimodal-capable assistant and login (the
// upload endpoint is authenticated), so anonymous visitors never see it.
const acceptsImages = computed(
  () =>
    canCompose.value &&
    !isAnonymous.value &&
    selectedAssistant.value?.can_process_images === true
)

const acceptsDocuments = computed(
  () =>
    canCompose.value &&
    !isAnonymous.value &&
    !!selectedAssistant.value &&
    selectedAssistant.value.selected_task !== 'general_chat' &&
    selectedAssistant.value.supports_document_attachments === true
)

const acceptsAttachments = computed(
  () => acceptsImages.value || acceptsDocuments.value
)

const attachmentAccept = computed(() => {
  const values = []
  if (acceptsImages.value) values.push(...IMAGE_MIME)
  if (acceptsDocuments.value) values.push(...DOCUMENT_EXTENSIONS)
  return values.join(',')
})

const hasUploadingAttachment = computed(() =>
  attachments.value.some((item) => item.status === 'uploading')
)

const canSubmit = computed(() => {
  if (!canCompose.value) {
    return false
  }
  if (loading.value.run) {
    return false
  }
  if (hasUploadingAttachment.value) {
    return false
  }
  const hasReadyAttachment = attachments.value.some(
    (item) => item.status === 'done'
  )
  return !!question.value.trim() || hasReadyAttachment
})

const hasAssistant = computed(() =>
  isAnonymous.value ? !!publicAssistant.value : !!selectedAssistantUuid.value
)

const canCompose = computed(
  () =>
    hasAssistant.value &&
    (isAnonymous.value ||
      selectedSession.value?.status === 'active' ||
      (!selectedSessionUuid.value && !showArchivedSessions.value))
)

const emptyVariant = computed(() =>
  userStore.userHasFeature('admin_console') ? 'admin' : 'visitor'
)

const assistantName = computed(
  () => selectedAssistant.value?.name || publicAssistant.value?.name || ''
)

const assistantDescription = computed(
  () =>
    selectedAssistant.value?.description?.trim() ||
    publicAssistant.value?.description?.trim() ||
    ''
)

const promptSuggestions = computed(() => {
  const task = (selectedAssistant.value || publicAssistant.value)?.selected_task
  return promptSuggestionKeys(task).map((key) => t(key))
})

const isGeneralChatAssistant = computed(
  () =>
    (selectedAssistant.value || publicAssistant.value)?.selected_task ===
    'general_chat'
)

// The top header turns the assistant name into a switcher only when an
// authenticated user has more than one assistant to choose from. Mirror the
// switcher's own visibility rule (active assistants only) so the header never
// renders an empty switcher in place of the name.
const switchable = computed(
  () =>
    !isAnonymous.value &&
    assistants.value.filter((item) => item.status === 'active').length > 1
)

// Slug of the assistant in view — drives the public Q&A list entry in the
// header for both authenticated and anonymous visitors.
const assistantSlug = computed(() => route.params.slug || '')

const sidebarCollapsedActive = computed(
  () => sidebarCollapsed.value && !isMobile.value
)

function handleSidebarLogoClick() {
  if (sidebarCollapsedActive.value) {
    sidebarCollapsed.value = false
    return
  }
  if (isMobile.value) {
    sidebarOpen.value = false
  }
  router.push('/dashboard')
}

const isRunActive = computed(() =>
  ['queued', 'running', 'streaming'].includes(currentRun.value?.status)
)

const showLiveAnswer = computed(
  () => isRunActive.value || partialAnswer.value || streamError.value
)

const showCursor = computed(
  () => isRunActive.value && !streamError.value && partialAnswer.value
)

const liveStatusText = computed(() => {
  if (currentRun.value?.resume_by) {
    return t('lens.chat.awaitingResume')
  }
  if (currentRun.value?.status === 'streaming') {
    return t('lens.chat.generating')
  }
  if (currentRun.value?.status === 'running') {
    return t('lens.chat.running')
  }
  if (currentRun.value?.status === 'queued') {
    if (queuePosition.value === null) return t('lens.chat.queued')
    if (queuePosition.value === 0) return t('lens.chat.queuedNext')
    return t('lens.chat.queuedPosition', { position: queuePosition.value })
  }
  return t('lens.chat.waiting')
})

const runtimePhaseText = computed(() => {
  const phase = runtimeState.value.phase
  if (!phase) return null
  const known = new Set([
    'analyzing',
    'planning',
    'executing',
    'answering',
    'completed'
  ])
  return known.has(phase) ? t(`lens.chat.runtime.phase.${phase}`) : null
})

const elapsedText = computed(() => {
  if (elapsedSeconds.value === 0) return null
  return formatDuration(elapsedSeconds.value)
})

function planProgressText(plan, durationSeconds = null, terminal = false) {
  const progress = summarizePlanProgress(plan, { terminal })
  if (!progress) return ''
  let text
  if (progress.isComplete) {
    text = t('lens.chat.runtime.planCompleted', progress)
  } else if (progress.isTerminal) {
    text = t('lens.chat.runtime.planEnded', progress)
  } else if (progress.currentTitle) {
    text = t('lens.chat.runtime.planProgressCurrent', progress)
  } else {
    text = t('lens.chat.runtime.planProgress', progress)
  }
  if (durationSeconds != null) {
    text += ` · ${formatDuration(durationSeconds)}`
  }
  return text
}

function stageProgressText(stages, durationSeconds = null, terminal = false) {
  const progress = summarizeStageProgress(stages, { terminal })
  if (!progress) return ''
  let text
  if (progress.isComplete) {
    text = t('lens.chat.runtime.stageCompleted', progress)
  } else if (progress.isTerminal) {
    text = t('lens.chat.runtime.stageEnded', progress)
  } else if (progress.currentTitle) {
    text = t('lens.chat.runtime.stageProgressCurrent', progress)
  } else {
    text = t('lens.chat.runtime.stageProgress', progress)
  }
  if (durationSeconds != null) {
    text += ` · ${formatDuration(durationSeconds)}`
  }
  return text
}

function structuredProgress(state) {
  return selectStructuredProgress({
    route: state?.route,
    plan: state?.plan,
    stages: state?.stages,
    activities: state?.activities,
    standaloneActivities: !isGeneralChatAssistant.value
  })
}

function structuredProgressText(
  state,
  durationSeconds = null,
  terminal = false
) {
  const progress = structuredProgress(state)
  if (progress.kind === 'plan') {
    return planProgressText(progress.items, durationSeconds, terminal)
  }
  if (progress.kind === 'stage') {
    return stageProgressText(progress.items, durationSeconds, terminal)
  }
  if (progress.kind === 'workflow') {
    const source = workflowProgressSource(progress.tasks, progress.hasPlan)
    if (source.kind === 'plan') {
      return planProgressText(source.items, durationSeconds, terminal)
    }
    return stageProgressText(source.items, durationSeconds, terminal)
  }
  if (progress.kind === 'activity') {
    return formatActivityProgressText(progress.items, {
      durationSeconds,
      terminal,
      translate: t
    })
  }
  return ''
}

function progressTitle(kind, hasPlan = false) {
  if (kind === 'plan') return t('lens.chat.runtime.planTitle')
  if (kind === 'activity') return t('lens.chat.agentActivity')
  if (kind === 'workflow') {
    return t(
      hasPlan ? 'lens.chat.runtime.planTitle' : 'lens.chat.runtime.stageTitle'
    )
  }
  return t('lens.chat.runtime.stageTitle')
}

const PATH_KINDS = new Set([
  'query_orders',
  'get_order_detail',
  'reading_order_commands',
  'checking_capability',
  'checking_tool',
  'checking_authentication',
  'authenticating',
  'querying_data',
  'count_results',
  'group_results',
  'analyzing_results',
  'summarizing_results'
])
function safePathKind(item) {
  let kind = PATH_KINDS.has(item?.kind) ? item.kind : 'querying_data'
  if (
    kind === 'query_orders' &&
    (!item.startDate || !item.endDate) &&
    !item.orderRef
  ) {
    kind = 'querying_data'
  }
  return kind
}

function workflowTaskTitle(task) {
  if (task.title) return task.title
  if (task.kind === 'get_order_detail') {
    return t(
      `lens.chat.runtime.workflow.task.${
        task.orderRef ? 'get_order_detail_ref' : 'get_order_detail'
      }`,
      { orderRef: task.orderRef }
    )
  }
  if (task.kind === 'query_orders') {
    return t(
      `lens.chat.runtime.workflow.task.${
        task.orderRef ? 'query_orders_ref' : 'query_orders'
      }`,
      { orderRef: task.orderRef }
    )
  }
  if (task.kind === 'analyze_results') {
    return t('lens.chat.runtime.workflow.task.analyze_results')
  }
  return t('lens.chat.runtime.workflow.task.query_data')
}

function workflowStageTitle(kind) {
  const known = new Set([
    'preparation',
    'order_query',
    'data_query',
    'result_analysis'
  ])
  const safeKind = known.has(kind) ? kind : 'data_query'
  return t(`lens.chat.runtime.workflow.stage.${safeKind}`)
}

function workflowStepTitle(item) {
  let kind = safePathKind(item)
  if (kind === 'get_order_detail' && item?.orderRef) {
    kind = 'get_order_detail_ref'
  } else if (kind === 'query_orders' && item?.orderRef) {
    kind = 'query_orders_ref'
  } else if (kind === 'summarizing_results' && item?.orderRef) {
    kind = 'summarizing_order'
  }
  return t(`lens.chat.runtime.pathStep.${kind}`, {
    startDate: item?.startDate,
    endDate: item?.endDate,
    orderRef: item?.orderRef
  })
}

function progressStatusIcon(status) {
  return {
    completed: '✓',
    in_progress: '●',
    pending: '○',
    failed: '×',
    skipped: '–'
  }[status]
}

const liveStructuredProgress = computed(() =>
  structuredProgress(runtimeState.value)
)

function livePlanStatus(item, items) {
  return planStepDisplayStatus(item, items, { active: isRunActive.value })
}

function nodeActivities(state, nodeId) {
  return activitiesForNode(state, nodeId)
}

function activityLabel(kind) {
  const known = new Set([
    'analyzingResults',
    'findingCapability',
    'preparingOutput',
    'queryingData',
    'readingContext',
    'readingSources',
    'searchingSources',
    'usingCapability'
  ])
  const safeKind = known.has(kind) ? kind : 'usingCapability'
  return t(`lens.chat.runtime.activity.${safeKind}`)
}

function isCurrentActivity(activity, item) {
  const latest = runtimeState.value.activities.at(-1)
  const items = liveStructuredProgress.value.items
  return (
    livePlanStatus(item, items) === 'in_progress' && latest?.id === activity.id
  )
}

function isCurrentStandaloneActivity(activity, items) {
  return isRunActive.value && items.at(-1)?.id === activity.id
}

watch(
  () => runtimeState.value.activities.length,
  async () => {
    await nextTick()
    const refs = liveActivityScrollRef.value
    const target = Array.isArray(refs) ? refs.at(-1) : refs
    if (target) target.scrollTop = target.scrollHeight
  }
)

const livePlanProgress = computed(() =>
  summarizePlanProgress(runtimeState.value.plan)
)

const livePlanProgressText = computed(() =>
  planProgressText(runtimeState.value.plan)
)

const liveFinalAnswerProgressText = computed(() => {
  const progress = livePlanProgress.value
  if (
    !isRunActive.value ||
    !progress?.isComplete ||
    runtimeState.value.phase !== 'answering'
  ) {
    return ''
  }
  return t('lens.chat.runtime.planCompletedAnswering', progress)
})

const liveDocumentProgressText = computed(() =>
  formatDocumentProgressText(runtimeState.value.documentProgress, t)
)

const liveStageProgressText = computed(() =>
  stageProgressText(runtimeState.value.stages)
)

const liveStructuredProgressText = computed(() =>
  ['workflow', 'activity'].includes(liveStructuredProgress.value.kind)
    ? structuredProgressText(runtimeState.value)
    : ''
)

const liveProgressText = computed(() => {
  if (currentRun.value?.resume_by) {
    // The node owning this run is disconnected; its progress is frozen
    // (and the run may resume on a reconnect), so surface that state
    // instead of the stale plan/stage text.
    return t('lens.chat.awaitingResume')
  }
  return selectLiveProgressText({
    finalAnswerProgressText: liveFinalAnswerProgressText.value,
    documentProgressText: liveDocumentProgressText.value,
    structuredProgressText: liveStructuredProgressText.value,
    planProgressText: livePlanProgressText.value,
    stageProgressText: runtimeState.value.route
      ? ''
      : liveStageProgressText.value,
    phaseText: runtimePhaseText.value,
    fallbackText: liveStatusText.value
  })
})

function runtimeStateFor(thinking) {
  let state = createRuntimeState()
  for (const event of thinking?.steps || []) {
    state = applyRuntimeEvent(state, event)
  }
  return applyRuntimeEvent(state, {
    type:
      thinking?.status === 'awaiting_user_input'
        ? 'awaiting_user_input'
        : 'done',
    status: thinking?.status,
    outcome: thinking?.outcome,
    clarification_answered_at: thinking?.clarification_answered_at,
    termination_detail: thinking?.termination_detail
  })
}

function clarificationRequestFor(message) {
  const request = message?._runtimeState?.clarificationRequest
  return request?.answer_type === 'text' ? request : null
}

function clarificationAnswerFor(message) {
  return clarificationAnswers.value[message?.run] || ''
}

function setClarificationAnswer(message, event) {
  clarificationAnswers.value = {
    ...clarificationAnswers.value,
    [message.run]: event.target.value
  }
  const errors = { ...clarificationErrors.value }
  delete errors[message.run]
  clarificationErrors.value = errors
}

function isClarificationAnswered(message) {
  return Boolean(message?._runtimeState?.clarificationAnsweredAt)
}

function isClarificationSubmitting(message) {
  return clarificationSubmitting.value.has(message?.run)
}

function clarificationErrorFor(message) {
  return clarificationErrors.value[message?.run] || ''
}

function capabilityRecovery(block) {
  const known = new Set([
    'capability',
    'configuration',
    'policy',
    'transient',
    'request',
    'tool',
    'verification'
  ])
  const errorType = known.has(block?.error_type) ? block.error_type : 'tool'
  return t(`lens.chat.runtime.recovery.${errorType}`)
}

function executionFailureRecovery(state) {
  return capabilityRecovery(state?.executionFailure)
}

function verificationFailureRecovery(state) {
  return capabilityRecovery(state?.verificationFailure)
}

const decoratedMessages = computed(() =>
  messages.value
    .filter(
      (m) =>
        !(
          m.role === 'assistant' &&
          !(m.content || '').trim() &&
          !m.thinking?.steps?.length
        )
    )
    .map((message) => {
      if (message.role === 'assistant' && message.thinking) {
        const runtime = runtimeStateFor(message.thinking)
        return { ...message, _runtimeState: runtime }
      }
      return message
    })
)

const isEmptyConversation = computed(
  () =>
    isMobile.value && !decoratedMessages.value.length && !showLiveAnswer.value
)

function applyPromptSuggestion(suggestion) {
  question.value = suggestion
  nextTick(() => {
    autoResizeTextarea()
    composerRef.value?.focus()
  })
}

// A finished turn that produced no answer text — show a transient,
// retry-oriented hint (framed as a temporary hiccup, not a product fault)
// instead of an empty bubble.
const showRetryHint = computed(() => {
  return shouldShowRetryHint({
    isRunActive: isRunActive.value,
    messages: messages.value,
    runStatusResolving:
      runStatusResolvingSessionUuid.value === selectedSessionUuid.value
  })
})

// Map a backend run error code to a clear, blame-clarifying message: a
// timeout is the model being slow (after retries), not a platform fault.
function mapRunError(code) {
  const c = String(code || '').toUpperCase()
  const lensNodeMessage = lensNodeErrorMessage(c, t)
  if (lensNodeMessage) return lensNodeMessage
  if (c.includes('IMAGE_PREPROCESSING')) {
    return t('lens.chat.errorImagePreprocessing')
  }
  if (c.includes('VISION_MODEL_CONFIGURATION_INVALID')) {
    return t('lens.chat.errorVisionModelConfiguration')
  }
  if (c.includes('MODEL_NOT_VISION_CAPABLE')) {
    return t('lens.chat.errorModelNotVisionCapable')
  }
  if (c.includes('VISION_MODEL_NOT_CONFIGURED')) {
    return t('lens.chat.errorVisionModelNotConfigured')
  }
  if (c.includes('VISION_PROVIDER_QUOTA_EXCEEDED')) {
    return t('lens.chat.errorVisionProviderQuotaExceeded')
  }
  if (c.includes('VISION_PROVIDER_UNAVAILABLE')) {
    return t('lens.chat.errorVisionProviderUnavailable')
  }
  if (c.includes('TIMEOUT')) {
    return t('lens.chat.errorModelTimeout')
  }
  if (c.includes('DISCONNECT') || c.includes('ORPHAN')) {
    return t('lens.chat.errorNodeLost')
  }
  if (c.includes('GENERAL_CHAT_SKILL_REQUIRED')) {
    return t('lens.chat.errorSkillRequired')
  }
  return t('lens.chat.emptyAnswerHint')
}

function isTerminalRunStatus(status) {
  return ['awaiting_user_input', 'done', 'failed', 'cancelled'].includes(status)
}

const retryHintMessage = computed(() =>
  failedRunError.value
    ? mapRunError(failedRunError.value)
    : t('lens.chat.emptyAnswerHint')
)

watch(isRunActive, (active) => {
  if (active) {
    const updateElapsedSeconds = () => {
      elapsedSeconds.value = calculateRunElapsedSeconds(currentRun.value)
    }
    updateElapsedSeconds()
    elapsedTimer = setInterval(() => {
      updateElapsedSeconds()
    }, 1000)
  } else {
    clearInterval(elapsedTimer)
    elapsedTimer = null
  }
})

const { isMobile } = useIsMobile()

function authHeaders() {
  const token = localStorage.getItem('access_token')
  return token ? { Authorization: `Bearer ${token}` } : {}
}

function resetStreamState() {
  streamController.value?.abort()
  partialAnswer.value = ''
  streamError.value = ''
  failedRunError.value = null
  queuePosition.value = null
  runtimeState.value = createRuntimeState()
  elapsedSeconds.value = 0
  seenStepEventCounts.clear()
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

function refreshUnreadSessions() {
  sessionActivity.setUnreadSessions(readUnreadSessions(window.localStorage))
}

function sessionHasUnreadAnswer(sessionUuid) {
  return (
    preferencesStore.answerCompletionIndicator &&
    selectedSessionUuid.value !== sessionUuid &&
    Boolean(sessionActivity.state.unreadSessions[sessionUuid])
  )
}

function handleCompletionStorage(event) {
  if (event.key === UNREAD_STORAGE_KEY) {
    refreshUnreadSessions()
  }
  if (event.key === 'answerCompletionIndicator') {
    preferencesStore.answerCompletionIndicator = event.newValue !== 'false'
    refreshUnreadSessions()
  }
  if (event.key === 'nativeBrowserNotifications') {
    preferencesStore.nativeBrowserNotifications = event.newValue === 'true'
  }
}

function handleCompletionVisibility() {
  const sessionUuid = selectedSessionUuid.value
  if (
    reviewingUnreadSession ||
    !shouldReviewUnreadSession({
      documentRef: document,
      selectedSessionUuid: sessionUuid,
      unreadSessions: sessionActivity.state.unreadSessions
    })
  ) {
    return
  }
  reviewingUnreadSession = true
  void selectSession({ uuid: sessionUuid }, false).finally(() => {
    reviewingUnreadSession = false
  })
}

function startCompletionTracking(run, sessionUuid) {
  const session = sessions.value.find((item) => item.uuid === sessionUuid)
  const assistantSlug =
    session?.assistant_slug || selectedAssistant.value?.slug || ''
  const navigationTarget = {
    name: 'LensAssistantChat',
    params: { slug: assistantSlug },
    query: { session: sessionUuid }
  }
  startRunCompletionTracking({
    getRun,
    maxAttempts: RUN_POLL_MAX_ATTEMPTS,
    run,
    sessionUuid,
    sleep: () => sleep(RUN_POLL_INTERVAL_MS),
    onTerminal: async (terminalRun) => {
      if (terminalRun.status === 'done') {
        void refreshSessionTitleUntilSettled(sessionUuid)
      }
      const visibleSessionUuid =
        route.name === 'LensAssistantChat' &&
        route.params.slug === assistantSlug
          ? selectedSessionUuid.value
          : ''
      const result = handleTerminalRun({
        documentRef: document,
        indicatorEnabled: preferencesStore.answerCompletionIndicator,
        nativeNotification: {
          body: t('settings.modal.nativeAnswerCompletedBody'),
          enabled: preferencesStore.nativeBrowserNotifications,
          NotificationRef: window.Notification,
          onOpenConversation: () => router.push(navigationTarget),
          title: t('settings.modal.nativeAnswerCompletedTitle'),
          windowRef: window
        },
        run: terminalRun,
        selectedSessionUuid: visibleSessionUuid,
        sessionUuid,
        storage: window.localStorage
      })
      if (result.unreadChanged) {
        refreshUnreadSessions()
      }
      if (!result.inAppNotificationRequested) {
        return
      }
      let summary = ''
      try {
        const completedMessages = await listMessages(sessionUuid)
        const answer = [...completedMessages].reverse().find((message) => {
          return (
            message.role === 'assistant' && message.run === terminalRun.uuid
          )
        })
        summary = answerSummary(answer?.content)
      } catch {
        summary = ''
      }
      if (
        document.visibilityState !== 'visible' ||
        document.hasFocus() === false
      ) {
        return
      }
      sessionActivity.notify({
        duration: 5000,
        message: summary || t('lens.chat.answerReady'),
        title: session?.title?.trim() || t('lens.chat.untitledSession'),
        to: navigationTarget,
        type: 'success'
      })
    }
  })
}

async function waitForRunTerminal(runUuid) {
  let run = await getRun(runUuid)
  for (let attempt = 0; attempt < RUN_POLL_MAX_ATTEMPTS; attempt += 1) {
    if (isTerminalRunStatus(run?.status)) {
      return run
    }
    await sleep(RUN_POLL_INTERVAL_MS)
    run = await getRun(runUuid)
  }
  return run
}

async function finishSubmittedRun(runUuid, sessionUuid) {
  currentRun.value = await getRun(runUuid)
  messages.value = await listMessages(sessionUuid)

  if (currentRun.value?.status === 'failed') {
    const errorCode = currentRun.value.error || 'RUN_FAILED'
    resetStreamState()
    failedRunError.value = errorCode
    showError(mapRunError(errorCode))
  } else {
    await nextTick()
    resetStreamState()
  }
  answerAutoScroller.request()
}

function pushAgentActivity(item) {
  runtimeState.value = applyRuntimeEvent(runtimeState.value, item)
}

function appendAnswerDelta(content) {
  partialAnswer.value += content
  answerAutoScroller.request()
}

function handleThreadScroll() {
  answerAutoScroller.handleScroll()
}

function scrollToBottom() {
  const el = scrollRef.value
  if (!el) return
  el.scrollTop = el.scrollHeight
  answerAutoScroller.handleScroll()
}

function syncMobileViewport() {
  const viewport = window.visualViewport
  const resolved = resolveChatViewport({
    layoutHeight: window.innerHeight,
    viewportHeight: viewport?.height,
    viewportOffsetTop: viewport?.offsetTop,
    viewportScale: viewport?.scale
  })
  visualViewportConstrained.value = resolved.constrained
  if (!viewport) {
    mobileViewportStyle.value = {}
    return
  }
  mobileViewportStyle.value = {
    '--chat-viewport-height': `${resolved.height}px`,
    '--chat-viewport-offset-top': `${resolved.offsetTop}px`
  }
}

async function bootstrap() {
  // Reset transient chat state up front so the previous assistant's draft,
  // active-run/stream state, or messages cannot leak across an assistant
  // switch (this runs on every slug change, before the async session load
  // below). selectedSessionUuid is intentionally NOT cleared here: if the
  // session load below throws, an empty uuid would leave the composer
  // permanently disabled. A stale submit during the brief load window is
  // instead guarded inside submit() by binding to the session it started in.
  question.value = ''
  currentRun.value = null
  runStatusResolvingSessionUuid.value = ''
  messages.value = []
  clarificationAnswers.value = {}
  clarificationSubmitting.value = new Set()
  clarificationErrors.value = {}
  mySharesOpen.value = false
  showArchivedSessions.value = false
  resetStreamState()
  booted.value = false

  // Anonymous visitors can browse the shared chat page and see the
  // assistant name, but only authenticated users load private sessions.
  if (isAnonymous.value) {
    try {
      publicAssistant.value = await getPublicAssistant(route.params.slug)
    } catch {
      publicAssistant.value = null
      showError(t('lens.chat.assistantNotFound'))
    }
    booted.value = true
    return
  }

  try {
    assistants.value = await listAssistants()
    // Share the loaded list with the store so the header AssistantSwitcher
    // renders immediately (no flash) and skips its own redundant fetch.
    lensStore.assistants = assistants.value

    const current =
      assistants.value.find((item) => item.slug === route.params.slug) ||
      assistants.value[0]

    if (!current) {
      // No assistants exist yet — surface the create-first-assistant guide
      // (admin) or a no-assistant notice (end-user) instead of a spinner.
      booted.value = true
      return
    }

    if (current.slug !== route.params.slug) {
      // Re-bootstraps under the canonical slug; keep showing the loader.
      await router.replace(`/lens/assistants/${current.slug}/chat`)
      return
    }

    selectedAssistantUuid.value = current.uuid
    await loadMyShareState()
    await loadSessions()
    booted.value = true
    const getScrollContainer = () => scrollRef.value
    await scrollConversationToBottomAfterRender(getScrollContainer, nextTick)
  } catch {
    showError(t('lens.chat.loadFailed'))
    booted.value = true
  }
}

function requireLogin() {
  showLoginModal.value = true
}

function openMyShares() {
  mySharesOpen.value = true
  if (isMobile.value) {
    sidebarOpen.value = false
  }
}

async function onLoginSuccess() {
  showLoginModal.value = false
  // Load the now-authenticated user's assistants and sessions so the
  // composer becomes usable without a full page reload.
  await bootstrap()
}

async function loadMyShareState() {
  try {
    const shares = await listMyShares()
    sharesByRun.value = Object.fromEntries(
      shares
        .filter((share) => share.run_uuid)
        .map((share) => [share.run_uuid, share])
    )
  } catch {
    sharesByRun.value = {}
  }
}

async function loadSessions(selectUuid = '', { useRouteSession = true } = {}) {
  if (!selectedAssistant.value) {
    return
  }

  sessions.value = await listSessions(selectedAssistant.value.slug, {
    archived: showArchivedSessions.value
  })

  const requestedUuid =
    selectUuid || (useRouteSession ? route.query.session || '' : '')
  let targetUuid = requestedUuid || sessions.value[0]?.uuid
  if (
    requestedUuid &&
    !sessions.value.some((session) => session.uuid === requestedUuid)
  ) {
    const replacement = await createNewSession(false)
    targetUuid = replacement?.uuid || ''
  }
  if (targetUuid) {
    await selectSession({ uuid: targetUuid })
  } else {
    clearSessionSelection()
  }
  await nextTick(() => composerRef.value?.focus())
}

async function createNewSession(notify = true) {
  if (!selectedAssistant.value) {
    return null
  }
  mySharesOpen.value = false
  const leavingArchivedView = showArchivedSessions.value
  showArchivedSessions.value = false
  if (leavingArchivedView) {
    sessions.value = []
  }

  let session
  try {
    session = await createSession({
      assistant_uuid: selectedAssistant.value.uuid,
      title: ''
    })
  } catch {
    showError(t('lens.chat.sessionCreateFailed'))
    return null
  }

  const existingIndex = sessions.value.findIndex(
    (item) => item.uuid === session.uuid
  )
  if (existingIndex >= 0) {
    sessions.value = sessions.value.map((item) =>
      item.uuid === session.uuid ? session : item
    )
  } else {
    sessions.value = [session, ...sessions.value]
  }
  sortManagedSessions()
  selectedSessionUuid.value = session.uuid
  question.value = ''
  retryDraft.value = null
  clarificationAnswers.value = {}
  clarificationErrors.value = {}
  if (composerRef.value) composerRef.value.style.height = 'auto'
  clearAttachments()
  messages.value = []
  currentRun.value = null
  resetStreamState()
  router.replace({
    path: route.path,
    query: { session: session.uuid }
  })

  if (notify && existingIndex < 0) {
    showSuccess(t('lens.chat.sessionCreated'))
  }

  return session
}

function setSessionTitle(uuid, title) {
  const session = sessions.value.find((item) => item.uuid === uuid)
  if (session) {
    session.title = title
  }
}

function clearSessionSelection() {
  sessionLoadGeneration += 1
  clearAttachments()
  selectedSessionUuid.value = ''
  messages.value = []
  currentRun.value = null
  question.value = ''
  retryDraft.value = null
  clarificationAnswers.value = {}
  clarificationErrors.value = {}
  resetStreamState()
  router.replace({ path: route.path })
}

async function switchSessionView(archived) {
  if (showArchivedSessions.value === archived) return
  showArchivedSessions.value = archived
  cancelRename()
  closeDeleteSession()
  await router.replace({ path: route.path })
  await loadSessions('', {
    useRouteSession: false
  })
}

function sortManagedSessions() {
  sessions.value = [...sessions.value].sort((left, right) => {
    const leftPinned = left.pinned_at ? Date.parse(left.pinned_at) : 0
    const rightPinned = right.pinned_at ? Date.parse(right.pinned_at) : 0
    if (leftPinned !== rightPinned) return rightPinned - leftPinned
    return Date.parse(right.created_at || 0) - Date.parse(left.created_at || 0)
  })
}

function hasShareableSessionAnswer(session) {
  if (session.has_shareable_answer) return true
  if (session.uuid !== selectedSessionUuid.value) return false
  return messages.value.some(
    (message) =>
      message.role === 'assistant' &&
      message.run &&
      Boolean((message.content || '').trim())
  )
}

function sessionActions(session) {
  const archived = session.status === 'archived'
  const shareDisabled = !hasShareableSessionAnswer(session)
  const actions = [
    {
      key: 'share',
      label: t('common.share'),
      icon: Share2,
      disabled: shareDisabled,
      disabledReason: shareDisabled ? t('lens.chat.shareUnavailable') : ''
    },
    {
      key: 'rename',
      label: t('lens.chat.renameSession'),
      icon: Pencil
    }
  ]
  if (archived) {
    actions.push({
      key: 'restore',
      label: t('lens.chat.restoreSession'),
      icon: ArchiveRestore
    })
  } else {
    actions.push({
      key: session.pinned_at ? 'unpin' : 'pin',
      label: session.pinned_at
        ? t('lens.chat.unpinSession')
        : t('lens.chat.pinSession'),
      icon: session.pinned_at ? PinOff : Pin
    })
    actions.push({
      key: 'archive',
      label: t('lens.chat.archiveSession'),
      icon: Archive
    })
  }
  actions.push({
    key: 'delete',
    label: t('lens.chat.deleteSession'),
    icon: Trash2,
    divider: true,
    variant: 'danger'
  })
  return actions
}

async function handleSessionAction(session, action) {
  if (action === 'share') await openSessionShare(session)
  else if (action === 'rename') startRename(session)
  else if (action === 'pin') await setSessionPinned(session, true)
  else if (action === 'unpin') await setSessionPinned(session, false)
  else if (action === 'archive') await archiveManagedSession(session)
  else if (action === 'restore') await restoreManagedSession(session)
  else if (action === 'delete') deleteSessionTarget.value = session
}

async function setSessionPinned(session, pinned) {
  try {
    const updated = pinned
      ? await pinSession(session.uuid)
      : await unpinSession(session.uuid)
    Object.assign(session, updated)
    sortManagedSessions()
    showSuccess(
      pinned ? t('lens.chat.sessionPinned') : t('lens.chat.sessionUnpinned')
    )
  } catch {
    showError(t('lens.chat.sessionActionFailed'))
  }
}

async function archiveManagedSession(session) {
  try {
    await archiveSession(session.uuid)
    sessions.value = sessions.value.filter((item) => item.uuid !== session.uuid)
    if (selectedSessionUuid.value === session.uuid) {
      const next = sessions.value[0]
      if (next) await selectSession(next)
      else clearSessionSelection()
    }
    showSuccess(t('lens.chat.sessionArchived'))
  } catch {
    showError(t('lens.chat.sessionActionFailed'))
  }
}

async function restoreManagedSession(session) {
  if (!session) return
  try {
    await restoreSession(session.uuid)
    sessions.value = sessions.value.filter((item) => item.uuid !== session.uuid)
    if (selectedSessionUuid.value === session.uuid) {
      const next = sessions.value[0]
      if (next) await selectSession(next)
      else clearSessionSelection()
    }
    showSuccess(t('lens.chat.sessionRestored'))
  } catch {
    showError(t('lens.chat.sessionActionFailed'))
  }
}

async function refreshSessionTitleUntilSettled(sessionUuid) {
  const localSession = sessions.value.find((item) => item.uuid === sessionUuid)
  const assistantSlug = localSession?.assistant_slug
  if (
    !assistantSlug ||
    !['pending', 'generating'].includes(localSession?.title_generation_status)
  ) {
    return
  }

  for (let attempt = 0; attempt < TITLE_POLL_MAX_ATTEMPTS; attempt += 1) {
    try {
      const latestSessions = await listSessions(assistantSlug)
      const latest = latestSessions.find((item) => item.uuid === sessionUuid)
      const current = sessions.value.find((item) => item.uuid === sessionUuid)
      if (!latest || !current) return
      Object.assign(current, latest)
      if (!['pending', 'generating'].includes(latest.title_generation_status)) {
        return
      }
    } catch {
      // Keep the completed answer visible while title refresh retries.
    }
    await sleep(TITLE_POLL_INTERVAL_MS)
  }
}

function deriveSessionTitle(text) {
  const clean = (text || '').replace(/\s+/g, ' ').trim()
  return clean.length > 24 ? `${clean.slice(0, 24)}…` : clean
}

function startRename(session) {
  renamingSessionUuid.value = session.uuid
  renameDraft.value = session.title || ''
  nextTick(() => {
    const el = document.querySelector('.session-rename-input')
    if (el) {
      el.focus()
      el.select()
    }
  })
}

function cancelRename() {
  renamingSessionUuid.value = ''
  renameDraft.value = ''
}

async function saveRename(session) {
  // Enter and blur can both fire; the guard makes the save idempotent.
  if (renamingSessionUuid.value !== session.uuid) {
    return
  }
  const title = renameDraft.value.trim()
  if (!title) {
    showError(t('lens.chat.titleRequired'))
    nextTick(() => document.querySelector('.session-rename-input')?.focus())
    return
  }
  renamingSessionUuid.value = ''
  renameDraft.value = ''
  if (title === (session.title || '')) {
    return
  }
  const previous = session.title || ''
  setSessionTitle(session.uuid, title)
  try {
    const updated = await updateSession(session.uuid, { title })
    Object.assign(session, updated)
  } catch {
    setSessionTitle(session.uuid, previous)
    showError(t('lens.chat.renameFailed'))
  }
}

function triggerFilePick() {
  fileInput.value?.click()
}

async function onFileInputChange(event) {
  const files = Array.from(event.target.files || [])
  event.target.value = ''
  for (const file of files) {
    await addAttachment(file)
  }
}

async function onComposerPaste(event) {
  if (!acceptsImages.value) return
  const items = Array.from(event.clipboardData?.items || [])
  const images = items.filter(
    (item) => item.kind === 'file' && item.type.startsWith('image/')
  )
  if (!images.length) return
  // Only swallow the paste when it actually carries an image, so pasting
  // text into the composer keeps working normally.
  event.preventDefault()
  for (const item of images) {
    const file = item.getAsFile()
    if (file) await addAttachment(file)
  }
}

async function addAttachment(file) {
  const validation = validateAttachment(file, {
    acceptsImages: acceptsImages.value,
    acceptsDocuments: acceptsDocuments.value,
    currentCount: attachments.value.length
  })
  if (validation.error) {
    showError(
      t(`lens.chat.${validation.error}`, {
        max: MAX_ATTACHMENTS
      })
    )
    return
  }
  if (validation.kind === 'image') {
    try {
      const { width, height } = await readImageDimensions(file)
      const dimensionError = validateImageDimensions(width, height)
      if (dimensionError) {
        showError(t(`lens.chat.${dimensionError}`))
        return
      }
    } catch {
      showError(t('lens.chat.attachmentUploadFailed'))
      return
    }
  }
  const sessionUuid = selectedSessionUuid.value
  if (!sessionUuid) return
  const item = {
    key: `${Date.now()}-${Math.random().toString(36).slice(2)}`,
    uuid: '',
    kind: validation.kind,
    name: file.name || validation.kind,
    localUrl: validation.kind === 'image' ? URL.createObjectURL(file) : '',
    status: 'uploading'
  }
  attachments.value = [...attachments.value, item]
  try {
    const result = await uploadAttachment(sessionUuid, file)
    item.uuid = result.uuid
    item.kind = result.kind || validation.kind
    item.name = result.original_name || item.name
    item.url = result.url || ''
    item.byte_size = result.byte_size || 0
    item.mime_type = result.mime_type || ''
    if (
      selectedSessionUuid.value !== sessionUuid ||
      !attachments.value.includes(item)
    ) {
      removeAttachment(item)
      return
    }
    item.status = 'done'
    attachments.value = [...attachments.value]
  } catch (error) {
    removeAttachment(item)
    showError(t(`lens.chat.${attachmentUploadError(error)}`))
  }
}

function removeAttachment(item) {
  if (item.localUrl) URL.revokeObjectURL(item.localUrl)
  attachments.value = attachments.value.filter((entry) => entry !== item)
  if (item.kind === 'document' && item.uuid) {
    deleteAttachment(item.uuid).catch(() => {
      showWarning(t('lens.chat.attachmentDeleteFailed'))
    })
  }
}

function clearAttachments() {
  attachments.value.forEach((item) => {
    if (item.kind === 'document' && item.uuid) {
      deleteAttachment(item.uuid).catch(() => {})
    }
  })
  attachments.value.forEach(
    (item) => item.localUrl && URL.revokeObjectURL(item.localUrl)
  )
  attachments.value = []
}

function insertNewline() {
  const el = composerRef.value
  if (!el) return
  const start = el.selectionStart
  const end = el.selectionEnd
  question.value =
    question.value.slice(0, start) + '\n' + question.value.slice(end)
  nextTick(() => {
    el.selectionStart = el.selectionEnd = start + 1
    autoResizeTextarea(el)
  })
}

function autoResizeTextarea(el) {
  const target = el?.target ?? el
  if (!target) return
  target.style.height = 'auto'
  target.style.height = Math.min(target.scrollHeight, 200) + 'px'
}

async function handlePrimaryAction() {
  if (isRunActive.value) {
    await cancel()
    return
  }
  await submit()
}

async function selectSession(session, updateRoute = true) {
  const loadGeneration = ++sessionLoadGeneration
  const isCurrentLoad = () =>
    loadGeneration === sessionLoadGeneration &&
    selectedSessionUuid.value === session.uuid
  const finishRunStatusResolution = () => {
    if (isCurrentLoad()) {
      runStatusResolvingSessionUuid.value = ''
    }
  }
  const sessionChanged = selectedSessionUuid.value !== session.uuid
  mySharesOpen.value = false
  clearAttachments()
  selectedSessionUuid.value = session.uuid
  runStatusResolvingSessionUuid.value = session.uuid
  let loadedMessages
  try {
    loadedMessages = await listMessages(session.uuid)
  } catch (error) {
    if (!isCurrentLoad()) return
    finishRunStatusResolution()
    if ([403, 404].includes(error?.response?.status)) {
      showError(t('lens.chat.sessionAccessDenied'))
      return
    }
    throw error
  }
  if (!isCurrentLoad()) return
  messages.value = loadedMessages
  // Session history is ready for display. An active run's SSE can stay open
  // for minutes, so it must not keep the whole chat behind the page loader.
  booted.value = true
  if (sessionChanged) {
    question.value = ''
    retryDraft.value = null
    if (composerRef.value) composerRef.value.style.height = 'auto'
  }
  currentRun.value = null
  resetStreamState()
  if (updateRoute) {
    router.replace({
      path: route.path,
      query: { session: session.uuid }
    })
  }
  await nextTick(scrollToBottom)
  if (!isCurrentLoad()) return
  clearUnreadSession(window.localStorage, session.uuid)
  refreshUnreadSessions()
  await maybeResumeActiveRun(session.uuid, isCurrentLoad)
}

// If the session has a run still in progress (e.g. the user navigated away
// mid-answer), re-attach the SSE stream so the live thinking panel and
// streamed answer resume, then finalize like a normal run.
async function maybeResumeActiveRun(sessionUuid, isCurrentLoad) {
  // the latest message carrying a run uuid (the user message of the most
  // recent turn) tells us whether that turn is still in progress
  const withRun = [...messages.value].reverse().find((m) => m.run)
  if (!withRun) {
    runStatusResolvingSessionUuid.value = ''
    return
  }
  const resolution = await resolveRunStatus(getRun, withRun.run)
  if (!isCurrentLoad()) return
  if (!resolution.resolved) return
  const run = resolution.run
  runStatusResolvingSessionUuid.value = ''
  if (!['queued', 'running', 'streaming'].includes(run?.status)) {
    // A historically-failed turn keeps its retry hint with the right
    // (blame-clarifying) message after a reload, not just live.
    if (run?.status === 'failed') {
      failedRunError.value = run.error || 'RUN_FAILED'
    }
    return
  }
  startCompletionTracking(run, sessionUuid)
  // hand the trailing in-progress assistant placeholder to the live row to
  // avoid showing it twice; the SSE sync replays its content and steps
  const last = messages.value[messages.value.length - 1]
  if (last && last.role === 'assistant') {
    messages.value = messages.value.slice(0, -1)
  }
  currentRun.value = run
  try {
    await readSse(run.uuid)
    const restoredRun = await getRun(run.uuid)
    if (!isCurrentLoad()) return
    currentRun.value = restoredRun
  } catch {
    // stream aborted (e.g. the user switched sessions) — fall through
  }
  if (!isCurrentLoad()) return
  const restoredMessages = await listMessages(sessionUuid)
  if (!isCurrentLoad()) return
  messages.value = restoredMessages
  resetStreamState()
}

async function readSse(runUuid) {
  streamController.value?.abort()
  const controller = new AbortController()
  streamController.value = controller

  const response = await fetch(
    `${apiConfig.apiBaseUrl}/lens/runs/${runUuid}/stream/`,
    {
      headers: { Accept: 'text/event-stream', ...authHeaders() },
      signal: controller.signal
    }
  )
  if (!response.ok || !response.body) {
    throw new Error('SSE failed')
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let streamDone = false

  while (!streamDone) {
    const { done, value } = await reader.read()
    if (done) {
      streamDone = true
      continue
    }
    buffer += decoder.decode(value, { stream: true })
    let boundary = buffer.indexOf('\n\n')
    while (boundary !== -1) {
      const raw = buffer.slice(0, boundary)
      buffer = buffer.slice(boundary + 2)
      boundary = buffer.indexOf('\n\n')
      const dataLine = raw.split('\n').find((line) => line.startsWith('data: '))
      if (dataLine) {
        handleEvent(JSON.parse(dataLine.slice(6)))
      }
    }
  }
}

function handleEvent(event) {
  runtimeState.value = applyRuntimeEvent(runtimeState.value, event)
  if (
    event.type === 'sync' ||
    event.type === 'status' ||
    event.type === 'awaiting_user_input'
  ) {
    currentRun.value = {
      ...currentRun.value,
      status: event.status,
      resume_by: event.resume_by ?? null
    }
    if (event.status !== 'queued') queuePosition.value = null
    if (event.type === 'sync') {
      event.steps?.forEach((step) => handleStepEvent(step, event.ts))
    }
    const terminalEvent = terminalSyncEvent(event)
    if (terminalEvent) {
      runtimeState.value = applyRuntimeEvent(runtimeState.value, terminalEvent)
    }
  }
  if (event.type === 'queue_position') {
    queuePosition.value = event.position
  }
  if (event.type === 'sync' && event.content) {
    partialAnswer.value = event.content
  }
  if (event.type === 'step') {
    handleStepEvent(event, event.ts)
  }
  if (event.type === 'token_reset') {
    partialAnswer.value = ''
  }
  if (event.type === 'token') {
    appendAnswerDelta(event.content)
  }
  if (event.type === 'error') {
    streamError.value = event.error?.code
      ? mapRunError(event.error.code)
      : event.error?.message || event.error || t('lens.chat.events.error')
  }
}

function handleStepEvent(event) {
  const events = event.detail?.events || []
  const stepKey = event.sequence || event.step || 'step'
  const seenCount = seenStepEventCounts.get(stepKey) || 0
  const newEvents = events.slice(seenCount)
  seenStepEventCounts.set(stepKey, events.length)

  newEvents.forEach(pushAgentActivity)
}

async function submitClarification(message) {
  const request = clarificationRequestFor(message)
  const runUuid = message?.run
  if (
    !request ||
    !runUuid ||
    isClarificationAnswered(message) ||
    isClarificationSubmitting(message)
  ) {
    return
  }
  const answer = clarificationAnswerFor(message).trim()
  if (!answer) {
    clarificationErrors.value = {
      ...clarificationErrors.value,
      [runUuid]: t('lens.chat.runtime.clarificationRequired')
    }
    return
  }

  clarificationSubmitting.value = new Set([
    ...clarificationSubmitting.value,
    runUuid
  ])
  const sessionAtSubmit = selectedSessionUuid.value
  loading.value.run = true
  try {
    const continuation = await answerRunClarification(
      runUuid,
      request.request_id,
      answer
    )
    if (selectedSessionUuid.value !== sessionAtSubmit) return
    clarificationAnswers.value = {
      ...clarificationAnswers.value,
      [runUuid]: answer
    }
    const errors = { ...clarificationErrors.value }
    delete errors[runUuid]
    clarificationErrors.value = errors
    resetStreamState()
    currentRun.value = continuation
    startCompletionTracking(continuation, sessionAtSubmit)
    messages.value = await listMessages(sessionAtSubmit)
    await nextTick(scrollToBottom)
    await readSse(continuation.uuid)
    if (selectedSessionUuid.value !== sessionAtSubmit) return
    await finishSubmittedRun(continuation.uuid, sessionAtSubmit)
  } catch (error) {
    if (error?.name !== 'AbortError') {
      clarificationErrors.value = {
        ...clarificationErrors.value,
        [runUuid]: t('lens.chat.runtime.clarificationFailed')
      }
    }
  } finally {
    const submitting = new Set(clarificationSubmitting.value)
    submitting.delete(runUuid)
    clarificationSubmitting.value = submitting
    if (selectedSessionUuid.value === sessionAtSubmit) {
      loading.value.run = false
    }
  }
}

async function submit() {
  // Unauthenticated visitors must log in before sending a message.
  if (isAnonymous.value) {
    requireLogin()
    return
  }
  if (loading.value.run) {
    return
  }
  if (!canSubmit.value) {
    return
  }
  loading.value.run = true
  if (!selectedSessionUuid.value) {
    const draftBeforeSessionCreation = question.value
    const session = await createNewSession(false)
    if (!session) {
      question.value = draftBeforeSessionCreation
      loading.value.run = false
      return
    }
    question.value = draftBeforeSessionCreation
  }
  // Bind this submit to the session it started in. If the user switches
  // assistant/session mid-flight, the stream is aborted on purpose — that is
  // not a failure, so we must not restore the draft, alarm the user, or write
  // into the now-current assistant's state.
  const sessionAtSubmit = selectedSessionUuid.value
  const isFirstMessage = messages.value.length === 0
  resetStreamState()
  const optimisticText = question.value.replace(/^\s*\n+|\n+\s*$/g, '')
  question.value = ''
  // Snapshot ready attachments, clear the composer strip, and keep the object
  // URLs alive for the optimistic bubble until the server reload replaces it.
  const pendingAttachments = attachments.value.filter(
    (item) => item.status === 'done'
  )
  const attachmentUuids = pendingAttachments.map((item) => item.uuid)
  const retryDraftAtSubmit = retryDraft.value
  const preparedSubmission = prepareRunSubmission({
    sessionUuid: sessionAtSubmit,
    question: optimisticText,
    attachmentUuids,
    retryDraft: retryDraftAtSubmit,
    pendingSubmission: pendingRunSubmission.value
  })
  pendingRunSubmission.value = preparedSubmission.submission
  attachments.value = []
  // Revoke the optimistic object URLs on every exit path, unless they are
  // restored to the composer for a retry (set below on real failures).
  let keepAttachments = false
  let submittedRunUuid = ''
  if (composerRef.value) composerRef.value.style.height = 'auto'
  messages.value = [
    ...messages.value,
    {
      role: 'user',
      content: optimisticText,
      uuid: '__optimistic__',
      created_at: new Date().toISOString(),
      attachments: pendingAttachments.map((item) => ({
        uuid: item.uuid,
        kind: item.kind,
        localUrl: item.localUrl,
        url: item.url,
        original_name: item.name,
        byte_size: item.byte_size,
        mime_type: item.mime_type
      }))
    }
  ]
  await nextTick(scrollToBottom)

  // Show the backend's first-question fallback immediately. The run creation
  // request persists the same value without treating it as a manual rename.
  const sessionAtSubmitObj = sessions.value.find(
    (item) => item.uuid === sessionAtSubmit
  )
  if (
    isFirstMessage &&
    optimisticText &&
    !(sessionAtSubmitObj?.title || '').trim()
  ) {
    const autoTitle = deriveSessionTitle(optimisticText)
    if (autoTitle) {
      setSessionTitle(sessionAtSubmit, autoTitle)
    }
  }
  currentRun.value = null
  try {
    const run = await createRun(sessionAtSubmit, preparedSubmission.payload)
    submittedRunUuid = run.uuid
    if (
      pendingRunSubmission.value?.idempotencyKey ===
      preparedSubmission.submission.idempotencyKey
    ) {
      pendingRunSubmission.value = null
    }
    retryDraft.value = null
    startCompletionTracking(run, sessionAtSubmit)
    // switched away between createRun and here — don't bind this run's live
    // state onto the now-current assistant
    if (selectedSessionUuid.value !== sessionAtSubmit) return
    currentRun.value = run
    await readSse(run.uuid)
    // switched away while streaming — leave the new assistant untouched
    if (selectedSessionUuid.value !== sessionAtSubmit) return
    await finishSubmittedRun(run.uuid, sessionAtSubmit)
    if (
      pendingRunSubmission.value?.idempotencyKey ===
      preparedSubmission.submission.idempotencyKey
    ) {
      pendingRunSubmission.value = null
    }
    retryDraft.value = null
  } catch (err) {
    // a deliberate stream abort (switch/navigate) or a switch away is not a
    // submit failure — bail silently without touching the current state
    if (
      err?.name === 'AbortError' ||
      selectedSessionUuid.value !== sessionAtSubmit
    ) {
      return
    }
    const runUuid = submittedRunUuid
    if (runUuid) {
      try {
        const run = await waitForRunTerminal(runUuid)
        if (selectedSessionUuid.value !== sessionAtSubmit) return
        currentRun.value = run
        await finishSubmittedRun(runUuid, sessionAtSubmit)
        if (
          pendingRunSubmission.value?.idempotencyKey ===
          preparedSubmission.submission.idempotencyKey
        ) {
          pendingRunSubmission.value = null
        }
        retryDraft.value = null
        return
      } catch {
        // Fall through to the true submit-failure recovery path.
      }
    }
    messages.value = messages.value.filter((m) => m.uuid !== '__optimistic__')
    question.value = optimisticText
    retryDraft.value = retryDraftAtSubmit
    // Only a 4xx response proves that the Run transaction rejected the
    // request. Network and server failures are ambiguous: the attachments may
    // already be bound to a committed Run and cannot be reused by a retry.
    const requestRejected =
      !runUuid && err?.response?.status >= 400 && err.response.status < 500
    const attachmentMissing = hasAttachmentErrorCode(
      err,
      'ATTACHMENT_NOT_FOUND'
    )
    if (pendingAttachments.length && requestRejected && !attachmentMissing) {
      attachments.value = [...attachments.value, ...pendingAttachments]
      keepAttachments = true
    }
    showError(t('lens.chat.submitFailed'))
  } finally {
    if (!keepAttachments) {
      pendingAttachments.forEach(
        (item) => item.localUrl && URL.revokeObjectURL(item.localUrl)
      )
    }
    if (selectedSessionUuid.value === sessionAtSubmit) {
      loading.value.run = false
    }
  }
}

async function cancel() {
  if (!currentRun.value) {
    return
  }
  streamController.value?.abort()
  currentRun.value = await cancelRun(currentRun.value.uuid)
  showWarning(t('lens.chat.runStopped'))
}

async function copyMessage(message) {
  try {
    await navigator.clipboard.writeText(message.content || '')
    showSuccess(t('lens.chat.messageCopied'))
  } catch {
    showWarning(t('lens.chat.copyFailed'))
  }
}

function isFeedbackUpdating(runUuid) {
  return feedbackUpdatingRuns.value.has(runUuid)
}

async function setFeedback(message, feedback) {
  const runUuid = message?.run
  if (!runUuid || isFeedbackUpdating(runUuid)) return
  const nextFeedback = message.feedback === feedback ? '' : feedback
  feedbackUpdatingRuns.value = new Set([...feedbackUpdatingRuns.value, runUuid])
  try {
    const result = await updateRunFeedback(runUuid, nextFeedback)
    messages.value = messages.value.map((item) =>
      item.uuid === message.uuid
        ? {
            ...item,
            feedback: result.feedback,
            feedback_updated_at: result.feedback_updated_at
          }
        : item
    )
  } catch {
    showError(t('lens.chat.feedbackFailed'))
  } finally {
    const updating = new Set(feedbackUpdatingRuns.value)
    updating.delete(runUuid)
    feedbackUpdatingRuns.value = updating
  }
}

const previewFile = ref(null)

function openPreview(file) {
  previewFile.value = file
}

function closePreview() {
  previewFile.value = null
}

function openCodeCitation(message, citation) {
  if (!message?.run || !citation?.id) return
  activeCitation.value = { runUuid: message.run, citation }
  citationDrawerOpen.value = true
  loadCodeCitation()
}

async function loadCodeCitation() {
  const active = activeCitation.value
  if (!active) return
  const requestId = ++citationRequestId
  citationSourceLoading.value = true
  citationSourceError.value = ''
  citationSource.value = null
  try {
    const source = await getRunCitationSource(
      active.runUuid,
      active.citation.id
    )
    if (requestId === citationRequestId) citationSource.value = source
  } catch {
    if (requestId === citationRequestId) {
      citationSourceError.value = t('lens.chat.citations.unavailable')
    }
  } finally {
    if (requestId === citationRequestId) citationSourceLoading.value = false
  }
}

function closeCodeCitation() {
  citationRequestId += 1
  citationDrawerOpen.value = false
  activeCitation.value = null
  citationSource.value = null
  citationSourceLoading.value = false
  citationSourceError.value = ''
}

function handleCardClick(file) {
  if (isPreviewable(file)) {
    openPreview(file)
  } else {
    downloadOutputFile(file)
  }
}

async function downloadOutputFile(file) {
  if (!file?.url) {
    return
  }
  try {
    const blob = await fetchDeliverableBlob(file)
    const objectUrl = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = objectUrl
    link.download = file.filename || 'download'
    document.body.appendChild(link)
    link.click()
    document.body.removeChild(link)
    URL.revokeObjectURL(objectUrl)
  } catch {
    showWarning(t('lens.chat.downloadFailed'))
  }
}

function downloadAttachmentFile(file) {
  return downloadOutputFile({
    ...file,
    filename: file.original_name || 'document'
  })
}

function formatBytes(size) {
  if (!size) {
    return ''
  }
  if (size < 1024) {
    return `${size} B`
  }
  if (size < 1024 * 1024) {
    return `${(size / 1024).toFixed(1)} KB`
  }
  return `${(size / (1024 * 1024)).toFixed(1)} MB`
}

function fileTypeLabel(file) {
  const ext = extensionOf(file.filename).toUpperCase()
  const size = formatBytes(file.byte_size)
  return [ext, size].filter(Boolean).join(' · ')
}

function nativeSharePayload(share) {
  const title = share.title || shareQuestion.value
  return {
    title,
    text: t('lens.qa.nativeShareText', {
      name: assistantName.value || t('lens.qa.genericAgent'),
      title
    }),
    url: qaShareUrl(share.token)
  }
}

async function openShare(message, sourceMessages = messages.value) {
  shareRunUuid.value = message.run || ''
  shareExisting.value = sharesByRun.value[shareRunUuid.value] || null
  shareAnswer.value = message.content || ''
  shareQuestion.value =
    precedingUserMessage(sourceMessages, message)?.content || ''

  if (isMobile.value && shareExisting.value && supportsNativeShare()) {
    const result = await shareWithNative(
      nativeSharePayload(shareExisting.value)
    )
    if (result.status === 'shared') return
    if (result.status === 'cancelled') return
    if (result.status === 'failed') {
      showError(t('lens.qa.nativeShareFailed'))
    }
  }
  shareOpen.value = true
}

async function openSessionShare(session) {
  if (!hasShareableSessionAnswer(session)) return
  try {
    const sourceMessages =
      session.uuid === selectedSessionUuid.value
        ? messages.value
        : await listMessages(session.uuid)
    const answer = [...sourceMessages]
      .reverse()
      .find(
        (message) =>
          message.role === 'assistant' &&
          message.run &&
          Boolean((message.content || '').trim())
      )
    if (!answer) {
      session.has_shareable_answer = false
      showWarning(t('lens.chat.shareUnavailable'))
      return
    }
    session.has_shareable_answer = true
    openShare(answer, sourceMessages)
  } catch {
    showError(t('lens.qa.shareFailed'))
  }
}

async function exportQa(message) {
  const sourceQuestion = userMessageForMessage(message)
  const questionText = sourceQuestion?.content || ''
  const session = sessions.value.find(
    (session) => session.uuid === selectedSessionUuid.value
  )
  const sessionSummary =
    session?.title?.trim() || t('lens.chat.untitledSession')
  const sessionUuid = selectedSessionUuid.value
  const activityId = sessionActivity.createActivityId('pdf')
  const sourceRoute = {
    name: 'LensAssistantChat',
    params: { slug: session?.assistant_slug || selectedAssistant.value?.slug },
    query: { session: sessionUuid }
  }
  sessionActivity.beginActivity(sessionUuid, activityId)
  try {
    const response = await getRunPdf(message.run)
    downloadQaPdf(response, {
      summary: sessionSummary,
      question: questionText
    })
    sessionActivity.notify({
      duration: 5000,
      message: t('lens.chat.pdfGenerated', { title: sessionSummary }),
      to: sourceRoute,
      type: 'success'
    })
  } catch {
    sessionActivity.notify({
      duration: 8000,
      message: t('lens.chat.pdfGenerationFailed'),
      to: sourceRoute,
      type: 'error'
    })
  } finally {
    sessionActivity.endActivity(sessionUuid, activityId)
  }
}

function isMessageShared(message) {
  return Boolean(message.run && sharesByRun.value[message.run])
}

function handleShareUpdated(share) {
  if (!share?.run_uuid) {
    return
  }
  sharesByRun.value = {
    ...sharesByRun.value,
    [share.run_uuid]: share
  }
  shareExisting.value = share
}

function handleShareRemoved(share) {
  if (!share?.run_uuid) {
    return
  }
  const next = { ...sharesByRun.value }
  delete next[share.run_uuid]
  sharesByRun.value = next
  shareExisting.value = null
}

function userMessageForMessage(message) {
  return precedingUserMessage(messages.value, message)
}

function retryLastQuestion(message = null) {
  const userMessage = retryableUserMessage(messages.value, message)
  if (!userMessage) {
    return
  }
  question.value = userMessage.content || ''
  const runUuid = retryRunUuid(messages.value, message)
  retryDraft.value = runUuid ? { question: question.value, runUuid } : null
  nextTick(() => {
    composerRef.value?.focus()
  })
}

function canRetryLastQuestion(message = null) {
  return retryableUserMessage(messages.value, message) !== null
}

function formatTime(isoString) {
  if (!isoString) return ''
  return new Date(isoString).toLocaleTimeString('zh-CN', {
    hour: '2-digit',
    minute: '2-digit',
    hour12: false
  })
}

function closeDeleteSession() {
  if (!deletingSession.value) {
    deleteSessionTarget.value = null
  }
}

async function doDeleteSession() {
  const session = deleteSessionTarget.value
  if (!session || deletingSession.value) return
  deletingSession.value = true
  try {
    await deleteSession(session.uuid)
    sessions.value = sessions.value.filter((s) => s.uuid !== session.uuid)
    if (clearUnreadSession(window.localStorage, session.uuid)) {
      refreshUnreadSessions()
    }
    if (selectedSessionUuid.value === session.uuid) {
      const next = sessions.value[0]
      if (next) {
        await selectSession(next)
      } else {
        clearSessionSelection()
      }
    }
    deleteSessionTarget.value = null
    showSuccess(t('lens.chat.sessionDeleted'))
  } catch {
    showError(t('lens.chat.deleteFailed'))
  } finally {
    deletingSession.value = false
  }
}

watch(
  () => route.query.session,
  (sessionUuid) => {
    if (
      route.name !== 'LensAssistantChat' ||
      route.params.slug !== selectedAssistant.value?.slug ||
      !sessionUuid ||
      sessionUuid === selectedSessionUuid.value
    ) {
      return
    }
    const session = sessions.value.find((item) => item.uuid === sessionUuid)
    if (session) {
      void selectSession(session, false)
    }
  }
)

watch(
  () => route.params.slug,
  () => {
    // On a hard load with a stored token, defer the first bootstrap to
    // onMounted so it runs after the user is hydrated — avoids a flash of
    // the anonymous view and a redundant public fetch.
    if (!userStore.user && localStorage.getItem('access_token')) {
      return
    }
    bootstrap()
  },
  { immediate: true }
)

onMounted(async () => {
  window.addEventListener('storage', handleCompletionStorage)
  window.addEventListener('focus', handleCompletionVisibility)
  window.visualViewport?.addEventListener('resize', syncMobileViewport)
  window.visualViewport?.addEventListener('scroll', syncMobileViewport)
  document.addEventListener('visibilitychange', handleCompletionVisibility)
  syncMobileViewport()
  refreshUnreadSessions()
  if (window.innerWidth < 1024) {
    sidebarOpen.value = false
  }
  // Public route: hydrate a stored user (if any), then bootstrap once.
  if (!userStore.user && localStorage.getItem('access_token')) {
    await userStore.checkAuthStatus()
    bootstrap()
  }
})

onBeforeUnmount(() => {
  window.removeEventListener('storage', handleCompletionStorage)
  window.removeEventListener('focus', handleCompletionVisibility)
  window.visualViewport?.removeEventListener('resize', syncMobileViewport)
  window.visualViewport?.removeEventListener('scroll', syncMobileViewport)
  document.removeEventListener('visibilitychange', handleCompletionVisibility)
  streamController.value?.abort()
  answerAutoScroller.dispose()
  clearInterval(elapsedTimer)
})
</script>

<style scoped>
.lens-chat-page {
  @apply flex h-screen w-full overflow-hidden;
  background: var(--sl-bg-surface);
  color: var(--sl-text-primary);
}

.lens-chat-page.visual-viewport-constrained {
  position: fixed;
  top: var(--chat-viewport-offset-top, 0px);
  right: 0;
  bottom: auto;
  left: 0;
  width: 100%;
  height: var(--chat-viewport-height, 100dvh);
  overflow: hidden;
  overscroll-behavior: none;
}

.sidebar {
  @apply flex h-full flex-shrink-0 flex-col border-r transition-[width] duration-300 ease-in-out;
  will-change: width;
  background: var(--sl-bg-surface);
  border-color: var(--sl-border-default);
}

.sidebar-expanded {
  @apply w-[264px];
}

.sidebar-collapsed {
  @apply w-[64px];
}

.side-head {
  @apply px-3 pt-4 pb-3;
}

.sidebar-brand {
  @apply relative block px-1 pb-4;
  height: 48px;
  transition: height 300ms ease-in-out;
}

.sidebar-brand-collapsed {
  height: 96px;
}

.sidebar-brand-link {
  @apply absolute left-0 top-0 flex h-12 items-center;
  width: calc(100% - 48px);
  transition:
    width 300ms ease-in-out,
    transform 300ms ease-in-out;
}

.sidebar-brand-collapsed .sidebar-brand-link {
  @apply left-1/2;
  width: 100%;
  transform: translateX(-50%);
}

.sidebar-logo-stage {
  @apply relative block h-12 w-[190px] overflow-hidden;
  contain: layout paint;
  will-change: width;
  transition: width 300ms ease-in-out;
}

.sidebar-logo-stage-collapsed {
  @apply w-10;
}

.sidebar-logo-layer {
  @apply absolute inset-0 flex h-12 items-center;
  will-change: opacity, transform;
  transition:
    opacity 200ms ease-in-out,
    transform 300ms ease-in-out;
}

.sidebar-wordmark-layer {
  @apply origin-left opacity-100;
}

.sidebar-mark-layer {
  @apply justify-center origin-center opacity-0 scale-[0.72];
}

.sidebar-logo-stage-collapsed .sidebar-wordmark-layer {
  @apply pointer-events-none opacity-0 -translate-x-2;
}

.sidebar-logo-stage-collapsed .sidebar-mark-layer {
  @apply opacity-100 translate-x-0;
}

.sidebar-collapse-btn {
  @apply absolute right-0 top-1 flex h-10 w-10 shrink-0 items-center
    justify-center rounded-md transition-all duration-300 ease-in-out;
  color: var(--sl-text-secondary);
}

.sidebar-brand-collapsed .sidebar-collapse-btn {
  @apply left-1/2 right-auto top-14;
  transform: translateX(-50%);
}

.sidebar-collapse-btn:hover {
  background: var(--sl-bg-hover);
}

.sidebar-collapse-btn svg {
  @apply h-5 w-5;
}

.new-chat-btn {
  @apply flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left text-sm font-medium transition-colors;
  color: var(--sl-text-secondary);
}

.new-chat-btn:hover {
  background: var(--sl-bg-hover);
}

.new-chat-btn svg {
  @apply h-4 w-4 shrink-0;
}

.new-chat-btn-collapsed {
  @apply justify-center px-0;
}

.side-scroll {
  @apply flex-1 overflow-y-auto px-3 pb-4 pt-3;
}

.sessions-head {
  @apply px-1 pb-2;
}

.session-filters {
  @apply flex items-center gap-1;
}

.session-filters button {
  @apply rounded-md px-2 py-1 text-[11px] font-semibold tracking-wide text-theme-muted transition-colors;
}

.session-filters button:hover,
.session-filter-active {
  @apply bg-surface-hover text-theme;
}

.sessions-list {
  @apply space-y-1;
}

.session-item {
  @apply flex w-full items-center gap-2 rounded-xl px-3 py-2 text-left transition-all duration-150;
}

.session-item:hover {
  background: var(--sl-bg-hover);
}

.session-item-active {
  background: var(--sl-bg-selected);
}

.session-title-row {
  @apply flex min-w-0 items-center gap-1.5;
}

.session-title {
  @apply min-w-0 flex-1 truncate text-sm font-medium;
  color: var(--sl-text-primary);
}

.session-activity-indicator {
  @apply flex h-6 w-6 shrink-0 items-center justify-center text-primary-600;
}

.session-activity-indicator svg {
  animation: spin 0.75s linear infinite;
}

.session-pinned-icon {
  @apply shrink-0 text-primary-600;
}

.session-list-empty {
  @apply px-3 py-4 text-center text-xs text-theme-subtle;
}

.session-unread-indicator {
  @apply h-2.5 w-2.5 shrink-0 rounded-full bg-primary-600;
}

.session-overflow {
  @apply opacity-0;
  transition: opacity 150ms ease;
}

.session-item:hover .session-overflow,
.session-item:focus-within .session-overflow,
.session-item-active .session-overflow,
.session-overflow.row-action-menu-open {
  @apply opacity-100;
}

.session-rename-input {
  @apply w-full rounded-md border px-2 py-1 text-sm font-medium outline-none;
  border-color: #c7d2fe;
  color: var(--sl-text-primary);
}

.main-shell {
  @apply relative flex min-w-0 flex-1 flex-col overflow-hidden;
  background: var(--sl-bg-surface);
}

.mobile-topbar {
  @apply flex flex-shrink-0 items-center gap-1 border-b px-2 py-1.5;
  border-color: var(--sl-border-default);
}

.mobile-topbar-title {
  @apply flex min-w-0 flex-1 justify-center;
}

.mobile-topbar-title-text {
  @apply min-w-0 truncate text-center text-sm font-semibold text-ink-900;
}

.chat-header {
  @apply flex flex-shrink-0 items-center gap-3 border-b px-5 py-3;
  border-color: var(--sl-border-default);
}

.chat-header-title {
  @apply min-w-0 truncate text-base font-semibold text-ink-900;
}

.chat-header-assistant {
  @apply min-w-0 flex-1;
}

.chat-header-description {
  @apply mt-0.5 max-w-2xl truncate text-xs leading-5 text-ink-500;
}

.chat-header-back {
  @apply flex h-8 w-8 shrink-0 items-center justify-center rounded-md text-ink-500 transition-colors;
}

.chat-header-back:hover {
  background: var(--sl-bg-hover);
  color: var(--sl-text-secondary);
}

.chat-header-link {
  @apply inline-flex shrink-0 items-center gap-1.5 rounded-lg border px-3 py-1.5 text-sm font-medium no-underline transition-colors;
  border-color: var(--sl-border-default);
  color: var(--sl-text-secondary);
}

.chat-header-link:hover {
  background: var(--sl-bg-canvas);
  border-color: var(--sl-border-strong);
  color: var(--sl-text-primary);
}

.retry-hint {
  @apply flex flex-wrap items-center gap-3 rounded-lg border px-3 py-2.5 text-sm;
  border-color: #fde68a;
  background: #fffbeb;
  color: #92400e;
}

.retry-hint-text {
  @apply min-w-0 flex-1;
}

.retry-hint-btn {
  @apply shrink-0 rounded-md px-3 py-1 text-sm font-medium text-white transition-colors;
  background: #d97706;
}

.retry-hint-btn:hover {
  background: #b45309;
}

.thread-scroll {
  @apply min-h-0 flex-1 overflow-y-auto;
  overflow-anchor: none;
  scrollbar-gutter: stable;
}

.thread-loading {
  @apply flex h-full items-center justify-center px-6;
}

.thread {
  @apply mx-auto w-full max-w-[860px] px-5 py-7;
  padding-bottom: 220px;
}

.message-row {
  @apply mb-8 flex items-start gap-3;
}

.message-row-user {
  @apply flex-row-reverse;
}

.message-body {
  @apply min-w-0 flex-1;
}

.message-row-user .message-body {
  @apply w-fit flex-none text-right;
  max-width: min(620px, 86%);
}

.message-card {
  @apply min-w-0;
}

.message-card.user {
  @apply rounded-xl px-3.5 py-2.5 text-left;
  background: var(--sl-bg-hover);
  overflow-wrap: anywhere;
}

.message-time {
  @apply mt-1 text-xs;
  color: var(--sl-text-subtle);
}

.message-time.user {
  @apply text-right;
}

.message-markdown :deep(.markdown-content) {
  @apply max-w-none break-words;
  color: var(--sl-text-primary);
}

.message-markdown :deep(.markdown-content h1),
.message-markdown :deep(.markdown-content h2),
.message-markdown :deep(.markdown-content h3),
.message-markdown :deep(.markdown-content h4) {
  color: var(--sl-text-primary);
}

.message-markdown :deep(.markdown-content p) {
  @apply mb-2.5 text-[15px] leading-6;
  color: var(--sl-text-primary);
}

.message-markdown :deep(.markdown-content ul),
.message-markdown :deep(.markdown-content ol) {
  @apply mb-3 pl-5;
}

.message-markdown :deep(.markdown-content li) {
  @apply mb-1.5 text-[15px] leading-6;
  color: var(--sl-text-primary);
}

.message-markdown :deep(.markdown-content :not(pre) > code) {
  background: #eef4fe;
  color: #0e278c;
}

.message-text {
  @apply whitespace-pre-wrap break-words text-[15px] leading-6;
  color: var(--sl-text-primary);
}

.message-actions {
  @apply mt-2 flex items-center gap-1;
}

.icon-btn {
  @apply flex h-[30px] w-[30px] items-center justify-center rounded-md transition-colors;
  color: var(--sl-text-subtle);
}

.icon-btn:hover {
  background: var(--sl-bg-hover);
  color: var(--sl-text-secondary);
}

.icon-btn:disabled {
  @apply cursor-wait opacity-60;
}

.icon-btn-feedback-positive {
  background: #dcfce7;
  color: #15803d;
}

.icon-btn-feedback-positive:hover {
  background: #bbf7d0;
  color: #166534;
}

.icon-btn-feedback-negative {
  background: #fee2e2;
  color: #b91c1c;
}

.icon-btn-feedback-negative:hover {
  background: #fecaca;
  color: #991b1b;
}

.icon-btn-shared {
  background: rgba(34, 197, 94, 0.1);
  color: #15803d;
}

.icon-btn-shared:hover {
  background: rgba(34, 197, 94, 0.16);
  color: #166534;
}

.icon-btn svg {
  @apply h-4 w-4;
}

.live-card,
.activity-card,
.timeline-card {
  @apply mb-9 max-w-[900px] rounded-lg border bg-surface px-4 py-3;
  border-color: var(--sl-border-default);
}

.card-head {
  @apply flex items-center justify-between gap-3;
}

.card-title,
.card-heading {
  @apply text-xs font-semibold uppercase tracking-wide;
  color: var(--sl-text-muted);
}

.card-state,
.card-caption {
  @apply text-xs;
  color: var(--sl-text-muted);
}

.live-progress-row {
  @apply mb-2;
}

.live-status-text {
  @apply min-w-0 truncate;
}

.thinking-elapsed {
  @apply shrink-0 text-xs tabular-nums;
  color: var(--sl-text-subtle);
}

.runtime-progress-card,
.runtime-block-card,
.runtime-artifact-card,
.runtime-outcome-card {
  margin-top: 0.5rem;
  border: 1px solid #e2e8f0;
  border-radius: 0.625rem;
  background: var(--sl-bg-raised);
  padding: 0.65rem 0.75rem;
  color: var(--sl-text-secondary);
  font-size: 0.78rem;
  line-height: 1.45;
}

.runtime-progress-card {
  margin-top: 0;
  margin-bottom: 0.5rem;
}

.runtime-progress-live {
  border-color: var(--sl-border-default);
  background: var(--sl-bg-raised);
}

.runtime-progress-live > .runtime-progress-summary {
  display: none;
}

.runtime-progress-summary {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  cursor: pointer;
  list-style: none;
}

.runtime-progress-summary::-webkit-details-marker {
  display: none;
}

.runtime-progress-summary .runtime-card-title {
  margin-bottom: 0;
}

.runtime-progress-summary-text {
  min-width: 0;
  flex: 1;
  overflow: hidden;
  color: var(--sl-text-muted);
  font-size: 0.72rem;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.runtime-progress-chevron {
  flex: 0 0 auto;
  color: var(--sl-text-subtle);
  font-size: 0.9rem;
  transition: transform 0.15s ease;
}

.runtime-progress-card[open] .runtime-progress-summary {
  margin-bottom: 0.35rem;
}

.runtime-progress-card[open] .runtime-progress-chevron {
  transform: rotate(180deg);
}

.runtime-card-title {
  margin-bottom: 0.35rem;
  color: var(--sl-text-secondary);
  font-weight: 600;
}

.runtime-plan-step {
  display: flex;
  align-items: flex-start;
  gap: 0.5rem;
  padding: 0.18rem 0;
}

.runtime-workflow-task + .runtime-workflow-task {
  margin-top: 0.3rem;
}

.runtime-task-row {
  color: var(--sl-text-secondary);
  font-weight: 600;
}

.runtime-workflow-stage {
  margin-left: 1.45rem;
  border-left: 1px solid #dbe3ec;
  padding-left: 0.65rem;
}

.runtime-workflow-task.is-direct .runtime-workflow-stage {
  margin-left: 0;
}

.runtime-stage-row {
  color: var(--sl-text-secondary);
  font-weight: 500;
}

.runtime-workflow-steps {
  max-height: 6.5rem;
  margin-left: 1.35rem;
  overflow-y: auto;
  color: var(--sl-text-muted);
  font-size: 0.72rem;
  scrollbar-width: thin;
}

.runtime-step-row {
  padding: 0.12rem 0;
}

.runtime-node-activities {
  max-height: 5.5rem;
  margin: 0.15rem 0 0.25rem 1.5rem;
  padding: 0.15rem 0.35rem;
  overflow-y: auto;
  border-left: 1px solid #e2e8f0;
  color: var(--sl-text-muted);
  font-size: 0.71rem;
  scrollbar-width: thin;
}

.runtime-standalone-activities {
  margin-left: 0;
}

.runtime-node-activity {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  min-height: 1.3rem;
}

.runtime-activity-indicator {
  display: inline-flex;
  width: 0.75rem;
  height: 0.75rem;
  align-items: center;
  justify-content: center;
  flex: 0 0 auto;
  color: #6b8b77;
  font-size: 0.62rem;
}

.runtime-activity-indicator.is-current {
  border: 1.5px solid #d8dce8;
  border-top-color: #6677a3;
  border-radius: 9999px;
  animation: spin 0.75s linear infinite;
}

.runtime-activity-count {
  flex: 0 0 auto;
  color: var(--sl-text-muted);
}

.runtime-plan-status {
  display: inline-flex;
  width: 1rem;
  height: 1rem;
  align-items: center;
  justify-content: center;
  flex: 0 0 auto;
  margin-top: 0.08rem;
  color: var(--sl-text-muted);
  font-size: 0.75rem;
  font-weight: 700;
  line-height: 1;
}

.runtime-plan-status.is-in_progress {
  width: 0.85rem;
  height: 0.85rem;
  margin: 0.15rem 0.075rem 0;
  border: 2px solid #ead7b4;
  border-top-color: #b7791f;
  border-radius: 9999px;
  color: transparent;
  font-size: 0;
  animation: spin 0.75s linear infinite;
}

.runtime-plan-step.is-active-ancestor {
  color: #74521e;
  font-weight: 600;
}

.runtime-plan-status.is-in_progress.is-active-ancestor {
  border: 0;
  animation: none;
}

.runtime-plan-status.is-completed {
  color: #3f7a5c;
}

.runtime-plan-status.is-failed {
  color: #b64949;
}

.runtime-plan-status.is-skipped {
  color: var(--sl-text-subtle);
}

.runtime-step-content {
  display: flex;
  min-width: 0;
  flex: 1;
  flex-direction: column;
}

.runtime-step-summary {
  margin-top: 0.08rem;
  color: var(--sl-text-muted);
  font-size: 0.72rem;
}

.runtime-progress-footer {
  margin-top: 0.4rem;
  padding-top: 0.4rem;
  border-top: 1px dashed #e2e8f0;
  color: var(--sl-text-muted);
  font-size: 0.72rem;
}

.live-status-card {
  @apply mb-2 flex items-center gap-2 rounded-lg px-3 py-2 text-sm;
  border: 1px solid #e5e7eb;
  background: var(--sl-bg-canvas);
  color: var(--sl-text-secondary);
}

.runtime-block-card {
  border-color: #e8c78f;
  background: #fff8e8;
  color: #74521e;
}

.runtime-outcome-card {
  border-color: #d5c8ae;
  background: #f7f1e4;
}

.clarification-card {
  @apply mt-2 rounded-lg border px-3 py-3;
  border-color: #b8c7ef;
  background: var(--sl-bg-raised);
}

.clarification-question {
  @apply mt-1 text-sm leading-5;
  color: var(--sl-text-primary);
}

.clarification-label {
  @apply mt-3 block text-xs font-medium;
  color: var(--sl-text-secondary);
}

.clarification-input {
  @apply mt-1 block w-full resize-y rounded-md border px-2.5 py-2 text-sm leading-5 outline-none;
  border-color: var(--sl-border-default);
  background: var(--sl-bg-canvas);
  color: var(--sl-text-primary);
}

.clarification-input:focus {
  border-color: #6b82db;
  box-shadow: 0 0 0 2px rgb(107 130 219 / 15%);
}

.clarification-input:disabled {
  cursor: not-allowed;
  opacity: 0.7;
}

.clarification-actions {
  @apply mt-2 flex flex-wrap items-center justify-end gap-2;
}

.clarification-submit {
  @apply rounded-md px-3 py-1.5 text-sm font-medium text-white transition-colors;
  background: #3152c9;
}

.clarification-submit:hover:not(:disabled) {
  background: #2744ab;
}

.clarification-submit:disabled {
  cursor: wait;
  opacity: 0.65;
}

.clarification-submitted {
  @apply mr-auto text-xs;
  color: var(--sl-text-muted);
}

.clarification-error {
  @apply mr-auto text-xs;
  color: #b42318;
}

.live-progress-dot {
  @apply h-1.5 w-1.5 shrink-0 rounded-full;
  background: #2b4ee6;
  animation: cursor-blink 1s steps(2, start) infinite;
}

.live-text {
  @apply whitespace-pre-wrap text-[15px] leading-6;
  color: var(--sl-text-primary);
}

.live-thinking {
  @apply flex items-center gap-2 text-sm;
  color: var(--sl-text-secondary);
}

.live-markdown.is-streaming :deep(.markdown-content > *:last-child)::after {
  content: '';
  @apply ml-1 inline-block h-4 w-1 align-middle;
  background: #2b4ee6;
  animation: cursor-blink 1s steps(2, start) infinite;
}

.activity-list {
  @apply mt-3 space-y-2;
}

.activity-item {
  @apply flex items-start justify-between gap-3 rounded-md px-3 py-2;
  background: var(--sl-bg-canvas);
}

.activity-title {
  @apply text-sm font-medium;
  color: var(--sl-text-primary);
}

.activity-detail {
  @apply mt-1 truncate text-xs;
  color: var(--sl-text-muted);
}

.activity-time,
.timeline-time {
  @apply shrink-0 text-xs;
  color: var(--sl-text-muted);
}

.timeline-toggle {
  @apply flex w-full items-center justify-between gap-3 text-left;
}

.timeline-list {
  @apply mt-3;
}

.timeline-list > * + * {
  border-top: 1px solid #f3f4f6;
}

.timeline-item {
  @apply py-3;
}

.timeline-line {
  @apply flex flex-wrap items-center gap-2;
}

.timeline-status {
  @apply rounded-full px-2 py-0.5 text-xs font-medium;
  background: #eef4fe;
  color: #0e278c;
}

.timeline-label {
  @apply text-xs;
  color: var(--sl-text-secondary);
}

.timeline-message {
  @apply mt-2 whitespace-pre-wrap text-xs leading-5;
  color: var(--sl-text-secondary);
}

.composer-wrap {
  @apply pointer-events-none absolute inset-x-0 bottom-0 z-20 px-6 pb-5;
  background: linear-gradient(
    to top,
    rgb(var(--sl-bg-surface-rgb) / 98%) 36%,
    rgb(var(--sl-bg-surface-rgb) / 78%) 72%,
    rgb(var(--sl-bg-surface-rgb) / 0%) 100%
  );
}

.archived-session-notice {
  @apply absolute inset-x-4 bottom-5 z-20 mx-auto flex max-w-[860px] items-center justify-center gap-2 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900 shadow-sm;
}

.archived-session-notice button {
  @apply rounded-md px-2 py-1 font-semibold text-primary-700 hover:bg-surface;
}

.composer-inner {
  @apply mx-auto w-full max-w-[860px];
}

.composer-shell {
  @apply pointer-events-auto;
}

.composer {
  @apply flex items-center gap-3 rounded-xl border bg-surface px-4 py-2.5;
  border-color: var(--sl-border-default);
  box-shadow: 0 8px 24px rgba(15, 23, 42, 0.08);
}

.composer:focus-within {
  @apply border-primary-300;
  box-shadow: 0 0 0 3px rgba(43, 78, 230, 0.08);
}

.composer-input {
  @apply flex-1 border-0 bg-transparent py-2 px-0 text-[16px] leading-6 outline-none;
  color: var(--sl-text-primary);
  min-height: 2.5rem;
  max-height: 200px;
  resize: none;
  overflow-y: auto;
  align-self: flex-end;
}

.composer-input::placeholder {
  color: var(--sl-text-subtle);
}

.composer-action-btn {
  @apply flex h-10 w-10 shrink-0 items-center justify-center rounded-full transition-colors;
  background: #111111;
  color: #fff;
}

.composer-action-btn:hover:not(:disabled) {
  background: #1f2937;
}

.composer-action-btn-stop {
  background: #111111;
}

.composer-action-btn:disabled {
  background: var(--sl-bg-selected);
  cursor: not-allowed;
}

.composer-action-btn svg {
  @apply h-[17px] w-[17px];
}

.composer-file-input {
  display: none;
}

.composer-attach-btn {
  @apply flex h-9 w-9 shrink-0 items-center justify-center rounded-full
    border border-line bg-surface text-theme-muted transition-colors;
}

.composer-attach-btn:hover:not(:disabled) {
  @apply border-primary-300 text-primary-600;
}

.composer-attach-btn:disabled {
  @apply text-gray-300 cursor-not-allowed;
}

.composer-attach-btn svg {
  @apply h-[18px] w-[18px];
}

.composer-attachments {
  @apply mb-2 flex flex-wrap gap-2;
}

.composer-thumb {
  @apply relative h-16 w-16 overflow-hidden rounded-lg border border-line;
}

.composer-thumb.is-document {
  @apply w-48 bg-surface-sunken;
}

.composer-document {
  @apply flex h-full min-w-0 items-center gap-2 px-3 pr-7 text-theme-secondary;
}

.composer-document span {
  @apply truncate text-xs font-medium;
}

.composer-thumb img {
  @apply h-full w-full object-cover;
}

.composer-thumb.is-uploading img {
  opacity: 0.5;
}

.composer-thumb-spinner {
  @apply absolute inset-0 m-auto h-5 w-5 rounded-full border-2
    border-line-strong border-t-primary-500;
  animation: spin 0.7s linear infinite;
}

.composer-thumb-remove {
  @apply absolute right-0.5 top-0.5 flex h-5 w-5 items-center justify-center;
}

.composer-thumb-remove span {
  @apply flex h-5 w-5 items-center justify-center rounded-full bg-black/55
    text-sm leading-none text-white;
}

@media (max-width: 1023px), (hover: none), (pointer: coarse) {
  .sidebar-collapse-btn,
  .composer-action-btn,
  .icon-btn,
  .composer-attach-btn,
  .composer-thumb-remove {
    @apply h-11 w-11;
  }

  .deliverable-action,
  .retry-hint-btn {
    min-width: 44px;
    min-height: 44px;
  }

  .session-overflow {
    @apply opacity-100;
  }

  .composer-thumb-remove {
    top: 0;
    right: 0;
  }

  .message-actions {
    gap: 0.125rem;
    flex-wrap: nowrap;
  }

  .message-actions .icon-btn {
    @apply h-11 w-11;
  }
}

@media (max-width: 1023px) {
  .lens-chat-page {
    position: fixed;
    top: var(--chat-viewport-offset-top, 0px);
    right: 0;
    bottom: auto;
    left: 0;
    width: 100%;
    height: var(--chat-viewport-height, 100dvh);
    overflow: hidden;
    overscroll-behavior: none;
  }

  .mobile-topbar {
    min-height: 52px;
    padding: 0.25rem 0.5rem;
    background: rgb(var(--sl-bg-surface-rgb) / 96%);
    border-color: var(--sl-border-soft);
  }

  .mobile-topbar .sidebar-collapse-btn {
    position: static;
    border-radius: 9999px;
    transform: none;
  }

  .mobile-topbar-title {
    align-items: center;
  }

  .mobile-topbar-title-text {
    font-size: 0.9375rem;
    line-height: 1.25rem;
  }

  .mobile-topbar-title :deep(.assistant-switcher-header-trigger) {
    max-width: min(62vw, 15rem);
    padding: 0.5rem 0.625rem;
    border-radius: 9999px;
  }

  .thread-scroll {
    scrollbar-gutter: auto;
    scrollbar-width: none;
  }

  .thread-scroll::-webkit-scrollbar {
    display: none;
  }

  .thread-scroll .thread {
    max-width: 47.5rem;
    padding: 1.125rem 1rem 9.5rem;
  }

  .chat-welcome {
    display: flex;
    min-height: calc(100dvh - 13rem);
    align-items: center;
    justify-content: center;
    flex-direction: column;
    padding: 0 1.5rem 13.5rem;
    text-align: center;
  }

  .chat-welcome-assistant {
    margin-bottom: 0.5rem;
    color: var(--sl-text-muted);
    font-size: 0.8125rem;
    font-weight: 600;
    line-height: 1.25rem;
  }

  .chat-welcome-title {
    color: var(--sl-text-primary);
    font-size: 1.375rem;
    font-weight: 600;
    line-height: 1.75rem;
    letter-spacing: -0.015em;
  }

  .chat-welcome-description {
    width: 100%;
    max-width: 19rem;
    margin-top: 0.5rem;
    overflow: hidden;
    color: var(--sl-text-muted);
    font-size: 0.875rem;
    line-height: 1.375rem;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .message-row {
    margin-bottom: 1.5rem;
    gap: 0;
  }

  .message-body,
  .message-row-assistant .message-body {
    width: 100%;
  }

  .message-row-user .message-body {
    width: fit-content;
    max-width: 86%;
  }

  .message-card.user {
    padding: 0.625rem 0.875rem;
    border-radius: 1.25rem;
    background: var(--sl-bg-hover);
  }

  .message-text,
  .live-text,
  .message-markdown :deep(.markdown-content p),
  .message-markdown :deep(.markdown-content li) {
    font-size: 1rem;
    line-height: 1.625rem;
  }

  .message-markdown :deep(.markdown-content p) {
    margin-bottom: 0.75rem;
  }

  .message-markdown :deep(.markdown-content ul),
  .message-markdown :deep(.markdown-content ol) {
    margin-bottom: 0.875rem;
  }

  .message-time {
    display: none;
  }

  .message-actions {
    margin-top: 0.25rem;
    margin-left: -0.5rem;
  }

  .runtime-block-card,
  .runtime-artifact-card,
  .runtime-outcome-card {
    border-radius: 0.75rem;
    padding: 0.625rem 0.75rem;
    background: var(--sl-bg-canvas);
  }

  .runtime-progress-card {
    border: 0;
    border-radius: 0;
    background: transparent;
    padding: 0;
  }

  .runtime-progress-card > .runtime-progress-summary {
    min-height: 2.75rem;
    gap: 0.375rem;
    padding: 0;
  }

  .runtime-progress-live > .runtime-progress-summary {
    display: flex;
    min-height: 2.75rem;
  }

  .runtime-progress-summary .runtime-card-title {
    color: var(--sl-text-primary);
    font-size: 0.875rem;
    line-height: 1.25rem;
  }

  .runtime-progress-summary-text {
    font-size: 0.75rem;
    line-height: 1rem;
  }

  .runtime-progress-chevron {
    display: inline-flex;
    width: 2.75rem;
    height: 2.75rem;
    align-items: center;
    justify-content: center;
    margin-right: -0.75rem;
  }

  .runtime-progress-card[open] .runtime-progress-summary {
    margin-bottom: 0.25rem;
  }

  .runtime-progress-content {
    margin-left: 0.5rem;
    border-left: 1px solid var(--sl-border-default);
    padding: 0.125rem 0 0.25rem 0.75rem;
  }

  .runtime-progress-card:not(.runtime-progress-live) > .runtime-workflow,
  .runtime-progress-card:not(.runtime-progress-live) > .runtime-node-activities,
  .runtime-progress-card:not(.runtime-progress-live) > .runtime-plan-node {
    margin-left: 0.5rem;
    padding-left: 0.75rem;
  }

  .runtime-progress-live .runtime-progress-desktop-title,
  .runtime-progress-live .runtime-progress-footer {
    display: none;
  }

  .live-status-card {
    padding: 0.25rem 0;
    border: 0;
    background: transparent;
  }

  .retry-hint {
    align-items: flex-start;
    flex-direction: column;
  }

  .message-attachments,
  .message-deliverables {
    width: 100%;
  }

  .message-document-card,
  .deliverable-card {
    max-width: 100%;
  }

  .main-shell .composer-wrap,
  .main-shell .composer-wrap-empty {
    position: absolute;
    top: auto;
    bottom: calc(0.75rem + env(safe-area-inset-bottom));
    padding-right: 0.5rem;
    padding-bottom: 0;
    padding-left: 0.5rem;
    background: transparent;
    transform: none;
  }

  .composer-inner {
    max-width: 47.5rem;
  }

  .composer {
    gap: 0.375rem;
    padding: 0.4375rem;
    border-color: var(--sl-border-strong);
    border-radius: 1.75rem;
    box-shadow:
      0 10px 30px rgba(15, 23, 42, 0.12),
      0 2px 8px rgba(15, 23, 42, 0.06);
  }

  .composer:focus-within {
    @apply border-primary-400;
    box-shadow:
      0 0 0 3px rgba(43, 78, 230, 0.12),
      0 10px 30px rgba(15, 23, 42, 0.12),
      0 2px 8px rgba(15, 23, 42, 0.06);
  }

  .composer-input {
    min-height: 2.75rem;
    max-height: 8.75rem;
    padding: 0.625rem 0.25rem;
    line-height: 1.5rem;
  }

  .composer-attach-btn {
    border-color: transparent;
  }

  .composer-action-btn {
    height: 2.75rem;
    width: 2.75rem;
  }

  .prompt-suggestions {
    display: flex;
    max-width: 36rem;
    flex-wrap: wrap;
    justify-content: center;
    gap: 0.5rem;
    margin: 0 auto 0.75rem;
  }

  .prompt-suggestion {
    min-height: 2.75rem;
    border: 1px solid var(--sl-border-default);
    border-radius: 9999px;
    padding: 0.5rem 0.875rem;
    background: var(--sl-bg-surface);
    color: var(--sl-text-secondary);
    font-size: 0.8125rem;
    line-height: 1.125rem;
    transition:
      border-color 150ms ease,
      background-color 150ms ease,
      color 150ms ease;
  }

  .prompt-suggestion:hover,
  .prompt-suggestion:focus-visible {
    @apply border-primary-300;
    background: var(--sl-bg-hover);
    color: var(--sl-text-primary);
    outline: none;
  }

  .disclaimer {
    margin-top: 0.375rem;
    font-size: 0.625rem;
    line-height: 0.875rem;
  }

  .mobile-disclaimer {
    margin: 1.5rem 0 0;
    padding: 0 0.75rem;
  }

  .archived-session-notice {
    bottom: calc(0.5rem + env(safe-area-inset-bottom));
    align-items: flex-start;
    flex-wrap: wrap;
    justify-content: flex-start;
    border-radius: 1rem;
  }

  .lens-chat-page .sidebar {
    width: min(calc(100vw - 2.5rem), 20rem);
    border-radius: 0;
  }

  .sidebar .side-scroll {
    padding-right: 0.5rem;
    padding-left: 0.5rem;
  }

  .sessions-list .session-item {
    border-radius: 0.625rem;
  }
}

@media (max-width: 1023px) and (max-height: 600px) {
  .prompt-suggestions {
    display: none;
  }
}

@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}

@media (prefers-reduced-motion: reduce) {
  .session-activity-indicator svg,
  .runtime-plan-status.is-in_progress,
  .runtime-activity-indicator.is-current {
    animation: none;
  }
}

.message-attachments {
  @apply mb-2 flex min-w-0 max-w-full flex-wrap justify-end gap-2;
}

.message-attachments :deep(.auth-image) {
  max-width: 220px;
  max-height: 220px;
  object-fit: cover;
}

.message-document-card {
  @apply flex w-full min-w-0 max-w-sm items-center gap-2 rounded-lg border
    border-line bg-surface px-3 py-2 text-left text-theme-secondary
    transition-colors;
}

.message-document-card:hover {
  @apply border-primary-300 text-primary-700;
}

.message-document-card span {
  @apply flex min-w-0 flex-1 flex-col;
}

.message-document-card strong {
  @apply truncate text-sm font-medium;
}

.message-document-card small {
  @apply text-xs text-theme-subtle;
}

.message-deliverables {
  @apply mt-3 flex flex-col gap-2;
}

.deliverable-card {
  @apply flex w-full max-w-sm items-center gap-3 rounded-xl border
    border-line bg-surface px-3 py-2.5 text-left transition-all;
}

.deliverable-card:hover {
  @apply border-primary-300 shadow-soft;
}

.deliverable-open {
  @apply flex min-w-0 flex-1 items-center gap-3 text-left;
}

.deliverable-thumb {
  @apply flex h-10 w-10 shrink-0 items-center justify-center rounded-lg
    bg-primary-50 text-primary-600;
}

.deliverable-meta {
  @apply flex min-w-0 flex-1 flex-col;
}

.deliverable-name {
  @apply truncate text-sm font-medium text-theme;
}

.deliverable-sub {
  @apply mt-0.5 text-xs uppercase tracking-wide text-theme-subtle;
}

.deliverable-actions {
  @apply flex shrink-0 items-center gap-1;
}

.deliverable-action {
  @apply flex h-8 w-8 shrink-0 items-center justify-center rounded-lg
    text-theme-subtle transition-colors;
}

.deliverable-action:hover {
  @apply bg-primary-50 text-primary-600;
}

.disclaimer {
  @apply mt-3 text-center text-xs;
  color: var(--sl-text-subtle);
}

.sidebar-footer {
  @apply border-t border-line p-3;
}

@keyframes cursor-blink {
  0%,
  45% {
    opacity: 1;
  }
  46%,
  100% {
    opacity: 0;
  }
}

@keyframes typing-dot {
  0%,
  80%,
  100% {
    opacity: 0.35;
    transform: translateY(0);
  }
  40% {
    opacity: 1;
    transform: translateY(-2px);
  }
}

@media (max-width: 1023px) {
  .sidebar {
    @apply fixed inset-y-0 left-0 z-30 -translate-x-full transition-transform duration-300;
    width: min(calc(100vw - 2.5rem), 20rem);
    border-right: none;
    border-radius: 0 1rem 1rem 0;
    box-shadow: 0 8px 40px rgba(0, 0, 0, 0.18);
  }

  .sidebar.sidebar-open {
    transform: translateX(0);
  }

  .side-head {
    @apply px-3 pt-3 pb-2;
  }

  .sidebar-brand {
    height: 44px;
    padding-bottom: 0.5rem;
  }

  .side-scroll {
    @apply px-2;
  }

  .new-chat-btn {
    @apply py-3;
  }

  .new-chat-btn span {
    @apply text-[15px];
  }

  .sessions-list {
    @apply space-y-1;
  }

  .session-item {
    @apply min-h-11 rounded-lg py-2;
  }

  .session-title {
    @apply text-[15px];
  }

  .sidebar-footer {
    @apply p-2;
  }

  .session-filters {
    @apply gap-0;
  }

  .session-filters button {
    @apply min-h-11 flex-1 px-3 py-2 text-xs;
  }

  .thread {
    @apply px-4 py-5;
    padding-bottom: 220px;
  }

  .main-shell {
    padding-top: env(safe-area-inset-top);
  }

  .composer-wrap {
    @apply px-4 pb-4;
  }

  .dock-menu {
    width: 100%;
  }
}

@media (min-width: 1024px) {
  .main-shell {
    box-shadow: inset 1px 0 0 #e5e7eb;
  }
}

:global(:root[data-theme='dark'] .sidebar) {
  background: var(--sl-bg-canvas);
  border-right: 0;
}

:global(:root[data-theme='dark'] .mobile-topbar),
:global(:root[data-theme='dark'] .chat-header),
:global(:root[data-theme='dark'] .sidebar-footer) {
  border-color: transparent;
}

@media (min-width: 1024px) {
  :global(:root[data-theme='dark'] .main-shell) {
    box-shadow: none;
  }
}
</style>
