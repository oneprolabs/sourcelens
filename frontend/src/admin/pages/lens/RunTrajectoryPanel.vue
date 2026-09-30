<template>
  <section data-testid="run-trajectory-workbench" class="run-trajectory">
    <BaseLoading v-if="loading && events.length === 0" />

    <template v-else>
      <div
        v-if="
          active &&
          (ACTIVE_RUN_STATUSES.has(runStatus) || pendingNewEventCount > 0)
        "
        class="trajectory-stream-bar"
      >
        <div
          v-if="ACTIVE_RUN_STATUSES.has(runStatus)"
          class="trajectory-live-state"
          :data-state="streamState"
          role="status"
        >
          <span class="trajectory-live-dot" aria-hidden="true" />
          {{ t(`lensRuns.trajectoryStream${streamStateLabel}`) }}
        </div>
        <button
          v-if="pendingNewEventCount > 0"
          type="button"
          class="trajectory-new-events"
          @click="scrollToLatestTrajectory"
        >
          {{ t('lensRuns.trajectoryNewEvents', { n: pendingNewEventCount }) }}
        </button>
      </div>
      <dl class="trajectory-stats" data-testid="trajectory-summary">
        <div class="stat">
          <dt>{{ t('lensRuns.trajectoryEvents') }}</dt>
          <dd>{{ summary.event_count || 0 }}</dd>
        </div>
        <div class="stat">
          <dt>{{ t('lensRuns.trajectoryDuration') }}</dt>
          <dd>{{ durationText(summary.duration_ms) }}</dd>
        </div>
        <div class="stat">
          <dt>{{ t('lensRuns.trajectoryModels') }}</dt>
          <dd>{{ summary.model_calls || 0 }}</dd>
        </div>
        <div class="stat">
          <dt>{{ t('lensRuns.trajectoryTools') }}</dt>
          <dd>{{ summary.tool_calls || 0 }}</dd>
        </div>
        <div class="stat">
          <dt>
            {{
              t(
                summary.usage_scope === 'metered'
                  ? 'lensRuns.trajectoryMeteredTokens'
                  : 'lensRuns.trajectoryObservedTokens'
              )
            }}
          </dt>
          <dd>{{ (summary.total_tokens || 0).toLocaleString() }}</dd>
        </div>
        <div class="stat stat-error">
          <dt>{{ t('lensRuns.trajectoryErrors') }}</dt>
          <dd>{{ summary.error_count || 0 }}</dd>
        </div>
      </dl>

      <details v-if="diagnosticSpans.length" class="trajectory-diagnostics">
        <summary>
          {{
            t('lensRuns.trajectoryDiagnostics', { n: diagnosticSpans.length })
          }}
        </summary>
        <button
          v-for="span in diagnosticSpans"
          :key="span.id"
          type="button"
          @click="selectAncestor(span.id)"
        >
          <strong>{{ spanLabel(span) || span.name }}</strong>
          <span>{{ diagnosticText(span) }}</span>
        </button>
      </details>
      <section
        v-if="childProgress.length"
        class="assistant-progress"
        data-testid="trajectory-assistant-progress"
        :aria-label="t('lensRuns.trajectoryAssistantProgress')"
      >
        <div class="assistant-progress-header">
          <strong>{{ t('lensRuns.trajectoryAssistantProgress') }}</strong>
          <span>
            {{
              t('lensRuns.trajectoryAssistantProgressCount', {
                completed: completedChildCount,
                total: childProgress.length
              })
            }}
          </span>
        </div>
        <div class="assistant-progress-list">
          <article
            v-for="run in childProgress"
            :key="run.run_uuid"
            class="assistant-progress-row"
          >
            <span
              class="assistant-progress-indicator"
              :data-status="run.status"
              aria-hidden="true"
            />
            <strong :title="run.assistant_name || 'Subagent'">
              {{ run.assistant_name || 'Subagent' }}
            </strong>
            <span class="assistant-progress-task" :title="run.task">
              {{ run.task }}
            </span>
            <span class="assistant-progress-meta">
              <span class="assistant-progress-status">
                {{ progressStatusLabel(run.status) }}
              </span>
              <span>
                {{
                  t('lensRuns.trajectoryAssistantEvents', {
                    n: run.event_count || 0
                  })
                }}
              </span>
              <span>{{ durationText(run.duration_ms) }}</span>
            </span>
            <details
              v-if="childRunAttempts(run).length > 1"
              class="assistant-attempts"
            >
              <summary>
                {{
                  t('lensRuns.trajectoryAssistantAttempts', {
                    n: childRunAttempts(run).length
                  })
                }}
              </summary>
              <div class="assistant-attempt-list">
                <div
                  v-for="attempt in childRunAttempts(run)"
                  :key="attempt.run_uuid"
                  class="assistant-attempt-row"
                >
                  <strong>
                    {{
                      t('lensRuns.trajectoryAssistantAttempt', {
                        n: attempt.attempt
                      })
                    }}
                  </strong>
                  <span :title="attempt.task">{{ attempt.task }}</span>
                  <span>{{ progressStatusLabel(attempt.status) }}</span>
                  <span>
                    {{
                      t('lensRuns.trajectoryAssistantEvents', {
                        n: attempt.event_count || 0
                      })
                    }}
                  </span>
                  <span>{{ durationText(attempt.duration_ms) }}</span>
                </div>
              </div>
            </details>
          </article>
        </div>
      </section>

      <div class="trajectory-toolbar" role="toolbar">
        <div class="toolbar-actions">
          <button
            v-for="item in categoryOptions"
            :key="item.value"
            type="button"
            class="toolbar-toggle"
            :aria-pressed="category === item.value"
            @click="setCategory(item.value)"
          >
            {{ item.label }}
            <span class="toolbar-count">{{ item.count }}</span>
          </button>
          <span class="toolbar-separator" aria-hidden="true" />
          <button
            type="button"
            class="toolbar-toggle"
            :disabled="collapsibleCallIds.length === 0"
            :aria-pressed="allCallsCollapsed"
            :aria-label="
              collapsibleCallIds.length === 0
                ? t('lensRuns.trajectoryNoCollapsibleCalls')
                : allCallsCollapsed
                  ? t('lensRuns.trajectoryExpandAllCalls')
                  : t('lensRuns.trajectoryCollapseAllCalls')
            "
            :title="
              collapsibleCallIds.length === 0
                ? t('lensRuns.trajectoryNoCollapsibleCalls')
                : allCallsCollapsed
                  ? t('lensRuns.trajectoryExpandAllCalls')
                  : t('lensRuns.trajectoryCollapseAllCalls')
            "
            @click="toggleAllCalls"
          >
            <span class="toolbar-glyph" aria-hidden="true">
              {{ allCallsCollapsed ? '⊞' : '⊟' }}
            </span>
            {{ t('lensRuns.trajectoryCalls') }}
          </button>
          <button
            type="button"
            class="toolbar-toggle"
            :aria-pressed="showSnapshots"
            :aria-label="
              showSnapshots
                ? t('lensRuns.trajectoryHideSnapshots')
                : t('lensRuns.trajectoryShowSnapshots')
            "
            :title="
              showSnapshots
                ? t('lensRuns.trajectoryHideSnapshots')
                : t('lensRuns.trajectoryShowSnapshots')
            "
            @click="showSnapshots = !showSnapshots"
          >
            <span class="toolbar-glyph" aria-hidden="true">
              {{ showSnapshots ? '⊞' : '⊟' }}
            </span>
            {{ t('lensRuns.trajectorySnapshots') }}
          </button>
        </div>
        <div class="toolbar-search">
          <Search :size="11" class="search-icon" aria-hidden="true" />
          <input
            v-model="query"
            data-testid="trajectory-search"
            class="search-input"
            type="search"
            :placeholder="t('lensRuns.trajectorySearch')"
          />
        </div>
      </div>

      <TrajectoryTimeline
        v-if="events.length"
        :lanes="timelineLanes"
        :boundaries="groupBoundaries"
        :selected-sequence="selectedEvent ? selectedEvent.sequence : null"
        :range="timelineRange"
        data-testid="trajectory-time-overview"
        @range-change="timelineRange = $event"
        @select-event="onTimelineSelect"
      />
      <div
        v-if="rows.length"
        data-testid="trajectory-ledger"
        class="trajectory-split"
        :class="{ 'trajectory-split-resizing': resizeActive }"
        ref="splitRef"
      >
        <div
          ref="tablePaneRef"
          class="table-pane sl-scrollbar"
          @scroll="handleTrajectoryScroll"
        >
          <table class="trajectory-table">
            <thead>
              <tr>
                <th class="span-header">Span</th>
                <th class="status-header">Status</th>
                <th class="duration-header">Duration</th>
                <th class="tokens-header">Tokens</th>
                <th class="waterfall-header">Waterfall</th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="(row, index) in rows"
                :key="trajectoryEventKey(row.event)"
                class="ledger-row"
                :class="{
                  'turn-start-row': index === 0,
                  'hierarchy-row': row.hasChildren
                }"
                :data-turn-start="index === 0 || undefined"
                :data-turn-end="index === rows.length - 1 || undefined"
                :data-selected="isSelected(row.event) || undefined"
                :data-error="isErrorRow(row) || undefined"
                :style="rowIndentStyle(row)"
                @click="selectEvent(row.event)"
                @keydown.enter="selectEvent(row.event)"
              >
                <td class="event-cell">
                  <span class="turn-rail" aria-hidden="true" />
                  <span
                    v-if="index === 0"
                    class="request-dot"
                    aria-hidden="true"
                  />
                  <span v-if="row.stepNumber !== null" class="seq">
                    #{{ row.stepNumber }}
                  </span>
                  <button
                    v-if="row.hasChildren"
                    type="button"
                    class="row-expand"
                    :aria-label="t('lensRuns.trajectoryToggle')"
                    :aria-expanded="!row.isCollapsed"
                    @click.stop="toggleSpan(row.span.id)"
                  >
                    <ChevronRight
                      v-if="collapsed.has(row.span.id)"
                      :size="14"
                    />
                    <ChevronDown v-else :size="14" />
                  </button>
                  <span v-else class="row-expand-spacer" aria-hidden="true" />
                  <span class="kind-tag" :class="tagClass(row.event)">
                    <span class="kind-tag-label">{{
                      kindLabel(row.event)
                    }}</span>
                  </span>
                  <span
                    v-if="row.event.trace_run_role === 'child'"
                    class="assistant-tag"
                  >
                    {{ row.event.assistant_name || 'Subagent' }}
                  </span>
                  <span class="content-text">
                    <span
                      class="content-title"
                      :class="{ 'content-title-error': isErrorRow(row) }"
                    >
                      {{ rowTitle(row) }}
                    </span>
                    <span v-if="rowSummary(row)" class="content-summary">
                      {{ rowSummary(row) }}
                    </span>
                  </span>
                  <span v-if="row.span.plugin" class="plugin-chip">
                    {{ row.span.plugin }}
                  </span>
                  <span
                    v-if="row.span.skill"
                    class="plugin-chip plugin-chip-skill"
                  >
                    {{ row.span.skill }}
                  </span>
                  <span v-if="row.span.events.length > 1" class="content-count">
                    {{
                      t('lensRuns.trajectoryDetailEventsCount', {
                        n: row.span.events.length
                      })
                    }}
                  </span>
                </td>
                <td class="status-cell">
                  <span :class="rowStatusClass(row)">{{
                    rowStatusLabel(row)
                  }}</span>
                </td>
                <td class="duration-cell">
                  <span v-if="row.showDuration">{{
                    durationText(row.displayDurationMs)
                  }}</span>
                </td>
                <td
                  class="tokens-cell"
                  :title="
                    row.showTokens
                      ? tokenDetailText(row.displayTokens)
                      : undefined
                  "
                >
                  <span v-if="row.showTokens">{{
                    tokenText(row.displayTokens)
                  }}</span>
                </td>
                <td class="waterfall-cell">
                  <span
                    v-if="row.showDuration"
                    class="waterfall-track"
                    aria-hidden="true"
                  >
                    <span
                      class="waterfall-bar"
                      :class="tagClass(row.event)"
                      :data-status="row.span.status"
                      :style="waterfallStyle(row)"
                    />
                  </span>
                </td>
              </tr>
            </tbody>
          </table>
        </div>

        <div
          v-if="selectedEvent"
          class="trajectory-resize-handle"
          role="separator"
          aria-label="Resize inspector"
          aria-orientation="vertical"
          tabindex="0"
          @pointerdown="startInspectorResize"
          @pointermove="resizeInspector"
          @lostpointercapture="finishInspectorResize"
          @keydown="resizeInspectorWithKeyboard"
        />

        <aside
          v-if="selectedEvent"
          ref="inspectorRef"
          class="trajectory-inspector"
          data-testid="trajectory-inspector"
          :style="inspectorStyle"
        >
          <div class="inspector-header">
            <div class="inspector-title">
              <span class="inspector-dot" aria-hidden="true" />
              <span class="inspector-name">{{
                spanLabel(selectedEvent.span) || eventTitle(selectedEvent)
              }}</span>
            </div>
            <div class="inspector-header-meta">
              <span v-if="selectedStepNumber" class="inspector-sequence">
                #{{ selectedStepNumber }}
              </span>
              <span class="kind-tag" :class="tagClass(selectedEvent)">
                {{ kindLabel(selectedEvent) }}
              </span>
            </div>
            <button
              type="button"
              class="inspector-close"
              aria-label="Close"
              @click="selectedEvent = null"
            >
              ×
            </button>
          </div>
          <div class="inspector-tabs" role="tablist">
            <button
              v-for="tab in inspectorTabs"
              :key="tab.id"
              type="button"
              role="tab"
              :aria-selected="inspectorTab === tab.id"
              class="inspector-tab"
              :class="{ 'inspector-tab-active': inspectorTab === tab.id }"
              @click="inspectorTab = tab.id"
            >
              {{ tab.label }}
            </button>
          </div>
          <div class="inspector-body">
            <template v-if="inspectorTab === 'summary'">
              <div class="inspector-event-card inspector-hero-card">
                <div class="inspector-event-card-title">
                  {{
                    spanLabel(selectedEvent.span) || eventTitle(selectedEvent)
                  }}
                </div>
                <div
                  v-if="spanSummary(selectedEvent.span)"
                  class="inspector-event-card-summary"
                >
                  {{ spanSummary(selectedEvent.span) }}
                </div>
                <p
                  v-if="selectedEvent.span?.diagnostics?.length"
                  class="diagnostic-message"
                >
                  {{ diagnosticText(selectedEvent.span) }}
                </p>
                <dl
                  v-if="selectedEvent.span?.category === 'run'"
                  class="overview"
                >
                  <div>
                    <dt>{{ t('lensRuns.trajectoryLifecycle') }}</dt>
                    <dd>{{ stateLabel(selectedEvent.span.lifecycle) }}</dd>
                  </div>
                  <div>
                    <dt>{{ t('lensRuns.trajectoryOutcome') }}</dt>
                    <dd>{{ stateLabel(selectedEvent.span.outcome) }}</dd>
                  </div>
                  <div>
                    <dt>{{ t('lensRuns.trajectoryHealth') }}</dt>
                    <dd>
                      {{
                        stateLabel(
                          selectedEvent.span.health === 'failed'
                            ? 'failed'
                            : selectedEvent.span.hasWarnings
                              ? 'degraded'
                              : selectedEvent.span.health
                        )
                      }}
                    </dd>
                  </div>
                </dl>
                <div class="inspector-event-chips">
                  <span
                    v-if="selectedEvent.attempt != null"
                    class="inspector-chip"
                  >
                    {{
                      t('lensRuns.trajectoryDetailAttempt', {
                        n: selectedEvent.attempt
                      })
                    }}
                  </span>
                  <span
                    v-if="selectedEvent.span?.events?.length > 1"
                    class="inspector-chip"
                  >
                    {{
                      t('lensRuns.trajectoryDetailEventsCount', {
                        n: selectedEvent.span.events.length
                      })
                    }}
                  </span>
                  <span v-if="selectedEvent.span?.plugin" class="plugin-chip">
                    {{ selectedEvent.span.plugin }}
                  </span>
                  <span
                    v-if="selectedEvent.span?.skill"
                    class="plugin-chip plugin-chip-skill"
                  >
                    {{ selectedEvent.span.skill }}
                  </span>
                </div>
              </div>
              <div class="inspector-kpi-grid">
                <div class="inspector-kpi">
                  <span>{{ t('lensRuns.trajectoryDetailStatus') }}</span>
                  <strong :class="rowStatusClass({ span: selectedEvent.span })">
                    {{ eventLifecycleLabel(selectedEvent) }}
                  </strong>
                </div>
                <div
                  v-if="selectedEvent.span?.recordType === 'call'"
                  class="inspector-kpi"
                >
                  <span>{{ t('lensRuns.trajectoryDetailDuration') }}</span>
                  <strong>{{
                    durationText(eventDuration(selectedEvent))
                  }}</strong>
                </div>
                <div
                  v-if="selectedEvent.span?.totalTokens != null"
                  class="inspector-kpi"
                >
                  <span>{{ t('lensRuns.totalTokens') }}</span>
                  <strong>{{ tokenText(selectedEvent.span) }}</strong>
                </div>
                <div class="inspector-kpi">
                  <span>{{ t('lensRuns.trajectoryEvents') }}</span>
                  <strong>{{ selectedEvent.span?.events?.length || 1 }}</strong>
                </div>
              </div>
              <section class="execution-context-card">
                <div class="execution-context-heading">
                  <span class="overview-heading">
                    {{ t('lensRuns.trajectoryDetailExecutionContext') }}
                  </span>
                </div>
                <div
                  class="execution-path"
                  :aria-label="t('lensRuns.trajectoryDetailExecutionContext')"
                >
                  <template
                    v-for="(item, index) in executionPath(selectedEvent)"
                    :key="item.id || `${item.label}-${index}`"
                  >
                    <span v-if="index > 0" class="execution-path-separator">
                      ›
                    </span>
                    <button
                      v-if="index < executionPath(selectedEvent).length - 1"
                      type="button"
                      class="execution-path-item execution-path-link"
                      @click="selectAncestor(item.id)"
                    >
                      {{ item.label }}
                    </button>
                    <span
                      v-else
                      class="execution-path-item"
                      :class="{
                        'execution-path-current':
                          index === executionPath(selectedEvent).length - 1
                      }"
                    >
                      {{ item.label }}
                    </span>
                  </template>
                </div>
              </section>
              <dl class="overview inspector-summary-details">
                <div>
                  <dt>
                    {{
                      t(
                        selectedEvent.span?.recordType === 'call'
                          ? 'lensRuns.trajectoryDetailStarted'
                          : 'lensRuns.trajectoryDetailTime'
                      )
                    }}
                  </dt>
                  <dd class="execution-time-value">
                    <strong>{{ eventTimeLabel(selectedEvent) }}</strong>
                    <span>{{ eventRelativeTime(selectedEvent) }}</span>
                  </dd>
                </div>
                <div v-if="selectedEvent.span?.recordType === 'call'">
                  <dt>{{ t('lensRuns.trajectoryDetailEnded') }}</dt>
                  <dd class="execution-time-value">
                    <strong>{{ eventTimeLabel(selectedEvent, true) }}</strong>
                    <span>{{ eventRelativeTime(selectedEvent, true) }}</span>
                  </dd>
                </div>
              </dl>
              <details class="technical-details">
                <summary>
                  {{ t('lensRuns.trajectoryDetailTechnical') }}
                </summary>
                <button
                  type="button"
                  class="technical-copy"
                  @click="copyTechnicalDetails"
                >
                  {{ t('lensRuns.trajectoryDetailCopy') }}
                </button>
                <dl
                  class="overview inspector-summary-details technical-details-grid"
                >
                  <div>
                    <dt>{{ t('lensRuns.trajectoryDetailEventType') }}</dt>
                    <dd class="mono wrap-value">
                      {{ selectedEvent.event_type }}
                    </dd>
                  </div>
                  <div>
                    <dt>{{ t('lensRuns.trajectoryDetailSequence') }}</dt>
                    <dd class="mono">#{{ selectedEvent.sequence }}</dd>
                  </div>
                  <div
                    v-if="selectedEvent.span?.callId || selectedEvent.call_id"
                  >
                    <dt>{{ t('lensRuns.trajectoryDetailCall') }}</dt>
                    <dd class="mono wrap-value">
                      {{ selectedEvent.span?.callId || selectedEvent.call_id }}
                    </dd>
                  </div>
                  <div
                    v-if="
                      selectedEvent.span?.parentCallId ||
                      selectedEvent.parent_call_id
                    "
                  >
                    <dt>{{ t('lensRuns.trajectoryDetailParent') }}</dt>
                    <dd class="mono wrap-value">
                      {{
                        selectedEvent.span?.parentCallId ||
                        selectedEvent.parent_call_id
                      }}
                    </dd>
                  </div>
                </dl>
              </details>
            </template>
            <template v-else-if="inspectorTab === 'data'">
              <section
                v-if="
                  inspectorInput(selectedEvent) !== undefined ||
                  inspectorOutput(selectedEvent) !== undefined
                "
                class="overview-section inspector-io-section"
              >
                <h4 class="overview-heading">
                  {{ dataTabLabel(selectedEvent) }}
                </h4>
                <div
                  v-if="inspectorInput(selectedEvent) !== undefined"
                  class="inspector-data-block"
                  :title="inspectorValue(inspectorInput(selectedEvent))"
                >
                  <span class="inspector-data-label">
                    {{ t('lensRuns.trajectoryDetailInput') }}
                  </span>
                  <TrajectoryValue :value="inspectorInput(selectedEvent)" />
                </div>
                <div
                  v-if="inspectorOutput(selectedEvent) !== undefined"
                  class="inspector-data-block"
                  :title="inspectorValue(inspectorOutput(selectedEvent))"
                >
                  <span class="inspector-data-label">
                    {{ t('lensRuns.trajectoryDetailOutput') }}
                  </span>
                  <TrajectoryValue :value="inspectorOutput(selectedEvent)" />
                </div>
              </section>
              <section
                v-if="
                  selectedEvent.span?.end &&
                  inspectorOutput(selectedEvent) === undefined
                "
                class="overview-section"
              >
                <h4 class="overview-heading">
                  {{ t('lensRuns.trajectoryDetailEndResult') }}
                </h4>
                <div class="inspector-data-block">
                  <span class="inspector-data-label">
                    {{ selectedEvent.span.end.event_type }}
                  </span>
                  <TrajectoryValue
                    :value="
                      inspectorOutput(selectedEvent.span.end) ??
                      selectedEvent.span.end.payload
                    "
                  />
                </div>
              </section>
              <section
                v-if="selectedEvent.span?.events?.length > 1"
                class="overview-section"
              >
                <h4 class="overview-heading">
                  {{ t('lensRuns.trajectoryDetailEvents') }}
                </h4>
                <ol class="span-event-list">
                  <li
                    v-for="event in selectedEvent.span.events"
                    :key="trajectoryEventKey(event)"
                    class="span-event-row"
                  >
                    <span class="span-event-seq">#{{ event.sequence }}</span>
                    <span class="span-event-type">
                      <strong>{{ eventTypeLabel(event) }}</strong>
                      <small>{{ event.event_type }}</small>
                    </span>
                    <time class="span-event-time">{{
                      relativeEventTime(event, selectedEvent.span)
                    }}</time>
                  </li>
                </ol>
              </section>
              <div
                v-if="!hasStructuredData(selectedEvent)"
                class="inspector-json-card"
              >
                <JsonTree :data="selectedEvent.payload" :indent="8" />
              </div>
            </template>
            <template v-else-if="inspectorTab === 'timing'">
              <section class="overview-section">
                <h4 class="overview-heading">
                  {{ t('lensRuns.trajectoryInspectorTiming') }}
                </h4>
                <dl class="overview">
                  <div v-if="selectedEvent.span?.recordType === 'call'">
                    <dt>{{ t('lensRuns.trajectoryDetailDuration') }}</dt>
                    <dd>{{ durationText(eventDuration(selectedEvent)) }}</dd>
                  </div>
                  <div v-else>
                    <dt>{{ t('lensRuns.trajectoryDetailTime') }}</dt>
                    <dd>{{ eventTimeLabel(selectedEvent) }}</dd>
                  </div>
                  <div
                    v-if="
                      selectedEvent.span?.ttftMs != null ||
                      selectedEvent.payload?.ttft_ms != null
                    "
                  >
                    <dt>{{ t('lensRuns.ttft') }}</dt>
                    <dd>
                      {{ ttftText(selectedEvent) }}
                    </dd>
                  </div>
                  <div v-if="selectedEvent.span?.usageSource">
                    <dt>{{ t('lensRuns.trajectoryUsageSource') }}</dt>
                    <dd>
                      {{
                        t(
                          `lensRuns.trajectorySource_${selectedEvent.span.usageSource}`
                        )
                      }}
                    </dd>
                  </div>
                  <div v-if="selectedEvent.span?.totalTokens != null">
                    <dt>{{ t('lensRuns.totalTokens') }}</dt>
                    <dd>
                      {{ formatTokenCount(selectedEvent.span.totalTokens) }}
                    </dd>
                  </div>
                </dl>
                <div
                  v-if="tokenBreakdown(selectedEvent.span).length"
                  class="token-usage-list"
                >
                  <div
                    v-for="item in tokenBreakdown(selectedEvent.span)"
                    :key="item.key"
                    class="token-usage-row"
                  >
                    <div class="token-usage-label">
                      <span
                        class="token-usage-dot"
                        :class="`token-usage-dot-${item.key}`"
                      />
                      <span>{{ item.label }}</span>
                    </div>
                    <strong>{{ formatTokenCount(item.value) }}</strong>
                    <div class="token-usage-track">
                      <span
                        class="token-usage-fill"
                        :class="`token-usage-fill-${item.key}`"
                        :style="{ width: `${item.percent}%` }"
                      />
                    </div>
                  </div>
                </div>
              </section>
            </template>
          </div>
        </aside>
      </div>

      <p v-else class="trajectory-empty" data-testid="trajectory-empty">
        {{ t('lensRuns.noTimeline') }}
      </p>

      <div v-if="hasMore" class="trajectory-load-more">
        <BaseButton
          variant="outline"
          size="sm"
          :loading="loading"
          @click="loadMore"
        >
          {{ t('lensRuns.trajectoryLoadMore') }}
        </BaseButton>
      </div>
    </template>
  </section>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { ChevronDown, ChevronRight, Search } from '@lucide/vue'
