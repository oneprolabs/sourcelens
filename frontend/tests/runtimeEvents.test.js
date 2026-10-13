import assert from 'node:assert/strict'
import test from 'node:test'

import {
  activitiesForNode,
  applyRuntimeEvent,
  buildAssistantActivityGroups,
  buildWorkflowTree,
  calculateRunElapsedSeconds,
  createConversationAutoScroller,
  createRuntimeState,
  formatDocumentProgressText,
  formatActivityProgressText,
  getMessageTimestamp,
  isActiveProgressAncestor,
  normalizePlanSteps,
  normalizeStages,
  planStepDisplayStatus,
  scrollConversationToBottomAfterRender,
  selectCurrentWorkflowStage,
  selectCurrentAssistantActivity,
  selectLiveProgressText,
  selectStructuredProgress,
  summarizeStageProgress,
  summarizePlanProgress,
  terminalSyncEvent,
  runtimeOutcomeNotice,
  runtimeStateFor,
  workflowProgressSource
} from '../src/pages/lens/runtimeEvents.js'
import { createStreamTextBuffer } from '../src/pages/lens/streamBuffer.js'

test('restores active history without closing unfinished plans or Skill activity', () => {
  for (const status of ['queued', 'running', 'streaming', undefined]) {
    const state = runtimeStateFor({
      status,
      steps: [
        {
          visibility: 'user',
          event_type: 'plan.updated',
          payload: {
            revision: 1,
            steps: [
              { id: 'query', title: 'Query invoices', status: 'in_progress' },
              { id: 'summary', title: 'Summarize invoices', status: 'pending' }
            ]
          }
        },
        {
          visibility: 'user',
          event_type: 'activity.recorded',
          payload: {
            id: 'skill', kind: 'querying_data', stage_kind: 'data_query', status: 'in_progress',
            plan_step_id: 'query'
          }
        }
      ]
    })
    assert.deepEqual(state.plan.map((item) => item.status), ['in_progress', 'pending'])
    assert.equal(state.activities[0].status, 'in_progress')
    assert.equal(summarizePlanProgress(state.plan).isTerminal, false)
  }
})

test('restores genuine partial, failed, cancelled and clarification outcomes', () => {
  for (const status of ['done', 'failed', 'cancelled', 'awaiting_user_input']) {
    const state = runtimeStateFor({
      status,
      outcome: 'partial',
      termination_detail: { reason: 'execution_failed' },
      steps: [{
        visibility: 'user',
        event_type: 'plan.updated',
        payload: { revision: 1, steps: [
          { id: 'query', title: 'Query invoices', status: 'in_progress' },
          { id: 'summary', title: 'Summarize invoices', status: 'pending' }
        ] }
      }]
    })
    assert.equal(state.outcome, 'partial')
    assert.equal(state.terminationDetail.reason, 'execution_failed')
    assert.equal(state.plan[0].status, status === 'awaiting_user_input' ? 'awaiting_user_input' : 'failed')
  }
})

test('batches streamed answer deltas into one render frame', () => {
  const frames = []
  const chunks = []
  const buffer = createStreamTextBuffer({
    onFlush: (chunk) => chunks.push(chunk),
    schedule: (callback) => {
      frames.push(callback)
      return frames.length
    },
    cancel: () => {}
  })

  buffer.push('Hel')
  buffer.push('lo')

  assert.equal(frames.length, 1)
  assert.deepEqual(chunks, [])

  frames.shift()()
  assert.deepEqual(chunks, ['Hello'])
})

test('tracks the latest delegated assistant activity for live progress', () => {
  let state = createRuntimeState()

  state = applyRuntimeEvent(state, {
    agent_event: 'tool.read_file.invoke',
    activity: 'reading_context',
    assistant_name: 'Repository Analyst'
  })
  state = applyRuntimeEvent(state, {
    agent_event: 'tool.search_workspace.invoke',
    activity: 'searching_sources',
    assistant_name: 'Knowledge Guide',
    delegated_task: 'Find the latest deployment notes'
  })

  assert.deepEqual(selectCurrentAssistantActivity(state), {
    id: 2,
    kind: 'searchingSources',
    nodeId: 'legacy-runtime',
    count: 1,
    assistantName: 'Knowledge Guide',
    delegatedTask: 'Find the latest deployment notes'
  })
})

test('keeps identical delegated operations separate per assistant task', () => {
  let state = createRuntimeState()
  const event = (assistantName, delegatedTask) => ({
    visibility: 'user',
    event_type: 'activity.recorded',
    payload: {
      id: 'query-1',
      kind: 'querying_data',
      stage_kind: 'data_query',
      status: 'in_progress',
      assistant_name: assistantName,
      delegated_task: delegatedTask
    }
  })

  state = applyRuntimeEvent(state, event('Office', 'Inspect invoices'))
  state = applyRuntimeEvent(state, event('ttt', 'Inspect tickets'))

  assert.equal(state.activities.length, 2)
  assert.deepEqual(
    state.activities.map((item) => [item.assistantName, item.delegatedTask]),
    [
      ['Office', 'Inspect invoices'],
      ['ttt', 'Inspect tickets']
    ]
  )
})

test('retains delegated task metadata even before tool activity arrives', () => {
  const state = applyRuntimeEvent(createRuntimeState(), {
    agent_event: 'deepagents.runtime.start',
    assistant_name: 'Office',
    delegated_task: 'Inspect invoices'
  })

  assert.deepEqual(state.delegations, [
    { assistantName: 'Office', delegatedTask: 'Inspect invoices' }
  ])
})

test('exposes delegated tasks as progress when no child tool activity exists', () => {
  const progress = selectStructuredProgress({
    route: 'direct_answer',
    plan: [],
    stages: [],
    activities: [],
    delegations: [
      { assistantName: 'Office', delegatedTask: 'Inspect invoices' }
    ],
    standaloneActivities: false
  })

  assert.equal(progress.kind, 'activity')
  assert.deepEqual(progress.items, [])
})

test('builds compact assistant activity groups without truncating tasks', () => {
  const delegatedTask = 'Inspect the complete company knowledge base. '.repeat(
    60
  )
  const groups = buildAssistantActivityGroups({
    delegations: [{ assistantName: 'Company-Knowledge-Base', delegatedTask }],
    activities: [
      {
        id: 1,
        assistantName: 'Company-Knowledge-Base',
        kind: 'readingSources',
        count: 2
      },
      {
        id: 2,
        assistantName: 'Company-Knowledge-Base',
        kind: 'searchingSources',
        count: 1
      },
      {
        id: 3,
        assistantName: 'Company-Knowledge-Base',
        kind: 'queryingData',
        count: 3
      },
      {
        id: 4,
        assistantName: 'Company-Knowledge-Base',
        kind: 'preparingOutput',
        count: 1
      },
      {
        id: 5,
        assistantName: 'Company-Knowledge-Base',
        kind: 'readingSources',
        count: 4
      }
    ]
  })

  assert.equal(groups[0].tasks[0], delegatedTask.trim())
  assert.deepEqual(
    groups[0].summaryItems.map((item) => [item.kind, item.count]),
    [
      ['queryingData', 3],
      ['preparingOutput', 1],
      ['readingSources', 6]
    ]
  )
})