import { useI18n } from 'vue-i18n'
import {
  getAdminRunTrajectory,
  listDataSources,
  streamAdminRunTrajectory
} from '@/api/lens'
import { useToast } from '@/composables/useToast'
import { extractErrorMessage } from '@/utils/api'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseLoading from '@/components/ui/BaseLoading.vue'
import JsonTree from '@/components/ui/JsonTree.vue'
import TrajectoryValue from './TrajectoryValue.vue'
import TrajectoryTimeline from './TrajectoryTimeline.vue'
import {
  ACTIVE_TRAJECTORY_RUN_STATUSES,
  applyTrajectoryStreamUpdate,
  buildTimelineGroups,
  buildTrajectoryRows,
  buildTrajectorySpans,
  childRunAttempts,
  childRunProgress,
  clampInspectorWidth,
  eventCategory,
  groupTrajectoryRows,
  mergeTrajectoryEvents,
  shouldKeepTrajectoryStream,
  trajectoryEventKey,
  trajectoryStepNumber,
  workspaceRelativePath
} from './runTrajectory'

const props = defineProps({
  runUuid: { type: String, default: '' },
  assistantName: { type: String, default: '' },
  active: { type: Boolean, default: false },
  runStatus: { type: String, default: '' },
  workspaceRoot: { type: String, default: '' }
})
const emit = defineEmits(['run-update'])

const { t, locale } = useI18n()
const { showError, showSuccess } = useToast()

const events = ref([])
const summary = ref({})
const loading = ref(false)
const hasMore = ref(false)
const nextAfterSequence = ref(0)
const query = ref('')
const category = ref('all')
const selectedEvent = ref(null)
const collapsed = ref(new Set())
const timelineRange = ref(null)
const inspectorTab = ref('summary')
const tablePaneRef = ref(null)
const splitRef = ref(null)
const inspectorRef = ref(null)
const inspectorWidth = ref(null)
const resizeActive = ref(false)
const showSnapshots = ref(false)
const streamCursor = ref('')
const streamRevision = ref('')
const streamSequence = ref(0)
const streamState = ref('idle')
const pendingNewEventCount = ref(0)
const awaitingStreamDone = ref(false)

let filterTimer = null
let fallbackTimer = null
let finalSyncTimer = null
let streamController = null
let reconnectDelay = 1000
let finalSyncDelay = 1000
let requestId = 0
let resizeOriginX = 0
let resizeOriginWidth = 0

const inspectorStyle = computed(() =>
  inspectorWidth.value === null
    ? undefined
    : {
        '--trajectory-inspector-width': `${inspectorWidth.value}px`
      }
)

const KIND_BY_CATEGORY = {
  model: 'model',
  assistant: 'model',
  reasoning: 'model',
  tool: 'tool',
  subtool: 'subtool',
  user: 'user',
  context: 'context',
  request: 'context',
  compaction: 'compacted',
  compacted: 'compacted',
  retry: 'retry',
  checkpoint: 'checkpoint',
  cancelled: 'cancelled',
  interrupted: 'cancelled',
  system: 'system',
  run: 'system',
  step: 'system'
}

const KIND_LABEL = {
  run: 'RUN',
  system: 'SYSTEM',
  user: 'USER',
  context: 'CONTEXT',
  compacted: 'COMPACTED',
  model: 'ASSISTANT',
  tool: 'TOOL',
  subtool: 'SUBTOOL',
  retry: 'RETRY',
  checkpoint: 'CHECKPOINT',
  cancelled: 'CANCELLED',
  plugin: 'PLUGIN',
  skill: 'SKILL',
  stage: 'STAGE',
  gate: 'GATE',
  evidence: 'EVIDENCE',
  agent: 'AGENT'
}

const SPAN_KIND_BY_BASE = {
  'deepagents.runtime': 'run',
  'deepagents.runtime.stage': 'stage',
  'deepagents.decision.gate': 'gate',
  'deepagents.evidence.review': 'evidence',
  'deepagents.evidence.verified': 'evidence',
  'deepagents.evidence.convergence': 'evidence',
  'deepagents.agent.create': 'agent',
  'deepagents.agent.invoke': 'agent'
}

const SPAN_LABEL_KEYS = {
  run: 'trajectoryStepRuntime',
  'deepagents.runtime': 'trajectoryStepRuntime',
  'deepagents.runtime.stage': 'trajectoryStepRuntimeStage',
  'deepagents.agent.invoke': 'trajectoryStepAgentInvoke',
  'deepagents.agent.create': 'trajectoryStepAgentCreate',
  'deepagents.offload.configured': 'trajectoryStepOffload',
  'deepagents.decision.gate': 'trajectoryStepDecisionGate',
  'deepagents.evidence.verified': 'trajectoryStepEvidenceVerified',
  'deepagents.evidence.convergence': 'trajectoryStepEvidenceConvergence',
  'deepagents.evidence.review': 'trajectoryStepEvidenceReview',
  'deepagents.capability.warning': 'trajectoryStepCapabilityWarning',
  'deepagents.stream.recovering': 'trajectoryStepStreamRecovering',
  'deepagents.plan.ready': 'trajectoryStepPlanReady',
  'deepagents.summarization.enabled': 'trajectoryStepSummarization',
  'deepagents.summarization.compacted': 'trajectoryStepSummarization',
  'resources.materialized': 'trajectoryStepResourcesMaterialized',
  'workflow.phase.changed': 'trajectoryStepPhaseChanged',
  'workflow.plan.updated': 'trajectoryStepPlanUpdated',
  'workflow.route.selected': 'trajectoryStepRouteSelected',
  'tool.plugin': 'trajectoryStepToolPlugin',
  'model.agent': 'trajectoryStepModelAgent',
  'model.control': 'trajectoryStepModelControl',
  'request.started': 'trajectoryStepRequest',
  'request.completed': 'trajectoryStepRequestDone',
  'user.message': 'trajectoryStepUserMessage',
  'run.completed': 'trajectoryStepRunCompleted',
  'checkpoint.saved': 'trajectoryStepCheckpoint',
  'system.snapshot': 'trajectoryStepSystemSnapshot',
  'tools.snapshot': 'trajectoryStepToolsSnapshot',
  'compaction.event': 'trajectoryStepCompaction',
  'compaction.completed': 'trajectoryStepCompaction'
}

const STAGE_LABEL_KEYS = {
  resources: 'trajectoryStageResources',
  model_tools: 'trajectoryStageModelTools',
  routing: 'trajectoryStageRouting'
}

const GATE_LABEL_KEYS = {
  search_needed: 'trajectoryGateSearchNeeded',
  evidence_sufficient: 'trajectoryGateEvidenceSufficient',
  answer_supported: 'trajectoryGateAnswerSupported',
  evidence_strength: 'trajectoryGateEvidenceStrength'
}

function humanizeSpanName(name) {
  return String(name)
    .replace(/^(deepagents|workflow)\./, '')
    .replace(/[._]/g, ' ')
}

function spanLabel(span) {
  const name = String(span?.name || '')
  if (!name) return ''
  const operations = {
    search_workspace: 'trajectoryOperationSearch',
    find_files: 'trajectoryOperationFind',
    read_workspace_file: 'trajectoryOperationRead',
    read_file: 'trajectoryOperationRead'
  }
  if (span.category === 'tool' || span.category === 'subtool') {
    return operations[name] ? t(`lensRuns.${operations[name]}`) : name
  }
  const toolEvent = /^tool\.(.+)\.(start|done|failed)$/.exec(name)
  if (toolEvent) {
    const phases = {
      start: 'trajectoryDetailPhaseStart',
      done: 'statusDone',
      failed: 'statusFailed'
    }
    const operation = operations[toolEvent[1]]
      ? t(`lensRuns.${operations[toolEvent[1]]}`)
      : toolEvent[1]
    return `${operation} · ${t(`lensRuns.${phases[toolEvent[2]]}`)}`
  }
  const base = name.split(' · ')[0]
  const stage = span.stage || (name.includes(' · ') ? name.split(' · ')[1] : '')
  if (base === 'deepagents.runtime.stage' && stage) {
    return STAGE_LABEL_KEYS[stage]
      ? t(`lensRuns.${STAGE_LABEL_KEYS[stage]}`)
      : humanizeSpanName(stage)
  }
  if (base === 'deepagents.decision.gate' && span.gate) {
    return GATE_LABEL_KEYS[span.gate]
      ? t(`lensRuns.${GATE_LABEL_KEYS[span.gate]}`)
      : humanizeSpanName(span.gate)
  }
  if (base === 'deepagents.agent.invoke') {
    return t('lensRuns.trajectoryStepAgentLoop')
  }
  const key = SPAN_LABEL_KEYS[base] || SPAN_LABEL_KEYS[name]
  if (key) return t(`lensRuns.${key}`)
  return humanizeSpanName(name)
}

const ACTIVE_RUN_STATUSES = ACTIVE_TRAJECTORY_RUN_STATUSES

const streamStateLabel = computed(() => {
  if (streamState.value === 'live') return 'Live'
  if (streamState.value === 'reconnecting') return 'Reconnecting'
  return 'Connecting'
})

const categoryOptions = computed(() => {
  const counts = summary.value.categories || {}
  const hiddenCategories = new Set(['checkpoint', 'system', 'user', 'run'])
  return [
    {
      value: 'all',
      label: t('lensRuns.trajectoryAll'),
      count: summary.value.event_count || 0
    },
    ...Object.entries(counts)
      .filter(([value]) => !hiddenCategories.has(value))
      .map(([value, count]) => ({ value, label: value, count }))
  ]
})

const SNAPSHOT_EVENT_TYPES = new Set(['system.snapshot', 'tools.snapshot'])

const baseEvents = computed(() => {
  if (showSnapshots.value) return events.value
  return events.value.filter(
    (event) => !SNAPSHOT_EVENT_TYPES.has(event.event_type)
  )
})

const filteredEvents = computed(() => {
  if (!timelineRange.value) return baseEvents.value
  return baseEvents.value.filter((event) => !isOutsideTimelineRange(event))
})

const trajectoryOptions = computed(() => ({
  aggregateCalls: true,
  complete:
    !hasMore.value &&
    !query.value.trim() &&
    category.value === 'all' &&
    !timelineRange.value,
  runStatuses: {
    [props.runUuid]: props.runStatus,
    ...Object.fromEntries(
      (summary.value.run_progress || []).flatMap((run) =>
        (run.attempts || [run]).map((attempt) => [
          attempt.run_uuid,
          attempt.status
        ])
      )
    )
  }
}))
const fullSpans = computed(() =>
  buildTrajectorySpans(events.value, trajectoryOptions.value)
)
const diagnosticSpans = computed(() =>
  fullSpans.value.filter(
    (span) =>
      span.diagnostics.length ||
      [
        'failed',
        'interrupted',
        'incomplete',
        'fallback',
        'timeout',
        'partial',
        'blocked',
        'degraded'
      ].includes(span.status)
  )
)
const rows = computed(() =>
  buildTrajectoryRows(
    filteredEvents.value,
    collapsed.value,
    trajectoryOptions.value
  )
)

function stateLabel(state) {
  return state ? t(`lensRuns.trajectoryState_${state}`) : '—'
}

function diagnosticText(span) {
  const messages = span.diagnostics.map((code) =>
    t(`lensRuns.trajectoryDiagnostic_${code}`)
  )
  if (span.status !== 'completed' && span.status !== 'point')
    messages.unshift(stateLabel(span.status))
  return [...new Set(messages)].join(' · ')
}

const selectedStepNumber = computed(() =>
  trajectoryStepNumber(
    selectedEvent.value?.span?.startEvent || selectedEvent.value
  )
)

const groupedRows = computed(() => groupTrajectoryRows(rows.value))

const timelineLanes = computed(() =>
  buildTimelineGroups(
    baseEvents.value,
    summary.value,
    props.assistantName || 'Parent Run',
    {
      input: t('lensRuns.trajectoryLaneInput'),
      model: t('lensRuns.trajectoryLaneModel'),
      tools: t('lensRuns.trajectoryLaneTools')
    }
  )
)

const childProgress = computed(() => childRunProgress(summary.value))

const completedChildCount = computed(
  () =>
    childProgress.value.filter((run) =>
      ['done', 'failed', 'cancelled'].includes(run.status)
    ).length
)

const PROGRESS_STATUS_KEYS = {
  awaiting_user_input: 'statusAwaitingInput',
  cancelled: 'statusCancelled',
  done: 'statusDone',
  failed: 'statusFailed',
  queued: 'statusQueued',
  running: 'statusRunning',
  streaming: 'statusRunning'
}

function progressStatusLabel(status) {
  const key = PROGRESS_STATUS_KEYS[status]
  return key ? t(`lensRuns.${key}`) : status || '—'
}

const timelineDomain = computed(() => {
  const times = []
  for (const lane of timelineLanes.value) {
    for (const step of lane.steps) {
      times.push(step.startMs, step.startMs + step.durationMs)
    }
  }
  const start = Math.min(...times)
  const end = Math.max(...times)
  return { start, end, duration: Math.max(1, end - start) }
})

const groupBoundaries = computed(() => {
  const domain = timelineDomain.value
  if (!Number.isFinite(domain.duration)) return []
  return groupedRows.value
    .map((group) => {
      const time = eventTime(group.rows[0]?.event)
      if (!Number.isFinite(time)) return null
      return { time }
    })
    .filter((boundary) => boundary !== null)
})

const collapsibleCallIds = computed(() =>
  rows.value
    .filter((row) => row.hasChildren)
    .map((row) => row.span?.id || row.event.call_id)
)

const allCallsCollapsed = computed(
  () =>
    collapsibleCallIds.value.length > 0 &&
    collapsibleCallIds.value.every((id) => collapsed.value.has(id))
)

const inspectorTabs = computed(() => [
  { id: 'summary', label: t('lensRuns.trajectoryInspectorSummary') },
  { id: 'data', label: t('lensRuns.trajectoryInspectorData') },
  { id: 'timing', label: t('lensRuns.trajectoryInspectorTiming') }
])

const runStartMs = computed(() => {
  const root = summary.value.run_progress?.find((run) => run.role === 'parent')
  return root?.started_at ? new Date(root.started_at).getTime() : null
})

function kindOf(event) {
  const span = event?.span
  const base = span ? String(span.name).split(' · ')[0] : ''
  if (SPAN_KIND_BY_BASE[base]) return SPAN_KIND_BY_BASE[base]
  if (span?.skill) return 'skill'
  if (span?.plugin) return 'plugin'
  const categoryValue = eventCategory(event)
  return KIND_BY_CATEGORY[categoryValue] || 'system'
}

function kindLabel(event) {
  return KIND_LABEL[kindOf(event)] || 'SYSTEM'
}

function tagClass(event) {
  return `tag-${kindOf(event)}`
}

function isSelected(event) {
  return (
    selectedEvent.value &&
    trajectoryEventKey(selectedEvent.value) === trajectoryEventKey(event)
  )
}