test('flushes pending stream text synchronously before completion', () => {
  const frames = []
  const chunks = []
  const buffer = createStreamTextBuffer({
    onFlush: (chunk) => chunks.push(chunk),
    schedule: (callback) => {
      frames.push(callback)
      return frames.length
    },
    cancel: () => {}
  })

  buffer.push('done')
  buffer.flush()

  assert.deepEqual(chunks, ['done'])
  assert.equal(frames.length, 1)
})

test('coalesces streamed answer scrolling and pauses after manual scroll', async () => {
  const element = {
    clientHeight: 100,
    scrollHeight: 640,
    scrollTop: 540
  }
  const frames = []
  const scroller = createConversationAutoScroller({
    getElement: () => element,
    waitForRender: async () => {},
    schedule: (callback) => {
      frames.push(callback)
      return frames.length
    },
    cancel: () => {}
  })

  assert.equal(scroller.request(), true)
  assert.equal(scroller.request(), false)
  assert.equal(frames.length, 1)

  element.scrollHeight = 720
  await frames.shift()()
  assert.equal(element.scrollTop, 720)

  element.scrollTop = 400
  scroller.handleScroll()
  assert.equal(scroller.request(), false)
  assert.equal(frames.length, 0)
})

test('waits for the conversation to render before scrolling to the bottom', async () => {
  let container = null

  const scrolled = await scrollConversationToBottomAfterRender(
    () => container,
    async () => {
      container = { scrollTop: 0, scrollHeight: 640 }
    }
  )

  assert.equal(scrolled, true)
  assert.equal(container.scrollTop, 640)
})

test('does nothing when the conversation container is not rendered', async () => {
  const scrolled = await scrollConversationToBottomAfterRender(
    () => null,
    async () => {}
  )

  assert.equal(scrolled, false)
})

test('uses run completion time for assistant messages', () => {
  const createdAt = '2026-07-27T05:00:00.000Z'
  const completedAt = '2026-07-27T05:03:12.000Z'

  assert.equal(
    getMessageTimestamp({
      role: 'assistant',
      created_at: createdAt,
      completed_at: completedAt
    }),
    completedAt
  )
  assert.equal(
    getMessageTimestamp({
      role: 'user',
      created_at: createdAt,
      completed_at: completedAt
    }),
    createdAt
  )
})

test('restores active run elapsed time from its server creation time', () => {
  assert.equal(
    calculateRunElapsedSeconds(
      { created_at: '2026-07-27T05:00:00.000Z' },
      Date.parse('2026-07-27T05:01:42.900Z')
    ),
    102
  )
})

test('caps elapsed time at the server finish time', () => {
  assert.equal(
    calculateRunElapsedSeconds(
      {
        created_at: '2026-07-27T05:00:00.000Z',
        finished_at: '2026-07-27T05:00:30.000Z'
      },
      Date.parse('2026-07-27T05:10:00.000Z')
    ),
    30
  )
})

test('returns zero for missing, invalid or future run timestamps', () => {
  const now = Date.parse('2026-07-27T05:00:00.000Z')

  assert.equal(calculateRunElapsedSeconds({}, now), 0)
  assert.equal(calculateRunElapsedSeconds({ created_at: 'invalid' }, now), 0)
  assert.equal(
    calculateRunElapsedSeconds({ created_at: '2026-07-27T05:00:01.000Z' }, now),
    0
  )
})

test('includes total time in completed agent activity summaries', () => {
  const text = formatActivityProgressText([{ count: 5 }, { count: 6 }], {
    durationSeconds: 74.4,
    terminal: true,
    translate: (_key, { count }) => `Completed ${count} activities`
  })

  assert.equal(text, 'Completed 11 activities · 1m 14s')
})

test('reduces route, phase, plan and capability events', () => {
  let state = createRuntimeState()
  state = applyRuntimeEvent(state, {
    event_type: 'route.selected',
    visibility: 'user',
    payload: {
      route: 'plan_execute',
      complexity: 'complex',
      evidence_requirement: 'none'
    }
  })
  state = applyRuntimeEvent(state, {
    event_type: 'phase.changed',
    visibility: 'user',
    payload: { phase: 'executing' }
  })
  state = applyRuntimeEvent(state, {
    event_type: 'plan.updated',
    visibility: 'user',
    payload: {
      steps: [
        { id: 'one', title: 'Inspect code', status: 'completed' },
        { id: 'two', title: 'Implement change', status: 'in_progress' }
      ]
    }
  })
  state = applyRuntimeEvent(state, {
    event_type: 'capability.blocked',
    visibility: 'user',
    payload: {
      capability: 'skill',
      reason: 'configuration',
      recovery: 'Ask an administrator to configure the Skill.'
    }
  })

  assert.equal(state.route, 'plan_execute')
  assert.equal(state.complexity, 'complex')
  assert.equal(state.evidenceRequirement, 'none')
  assert.equal(state.phase, 'executing')
  assert.equal(state.plan[1].status, 'in_progress')
  assert.equal(state.capabilityBlock.capability, 'skill')
})

test('keeps execution failures separate from capability availability', () => {
  let state = createRuntimeState()
  state = applyRuntimeEvent(state, {
    event_type: 'execution.failed',
    visibility: 'user',
    payload: {
      reason: 'execution_failed',
      capability: 'skill',
      error_type: 'transient'
    }
  })

  assert.equal(state.capabilityBlock, null)
  assert.equal(state.executionFailure.reason, 'execution_failed')
  assert.equal(state.executionFailure.error_type, 'transient')
})

test('keeps verification failures separate from tool execution failures', () => {
  let state = createRuntimeState()
  state = applyRuntimeEvent(state, {
    event_type: 'verification.failed',
    visibility: 'user',
    payload: {
      reason: 'evidence_unavailable',
      capability: 'skill',
      error_type: 'verification'
    }
  })

  assert.equal(state.executionFailure, null)
  assert.equal(state.verificationFailure.reason, 'evidence_unavailable')
  assert.equal(state.verificationFailure.error_type, 'verification')
})

test('shows one terminal notice when a verification failure has a partial answer', () => {
  const state = applyRuntimeEvent(createRuntimeState(), {
    type: 'done',
    outcome: 'partial',
    termination_detail: { reason: 'evidence_unavailable' }
  })

  assert.deepEqual(runtimeOutcomeNotice(state), {
    kind: 'verification',
    errorType: 'verification'
  })
})