const eventTimeRange = computed(() => {
  const map = new Map()
  for (const lane of timelineLanes.value) {
    for (const step of lane.steps) {
      const start = step.startMs
      const end = start + step.durationMs
      for (const seq of step.seqs || [step.event.sequence]) {
        const previous = map.get(seq)
        if (previous) {
          previous.start = Math.min(previous.start, start)
          previous.end = Math.max(previous.end, end)
        } else {
          map.set(seq, { start, end })
        }
      }
    }
  }
  return map
})

function eventTime(event) {
  if (!event) return NaN
  if (event._ms !== undefined && Number.isFinite(event._ms)) return event._ms
  return new Date(event.timestamp).getTime()
}

function isOutsideTimelineRange(event) {
  if (!timelineRange.value) return false
  const span = eventTimeRange.value.get(event.sequence)
  if (!span) {
    const time = eventTime(event)
    if (!Number.isFinite(time)) return false
    return time < timelineRange.value.start || time > timelineRange.value.end
  }
  return (
    span.end < timelineRange.value.start || span.start > timelineRange.value.end
  )
}

const FAILED_SPAN_STATUSES = new Set(['failed'])
const WARNING_SPAN_STATUSES = new Set([
  'cancelled',
  'interrupted',
  'incomplete',
  'timeout',
  'fallback',
  'degraded',
  'partial',
  'blocked'
])

function isErrorRow(row) {
  return FAILED_SPAN_STATUSES.has(row?.span?.status)
}

function rowTitle(row) {
  return spanLabel(row?.span) || eventTitle(row?.event)
}

function joinSummaryParts(parts) {
  return parts
    .filter((part) => part !== null && part !== undefined && part !== '')
    .join(' · ')
}

function resultContent(payload) {
  const content = payload?.result?.content
  if (typeof content !== 'string') return null
  try {
    return JSON.parse(content)
  } catch {
    return null
  }
}

function evidenceSummary(payload) {
  const parts = []
  if (payload.action) parts.push(String(payload.action))
  for (const [key, value] of Object.entries(payload.verdicts || {})) {
    const mark = value === true ? '✓' : value === false ? '✗' : value
    parts.push(`${key} ${mark}`)
  }
  return parts.join(' · ')
}

function spanSummary(span, showTiming = true) {
  if (!span) return ''
  const start = span.startEvent?.payload || {}
  const end = span.endEvent?.payload || {}
  const payload = { ...start, ...end }
  const base = String(span.name).split(' · ')[0]

  if (base === 'deepagents.decision.gate') {
    const parts = []
    if (payload.value != null && payload.threshold != null) {
      parts.push(`${payload.value} / ${payload.threshold}`)
    } else if (payload.value != null) {
      parts.push(String(payload.value))
    }
    if (payload.fallback_reason) {
      parts.push(
        t('lensRuns.trajectorySummaryFallback', {
          reason: payload.fallback_reason
        })
      )
    } else if (payload.verdict) {
      parts.push(String(payload.verdict))
    }
    return parts.join(' · ')
  }
  if (base === 'tool.plugin') {
    const failed = payload.ok === false
    return joinSummaryParts([
      payload.tool,
      failed ? '✗' : '✓',
      failed ? payload.error : ''
    ])
  }
  if (
    base === 'deepagents.evidence.review' ||
    base === 'deepagents.evidence.verified'
  ) {
    return evidenceSummary(payload)
  }
  if (base === 'deepagents.evidence.convergence') {
    return joinSummaryParts([
      payload.action,
      payload.turn != null ? `turn ${payload.turn}` : ''
    ])
  }
  if (base === 'deepagents.offload.configured') {
    return payload.tool_tokens != null ? `${payload.tool_tokens} tokens` : ''
  }
  if (base === 'resources.materialized') {
    return joinSummaryParts([
      payload.skill_count != null
        ? t('lensRuns.trajectorySummarySkills', { n: payload.skill_count })
        : '',
      payload.mcp_count != null
        ? t('lensRuns.trajectorySummaryMcp', { n: payload.mcp_count })
        : ''
    ])
  }
  if (base === 'workflow.phase.changed') {
    return String(payload.payload?.phase || '')
  }
  if (base === 'workflow.route.selected') {
    const inner = payload.payload || {}
    return joinSummaryParts([inner.route, inner.intent, inner.complexity])
  }
  if (base === 'workflow.plan.updated') {
    const steps = payload.payload?.steps
    if (Array.isArray(steps)) {
      const revision = payload.payload?.revision
      return joinSummaryParts([
        revision != null ? `v${revision}` : '',
        t('lensRuns.trajectorySummarySteps', { n: steps.length })
      ])
    }
    return ''
  }
  if (base === 'deepagents.agent.create') {
    return joinSummaryParts([
      span.toolCount != null
        ? t('lensRuns.trajectorySummaryTools', { n: span.toolCount })
        : '',
      span.skillCount
        ? t('lensRuns.trajectorySummarySkills', { n: span.skillCount })
        : '',
      span.pluginToolCount
        ? t('lensRuns.trajectorySummaryPluginTools', {
            n: span.pluginToolCount
          })
        : ''
    ])
  }
  if (base === 'deepagents.agent.invoke') {
    return joinSummaryParts([
      payload.max_agent_turns != null
        ? t('lensRuns.trajectorySummaryTurns', { n: payload.max_agent_turns })
        : ''
    ])
  }
  if (base === 'deepagents.plan.ready') {
    return showTiming && payload.duration_ms != null
      ? durationText(payload.duration_ms)
      : ''
  }
  if (span.category === 'model') {
    return joinSummaryParts([
      span.totalTokens != null
        ? `${span.totalTokens.toLocaleString()} tokens`
        : '',
      showTiming && span.ttftMs != null
        ? `TTFT ${durationText(span.ttftMs)}`
        : ''
    ])
  }
  if (span.category === 'tool' || span.category === 'subtool') {
    const args = start.arguments || {}
    const result = resultContent(end) || resultContent(start)
    if (span.name === 'search_workspace') {
      const count =
        result?.count ?? result?.matches?.length ?? result?.files?.length
      return joinSummaryParts([
        args.query,
        count != null ? t('lensRuns.trajectorySummaryHits', { n: count }) : ''
      ])
    }
    if (span.name === 'find_files') {
      const count = result?.files?.length ?? result?.count
      return joinSummaryParts([
        args.pattern,
        count != null ? t('lensRuns.trajectorySummaryFiles', { n: count }) : ''
      ])
    }
    if (span.name === 'read_workspace_file' || span.name === 'read_file') {
      return result?.returned_lines != null
        ? t('lensRuns.trajectorySummaryLines', {
            n: result.returned_lines
          })
        : ''
    }
  }
  return ''
}

function rowSummary(row) {
  if (row?.span?.category === 'model') {
    if (!row.showTokens) return ''
    return spanSummary({ ...row.span, ...row.displayTokens }, row.showDuration)
  }
  return spanSummary(row?.span, row?.showDuration)
}

function rowStatusClass(row) {
  const status = row?.span?.status
  if (FAILED_SPAN_STATUSES.has(status)) return 'span-status span-status-error'
  if (row?.span?.hasWarnings || WARNING_SPAN_STATUSES.has(status))
    return 'span-status span-status-warning'
  if (status === 'completed') return 'span-status span-status-success'
  if (status === 'running') return 'span-status span-status-running'
  return 'span-status span-status-neutral'
}

function rowStatusLabel(row) {
  const status = row?.span?.status
  if (status === 'completed' && row?.span?.hasWarnings)
    return stateLabel('degraded')
  return status === 'point' ? '—' : stateLabel(status)
}

function waterfallStyle(row) {
  return {
    left: `${row?.waterfall?.left ?? 0}%`,
    width: `${row?.waterfall?.width ?? 0}%`
  }
}

function eventTitle(event) {
  return event?.payload?.name || event?.payload?.model_ref || event?.event_type
}

function eventDuration(event) {
  const span = event?.span
  if (!span) return event?.payload?.duration_ms
  if (span.recordType !== 'call' || !span.startedAt || !span.finishedAt)
    return null
  const start = new Date(span.startedAt).getTime()
  const end = new Date(span.finishedAt).getTime()
  return Number.isFinite(start) && Number.isFinite(end) && end >= start
    ? end - start
    : null
}

function spanPayloads(event) {
  return [
    event?.payload,
    event?.span?.startEvent?.payload,
    event?.span?.endEvent?.payload
  ].filter(Boolean)
}

function payloadValue(event, keys) {
  for (const payload of spanPayloads(event)) {
    for (const key of keys) {
      if (payload[key] !== undefined && payload[key] !== null) {
        return payload[key]
      }
    }
  }
  return undefined
}

function hasStructuredData(event) {
  return Boolean(
    inspectorInput(event) !== undefined ||
      inspectorOutput(event) !== undefined ||
      event?.span?.end ||
      event?.span?.events?.length > 1
  )
}

function dataTabLabel(event) {
  if (
    inspectorInput(event) !== undefined ||
    inspectorOutput(event) !== undefined
  ) {
    return t('lensRuns.trajectoryInspectorInputOutput')
  }
  if (event?.span?.events?.length > 1) {
    return t('lensRuns.trajectoryInspectorEvents')
  }
  return t('lensRuns.trajectoryInspectorData')
}

function modelRequestData(event) {
  if (event?.span?.category !== 'model') return undefined
  const payload = event?.span?.startEvent?.payload || event?.payload || {}
  const fields = {}
  for (const key of [
    'model_ref',
    'messages',
    'tools',
    'max_tokens',
    'temperature',
    'tool_choice',
    'reasoning_effort'
  ]) {
    if (payload[key] !== undefined && payload[key] !== null) {
      fields[key] = payload[key]
    }
  }
  return Object.keys(fields).length ? fields : undefined
}

function inspectorInput(event) {
  return (
    modelRequestData(event) ??
    payloadValue(event, ['arguments', 'input', 'params', 'request'])
  )
}

function inspectorOutput(event) {
  return payloadValue(event, ['result', 'output', 'response'])
}

function relativizeValue(value, depth = 0) {
  if (depth > 6) return value
  if (typeof value === 'string') {
    if (!value.includes('/')) return value
    return workspaceRelativePath(value, {
      datasourceNames: datasourceNames.value,
      workspaceRoot: props.workspaceRoot
    })
  }
  if (Array.isArray(value)) {
    return value.map((item) => relativizeValue(item, depth + 1))
  }
  if (value && typeof value === 'object') {
    const output = {}
    for (const [key, item] of Object.entries(value)) {
      output[key] = relativizeValue(item, depth + 1)
    }
    return output
  }
  return value
}

function inspectorValue(value) {
  const normalized = relativizeValue(value)
  if (typeof normalized === 'string') return normalized.slice(0, 2400)
  try {
    return JSON.stringify(normalized, null, 2).slice(0, 2400)
  } catch {
    return String(normalized)
  }
}

function eventTypeLabel(event) {
  const type = String(event?.event_type || '')
  const status = type.split('.').pop()
  if (['started', 'start'].includes(status)) return 'Started'
  if (['completed', 'done'].includes(status)) return 'Completed'
  if (['failed', 'cancelled', 'interrupted'].includes(status)) {
    return status[0].toUpperCase() + status.slice(1)
  }
  return type.split('.').slice(-1)[0] || 'Event'
}

function eventLifecycleLabel(event) {
  const span = event?.span
  if (span?.recordType !== 'call')
    return t('lensRuns.trajectoryDetailInstantEvent')
  if (span.finishedAt || span.status === 'incomplete')
    return rowStatusLabel({ span })
  return t(
    `lensRuns.${ACTIVE_RUN_STATUSES.has(props.runStatus) ? 'statusRunning' : 'trajectoryDetailEndMissing'}`
  )
}

function executionPath(event) {
  const span = event?.span
  const ancestors = span?.ancestors || []
  const path = ancestors.map((ancestor) => ({
    id: ancestor.id,
    label: spanLabel(ancestor)
  }))
  path.push({
    id: span?.id || event?.event_id || event?.sequence,
    label: spanLabel(span) || eventTitle(event)
  })
  return path
}

function absoluteTimeLabel(value) {
  if (!value) return t('lensRuns.trajectoryDetailNotRecorded')
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return String(value)
  try {
    return date.toLocaleString(locale.value, {
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
      fractionalSecondDigits: 3,
      timeZoneName: 'short',
      hour12: false
    })
  } catch {
    return `${date.toLocaleString([], { hour12: false })}.${String(date.getMilliseconds()).padStart(3, '0')}`
  }
}

function eventTimestamp(event, end = false) {
  if (event?.span?.recordType === 'call') {
    return end ? event.span.finishedAt : event.span.startedAt
  }
  return end ? null : event?.timestamp
}

function eventTimeLabel(event, end = false) {
  const timestamp = eventTimestamp(event, end)
  if (!timestamp && end && ACTIVE_RUN_STATUSES.has(props.runStatus)) {
    return t('lensRuns.trajectoryDetailAwaitingEnd')
  }
  return absoluteTimeLabel(timestamp)
}

function relativeDurationLabel(value) {
  if (!Number.isFinite(value)) return '-'
  return `+${durationText(Math.max(0, value))}`
}

function eventRelativeTime(event, end = false) {
  const timestamp = eventTimestamp(event, end)
  if (!timestamp) return ''
  const current = new Date(timestamp).getTime()
  if (!Number.isFinite(current) || !Number.isFinite(runStartMs.value)) return ''
  return t('lensRuns.trajectoryDetailFromRunStart', {
    value: relativeDurationLabel(current - runStartMs.value)
  })
}

function relativeEventTime(event, span) {
  const start = eventTime(span?.startEvent || event)
  const current = eventTime(event)
  if (!Number.isFinite(start) || !Number.isFinite(current))
    return timeText(event?.timestamp)
  return `+${durationText(Math.max(0, current - start))}`
}

function ttftText(event) {
  const value = event?.span?.ttftMs ?? event?.payload?.ttft_ms
  return value == null ? '-' : durationText(value)
}