test('restores the correct block card from terminal run details', () => {
  const capabilityState = applyRuntimeEvent(createRuntimeState(), {
    type: 'done',
    outcome: 'blocked',
    termination_detail: {
      reason: 'capability_unavailable',
      error_type: 'capability'
    }
  })
  const executionState = applyRuntimeEvent(createRuntimeState(), {
    type: 'done',
    outcome: 'blocked',
    termination_detail: {
      reason: 'execution_failed',
      error_type: 'transient'
    }
  })
  const verificationState = applyRuntimeEvent(createRuntimeState(), {
    type: 'done',
    outcome: 'blocked',
    termination_detail: {
      reason: 'evidence_unavailable',
      error_type: 'verification'
    }
  })

  assert.equal(capabilityState.capabilityBlock.error_type, 'capability')
  assert.equal(capabilityState.executionFailure, null)
  assert.equal(executionState.capabilityBlock, null)
  assert.equal(executionState.executionFailure.error_type, 'transient')
  assert.equal(verificationState.executionFailure, null)
  assert.equal(verificationState.verificationFailure.error_type, 'verification')
})

test('ignores internal events and applies terminal outcome', () => {
  const initial = createRuntimeState()
  const unchanged = applyRuntimeEvent(initial, {
    event_type: 'plan.updated',
    visibility: 'internal',
    payload: { steps: [{ title: 'secret' }] }
  })
  const terminal = applyRuntimeEvent(unchanged, {
    type: 'done',
    outcome: 'partial',
    termination_detail: { reason: 'capability_unavailable' }
  })

  assert.deepEqual(unchanged.plan, [])
  assert.equal(terminal.outcome, 'partial')
  assert.equal(terminal.terminationDetail.reason, 'capability_unavailable')
})

test('summarizes plan progress for a compact answer header', () => {
  assert.deepEqual(
    summarizePlanProgress([
      { id: 'one', title: 'Inspect', status: 'completed' },
      { id: 'two', title: 'Implement', status: 'in_progress' },
      { id: 'three', title: 'Verify', status: 'pending' }
    ]),
    {
      completed: 1,
      total: 3,
      currentTitle: 'Implement',
      isComplete: false,
      isTerminal: false
    }
  )
  assert.equal(summarizePlanProgress([]), null)
})

test('marks incomplete plan progress as terminal after the run ends', () => {
  assert.deepEqual(
    summarizePlanProgress(
      [
        { id: 'one', title: 'Inspect', status: 'completed' },
        { id: 'two', title: 'Implement', status: 'in_progress' },
        { id: 'three', title: 'Verify', status: 'pending' }
      ],
      { terminal: true }
    ),
    {
      completed: 1,
      total: 3,
      currentTitle: 'Implement',
      isComplete: false,
      isTerminal: true
    }
  )
})

test('promotes the current pending plan step while the run is active', () => {
  const steps = [
    { id: 'one', title: 'Inspect', status: 'completed' },
    { id: 'two', title: 'Implement', status: 'completed' },
    { id: 'three', title: 'Verify', status: 'pending' },
    { id: 'four', title: 'Release', status: 'pending' }
  ]
  assert.equal(
    planStepDisplayStatus(steps[0], steps, { active: true }),
    'completed'
  )
  assert.equal(
    planStepDisplayStatus(steps[2], steps, { active: true }),
    'in_progress'
  )
  assert.equal(
    planStepDisplayStatus(steps[3], steps, { active: true }),
    'pending'
  )
})

test('keeps the reported status when the run is not active', () => {
  const steps = [
    { id: 'one', status: 'completed' },
    { id: 'two', status: 'pending' }
  ]
  assert.equal(planStepDisplayStatus(steps[1], steps), 'pending')
  assert.equal(
    planStepDisplayStatus(steps[1], steps, { active: false }),
    'pending'
  )
})

test('does not promote a step past an earlier in_progress step', () => {
  const steps = [
    { id: 'one', status: 'in_progress' },
    { id: 'two', status: 'pending' }
  ]
  assert.equal(
    planStepDisplayStatus(steps[0], steps, { active: true }),
    'in_progress'
  )
  assert.equal(
    planStepDisplayStatus(steps[1], steps, { active: true }),
    'pending'
  )
})

test('shows real plan completion before lower-level execution details', () => {
  assert.equal(
    selectLiveProgressText({
      planProgressText: 'Completed 1/3 · Implement change',
      latestStep: 'Reading files',
      phaseText: 'Executing',
      activityMessage: 'Loading resources',
      fallbackText: 'Running'
    }),
    'Completed 1/3 · Implement change'
  )
})

test('shows final answer synthesis after all plan steps complete', () => {
  assert.equal(
    selectLiveProgressText({
      finalAnswerProgressText: 'Plan completed 4/4 · Generating final answer…',
      structuredProgressText: 'Completed 4/4',
      planProgressText: 'Completed 4/4',
      phaseText: 'Preparing the answer',
      fallbackText: 'Generating'
    }),
    'Plan completed 4/4 · Generating final answer…'
  )
})

test('keeps the legacy stage status for non-General Chat modes', () => {
  assert.equal(
    selectLiveProgressText({
      stageProgressText: 'Completed 1/3 · Execute required operations',
      latestStep: 'Reading files',
      phaseText: 'Executing',
      activityMessage: 'Calling a tool',
      fallbackText: 'Running'
    }),
    'Completed 1/3 · Execute required operations'
  )
})

test('isolates General Chat workflow trees from legacy progress modes', () => {
  const stages = [{ id: 'execute', title: 'Execute', status: 'in_progress' }]
  const plan = [{ id: 'step-1', title: 'Inspect', status: 'in_progress' }]
  const activities = [
    {
      id: 'activity-1',
      taskId: 'step-1',
      stageId: 'step-1:order_query',
      stageKind: 'order_query',
      kind: 'query_orders',
      status: 'in_progress',
      startDate: '2026-07-20',
      endDate: '2026-07-26',
      structured: true
    }
  ]

  const general = selectStructuredProgress({
    route: 'plan_execute',
    plan,
    activities,
    stages
  })
  assert.equal(general.kind, 'workflow')
  assert.equal(general.hasPlan, true)
  assert.equal(general.tasks[0].stages[0].steps[0].id, 'activity-1')
  assert.deepEqual(selectStructuredProgress({ plan, activities, stages }), {
    kind: 'plan',
    items: plan
  })
  assert.deepEqual(selectStructuredProgress({ plan: [], activities, stages }), {
    kind: 'stage',
    items: stages
  })
  assert.deepEqual(
    selectStructuredProgress({ plan: [], activities: [], stages }),
    {
      kind: 'stage',
      items: stages
    }
  )
  assert.deepEqual(
    selectStructuredProgress({
      plan: [],
      activities: [],
      stages: []
    }),
    {
      kind: null,
      items: []
    }
  )
})

test('shows standalone legacy activities without plan or stage nodes', () => {
  let state = createRuntimeState()
  for (const agent_event of [
    'tool.search_workspace.start',
    'tool.read_workspace_file.start'
  ]) {
    state = applyRuntimeEvent(state, { agent_event })
  }

  assert.deepEqual(
    state.activities.map((item) => ({
      nodeId: item.nodeId,
      kind: item.kind
    })),
    [
      { nodeId: 'legacy-runtime', kind: 'searchingSources' },
      { nodeId: 'legacy-runtime', kind: 'readingSources' }
    ]
  )
  assert.deepEqual(
    selectStructuredProgress({
      route: null,
      plan: [],
      stages: [],
      activities: state.activities,
      standaloneActivities: true
    }),
    {
      kind: 'activity',
      items: state.activities
    }
  )
  assert.deepEqual(
    selectStructuredProgress({
      route: 'direct_execute',
      plan: [],
      stages: [],
      activities: state.activities,
      standaloneActivities: true
    }),
    { kind: null, items: [] }
  )
})

test('uses the active real operation as the General Chat stage', () => {
  const tasks = buildWorkflowTree(
    [],
    [
      {
        id: 'capability-1',
        taskId: 'task-execute',
        stageId: 'task-execute:order_query',
        stageKind: 'order_query',
        kind: 'checking_capability',
        status: 'completed',
        structured: true
      },
      {
        id: 'orders-1',
        taskId: 'task-execute',
        stageId: 'task-execute:data_query',
        stageKind: 'data_query',
        kind: 'query_orders',
        status: 'in_progress',
        structured: true
      }
    ]
  )

  assert.equal(selectCurrentWorkflowStage(tasks).kind, 'data_query')
})

test('records and completes a replayable real order-query activity', () => {
  let state = createRuntimeState()
  state = applyRuntimeEvent(state, {
    event_type: 'activity.recorded',
    visibility: 'user',
    payload: {
      id: 'activity-123',
      kind: 'query_orders',
      stage_kind: 'order_query',
      status: 'in_progress',
      start_date: '2026-07-20',
      end_date: '2026-07-26'
    }
  })
  state = applyRuntimeEvent(state, {
    event_type: 'activity.recorded',
    visibility: 'user',
    payload: {
      id: 'activity-123',
      kind: 'querying_data',
      stage_kind: 'data_query',
      status: 'completed'
    }
  })

  assert.deepEqual(state.activities, [
    {
      id: 'activity-123',
      taskId: 'task-execute',
      stageId: 'task-execute:order_query',
      stageKind: 'order_query',
      kind: 'query_orders',
      status: 'completed',
      startDate: '2026-07-20',
      endDate: '2026-07-26',
      planRevision: 0,
      structured: true
    }
  ])
})

test('hides model rounds when real General Chat operations exist', () => {
  let state = applyRuntimeEvent(createRuntimeState(), {
    event_type: 'route.selected',
    visibility: 'user',
    payload: { route: 'direct_execute' }
  })
  for (let round = 1; round <= 3; round += 1) {
    for (const status of ['in_progress', 'completed']) {
      state = applyRuntimeEvent(state, {
        event_type: 'activity.recorded',
        visibility: 'user',
        payload: {
          id: `model-round-${round}`,
          kind: 'analyzing_request',
          stage_kind: 'reasoning',
          status,
          round
        }
      })
    }
  }
  for (const payload of [
    {
      id: 'capability-1',
      kind: 'checking_capability',
      stage_kind: 'order_query',
      status: 'completed'
    },
    {
      id: 'detail-1',
      kind: 'get_order_detail',
      stage_kind: 'order_query',
      status: 'completed',
      order_ref: 'HWINSTAD2025071509'
    }
  ]) {
    state = applyRuntimeEvent(state, {
      event_type: 'activity.recorded',
      visibility: 'user',
      payload
    })
  }

  const progress = selectStructuredProgress({
    route: state.route,
    plan: state.plan,
    stages: state.stages,
    activities: state.activities
  })
  assert.equal(progress.kind, 'workflow')
  assert.equal(progress.tasks.length, 1)
  assert.equal(progress.tasks[0].kind, 'get_order_detail')
  assert.equal(progress.tasks[0].orderRef, 'HWINSTAD2025071509')
  assert.deepEqual(
    progress.tasks[0].stages.map((stage) => stage.kind),
    ['order_query']
  )
  assert.deepEqual(
    progress.tasks[0].stages[0].steps.map((step) => step.kind),
    ['checking_capability', 'get_order_detail']
  )
})

test('does not build placeholder workflow nodes from model rounds', () => {
  const activities = [1, 2, 3].map((round) => ({
    id: `model-round-${round}`,
    taskId: 'task-execute',
    stageId: 'task-execute:reasoning',
    stageKind: 'reasoning',
    kind: 'analyzing_request',
    status: 'completed',
    round,
    structured: true
  }))

  const tasks = buildWorkflowTree([], activities)

  assert.deepEqual(tasks, [])
})

test('identifies active ancestors without suppressing parallel leaves', () => {
  const activeSteps = [
    { id: 'query-primary', status: 'in_progress' },
    { id: 'query-secondary', status: 'in_progress' }
  ]
  const activeStage = {
    id: 'order-query',
    status: 'in_progress',
    steps: activeSteps
  }
  const activeTask = {
    id: 'query-orders',
    status: 'in_progress',
    stages: [activeStage]
  }

  assert.equal(isActiveProgressAncestor(activeTask, activeTask.stages), true)
  assert.equal(isActiveProgressAncestor(activeStage, activeStage.steps), true)
  for (const step of activeSteps) {
    assert.equal(isActiveProgressAncestor(step, []), false)
  }
})

test('keeps an active node animated when none of its children are active', () => {
  const activeStage = { id: 'order-query', status: 'in_progress' }
  const completedSteps = [
    { id: 'check-login', status: 'completed' },
    { id: 'query-orders', status: 'completed' }
  ]

  assert.equal(isActiveProgressAncestor(activeStage, completedSteps), false)
})

test('uses a business task fallback instead of a placeholder task', () => {
  const tasks = buildWorkflowTree(
    [],
    [
      {
        id: 'query-1',
        taskId: 'task-execute',
        stageId: 'task-execute:data_query',
        stageKind: 'data_query',
        kind: 'querying_data',
        status: 'completed',
        structured: true
      }
    ]
  )

  assert.equal(tasks.length, 1)
  assert.equal(tasks[0].kind, 'query_data')
})

test('builds preparation, operation and summary from real activities', () => {
  let state = createRuntimeState()
  for (const payload of [
    {
      id: 'tool-version',
      stage_kind: 'preparation',
      kind: 'checking_tool',
      status: 'completed'
    },
    {
      id: 'auth-status',
      stage_kind: 'preparation',
      kind: 'checking_authentication',
      status: 'completed'
    },
    {
      id: 'auth-login',
      stage_kind: 'preparation',
      kind: 'authenticating',
      status: 'completed'
    },
    {
      id: 'order-query',
      stage_kind: 'order_query',
      kind: 'query_orders',
      status: 'completed',
      order_ref: 'HWINSTAD2025071509'
    },
    {
      id: 'summarize-results',
      stage_kind: 'result_analysis',
      kind: 'summarizing_results',
      status: 'completed',
      order_ref: 'HWINSTAD2025071509'
    }
  ]) {
    state = applyRuntimeEvent(state, {
      event_type: 'activity.recorded',
      visibility: 'user',
      payload
    })
  }
  const tasks = buildWorkflowTree([], state.activities)

  assert.equal(tasks[0].kind, 'query_orders')
  assert.deepEqual(
    tasks[0].stages.map((stage) => stage.kind),
    ['preparation', 'order_query', 'result_analysis']
  )
  assert.deepEqual(
    tasks[0].stages.map((stage) => stage.steps.map((step) => step.kind)),
    [
      ['checking_tool', 'checking_authentication', 'authenticating'],
      ['query_orders'],
      ['summarizing_results']
    ]
  )
})