function tokenBreakdown(span) {
  if (!span) return []
  const items = [
    {
      key: 'input',
      label: t('lensRuns.promptTokens'),
      value: span.inputTokens
    },
    {
      key: 'output',
      label: t('lensRuns.completionTokens'),
      value: span.outputTokens
    },
    {
      key: 'cached',
      label: t('lensRuns.cachedTokens'),
      value: span.cachedTokens
    },
    {
      key: 'cache-creation',
      label: t('lensRuns.cacheCreationTokens'),
      value: span.cacheCreationTokens
    },
    {
      key: 'reasoning',
      label: t('lensRuns.reasoningTokens'),
      value: span.reasoningTokens
    }
  ].filter((item) => item.value != null)
  const total = Math.max(
    1,
    span.totalTokens ||
      items.reduce((sum, item) => sum + Number(item.value || 0), 0)
  )
  return items.map((item) => ({
    ...item,
    percent: Math.min(100, Math.max(4, (Number(item.value) / total) * 100))
  }))
}

function durationText(value) {
  if (value === null || value === undefined) return '-'
  if (value < 1000) return `${Math.round(value)}ms`
  return `${(value / 1000).toFixed(1)}s`
}

function formatTokenCount(value) {
  if (value === null || value === undefined) return '-'
  return Number(value).toLocaleString()
}

function tokenText(span) {
  if (!span) return '-'
  if (span.totalTokens != null) return formatTokenCount(span.totalTokens)
  const parts = []
  if (span.inputTokens != null)
    parts.push(`in ${formatTokenCount(span.inputTokens)}`)
  if (span.outputTokens != null) {
    parts.push(`out ${formatTokenCount(span.outputTokens)}`)
  }
  return parts.join(' / ') || '-'
}

function tokenDetailText(span) {
  if (!span) return ''
  return joinSummaryParts([
    span.inputTokens != null
      ? `${t('lensRuns.promptTokens')}: ${formatTokenCount(span.inputTokens)}`
      : '',
    span.outputTokens != null
      ? `${t('lensRuns.completionTokens')}: ${formatTokenCount(span.outputTokens)}`
      : '',
    span.cachedTokens != null
      ? `${t('lensRuns.cachedTokens')}: ${formatTokenCount(span.cachedTokens)}`
      : '',
    span.cacheCreationTokens != null
      ? `${t('lensRuns.cacheCreationTokens')}: ${formatTokenCount(span.cacheCreationTokens)}`
      : '',
    span.reasoningTokens != null
      ? `${t('lensRuns.reasoningTokens')}: ${formatTokenCount(span.reasoningTokens)}`
      : '',
    span.ttftMs != null ? `TTFT: ${durationText(span.ttftMs)}` : ''
  ])
}

function timeText(value) {
  if (!value) return '-'
  const date = new Date(value)
  return Number.isNaN(date.getTime())
    ? String(value)
    : date.toLocaleTimeString([], { hour12: false })
}

function selectEvent(event) {
  selectedEvent.value = event
}

async function selectAncestor(id) {
  const span = fullSpans.value.find((item) => item.id === id)
  if (!span) return
  timelineRange.value = null
  const next = new Set(collapsed.value)
  for (const ancestor of span.ancestors) next.delete(ancestor.id)
  collapsed.value = next
  selectEvent({ ...span.startEvent, span })
  await nextTick()
  scrollSelectedIntoView()
}

async function copyTechnicalDetails() {
  const event = selectedEvent.value
  if (!event) return
  const span = event.span
  try {
    await navigator.clipboard.writeText(
      JSON.stringify(
        {
          event_type: event.event_type,
          sequence: event.sequence,
          call_id: span?.callId || event.call_id || null,
          parent_call_id: span?.parentCallId || event.parent_call_id || null,
          timestamp: event.timestamp,
          started_at: span?.startedAt || null,
          finished_at: span?.finishedAt || null
        },
        null,
        2
      )
    )
    showSuccess(t('lensRuns.trajectoryDetailCopied'))
  } catch {
    showError(t('lensRuns.trajectoryDetailCopyFailed'))
  }
}

function rowIndentStyle(row) {
  const depth = Math.min(Math.max(Number(row.depth) || 0, 0), 8)
  return { '--trajectory-indent': `${depth * 18}px` }
}

function setInspectorWidth(width) {
  const splitWidth = splitRef.value?.clientWidth
  if (!splitWidth) return
  inspectorWidth.value = clampInspectorWidth(splitWidth, width)
}

function startInspectorResize(event) {
  if (event.button !== 0) return
  event.preventDefault()
  resizeOriginX = event.clientX
  resizeOriginWidth = inspectorRef.value?.getBoundingClientRect().width || 0
  resizeActive.value = true
  event.currentTarget.setPointerCapture(event.pointerId)
}

function resizeInspector(event) {
  if (!resizeActive.value) return
  setInspectorWidth(resizeOriginWidth - (event.clientX - resizeOriginX))
}

function finishInspectorResize() {
  resizeActive.value = false
}

function resizeInspectorWithKeyboard(event) {
  if (!['ArrowLeft', 'ArrowRight'].includes(event.key)) return
  event.preventDefault()
  const currentWidth =
    inspectorWidth.value ||
    inspectorRef.value?.getBoundingClientRect().width ||
    0
  const delta = event.key === 'ArrowLeft' ? 32 : -32
  setInspectorWidth(currentWidth + delta)
}

async function scrollSelectedIntoView() {
  await nextTick()
  const row = tablePaneRef.value?.querySelector('tr[data-selected="true"]')
  row?.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
}

function onTimelineSelect(sequence) {
  const row = rows.value.find(
    (candidate) => candidate.event.sequence === sequence
  )
  const event =
    row?.event ||
    events.value.find((candidate) => candidate.sequence === sequence)
  if (event) {
    selectEvent(event)
    scrollSelectedIntoView()
  }
}

function isFollowingTrajectoryTail() {
  const pane = tablePaneRef.value
  if (!pane) return true
  return pane.scrollHeight - pane.scrollTop - pane.clientHeight <= 48
}

function handleTrajectoryScroll() {
  if (isFollowingTrajectoryTail()) pendingNewEventCount.value = 0
}

async function scrollToLatestTrajectory() {
  await nextTick()
  const pane = tablePaneRef.value
  if (pane) pane.scrollTop = pane.scrollHeight
  pendingNewEventCount.value = 0
}

function toggleSpan(spanId) {
  const next = new Set(collapsed.value)
  if (next.has(spanId)) next.delete(spanId)
  else next.add(spanId)
  collapsed.value = next
}

function toggleAllCalls() {
  if (collapsibleCallIds.value.length === 0) return
  const next = new Set(collapsed.value)
  if (allCallsCollapsed.value) {
    for (const id of collapsibleCallIds.value) next.delete(id)
  } else {
    for (const id of collapsibleCallIds.value) next.add(id)
  }
  collapsed.value = next
}

function setCategory(value) {
  category.value = value
}

function normalizeEvents(items) {
  return (items || []).map((event) => {
    const payload = event.payload || {}
    return {
      ...event,
      call_id:
        event.call_id || payload.call_id || payload.invocation_id || null,
      parent_call_id: event.parent_call_id || payload.parent_call_id || null,
      _ms: new Date(event.timestamp).getTime()
    }
  })
}

async function fetchTrajectory(
  append = false,
  { preserveState = false, silent = false } = {}
) {
  if (!props.runUuid) return
  const currentRequestId = ++requestId
  const selectedKey = selectedEvent.value
    ? trajectoryEventKey(selectedEvent.value)
    : ''
  loading.value = true
  try {
    const afterSequence = append ? nextAfterSequence.value : 0
    const data = await getAdminRunTrajectory(props.runUuid, {
      page_size: 500,
      after_sequence: afterSequence,
      q: query.value.trim() || undefined,
      category: category.value === 'all' ? undefined : category.value
    })
    if (currentRequestId !== requestId) return
    const nextEvents = normalizeEvents(data.results)
    events.value = append
      ? mergeTrajectoryEvents(events.value, nextEvents)
      : nextEvents
    hasMore.value = Boolean(data.has_more)
    nextAfterSequence.value = Number(data.next_after_sequence) || 0
    if (!append) {
      summary.value = data.summary || {}
      streamCursor.value = data.stream_cursor || streamCursor.value
      streamRevision.value = data.revision || streamRevision.value
      streamSequence.value = Number(data.stream_sequence) || 0
    }
    if (!append && !preserveState) {
      selectedEvent.value = null
      timelineRange.value = null
      inspectorTab.value = 'summary'
    } else if (selectedKey) {
      selectedEvent.value =
        rows.value.find((row) => trajectoryEventKey(row.event) === selectedKey)
          ?.event ||
        events.value.find(
          (event) => trajectoryEventKey(event) === selectedKey
        ) ||
        null
    }
    return true
  } catch (error) {
    if (currentRequestId !== requestId) return
    if (!silent) showError(extractErrorMessage(error, t('common.error')))
    hasMore.value = false
    return false
  } finally {
    if (currentRequestId === requestId) {
      loading.value = false
    }
  }
}

function shouldFollowTrajectory() {
  return shouldKeepTrajectoryStream({
    active: props.active,
    runUuid: props.runUuid,
    runStatus: props.runStatus,
    awaitingDone: awaitingStreamDone.value
  })
}

function stopTrajectoryStream() {
  const controller = streamController
  streamController = null
  controller?.abort()
  clearTimeout(fallbackTimer)
  fallbackTimer = null
  streamState.value = 'idle'
}

function scheduleFallbackRefresh() {
  clearTimeout(fallbackTimer)
  if (!shouldFollowTrajectory()) return
  const delay = reconnectDelay
  reconnectDelay = Math.min(reconnectDelay * 2, 15000)
  streamState.value = 'reconnecting'
  fallbackTimer = setTimeout(async () => {
    fallbackTimer = null
    await refreshAndFollow(true, true)
  }, delay)
}

function cancelTerminalSync() {
  clearTimeout(finalSyncTimer)
  finalSyncTimer = null
  finalSyncDelay = 1000
}

async function syncTerminalTrajectory(runUuid) {
  if (!props.active || props.runUuid !== runUuid) return
  const loaded = await fetchTrajectory(false, {
    preserveState: true,
    silent: true
  })
  if (loaded !== false || !props.active || props.runUuid !== runUuid) {
    finalSyncDelay = 1000
    return
  }
  const delay = finalSyncDelay
  finalSyncDelay = Math.min(finalSyncDelay * 2, 15000)
  finalSyncTimer = setTimeout(() => {
    finalSyncTimer = null
    void syncTerminalTrajectory(runUuid)
  }, delay)
}

function handleTrajectoryStreamEvent(message) {
  if (message?.type === 'ping') return
  const existingKeys = new Set(events.value.map(trajectoryEventKey))
  const incomingEvents = normalizeEvents(message.events)
  const newEventCount = incomingEvents.filter(
    (event) => !existingKeys.has(trajectoryEventKey(event))
  ).length
  const followsTail = isFollowingTrajectoryTail()
  const current = {
    events: events.value,
    summary: summary.value,
    revision: streamRevision.value,
    cursor: streamCursor.value,
    sequence: streamSequence.value,
    run: null
  }
  const next = applyTrajectoryStreamUpdate(current, {
    ...message,
    events: incomingEvents
  })
  if (next.requiresResync) {
    stopTrajectoryStream()
    void refreshAndFollow(true, true)
    return
  }
  const selectedSpanId = selectedEvent.value?.span?.id
  events.value = next.events
  summary.value = next.summary
  if (selectedSpanId) {
    const span = fullSpans.value.find((item) => item.id === selectedSpanId)
    if (span) selectedEvent.value = { ...span.startEvent, span }
  }
  streamRevision.value = next.revision
  streamCursor.value = next.cursor
  streamSequence.value = next.sequence
  reconnectDelay = 1000
  streamState.value = message.type === 'done' ? 'idle' : 'live'
  if (message.type === 'done') awaitingStreamDone.value = false
  if (next.run) emit('run-update', next.run)
  if (newEventCount > 0) {
    if (followsTail) void scrollToLatestTrajectory()
    else pendingNewEventCount.value += newEventCount
  }
  if (message.type === 'done') {
    cancelTerminalSync()
    void syncTerminalTrajectory(props.runUuid)
  }
}

async function connectTrajectoryStream() {
  if (!shouldFollowTrajectory() || streamController) return
  clearTimeout(fallbackTimer)
  fallbackTimer = null
  const controller = new AbortController()
  const runUuid = props.runUuid
  streamController = controller
  awaitingStreamDone.value = true
  streamState.value = 'connecting'
  let completed = false
  try {
    await streamAdminRunTrajectory(runUuid, {
      cursor: streamCursor.value,
      revision: streamRevision.value,
      sequence: streamSequence.value,
      q: query.value.trim(),
      category: category.value === 'all' ? '' : category.value,
      signal: controller.signal,
      onEvent(message) {
        if (controller !== streamController || runUuid !== props.runUuid) return
        if (message?.type === 'done') completed = true
        handleTrajectoryStreamEvent(message)
      }
    })
  } catch (error) {
    if (error?.name === 'AbortError') return
  } finally {
    if (streamController === controller) streamController = null
  }
  if (!completed) scheduleFallbackRefresh()
}

async function refreshAndFollow(preserveState = false, silent = false) {
  stopTrajectoryStream()
  const loaded = await fetchTrajectory(false, { preserveState, silent })
  if (loaded && shouldFollowTrajectory()) {
    void connectTrajectoryStream()
  } else if (loaded === false) {
    scheduleFallbackRefresh()
  }
}

function reset() {
  stopTrajectoryStream()
  cancelTerminalSync()
  requestId += 1
  events.value = []
  summary.value = {}
  selectedEvent.value = null
  collapsed.value = new Set()
  query.value = ''
  category.value = 'all'
  hasMore.value = false
  nextAfterSequence.value = 0
  timelineRange.value = null
  pendingNewEventCount.value = 0
  streamCursor.value = ''
  streamRevision.value = ''
  streamSequence.value = 0
  reconnectDelay = 1000
  awaitingStreamDone.value = false
}

function loadMore() {
  fetchTrajectory(true)
}

watch(
  () => props.runUuid,
  () => {
    reset()
    if (props.active) void refreshAndFollow()
  }
)

watch([query, category], () => {
  if (!props.active) return
  clearTimeout(filterTimer)
  filterTimer = setTimeout(() => {
    void refreshAndFollow()
  }, 250)
})

watch(filteredEvents, (filtered) => {
  if (
    selectedEvent.value &&
    !filtered.some(
      (event) =>
        trajectoryEventKey(event) === trajectoryEventKey(selectedEvent.value)
    )
  ) {
    selectedEvent.value = null
  }
})

watch(
  [() => props.active, () => props.runStatus],
  ([active]) => {
    if (!active) {
      awaitingStreamDone.value = false
      cancelTerminalSync()
      stopTrajectoryStream()
      return
    }
    if (streamController) return
    if (active && events.value.length === 0) {
      void refreshAndFollow()
      return
    }
    if (shouldFollowTrajectory()) void connectTrajectoryStream()
    else stopTrajectoryStream()
  },
  { immediate: true }
)

onBeforeUnmount(() => {
  clearTimeout(filterTimer)
  cancelTerminalSync()
  stopTrajectoryStream()
})

const datasourceNames = ref(new Map())

async function loadDatasourceNames() {
  try {
    const data = await listDataSources({ page_size: 500 })
    const list = Array.isArray(data) ? data : data?.results || []
    const map = new Map()
    for (const item of list) {
      if (item?.uuid) {
        map.set(String(item.uuid).toLowerCase(), item.name || '')
      }
    }
    datasourceNames.value = map
  } catch {
    // Datasource names are a display nicety; ignore lookup failures.
  }
}

onMounted(() => {
  void loadDatasourceNames()
})
</script>

<style scoped>
.run-trajectory {
  --t-accent: #4176e6;
  --t-bg-1: #ffffff;
  --t-bg-2: #ffffff;
  --t-bg-hover: rgba(0, 0, 0, 0.045);
  --t-bg-active: rgba(0, 0, 0, 0.07);
  --t-border-l1: rgba(0, 0, 0, 0.05);
  --t-border-l2: rgba(0, 0, 0, 0.11);
  --t-text-1: #0f1115;
  --t-text-2: #61666b;
  --t-text-3: #81858c;
  --t-text-4: #adb2b8;
  --t-user: #4176e6;
  --t-user-bg: #e4edfd;
  --t-context: #22c55e;
  --t-context-bg: #e6faed;
  --t-model: #886bae;
  --t-model-bg: #efeff6;
  --t-tool: #dd8629;
  --t-tool-bg: #fef5e7;
  --t-subtool: #ba864f;
  --t-subtool-bg: #fef9f1;
  --t-system: #61666b;
  --t-system-bg: #f3f4f6;
  --t-retry: #dd8629;
  --t-retry-bg: #fef5e7;
  --t-checkpoint: #61666b;
  --t-checkpoint-bg: #f3f4f6;
  --t-cancelled: #ec1313;
  --t-cancelled-bg: #fef0f0;
  --t-error: #ec1313;
  --t-stage: #0ea5e9;
  --t-stage-bg: #e0f2fe;
  --t-gate: #8b5cf6;
  --t-gate-bg: #ede9fe;
  --t-evidence: #0d9488;
  --t-evidence-bg: #ccfbf1;
  --t-agent: #6366f1;
  --t-agent-bg: #e0e7ff;

  display: flex;
  flex-direction: column;
  min-width: 0;
  color: var(--t-text-1);
  background: var(--t-bg-1);
}

.trajectory-live-state {
  display: inline-flex;
  align-items: center;
  min-height: 24px;
  gap: 6px;
  color: var(--t-text-3);
  font-size: 11px;
  font-weight: 600;
}

.trajectory-stream-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  min-height: 24px;
  margin-bottom: 8px;
  gap: 12px;
}

.trajectory-new-events {
  padding: 2px 8px;
  border: 1px solid color-mix(in srgb, var(--t-accent) 35%, transparent);
  border-radius: 4px;
  color: var(--t-accent);
  background: color-mix(in srgb, var(--t-accent) 7%, var(--t-bg-1));
  cursor: pointer;
  font-size: 11px;
  font-weight: 600;
}

.trajectory-new-events:hover,
.trajectory-new-events:focus-visible {
  border-color: var(--t-accent);
  outline: 0;
}

.trajectory-live-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--t-text-4);
}

.trajectory-live-state[data-state='live'] .trajectory-live-dot {
  background: #16a34a;
  box-shadow: 0 0 0 3px rgb(22 163 74 / 12%);
}

.trajectory-live-state[data-state='connecting'] .trajectory-live-dot,
.trajectory-live-state[data-state='reconnecting'] .trajectory-live-dot {
  background: #d97706;
  animation: trajectory-live-pulse 1.2s ease-in-out infinite;
}

@keyframes trajectory-live-pulse {
  50% {
    opacity: 0.35;
  }
}