test('keeps non-order assistants on their actual generic operation path', () => {
  let state = createRuntimeState()
  for (const payload of [
    {
      id: 'ticket-query',
      stage_kind: 'data_query',
      kind: 'querying_data',
      status: 'completed'
    },
    {
      id: 'summarize-results',
      stage_kind: 'result_analysis',
      kind: 'summarizing_results',
      status: 'completed'
    }
  ]) {
    state = applyRuntimeEvent(state, {
      event_type: 'activity.recorded',
      visibility: 'user',
      payload
    })
  }
  const tasks = buildWorkflowTree([], state.activities)

  assert.equal(tasks[0].kind, 'query_data')
  assert.deepEqual(
    tasks[0].stages.map((stage) => stage.kind),
    ['data_query', 'result_analysis']
  )
  assert.equal(
    tasks[0].stages.some((stage) => stage.kind === 'order_query'),
    false
  )
})

test('coalesces repeated steps with the same public operation context', () => {
  let state = createRuntimeState()
  for (const payload of [
    {
      id: 'order-get-help',
      stage_kind: 'preparation',
      kind: 'reading_order_commands',
      status: 'completed'
    },
    {
      id: 'order-list-help',
      stage_kind: 'preparation',
      kind: 'reading_order_commands',
      status: 'completed'
    }
  ]) {
    state = applyRuntimeEvent(state, {
      event_type: 'activity.recorded',
      visibility: 'user',
      payload
    })
  }

  const steps = buildWorkflowTree([], state.activities)[0].stages[0].steps

  assert.equal(steps.length, 1)
  assert.equal(steps[0].kind, 'reading_order_commands')
  assert.equal(steps[0].status, 'completed')
})

test('coalesces a failed step with a successful semantic retry', () => {
  let state = createRuntimeState()
  for (const payload of [
    {
      id: 'analysis-attempt-1',
      stage_kind: 'result_analysis',
      kind: 'analyzing_results',
      status: 'in_progress'
    },
    {
      id: 'analysis-attempt-1',
      stage_kind: 'result_analysis',
      kind: 'analyzing_results',
      status: 'failed'
    },
    {
      id: 'analysis-attempt-2',
      stage_kind: 'result_analysis',
      kind: 'analyzing_results',
      status: 'in_progress'
    },
    {
      id: 'analysis-attempt-2',
      stage_kind: 'result_analysis',
      kind: 'analyzing_results',
      status: 'completed'
    }
  ]) {
    state = applyRuntimeEvent(state, {
      event_type: 'activity.recorded',
      visibility: 'user',
      payload
    })
  }

  const steps = buildWorkflowTree([], state.activities)[0].stages[0].steps

  assert.equal(steps.length, 1)
  assert.equal(steps[0].status, 'completed')
})

test('keeps a recoverable semantic failure active until the run ends', () => {
  let state = createRuntimeState()
  for (const status of ['in_progress', 'failed']) {
    state = applyRuntimeEvent(state, {
      event_type: 'activity.recorded',
      visibility: 'user',
      payload: {
        id: 'analysis-attempt-1',
        stage_kind: 'result_analysis',
        kind: 'analyzing_results',
        status
      }
    })
  }

  assert.equal(state.activities[0].status, 'in_progress')

  state = applyRuntimeEvent(state, { type: 'done', outcome: 'partial' })

  assert.equal(state.activities[0].status, 'failed')
})

test('does not downgrade a successful semantic retry after a later failure', () => {
  let state = createRuntimeState()
  for (const [id, status] of [
    ['analysis-attempt-1', 'completed'],
    ['analysis-attempt-2', 'in_progress'],
    ['analysis-attempt-2', 'failed']
  ]) {
    state = applyRuntimeEvent(state, {
      event_type: 'activity.recorded',
      visibility: 'user',
      payload: {
        id,
        stage_kind: 'result_analysis',
        kind: 'analyzing_results',
        status
      }
    })
  }

  assert.equal(state.activities[0].status, 'completed')
})

test('waits for a terminal SSE payload before closing live progress', () => {
  assert.equal(terminalSyncEvent({ type: 'status', status: 'done' }), null)
  assert.deepEqual(
    terminalSyncEvent({
      type: 'sync',
      status: 'done',
      outcome: 'partial',
      termination_detail: { reason: 'token_budget_wrapup' }
    }),
    {
      type: 'done',
      outcome: 'partial',
      termination_detail: { reason: 'token_budget_wrapup' }
    }
  )
  assert.deepEqual(
    terminalSyncEvent({
      type: 'sync',
      status: 'awaiting_user_input',
      outcome: 'blocked',
      termination_detail: {
        reason: 'needs_user_input',
        request: {
          request_id: 'clarification-1',
          question: 'Which environment should I inspect?',
          reason: 'ambiguous_scope',
          answer_type: 'text'
        }
      }
    }),
    {
      type: 'awaiting_user_input',
      outcome: 'blocked',
      termination_detail: {
        reason: 'needs_user_input',
        request: {
          request_id: 'clarification-1',
          question: 'Which environment should I inspect?',
          reason: 'ambiguous_scope',
          answer_type: 'text'
        }
      }
    }
  )
})

test('keeps a text clarification out of retry failure state', () => {
  const state = applyRuntimeEvent(createRuntimeState(), {
    type: 'awaiting_user_input',
    status: 'awaiting_user_input',
    outcome: 'blocked',
    termination_detail: {
      reason: 'needs_user_input',
      request: {
        request_id: 'clarification-1',
        question: 'Which environment should I inspect?',
        reason: 'ambiguous_scope',
        answer_type: 'text'
      }
    }
  })

  assert.equal(state.outcome, 'blocked')
  assert.equal(state.clarificationRequest.request_id, 'clarification-1')
  assert.equal(state.plan.length, 0)
  assert.equal(state.executionFailure, null)
  assert.equal(state.verificationFailure, null)
})

test('attaches final summary to the last real plan task', () => {
  let state = applyRuntimeEvent(createRuntimeState(), {
    event_type: 'plan.updated',
    visibility: 'user',
    payload: {
      revision: 1,
      steps: [
        { id: 'step-1', title: '查询订单', status: 'completed' },
        { id: 'step-2', title: '生成报告', status: 'completed' }
      ]
    }
  })
  state = applyRuntimeEvent(state, {
    event_type: 'activity.recorded',
    visibility: 'user',
    payload: {
      id: 'summarize-results',
      stage_kind: 'result_analysis',
      kind: 'summarizing_results',
      status: 'completed'
    }
  })

  const tasks = buildWorkflowTree(state.plan, state.activities)

  assert.equal(tasks.length, 2)
  assert.equal(tasks[1].stages[0].steps[0].kind, 'summarizing_results')
})