.assistant-tag {
  display: inline-flex;
  max-width: 220px;
  overflow: hidden;
  padding: 2px 6px;
  border: 1px solid var(--sl-border, #cbd5e1);
  border-radius: 3px;
  color: var(--sl-text, #334155);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.plugin-chip {
  display: inline-flex;
  flex: none;
  align-items: center;
  height: 18px;
  padding: 0 8px;
  border-radius: 999px;
  color: var(--t-tool);
  background: var(--t-tool-bg);
  font-size: 10px;
  font-weight: 600;
  white-space: nowrap;
}

.plugin-chip-skill {
  color: var(--t-model);
  background: var(--t-model-bg);
}

:root[data-theme='dark'] .run-trajectory {
  --t-accent: #679efe;
  --t-bg-1: #232324;
  --t-bg-2: #2c2c2e;
  --t-bg-hover: rgba(255, 255, 255, 0.06);
  --t-bg-active: rgba(255, 255, 255, 0.1);
  --t-border-l1: rgba(255, 255, 255, 0.07);
  --t-border-l2: rgba(255, 255, 255, 0.13);
  --t-text-1: #f9fafb;
  --t-text-2: #cfd3d6;
  --t-text-3: #adb2b8;
  --t-text-4: #85878b;
  --t-user: #679efe;
  --t-user-bg: #34415b;
  --t-context: #22c55e;
  --t-context-bg: #233c2c;
  --t-model: #9474bc;
  --t-model-bg: #2e2837;
  --t-tool: #dd8629;
  --t-tool-bg: #27241f;
  --t-subtool: #cf9a56;
  --t-subtool-bg: #2a2825;
  --t-system: #cfd3d6;
  --t-system-bg: #3a3a3c;
  --t-retry: #dd8629;
  --t-retry-bg: #27241f;
  --t-checkpoint: #cfd3d6;
  --t-checkpoint-bg: #3a3a3c;
  --t-cancelled: #f25a5a;
  --t-cancelled-bg: #3b2626;
  --t-error: #f25a5a;
  --t-stage: #38bdf8;
  --t-stage-bg: #12303f;
  --t-gate: #a78bfa;
  --t-gate-bg: #2c2440;
  --t-evidence: #2dd4bf;
  --t-evidence-bg: #14332f;
  --t-agent: #818cf8;
  --t-agent-bg: #242a4a;
}

/* Stats bar */
.trajectory-stats {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px;
  margin: 0 0 12px;
}

.trajectory-stats .stat {
  min-width: 0;
  padding: 8px 12px;
  border: 1px solid var(--t-border-l2);
  border-radius: 8px;
  background: var(--t-bg-2);
}

.trajectory-stats dt {
  overflow: hidden;
  color: var(--t-text-3);
  font-size: 11px;
  line-height: 16px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.trajectory-stats dd {
  margin: 1px 0 0;
  color: var(--t-text-1);
  font-size: 17px;
  font-variant-numeric: tabular-nums;
  font-weight: 600;
  line-height: 22px;
}

.trajectory-stats .stat-error dd {
  color: var(--t-error);
}

@media (min-width: 640px) {
  .trajectory-stats {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }
}

@media (min-width: 1024px) {
  .trajectory-stats {
    grid-template-columns: repeat(6, minmax(0, 1fr));
    gap: 0;
    overflow: hidden;
    border: 1px solid var(--t-border-l2);
    border-radius: 8px;
  }

  .trajectory-stats .stat {
    border: 0;
    border-right: 1px solid var(--t-border-l1);
    border-radius: 0;
  }

  .trajectory-stats .stat:last-child {
    border-right: 0;
  }
}

/* Delegated Run progress */
.assistant-progress {
  margin: 0 0 12px;
  overflow: hidden;
  border: 1px solid var(--t-border-l2);
  border-radius: 8px;
  background: var(--t-bg-1);
}

.assistant-progress-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  min-height: 30px;
  padding: 0 10px;
  border-bottom: 1px solid var(--t-border-l1);
  color: var(--t-text-3);
  background: var(--t-bg-2);
  font-size: 11px;
}

.assistant-progress-header strong {
  color: var(--t-text-2);
  font-size: 12px;
  font-weight: 600;
}

.assistant-progress-list {
  display: grid;
}

.assistant-progress-row {
  display: grid;
  grid-template-columns: 10px minmax(110px, 180px) minmax(0, 1fr) auto;
  align-items: center;
  min-height: 36px;
  padding: 0 10px;
  gap: 8px;
  border-bottom: 1px solid var(--t-border-l1);
  font-size: 12px;
}

.assistant-progress-row:last-child {
  border-bottom: 0;
}

.assistant-progress-row > strong,
.assistant-progress-task {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.assistant-progress-row > strong {
  color: var(--t-text-1);
  font-weight: 600;
}

.assistant-progress-task {
  color: var(--t-text-2);
}

.assistant-progress-indicator {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--t-text-4);
}

.assistant-progress-indicator[data-status='done'] {
  background: var(--t-context);
}

.assistant-progress-indicator[data-status='running'],
.assistant-progress-indicator[data-status='streaming'] {
  box-sizing: border-box;
  width: 9px;
  height: 9px;
  border: 2px solid color-mix(in srgb, var(--t-accent) 28%, transparent);
  border-top-color: var(--t-accent);
  background: transparent;
  animation: assistant-progress-spin 800ms linear infinite;
}

.assistant-progress-indicator[data-status='failed'],
.assistant-progress-indicator[data-status='cancelled'] {
  background: var(--t-error);
}

.assistant-progress-indicator[data-status='awaiting_user_input'] {
  background: var(--t-tool);
}

.assistant-progress-meta {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  color: var(--t-text-4);
  font-size: 11px;
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}

.assistant-attempts {
  grid-column: 2 / -1;
  min-width: 0;
  margin: 0 0 7px;
  color: var(--t-text-3);
  font-size: 11px;
}

.assistant-attempts summary {
  width: fit-content;
  cursor: pointer;
  color: var(--t-accent);
}

.assistant-attempt-list {
  display: grid;
  margin-top: 6px;
  border-top: 1px solid var(--t-border-l1);
}

.assistant-attempt-row {
  display: grid;
  grid-template-columns: 76px minmax(0, 1fr) auto auto auto;
  align-items: center;
  min-height: 28px;
  gap: 8px;
  border-bottom: 1px solid var(--t-border-l1);
}

.assistant-attempt-row > strong {
  color: var(--t-text-2);
  font-weight: 600;
}

.assistant-attempt-row > span {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.assistant-progress-status {
  color: var(--t-text-2);
}

@keyframes assistant-progress-spin {
  to {
    transform: rotate(360deg);
  }
}

@media (max-width: 720px) {
  .assistant-progress-row {
    grid-template-columns: 10px minmax(0, 1fr) auto;
    padding-top: 7px;
    padding-bottom: 7px;
  }

  .assistant-progress-task {
    grid-column: 2 / 4;
    grid-row: 2;
  }

  .assistant-attempts {
    grid-column: 2 / 4;
  }

  .assistant-attempt-row {
    grid-template-columns: 68px minmax(0, 1fr) auto;
  }

  .assistant-attempt-row > span:nth-last-child(-n + 2) {
    display: none;
  }
}

@media (prefers-reduced-motion: reduce) {
  .assistant-progress-indicator[data-status='running'],
  .assistant-progress-indicator[data-status='streaming'] {
    animation: none;
  }
}

/* Toolbar */
.trajectory-toolbar {
  display: flex;
  flex: none;
  align-items: center;
  box-sizing: border-box;
  width: 100%;
  height: 34px;
  padding: 0 6px;
  gap: 8px;
  border: 1px solid var(--t-border-l2);
  border-radius: 8px 8px 0 0;
  background: var(--t-bg-1);
}

.toolbar-actions {
  display: flex;
  flex: none;
  align-items: center;
  min-width: 0;
  gap: 2px;
  overflow-x: auto;
  scrollbar-width: none;
}

.toolbar-actions::-webkit-scrollbar {
  display: none;
}

.toolbar-toggle {
  display: inline-flex;
  flex: none;
  align-items: center;
  height: 20px;
  padding: 0 7px;
  gap: 4px;
  border: 0;
  border-radius: 3px;
  color: var(--t-text-3);
  background: transparent;
  cursor: pointer;
  font-size: 12px;
  line-height: 20px;
}

.toolbar-toggle:hover {
  color: var(--t-text-1);
  background: var(--t-bg-hover);
}

.toolbar-toggle[aria-pressed='true'] {
  color: var(--t-text-1);
  background: var(--t-bg-hover);
}

.toolbar-toggle:disabled {
  color: var(--t-text-4);
  cursor: not-allowed;
  background: transparent;
}

.toolbar-toggle:focus-visible {
  outline: 1px solid var(--t-accent);
  outline-offset: 1px;
}

.toolbar-count {
  color: var(--t-text-4);
  font-size: 10px;
  font-variant-numeric: tabular-nums;
}

.toolbar-separator {
  flex: none;
  width: 1px;
  height: 14px;
  margin: 0 3px;
  background: var(--t-border-l2);
}

.toolbar-glyph {
  color: var(--t-text-3);
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 14px;
  line-height: 14px;
}

.toolbar-search {
  display: flex;
  flex: 0 1 200px;
  align-items: center;
  min-width: 84px;
  height: 22px;
  margin-left: auto;
  padding: 0 6px;
  gap: 4px;
  border: 1px solid var(--t-border-l2);
  border-radius: 4px;
  color: var(--t-text-4);
  background: var(--t-bg-2);
}

.toolbar-search:hover {
  border-color: var(--t-text-4);
}

.toolbar-search:focus-within {
  border-color: var(--t-accent);
  background: var(--t-bg-1);
}

.search-icon {
  flex: none;
}

.search-input {
  min-width: 0;
  width: 100%;
  padding: 0;
  border: 0;
  outline: 0;
  color: var(--t-text-1);
  background: transparent;
  font-size: 12px;
  line-height: 20px;
}

.search-input::placeholder {
  color: var(--t-text-4);
}

.search-input::-webkit-search-cancel-button {
  width: 12px;
  height: 12px;
  cursor: pointer;
}

/* Split layout */
.trajectory-split {
  position: relative;
  display: flex;
  min-height: 28rem;
  max-height: 42rem;
  overflow: hidden;
  border: 1px solid var(--t-border-l2);
  border-top: 0;
  border-radius: 0 0 8px 8px;
  background: var(--t-bg-1);
}

.trajectory-split-resizing {
  cursor: col-resize;
  user-select: none;
}

.table-pane {
  flex: 1;
  min-width: 420px;
  overflow: auto;
}

/* Ledger table */
.trajectory-table {
  width: 100%;
  min-width: 560px;
  border-spacing: 0;
  table-layout: fixed;
  color: var(--t-text-1);
  background: var(--t-bg-1);
  font-size: 12px;
}

.trajectory-table th {
  position: sticky;
  top: 0;
  z-index: 3;
  box-sizing: border-box;
  height: 30px;
  padding: 0 8px;
  overflow: hidden;
  border-bottom: 1px solid var(--t-border-l2);
  color: var(--t-text-3);
  background: var(--t-bg-2);
  font-size: 12px;
  font-weight: 500;
  text-align: left;
  text-overflow: ellipsis;
  user-select: none;
  white-space: nowrap;
}

.span-header {
  padding-left: 34px !important;
}

.status-header {
  width: 76px;
}

.duration-header {
  width: 70px;
}

.waterfall-header {
  width: 22%;
  min-width: 120px;
}

.trajectory-table td {
  box-sizing: border-box;
  height: 30px;
  padding: 0 8px;
  overflow: hidden;
  border-bottom: 1px solid var(--t-border-l1);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.status-header,
.status-cell {
  width: 76px;
}

.duration-header,
.duration-cell {
  width: 70px;
}

.tokens-header,
.tokens-cell {
  width: 88px;
}

.duration-cell {
  color: var(--t-text-2);
  font-variant-numeric: tabular-nums;
}

.tokens-cell {
  color: var(--t-text-2);
  font-variant-numeric: tabular-nums;
}

.span-status {
  display: inline-flex;
  align-items: center;
  height: 18px;
  padding: 0 7px;
  border-radius: 999px;
  font-size: 10px;
  font-weight: 600;
  line-height: 16px;
}

.trajectory-diagnostics {
  margin: 12px 0;
  padding: 12px 16px;
  border: 1px solid var(--t-border);
  border-radius: 8px;
}
.trajectory-diagnostics summary {
  cursor: pointer;
  font-weight: 600;
}
.trajectory-diagnostics button {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 16px;
  width: 100%;
  padding: 8px 0;
  text-align: left;
}
.trajectory-diagnostics button span,
.diagnostic-message {
  color: #b45309;
}
.span-status-warning {
  color: #b45309;
  background: rgba(245, 158, 11, 0.12);
}
:global(.dark) .span-status-warning,
:global(.dark) .diagnostic-message {
  color: #fbbf24;
}
.span-status-success {
  color: var(--t-context);
  background: var(--t-context-bg);
}

.span-status-error {
  color: var(--t-cancelled);
  background: var(--t-cancelled-bg);
}

.span-status-running {
  color: var(--t-accent);
  background: var(--t-user-bg);
}

.span-status-neutral {
  color: var(--t-text-3);
  background: var(--t-system-bg);
}

.waterfall-track {
  position: relative;
  display: block;
  height: 8px;
  border-radius: 4px;
  background: color-mix(in srgb, var(--t-text-4) 16%, transparent);
}

.waterfall-bar {
  position: absolute;
  top: 0;
  bottom: 0;
  min-width: 3px;
  border-radius: 4px;
  background: var(--t-accent);
}

.waterfall-bar.tag-model {
  background: var(--t-model);
}

.waterfall-bar.tag-tool,
.waterfall-bar.tag-subtool {
  background: var(--t-tool);
}

.waterfall-bar.tag-user,
.waterfall-bar.tag-context {
  background: var(--t-context);
}

.waterfall-bar[data-status='failed'],
.waterfall-bar[data-status='cancelled'],
.waterfall-bar[data-status='interrupted'] {
  background: var(--t-error);
}

.trajectory-table tbody tr {
  cursor: default;
  outline: none;
  transition:
    background-color 120ms ease-in-out,
    opacity 120ms ease-in-out;
}

.trajectory-table tbody tr:hover {
  background: var(--t-bg-hover);
}

.trajectory-table tbody tr[data-selected='true'] {
  background: var(--t-bg-active);
}

.trajectory-table tbody tr:focus-visible {
  box-shadow: inset 0 0 0 1px var(--t-accent);
}

/* Turn boundary + rail */
.turn-start-row td {
  position: relative;
  overflow: visible;
}

.trajectory-table tbody .turn-start-row:not(:first-child) td::before {
  position: absolute;
  z-index: 1;
  top: 0;
  right: 0;
  left: 0;
  height: 2px;
  background: var(--t-border-l1);
  content: '';
  pointer-events: none;
  transform: translateY(-50%);
}

.event-cell {
  position: relative;
  display: flex;
  align-items: center;
  gap: 6px;
  padding-right: 4px !important;
  padding-left: calc(34px + var(--trajectory-indent, 0px)) !important;
}

.turn-rail {
  position: absolute;
  top: -1px;
  bottom: -1px;
  left: 0;
  width: 2px;
  background: color-mix(in srgb, var(--t-accent) 22%, var(--t-bg-1));
  pointer-events: none;
}

.ledger-row[data-error='true'] .turn-rail {
  background: color-mix(in srgb, var(--t-error) 22%, var(--t-bg-1));
}

.ledger-row[data-selected='true'] .turn-rail {
  top: 0;
  bottom: 0;
  width: 3px;
  background: var(--t-accent);
}

.ledger-row[data-error='true'][data-selected='true'] .turn-rail {
  background: var(--t-error);
}

.request-dot {
  position: absolute;
  z-index: 6;
  top: -8px;
  left: 12px;
  width: 16px;
  height: 16px;
  pointer-events: none;
}

.request-dot::before {
  position: absolute;
  top: 5.5px;
  left: 5.5px;
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: var(--t-text-4);
  box-shadow:
    0 0 0 2px var(--t-bg-1),
    0 0 0 3px transparent;
  content: '';
}

.ledger-row[data-error='true'] .request-dot::before {
  background: var(--t-error);
}

.seq {
  position: absolute;
  top: 0;
  bottom: 0;
  left: 8px;
  display: flex;
  align-items: center;
  color: var(--t-text-3);
  font:
    10px/12px ui-monospace,
    SFMono-Regular,
    Menlo,
    monospace;
  font-variant-numeric: tabular-nums;
}

.kind-tag {
  display: inline-flex;
  flex: none;
  align-items: center;
  box-sizing: border-box;
  height: 19px;
  padding: 0 5px;
  border: 1px solid transparent;
  border-radius: 4px;
  font-size: 10px;
  font-weight: 650;
  letter-spacing: 0.035em;
  line-height: 16px;
  user-select: none;
}

.kind-tag-label {
  display: inline-block;
  max-width: 72px;
  overflow: hidden;
  white-space: nowrap;
}

.tag-user {
  color: var(--t-user);
  background: var(--t-user-bg);
}
.tag-context {
  color: var(--t-context);
  background: var(--t-context-bg);
}
.tag-model {
  color: var(--t-model);
  background: var(--t-model-bg);
}
.tag-tool {
  color: var(--t-tool);
  background: var(--t-tool-bg);
}
.tag-subtool {
  color: var(--t-subtool);
  background: var(--t-subtool-bg);
}
.tag-system,
.tag-run,
.tag-compacted,
.tag-checkpoint {
  color: var(--t-system);
  background: var(--t-system-bg);
}
.tag-plugin {
  color: var(--t-tool);
  background: var(--t-tool-bg);
}
.tag-skill {
  color: var(--t-model);
  background: var(--t-model-bg);
}
.tag-stage {
  color: var(--t-stage);
  background: var(--t-stage-bg);
}
.tag-gate {
  color: var(--t-gate);
  background: var(--t-gate-bg);
}
.tag-evidence {
  color: var(--t-evidence);
  background: var(--t-evidence-bg);
}
.tag-agent {
  color: var(--t-agent);
  background: var(--t-agent-bg);
}
.tag-retry {
  color: var(--t-retry);
  background: var(--t-retry-bg);
}
.tag-cancelled {
  color: var(--t-cancelled);
  background: var(--t-cancelled-bg);
}

/* Span row */
.row-expand {
  display: inline-flex;
  flex: none;
  align-items: center;
  justify-content: center;
  width: 20px;
  height: 20px;
  margin-left: -4px;
  padding: 0;
  border: 0;
  border-radius: 3px;
  color: var(--t-text-3);
  background: transparent;
  cursor: pointer;
}

.row-expand-spacer {
  flex: none;
  width: 20px;
  height: 20px;
  margin-left: -4px;
}

.row-expand:hover {
  color: var(--t-text-1);
  background: var(--t-bg-hover);
}

.row-expand:focus-visible {
  outline: 1px solid var(--t-accent);
}

.content-text {
  display: flex;
  flex: 1 1 auto;
  align-items: baseline;
  min-width: 0;
  gap: 7px;
}

.content-title {
  flex: none;
  max-width: 60%;
  overflow: hidden;
  color: var(--t-text-1);
  font-size: 12px;
  line-height: 18px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.content-title-error {
  color: var(--t-error);
}

.content-summary {
  flex: 1 1 auto;
  min-width: 0;
  overflow: hidden;
  color: var(--t-text-3);
  font-size: 11px;
  line-height: 18px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.content-count {
  flex: none;
  color: var(--t-text-4);
  font-size: 11px;
  white-space: nowrap;
}

/* Inspector drawer */
.trajectory-resize-handle {
  position: relative;
  z-index: 4;
  flex: 0 0 8px;
  width: 8px;
  margin: 0 -4px;
  outline: 0;
  cursor: col-resize;
  touch-action: none;
}

.trajectory-resize-handle::after {
  position: absolute;
  top: 0;
  bottom: 0;
  left: 3px;
  width: 2px;
  background: transparent;
  content: '';
}

.trajectory-resize-handle:hover::after,
.trajectory-resize-handle:focus-visible::after,
.trajectory-split-resizing .trajectory-resize-handle::after {
  background: var(--t-accent);
}

.trajectory-inspector {
  display: flex;
  flex: none;
  flex-direction: column;
  width: var(--trajectory-inspector-width, clamp(320px, 42%, 520px));
  max-width: calc(100% - 420px);
  min-width: 0;
  min-height: 0;
  border-left: 1px solid var(--t-border-l2);
  background: var(--t-bg-1);
  animation: inspector-slide-in 180ms ease-out;
}

@keyframes inspector-slide-in {
  from {
    opacity: 0;
    transform: translateX(12px);
  }
  to {
    opacity: 1;
    transform: translateX(0);
  }
}

.inspector-header {
  display: flex;
  flex: none;
  align-items: center;
  justify-content: space-between;
  box-sizing: border-box;
  height: 42px;
  padding: 0 8px 0 12px;
  border-bottom: 1px solid var(--t-border-l2);
}

.inspector-title {
  display: flex;
  align-items: center;
  min-width: 0;
  gap: 8px;
}

.inspector-header-meta {
  display: inline-flex;
  flex: none;
  align-items: center;
  gap: 5px;
}

.inspector-sequence {
  color: var(--t-text-3);
  font:
    10px/16px ui-monospace,
    SFMono-Regular,
    Menlo,
    monospace;
  font-variant-numeric: tabular-nums;
}

.inspector-header-meta .kind-tag {
  height: 18px;
  padding: 0 5px;
  font-size: 9px;
}

.inspector-dot {
  flex: none;
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: var(--t-text-3);
}

.inspector-name {
  flex: none;
  max-width: 180px;
  overflow: hidden;
  font:
    500 12px/16px ui-monospace,
    SFMono-Regular,
    Menlo,
    monospace;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.inspector-location {
  min-width: 0;
  overflow: hidden;
  color: var(--t-text-3);
  font:
    11px/16px ui-monospace,
    SFMono-Regular,
    Menlo,
    monospace;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.inspector-close {
  display: inline-flex;
  flex: none;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  padding: 0;
  border: 0;
  border-radius: 6px;
  color: var(--t-text-2);
  background: transparent;
  cursor: pointer;
  font-size: 18px;
  line-height: 18px;
}

.inspector-close:hover {
  color: var(--t-text-1);
  background: var(--t-bg-hover);
}

.inspector-close:focus-visible,
.inspector-tab:focus-visible {
  outline: 1px solid var(--t-accent);
  outline-offset: -1px;
}

.inspector-tabs {
  display: flex;
  flex: none;
  box-sizing: border-box;
  width: 100%;
  height: 34px;
  padding: 0 8px;
  gap: 1px;
  overflow-x: auto;
  border-bottom: 1px solid var(--t-border-l2);
  scrollbar-width: none;
  white-space: nowrap;
}

.inspector-tabs::-webkit-scrollbar {
  display: none;
}

.inspector-tab {
  position: relative;
  flex: none;
  padding: 0 9px;
  border: 0;
  color: var(--t-text-3);
  background: transparent;
  cursor: pointer;
  font-size: 13px;
}

.inspector-tab:hover {
  color: var(--t-text-1);
  background: var(--t-bg-hover);
}

.inspector-tab-active {
  color: var(--t-accent);
}

.inspector-tab-active::after {
  position: absolute;
  right: 9px;
  bottom: 0;
  left: 9px;
  height: 2px;
  border-radius: 1px 1px 0 0;
  background: var(--t-accent);
  content: '';
}

.inspector-body {
  display: flex;
  flex: 1;
  min-height: 0;
  flex-direction: column;
  overflow: auto;
  padding: 0 0 12px;
}

.inspector-event-card {
  margin: 10px 12px 2px;
  padding: 10px 11px;
  border: 1px solid var(--t-border-l1);
  border-radius: 7px;
  background: var(--t-bg-2);
}

.inspector-event-card-title {
  overflow: hidden;
  color: var(--t-text-1);
  font-size: 13px;
  font-weight: 600;
  line-height: 18px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.inspector-event-card-type {
  margin-top: 2px;
  overflow-wrap: anywhere;
  color: var(--t-text-3);
  font:
    11px/16px ui-monospace,
    SFMono-Regular,
    Menlo,
    monospace;
}

.inspector-event-card-summary {
  margin-top: 4px;
  overflow-wrap: anywhere;
  color: var(--t-text-2);
  font-size: 12px;
  line-height: 17px;
}

.inspector-hero-card {
  border-color: color-mix(in srgb, var(--t-accent) 28%, var(--t-border-l1));
  background: color-mix(in srgb, var(--t-accent) 4%, var(--t-bg-2));
}

.inspector-kpi-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 6px;
  margin: 8px 12px 0;
}

.inspector-kpi {
  min-width: 0;
  padding: 8px 9px;
  border: 1px solid var(--t-border-l1);
  border-radius: 6px;
  background: var(--t-bg-2);
}

.inspector-kpi span,
.inspector-timing-card span {
  display: block;
  overflow: hidden;
  color: var(--t-text-3);
  font-size: 10px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.inspector-kpi strong,
.inspector-timing-card strong {
  display: block;
  margin-top: 3px;
  overflow: hidden;
  color: var(--t-text-1);
  font-size: 13px;
  font-variant-numeric: tabular-nums;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.inspector-kpi .span-status {
  display: inline-flex;
  width: max-content;
  max-width: 100%;
  margin-top: 3px;
}

.inspector-section {
  margin-top: 10px;
  border-top: 1px solid var(--t-border-l1);
}

.inspector-detail-grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  margin: 0;
  padding: 3px 14px 8px;
}

.inspector-detail-grid > div {
  display: grid;
  grid-template-columns: 76px minmax(0, 1fr);
  gap: 8px;
  padding: 5px 0;
  border-bottom: 1px solid var(--t-border-l1);
}

.inspector-detail-grid > div:last-child {
  border-bottom: 0;
}

.inspector-detail-grid dt {
  color: var(--t-text-3);
  font-size: 11px;
}

.inspector-detail-grid dd {
  min-width: 0;
  margin: 0;
  overflow-wrap: anywhere;
  color: var(--t-text-1);
  font-size: 11px;
}

.inspector-event-chips {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 5px;
  margin-top: 8px;
}

.inspector-event-chips .status-badge {
  margin: 0;
}

.inspector-chip {
  display: inline-flex;
  align-items: center;
  min-height: 18px;
  padding: 0 6px;
  border: 1px solid var(--t-border-l1);
  border-radius: 999px;
  color: var(--t-text-2);
  background: var(--t-bg-1);
  font-size: 10px;
  line-height: 16px;
}

.status-badge {
  display: inline-flex;
  margin: 10px 12px 0;
  padding: 1px 8px;
  border-radius: 999px;
  font-size: 10px;
  font-weight: 600;
  line-height: 16px;
}

.status-badge-error {
  color: var(--t-cancelled);
  background: var(--t-cancelled-bg);
}
.status-badge-success {
  color: var(--t-context);
  background: var(--t-context-bg);
}
.status-badge-neutral {
  color: var(--t-text-3);
  background: var(--t-system-bg);
}

.overview {
  margin: 0;
  padding: 8px 0 4px;
  font-size: 13px;
  line-height: 20px;
}

.overview > div {
  display: grid;
  grid-template-columns: 82px minmax(0, 1fr);
  min-height: 22px;
  padding: 0 14px;
  align-items: start;
}

.overview dt {
  padding-top: 1px;
  color: var(--t-text-3);
}

.overview dt.indent {
  padding-left: 12px;
}

.overview dd {
  min-width: 0;
  margin: 0;
  overflow: hidden;
  color: var(--t-text-1);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.overview dd.mono {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 12px;
}

.overview dd.wrap-value {
  overflow-wrap: anywhere;
  white-space: normal;
}

.execution-context-card {
  margin: 8px 12px 6px;
  padding: 10px 11px;
  border: 1px solid var(--t-border-l1);
  border-radius: 7px;
  background: var(--t-bg-2);
}

.execution-context-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.execution-path {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 4px;
  margin-top: 7px;
  color: var(--t-text-2);
  font-size: 12px;
  line-height: 18px;
}

.execution-path-item {
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.execution-path-current {
  color: var(--t-text-1);
  font-weight: 650;
}

.execution-path-link {
  padding: 0;
  border: 0;
  background: transparent;
  color: var(--t-accent);
  font: inherit;
  cursor: pointer;
  text-align: left;
}

.execution-path-link:hover {
  text-decoration: underline;
}

.execution-path-link:focus-visible,
.technical-copy:focus-visible {
  outline: 2px solid var(--t-accent);
  outline-offset: 2px;
}

.execution-path-separator {
  color: var(--t-text-4);
}

.overview dd.execution-time-value {
  display: flex;
  flex-direction: column;
  gap: 1px;
  white-space: normal;
}

.execution-time-value strong {
  color: var(--t-text-1);
  font-size: 12px;
  font-weight: 600;
}

.execution-time-value span {
  color: var(--t-text-3);
  font-size: 11px;
}

.technical-details {
  margin: 4px 12px 8px;
  border-top: 1px solid var(--t-border-l1);
}

.technical-details summary {
  padding: 7px 2px 5px;
  color: var(--t-text-3);
  cursor: pointer;
  font-size: 11px;
  user-select: none;
}

.technical-details-grid {
  padding-top: 2px;
}

.technical-copy {
  margin: 4px 0;
  padding: 4px 8px;
  border: 1px solid var(--t-border-l1);
  border-radius: 4px;
  background: var(--t-bg-2);
  color: var(--t-text-2);
  cursor: pointer;
  font-size: 11px;
}

.technical-details-grid > div {
  padding-right: 2px;
  padding-left: 2px;
}

.inspector-io-section {
  padding-bottom: 6px;
}

.inspector-data-block {
  margin: 5px 12px 8px;
  padding: 8px 9px;
  border: 1px solid var(--t-border-l1);
  border-radius: 6px;
  background: var(--t-bg-2);
}

.inspector-data-label {
  display: block;
  margin-bottom: 3px;
  color: var(--t-text-3);
  font-size: 11px;
  font-weight: 600;
  text-transform: uppercase;
}

.inspector-data-block pre {
  max-height: 160px;
  margin: 0;
  padding: 8px 9px;
  overflow: auto;
  border: 1px solid var(--t-border-l1);
  border-radius: 5px;
  color: var(--t-text-1);
  background: var(--t-bg-2);
  font:
    11px/16px ui-monospace,
    SFMono-Regular,
    Menlo,
    monospace;
  overflow-wrap: anywhere;
  white-space: pre-wrap;
}

.inspector-data-block > .trajectory-value {
  max-height: 280px;
  overflow: auto;
}

.inspector-data-grid {
  display: grid;
  gap: 8px;
  padding: 0 12px 10px;
}

.inspector-data-card {
  min-width: 0;
  padding: 8px 9px;
  border: 1px solid var(--t-border-l1);
  border-left: 3px solid var(--t-border-l1);
  border-radius: 6px;
  background: var(--t-bg-2);
}

.inspector-data-card-input {
  border-left-color: var(--t-context);
}

.inspector-data-card-output {
  border-left-color: var(--t-model);
}

.inspector-data-card-neutral {
  margin: 0 12px 10px;
  border-left-color: var(--t-text-3);
}

.inspector-data-card-heading {
  display: flex;
  align-items: center;
  gap: 7px;
  margin-bottom: 6px;
  color: var(--t-text-2);
  font-size: 11px;
  font-weight: 600;
}

.inspector-data-card-mark {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 27px;
  height: 16px;
  border-radius: 4px;
  color: var(--t-text-1);
  background: var(--t-bg-1);
  font:
    600 9px/16px ui-monospace,
    SFMono-Regular,
    Menlo,
    monospace;
}

.inspector-data-card > .trajectory-value {
  max-height: 300px;
  overflow: auto;
}

.inspector-json-card {
  min-height: 100px;
  max-height: 360px;
  margin: 0 12px 10px;
  overflow: auto;
  border: 1px solid var(--t-border-l1);
  border-radius: 6px;
  background: var(--t-bg-2);
}

.inspector-json-card .json-tree {
  background: transparent;
}

.inspector-timing-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 6px;
  padding: 10px 12px 2px;
}

.inspector-timing-card {
  min-width: 0;
  padding: 9px;
  border: 1px solid var(--t-border-l1);
  border-radius: 6px;
  background: var(--t-bg-2);
}

.inspector-timing-card-primary {
  border-color: color-mix(in srgb, var(--t-accent) 35%, var(--t-border-l1));
}

.token-usage-list {
  padding: 1px 14px 10px;
}

.token-usage-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  align-items: center;
  gap: 7px;
  padding: 6px 0;
}

.token-usage-label {
  display: flex;
  min-width: 0;
  align-items: center;
  gap: 6px;
  overflow: hidden;
  color: var(--t-text-2);
  font-size: 11px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.token-usage-row > strong {
  color: var(--t-text-1);
  font:
    600 11px/16px ui-monospace,
    SFMono-Regular,
    Menlo,
    monospace;
}

.token-usage-dot {
  width: 6px;
  height: 6px;
  flex: none;
  border-radius: 50%;
  background: var(--t-accent);
}

.token-usage-dot-output {
  background: var(--t-model);
}
.token-usage-dot-cached {
  background: var(--t-context);
}
.token-usage-dot-cache-creation {
  background: var(--t-stage);
}
.token-usage-dot-reasoning {
  background: var(--t-gate);
}

.token-usage-track {
  grid-column: 1 / -1;
  height: 4px;
  overflow: hidden;
  border-radius: 2px;
  background: var(--t-bg-2);
}

.token-usage-fill {
  display: block;
  height: 100%;
  border-radius: inherit;
  background: var(--t-accent);
}

.token-usage-fill-output {
  background: var(--t-model);
}
.token-usage-fill-cached {
  background: var(--t-context);
}
.token-usage-fill-cache-creation {
  background: var(--t-stage);
}
.token-usage-fill-reasoning {
  background: var(--t-gate);
}

.overview dd .sub {
  color: var(--t-text-3);
}

.overview-section {
  border-top: 1px solid var(--t-border-l1);
}

.overview-heading {
  margin: 0;
  padding: 6px 14px 2px;
  color: var(--t-text-2);
  font-size: 13px;
  font-weight: 600;
  user-select: none;
}

.span-event-list {
  margin: 4px 12px 8px;
  padding: 0;
  list-style: none;
  border: 1px solid var(--t-border-l1);
  border-radius: 5px;
  overflow: hidden;
}

.span-event-row {
  display: grid;
  grid-template-columns: 44px minmax(0, 1fr) auto;
  align-items: center;
  min-height: 26px;
  padding: 0 9px;
  gap: 8px;
  border-bottom: 1px solid var(--t-border-l1);
  font-size: 11px;
}

.span-event-row:last-child {
  border-bottom: 0;
}

.span-event-seq {
  color: var(--t-text-4);
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-variant-numeric: tabular-nums;
}

.span-event-type {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 1px;
  overflow: hidden;
  color: var(--t-text-1);
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.span-event-type strong,
.span-event-type small {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.span-event-type strong {
  font-size: 11px;
  font-weight: 500;
}

.span-event-type small {
  color: var(--t-text-4);
  font-size: 10px;
}

.span-event-time {
  color: var(--t-text-4);
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-variant-numeric: tabular-nums;
}

.trajectory-empty {
  margin: 0;
  padding: 48px 20px;
  border: 1px dashed var(--t-border-l2);
  border-radius: 8px;
  color: var(--t-text-4);
  font-size: 13px;
  text-align: center;
}

.trajectory-load-more {
  display: flex;
  justify-content: center;
  padding-top: 12px;
}

/* Scrollbars: only the ledger pane and JSON code blocks scroll, using the
   app-wide thin scrollbar convention. */
@media (max-width: 900px) {
  .table-pane {
    min-width: 0;
  }

  .trajectory-resize-handle {
    display: none;
  }

  .trajectory-inspector {
    position: absolute;
    z-index: 5;
    top: 0;
    right: 0;
    bottom: 0;
    width: min(92%, 420px);
    max-width: 92%;
    border-left-color: var(--t-border-l2);
    box-shadow: -12px 0 32px rgba(0, 0, 0, 0.14);
  }

  .waterfall-header,
  .waterfall-cell {
    display: none;
  }

  .event-cell {
    padding-left: calc(30px + var(--trajectory-indent, 0px)) !important;
  }

  .inspector-timing-grid {
    grid-template-columns: 1fr;
  }
}
</style>