test('migrates pre-plan activities into a late five-task plan', () => {
  let state = createRuntimeState()
  const activities = [
    ['auth', 'checking_authentication', 'preparation'],
    ['orders', 'query_orders', 'order_query'],
    ['validation', 'analyzing_results', 'result_analysis'],
    ['details', 'get_order_detail', 'order_query']
  ]
  for (const [id, kind, stage_kind] of activities) {
    state = applyRuntimeEvent(state, {
      event_type: 'activity.recorded',
      visibility: 'user',
      payload: { id, kind, stage_kind, status: 'completed' }
    })
  }

  state = applyRuntimeEvent(state, {
    event_type: 'plan.updated',
    visibility: 'user',
    payload: {
      revision: 1,
      steps: [
        {
          id: 'step-1',
          title: 'Verify Income authentication',
          status: 'completed'
        },
        { id: 'step-2', title: 'Query all July orders', status: 'completed' },
        { id: 'step-3', title: 'Validate all records', status: 'completed' },
        { id: 'step-4', title: 'Collect order details', status: 'completed' },
        {
          id: 'step-5',
          title: 'Generate Markdown report',
          status: 'in_progress'
        }
      ]
    }
  })

  assert.equal(
    state.activities.some((item) => item.taskId === 'task-execute'),
    false
  )
  let tasks = buildWorkflowTree(state.plan, state.activities)
  let progress = summarizePlanProgress(tasks)
  assert.equal(tasks.length, 5)
  assert.deepEqual(
    { completed: progress.completed, total: progress.total },
    { completed: 4, total: 5 }
  )

  state = applyRuntimeEvent(state, {
    event_type: 'plan.updated',
    visibility: 'user',
    payload: {
      revision: 2,
      steps: state.plan.map((item) => ({ ...item, status: 'completed' }))
    }
  })
  state = applyRuntimeEvent(state, { type: 'done', outcome: 'completed' })
  tasks = buildWorkflowTree(state.plan, state.activities)
  progress = summarizePlanProgress(tasks, { terminal: true })

  assert.deepEqual(
    { completed: progress.completed, total: progress.total },
    { completed: 5, total: 5 }
  )
})

test('uses plan tasks for the progress count when a plan exists', () => {
  const tasks = [
    { id: 'step-1', status: 'completed', stages: [{}] },
    { id: 'step-2', status: 'completed', stages: [{}] },
    { id: 'step-3', status: 'completed', stages: [{}] },
    { id: 'step-4', status: 'in_progress', stages: [] }
  ]

  const source = workflowProgressSource(tasks, true)
  const progress = summarizePlanProgress(source.items)

  assert.equal(source.kind, 'plan')
  assert.equal(progress.completed, 3)
  assert.equal(progress.total, 4)
})

test('completes a plan task when all of its workflow stages complete', () => {
  const tasks = buildWorkflowTree(
    [{ id: 'step-1', title: 'Query orders', status: 'in_progress' }],
    [
      {
        id: 'query-orders',
        taskId: 'step-1',
        stageId: 'step-1:order_query',
        stageKind: 'order_query',
        kind: 'query_orders',
        status: 'completed',
        structured: true
      }
    ]
  )

  assert.equal(tasks[0].status, 'completed')
})

test('does not regress an explicit plan terminal state from child activity', () => {
  const activity = {
    id: 'query-orders',
    taskId: 'step-1',
    stageId: 'step-1:order_query',
    stageKind: 'order_query',
    kind: 'query_orders',
    structured: true
  }

  const completed = buildWorkflowTree(
    [{ id: 'step-1', title: 'Query orders', status: 'completed' }],
    [{ ...activity, status: 'in_progress' }]
  )
  const failed = buildWorkflowTree(
    [{ id: 'step-1', title: 'Query orders', status: 'failed' }],
    [{ ...activity, status: 'completed' }]
  )

  assert.equal(completed[0].status, 'completed')
  assert.equal(failed[0].status, 'failed')
})

test('keeps plan task titles stable across later revisions', () => {
  let state = createRuntimeState()
  state = applyRuntimeEvent(state, {
    event_type: 'plan.updated',
    visibility: 'user',
    payload: {
      revision: 1,
      steps: [
        {
          id: 'step-1',
          title: '查询订单',
          status: 'in_progress'
        }
      ]
    }
  })
  state = applyRuntimeEvent(state, {
    event_type: 'plan.updated',
    visibility: 'user',
    payload: {
      revision: 2,
      steps: [
        {
          id: 'step-1',
          title: '重新命名后的任务',
          status: 'completed'
        }
      ]
    }
  })

  assert.equal(state.plan[0].title, '查询订单')
  assert.equal(state.plan[0].status, 'completed')
})

test('keeps the initial plan shape when later revisions append tasks', () => {
  let state = createRuntimeState()
  state = applyRuntimeEvent(state, {
    event_type: 'plan.updated',
    visibility: 'user',
    payload: {
      revision: 1,
      steps: [
        { id: 'step-1', title: 'Query orders', status: 'in_progress' },
        { id: 'step-2', title: 'Summarize results', status: 'pending' }
      ]
    }
  })
  state = applyRuntimeEvent(state, {
    event_type: 'plan.updated',
    visibility: 'user',
    payload: {
      revision: 2,
      steps: [
        { id: 'step-1', title: 'Query orders', status: 'completed' },
        { id: 'step-2', title: 'Summarize results', status: 'in_progress' },
        { id: 'step-3', title: 'Check totals', status: 'pending' }
      ]
    }
  })

  assert.deepEqual(state.plan, [
    { id: 'step-1', title: 'Query orders', status: 'completed' },
    { id: 'step-2', title: 'Summarize results', status: 'in_progress' }
  ])
})

test('moves trailing stages to tasks completed by a batched revision', () => {
  let state = applyRuntimeEvent(createRuntimeState(), {
    event_type: 'plan.updated',
    visibility: 'user',
    payload: {
      revision: 2,
      steps: [
        { id: 'step-1', title: '登录', status: 'completed' },
        { id: 'step-2', title: '查询订单', status: 'in_progress' },
        { id: 'step-3', title: '统计订单', status: 'pending' },
        { id: 'step-4', title: '生成报告', status: 'pending' }
      ]
    }
  })
  for (const payload of [
    {
      id: 'query-orders',
      stage_kind: 'order_query',
      kind: 'query_orders',
      status: 'completed'
    },
    {
      id: 'count-orders',
      stage_kind: 'result_analysis',
      kind: 'count_results',
      status: 'completed'
    }
  ]) {
    state = applyRuntimeEvent(state, {
      event_type: 'activity.recorded',
      visibility: 'user',
      payload
    })
  }
  state = applyRuntimeEvent(state, {
    event_type: 'plan.updated',
    visibility: 'user',
    payload: {
      revision: 3,
      steps: [
        { id: 'step-1', title: '登录', status: 'completed' },
        { id: 'step-2', title: '查询订单', status: 'completed' },
        { id: 'step-3', title: '统计订单', status: 'completed' },
        { id: 'step-4', title: '生成报告', status: 'in_progress' }
      ]
    }
  })

  const tasks = buildWorkflowTree(state.plan, state.activities)

  assert.deepEqual(
    tasks[1].stages.map((stage) => stage.kind),
    ['order_query']
  )
  assert.deepEqual(
    tasks[2].stages.map((stage) => stage.kind),
    ['result_analysis']
  )
})

test('accepts real order-detail and command-discovery activities', () => {
  let state = createRuntimeState()
  for (const [id, kind] of [
    ['help-123', 'reading_order_commands'],
    ['detail-123', 'get_order_detail']
  ]) {
    state = applyRuntimeEvent(state, {
      event_type: 'activity.recorded',
      visibility: 'user',
      payload: {
        id,
        kind,
        stage_kind: 'order_query',
        status: 'in_progress'
      }
    })
  }

  assert.deepEqual(
    state.activities.map((item) => item.kind),
    ['reading_order_commands', 'get_order_detail']
  )
})

test('drops invalid activity dates and fails open work on partial completion', () => {
  let state = applyRuntimeEvent(createRuntimeState(), {
    event_type: 'activity.recorded',
    visibility: 'user',
    payload: {
      id: 'activity-123',
      kind: 'query_orders',
      stage_kind: 'order_query',
      status: 'in_progress',
      start_date: '2026-02-30',
      end_date: '2026-03-01'
    }
  })
  state = applyRuntimeEvent(state, { type: 'done', outcome: 'partial' })

  assert.equal(state.activities[0].startDate, undefined)
  assert.equal(state.activities[0].endDate, '2026-03-01')
  assert.equal(state.activities[0].status, 'failed')
})

test('closes every legacy and General Chat spinner at terminal failure', () => {
  let state = createRuntimeState()
  state = applyRuntimeEvent(state, {
    event_type: 'plan.updated',
    visibility: 'user',
    payload: {
      revision: 1,
      steps: [
        { id: 'one', title: 'Query orders', status: 'in_progress' },
        { id: 'two', title: 'Summarize', status: 'pending' }
      ]
    }
  })
  state = applyRuntimeEvent(state, {
    event_type: 'stage.updated',
    visibility: 'user',
    payload: {
      id: 'query',
      title: 'Query orders',
      status: 'in_progress',
      order: 1,
      revision: 1
    }
  })
  state = applyRuntimeEvent(state, {
    event_type: 'activity.recorded',
    visibility: 'user',
    payload: {
      id: 'query-1',
      kind: 'query_orders',
      stage_kind: 'order_query',
      status: 'in_progress',
      start_date: '2026-07-20',
      end_date: '2026-07-26'
    }
  })
  state = applyRuntimeEvent(state, { type: 'error' })

  assert.deepEqual(
    state.plan.map((item) => item.status),
    ['failed', 'skipped']
  )
  assert.equal(state.stages[0].status, 'failed')
  assert.equal(state.activities[0].status, 'failed')
})

test('generic activity cannot evict a real path from the bounded history', () => {
  let state = applyRuntimeEvent(createRuntimeState(), {
    event_type: 'activity.recorded',
    visibility: 'user',
    payload: {
      id: 'activity-123',
      kind: 'get_order_detail',
      stage_kind: 'order_query',
      status: 'completed'
    }
  })
  state = applyRuntimeEvent(state, {
    event_type: 'stage.updated',
    visibility: 'user',
    payload: {
      id: 'execute',
      title: 'Execute',
      status: 'in_progress',
      revision: 1
    }
  })
  for (let index = 0; index < 25; index += 1) {
    state = applyRuntimeEvent(state, {
      agent_event:
        index % 2 === 0
          ? 'tool.read_file.invoke'
          : 'tool.search_workspace.invoke'
    })
  }

  assert.equal(state.activities.filter((item) => item.structured).length, 1)
  assert.equal(activitiesForNode(state, 'execute').length, 20)
})

test('keeps model rounds out of the bounded General Chat hierarchy', () => {
  let state = createRuntimeState()
  for (let index = 1; index <= 30; index += 1) {
    state = applyRuntimeEvent(state, {
      event_type: 'activity.recorded',
      visibility: 'user',
      payload: {
        id: `model-round-${index}`,
        kind: 'analyzing_request',
        stage_kind: 'reasoning',
        status: 'completed',
        round: index
      }
    })
  }

  assert.equal(state.activities.filter((item) => item.structured).length, 0)
})

test('keeps raw tool activity out of the primary progress text', () => {
  assert.equal(
    selectLiveProgressText({
      planProgressText: '',
      latestStep: 'Loading context...',
      phaseText: 'Understanding the request',
      activityMessage: 'Calling a tool',
      fallbackText: 'Running'
    }),
    'Understanding the request'
  )
})

test('falls back through phase, activity and run status', () => {
  assert.equal(
    selectLiveProgressText({
      phaseText: 'Analyzing',
      activityMessage: 'Loading resources',
      fallbackText: 'Running'
    }),
    'Analyzing'
  )
  assert.equal(
    selectLiveProgressText({
      activityMessage: 'Loading resources',
      fallbackText: 'Running'
    }),
    'Loading resources'
  )
  assert.equal(selectLiveProgressText({ fallbackText: 'Waiting' }), 'Waiting')
})

test('formats active document preparation without exposing document data', () => {
  const translate = (key, values) => `${key}:${JSON.stringify(values)}`

  assert.equal(
    formatDocumentProgressText(
      {
        stage: 'downloading',
        documentIndex: 1,
        documentTotal: 2
      },
      translate
    ),
    'lens.chat.runtime.documentProgress.downloading:{"current":1,"total":2}'
  )
  assert.equal(
    formatDocumentProgressText(
      {
        stage: 'recognizing_images',
        imageCompleted: 3,
        imageTotal: 5
      },
      translate
    ),
    'lens.chat.runtime.documentProgress.recognizingImages:{"completed":3,"total":5}'
  )
  assert.equal(
    formatDocumentProgressText(
      {
        stage: 'extracting_text',
        documentIndex: 2,
        documentTotal: 2
      },
      translate
    ),
    'lens.chat.runtime.documentProgress.extractingText:{"current":2,"total":2}'
  )
  assert.equal(formatDocumentProgressText({ stage: 'ready' }, translate), '')
})

test('shows document preparation before generic run status', () => {
  assert.equal(
    selectLiveProgressText({
      documentProgressText: 'Recognizing document images 3/5',
      phaseText: 'Analyzing',
      fallbackText: 'Generating'
    }),
    'Recognizing document images 3/5'
  )
})

test('ignores stale plan revisions after reconnect', () => {
  let state = createRuntimeState()
  state = applyRuntimeEvent(state, {
    event_type: 'plan.updated',
    visibility: 'user',
    payload: {
      revision: 2,
      steps: [{ id: 'new', title: 'New plan', status: 'in_progress' }]
    }
  })
  state = applyRuntimeEvent(state, {
    event_type: 'plan.updated',
    visibility: 'user',
    payload: {
      revision: 1,
      steps: [{ id: 'old', title: 'Old plan', status: 'pending' }]
    }
  })

  assert.equal(state.planRevision, 2)
  assert.equal(state.plan[0].title, 'New plan')
})

test('keeps the latest document progress after reconnect', () => {
  let state = createRuntimeState()
  state = applyRuntimeEvent(state, {
    event_type: 'document.progress',
    visibility: 'user',
    payload: {
      revision: 2,
      stage: 'recognizing_images',
      document_index: 1,
      document_total: 1,
      image_completed: 3,
      image_total: 5
    }
  })
  state = applyRuntimeEvent(state, {
    event_type: 'document.progress',
    visibility: 'user',
    payload: {
      revision: 1,
      stage: 'extracting_text',
      document_index: 1,
      document_total: 1
    }
  })

  assert.equal(state.documentProgressRevision, 2)
  assert.deepEqual(state.documentProgress, {
    revision: 2,
    stage: 'recognizing_images',
    documentIndex: 1,
    documentTotal: 1,
    imageCompleted: 3,
    imageTotal: 5
  })
})

test('reduces ordered direct-execution stages and ignores stale revisions', () => {
  let state = createRuntimeState()
  state = applyRuntimeEvent(state, {
    event_type: 'stage.updated',
    visibility: 'user',
    payload: {
      id: 'execute',
      title: 'Execute required operations',
      status: 'in_progress',
      summary: 'Started 1 operation',
      order: 2,
      revision: 2
    }
  })
  state = applyRuntimeEvent(state, {
    event_type: 'stage.updated',
    visibility: 'user',
    payload: {
      id: 'understand',
      title: 'Understand the request',
      status: 'completed',
      order: 1,
      revision: 3
    }
  })
  state = applyRuntimeEvent(state, {
    event_type: 'stage.updated',
    visibility: 'user',
    payload: {
      id: 'execute',
      title: 'Stale title',
      status: 'pending',
      order: 2,
      revision: 1
    }
  })

  assert.equal(state.stageRevision, 3)
  assert.deepEqual(
    state.stages.map((stage) => stage.id),
    ['understand', 'execute']
  )
  assert.equal(state.stages[1].status, 'in_progress')
  assert.equal(state.stages[1].summary, 'Started 1 operation')
})

test('attaches readable activity to the current progress node', () => {
  let state = createRuntimeState()
  state = applyRuntimeEvent(state, {
    event_type: 'stage.updated',
    visibility: 'user',
    payload: {
      id: 'execute',
      title: 'Execute required operations',
      status: 'in_progress',
      order: 2,
      revision: 1
    }
  })
  state = applyRuntimeEvent(state, {
    agent_event: 'tool.read_file.invoke',
    activity: 'running_tool'
  })
  state = applyRuntimeEvent(state, {
    agent_event: 'tool.read_file.invoke',
    activity: 'running_tool'
  })
  state = applyRuntimeEvent(state, {
    agent_event: 'tool.read_file.done',
    activity: 'completed'
  })
  state = applyRuntimeEvent(state, {
    agent_event: 'tool.run_skill_script.start',
    activity: 'running_tool'
  })

  assert.deepEqual(activitiesForNode(state, 'execute'), [
    {
      id: 1,
      nodeId: 'execute',
      kind: 'readingContext',
      count: 2
    },
    {
      id: 2,
      nodeId: 'execute',
      kind: 'queryingData',
      count: 1
    }
  ])
})

test('keeps only the latest twenty progress activities', () => {
  let state = createRuntimeState()
  state = applyRuntimeEvent(state, {
    event_type: 'stage.updated',
    visibility: 'user',
    payload: {
      id: 'execute',
      title: 'Execute required operations',
      status: 'in_progress',
      order: 2,
      revision: 1
    }
  })
  for (let index = 0; index < 22; index += 1) {
    state = applyRuntimeEvent(state, {
      agent_event:
        index % 2 === 0
          ? 'tool.read_file.invoke'
          : 'tool.run_skill_script.start',
      activity: 'running_tool'
    })
  }

  assert.equal(activitiesForNode(state, 'execute').length, 20)
  assert.equal(activitiesForNode(state, 'execute')[0].id, 3)
})

test('moves subsequent activity to the newly active plan node', () => {
  let state = createRuntimeState()
  state = applyRuntimeEvent(state, {
    event_type: 'plan.updated',
    visibility: 'user',
    payload: {
      revision: 1,
      steps: [
        { id: 'one', title: 'Inspect', status: 'in_progress' },
        { id: 'two', title: 'Verify', status: 'pending' }
      ]
    }
  })
  state = applyRuntimeEvent(state, {
    agent_event: 'tool.search_workspace.invoke',
    activity: 'running_tool'
  })
  state = applyRuntimeEvent(state, {
    event_type: 'plan.updated',
    visibility: 'user',
    payload: {
      revision: 2,
      steps: [
        { id: 'one', title: 'Inspect', status: 'completed' },
        { id: 'two', title: 'Verify', status: 'in_progress' }
      ]
    }
  })
  state = applyRuntimeEvent(state, {
    agent_event: 'tool.analyze_structured_output.start',
    activity: 'running_tool'
  })

  assert.equal(activitiesForNode(state, 'one')[0].kind, 'searchingSources')
  assert.equal(activitiesForNode(state, 'two')[0].kind, 'analyzingResults')
})

test('normalizes stage bounds and drops invalid stages', () => {
  const stages = normalizeStages([
    {
      id: 'x'.repeat(100),
      title: 'T'.repeat(300),
      summary: 'S'.repeat(300),
      status: 'failed',
      order: 99
    },
    { id: 'invalid', title: 'Invalid', status: 'unknown' }
  ])

  assert.equal(stages.length, 1)
  assert.equal(stages[0].id.length, 64)
  assert.equal(stages[0].title.length, 240)
  assert.equal(stages[0].summary.length, 240)
  assert.equal(stages[0].order, 12)
})

test('summarizes real stage completion for a compact answer header', () => {
  assert.deepEqual(
    summarizeStageProgress([
      { id: 'one', title: 'Understand', status: 'completed', order: 1 },
      { id: 'two', title: 'Execute', status: 'in_progress', order: 2 },
      { id: 'three', title: 'Finish', status: 'pending', order: 3 }
    ]),
    {
      completed: 1,
      total: 3,
      currentTitle: 'Execute',
      currentSummary: '',
      isComplete: false,
      isTerminal: false
    }
  )
})

test('deduplicates replayed artifact events', () => {
  const event = {
    event_type: 'artifact.created',
    visibility: 'user',
    payload: {
      filename: 'report.md',
      byte_size: 42,
      content_type: 'text/markdown'
    }
  }
  let state = applyRuntimeEvent(createRuntimeState(), event)
  state = applyRuntimeEvent(state, event)

  assert.equal(state.artifacts.length, 1)
})

test('bounds and normalizes untrusted plan fields', () => {
  const steps = normalizePlanSteps([
    { title: 'x'.repeat(300), status: 'unknown' },
    ...Array.from({ length: 20 }, (_, index) => ({
      title: `Step ${index}`,
      status: 'pending'
    }))
  ])

  assert.equal(steps.length, 12)
  assert.equal(steps[0].title.length, 240)
  assert.equal(steps[0].status, 'pending')
})
