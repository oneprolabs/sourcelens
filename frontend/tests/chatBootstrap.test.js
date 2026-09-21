import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

const chatSource = () =>
  readFile(new URL('../src/pages/lens/Chat.vue', import.meta.url), 'utf8')

const assistantSwitcherSource = () =>
  readFile(
    new URL('../src/components/lens/AssistantSwitcher.vue', import.meta.url),
    'utf8'
  )

const userDockSource = () =>
  readFile(
    new URL('../src/components/lens/UserDock.vue', import.meta.url),
    'utf8'
  )

test('reveals loaded chat before waiting for active run recovery', async () => {
  const source = await chatSource()
  const messagesLoaded = source.indexOf('messages.value = loadedMessages')
  const resumeActiveRun = source.indexOf(
    'await maybeResumeActiveRun(session.uuid, isCurrentLoad)',
    messagesLoaded
  )
  const chatRevealed = source.indexOf('booted.value = true', messagesLoaded)

  assert.notEqual(messagesLoaded, -1)
  assert.notEqual(resumeActiveRun, -1)
  assert.ok(chatRevealed > messagesLoaded)
  assert.ok(chatRevealed < resumeActiveRun)
})

test('synchronizes reviewed chat state before active run recovery', async () => {
  const source = await chatSource()
  const selectSession = source.indexOf('async function selectSession(')
  const resumeActiveRun = source.indexOf(
    'await maybeResumeActiveRun(session.uuid, isCurrentLoad)',
    selectSession
  )
  const clearUnread = source.indexOf(
    'clearUnreadSession(window.localStorage, session.uuid)',
    selectSession
  )

  assert.notEqual(selectSession, -1)
  assert.notEqual(resumeActiveRun, -1)
  assert.notEqual(clearUnread, -1)
  assert.ok(clearUnread < resumeActiveRun)
  assert.match(
    source.slice(clearUnread, resumeActiveRun),
    /clearUnreadSession\(window\.localStorage, session\.uuid\)\s*\n\s*refreshUnreadSessions\(\)/
  )
})

test('guards restored run state with the current session load', async () => {
  const source = await chatSource()

  assert.match(
    source,
    /await maybeResumeActiveRun\(session\.uuid, isCurrentLoad\)/
  )
  assert.match(
    source,
    /async function maybeResumeActiveRun\(sessionUuid, isCurrentLoad\)/
  )
})

test('closes the delete dialog before loading the replacement session', async () => {
  const source = await chatSource()
  const deleteHandler = source.indexOf('async function doDeleteSession()')
  const deleteHandlerEnd = source.indexOf('\n}\n\nwatch(', deleteHandler)
  const handlerSource = source.slice(deleteHandler, deleteHandlerEnd)
  const closeDialog = handlerSource.indexOf('deleteSessionTarget.value = null')
  const replacementLoad = handlerSource.indexOf('await selectSession(next)')

  assert.notEqual(deleteHandler, -1)
  assert.notEqual(closeDialog, -1)
  assert.notEqual(replacementLoad, -1)
  assert.ok(closeDialog < replacementLoad)
})

test('does not reopen the delete dialog during replacement loading', async () => {
  const source = await chatSource()
  assert.match(
    source,
    /action === 'delete' && !deletingSession\.value\)\s*\{\s*deleteSessionTarget\.value = session/
  )
})

test('shows a retryable node-offline error when run creation is unavailable', async () => {
  const source = await chatSource()

  assert.match(source, /const errorCode = err\?\.response\?\.data\?\.detail/)
  assert.match(source, /lensNodeErrorMessage\(errorCode, t\)/)
})

test('keeps the conversation viewport aligned with runtime events', async () => {
  const source = await chatSource()
  const handleEvent = source.indexOf('function handleEvent(event)')
  const nextHandler = source.indexOf(
    'function handleStepEvent(event)',
    handleEvent
  )
  const handlerSource = source.slice(handleEvent, nextHandler)

  assert.notEqual(handleEvent, -1)
  assert.match(handlerSource, /answerAutoScroller\.request\(\)/)
})

test('selects a conversation when its route query changes', async () => {
  const source = await chatSource()
  const queryWatcher = source.indexOf('() => route.query.session')
  const sessionLookup = source.indexOf(
    'sessions.value.find((item) => item.uuid === sessionUuid)',
    queryWatcher
  )
  const selectWithoutRouteUpdate = source.indexOf(
    'void selectSession(session, false)',
    sessionLookup
  )

  assert.notEqual(queryWatcher, -1)
  assert.notEqual(sessionLookup, -1)
  assert.notEqual(selectWithoutRouteUpdate, -1)
})

test('does not bootstrap again when only the session query changes', async () => {
  const source = await chatSource()

  assert.match(
    source,
    /watch\(\s*\[\(\) => route\.name, \(\) => route\.params\.slug\],/
  )
  assert.doesNotMatch(
    source,
    /watch\(\s*\(\) => \[route\.name, route\.params\.slug\],/
  )
})

test('switches assistants through the assistant chat route', async () => {
  const source = await assistantSwitcherSource()

  assert.match(
    source,
    /await router\.push\(`\/lens\/assistants\/\$\{slug\}\/chat`\)/
  )
})

test('marks assistant filtering as search without autofill', async () => {
  const source = await assistantSwitcherSource()

  assert.match(
    source,
    /v-model="query"\s+type="search"\s+name="assistant-search"\s+autocomplete="off"/
  )
  assert.match(source, /inputmode="search"/)
})

test('loads sessions after selecting the route assistant', async () => {
  const source = await chatSource()
  const selectAssistant = source.indexOf(
    'selectedAssistantUuid.value = current.uuid'
  )
  const loadSessions = source.indexOf('await loadSessions()', selectAssistant)

  assert.notEqual(selectAssistant, -1)
  assert.notEqual(loadSessions, -1)
  assert.ok(loadSessions > selectAssistant)
})

test('ignores a stale session list after switching to archived sessions', async () => {
  const source = await chatSource()

  assert.match(source, /const loadGeneration = \+\+sessionLoadGeneration/)
  assert.match(source, /loadedSessions = await listSessions\(/)
  assert.match(source, /if \(loadGeneration !== sessionLoadGeneration\) return/)
  assert.match(
    source,
    /async function switchSessionView\(archived\) \{\s*if \(showArchivedSessions\.value === archived\) return\s*sessionLoadGeneration \+= 1/
  )
  const switchStart = source.indexOf('async function switchSessionView')
  const switchEnd = source.indexOf('function sortManagedSessions', switchStart)
  const switchSource = source.slice(switchStart, switchEnd)

  assert.ok(
    switchSource.indexOf('await loadSessions') <
      switchSource.indexOf('router.replace') ||
      !switchSource.includes('router.replace')
  )
})

test('shows an assistant welcome state on mobile before the first message', async () => {
  const source = await chatSource()

  assert.match(
    source,
    /v-else-if="\s*isMobile && !decoratedMessages\.length && !showLiveAnswer\s*"\s+class="chat-welcome"/
  )
  assert.match(source, /<h1 class="chat-welcome-title">/)
  assert.match(source, /\{\{ assistantName \}\}/)
  assert.match(source, /v-for="suggestion in promptSuggestions"/)
  assert.match(source, /@click="applyPromptSuggestion\(suggestion\)"/)
  assert.match(source, /'composer-wrap-empty': isEmptyConversation/)
})

test('keeps the closed mobile sidebar out of keyboard navigation', async () => {
  const source = await chatSource()

  assert.match(source, /:inert="isMobile && !sidebarOpen"/)
  assert.match(source, /:aria-hidden="isMobile && !sidebarOpen"/)
})

test('collapses the conversation history into a compact Recent row', async () => {
  const source = await chatSource()

  assert.match(source, /sessionHistoryCollapsed = true/)
  assert.match(source, /class="sessions-collapsed"/)
  assert.match(source, /class="sessions-collapsed-trigger"[\s\S]*<ChevronRight/)
  assert.match(source, /class="sessions-collapsed-action"/)
  assert.match(source, /placement="right"/)
  assert.match(source, /\.sessions-collapsed\s*\{[\s\S]*items-center/)
})

test('shows only a direct share action for mobile answer tools', async () => {
  const source = await chatSource()

  assert.match(
    source,
    /v-if="isMobile && !isAnonymous && message\.run"\s+type="button"\s+class="icon-btn mobile-share-btn"/
  )
  assert.doesNotMatch(source, /messageSecondaryActions\(message\)/)
  assert.doesNotMatch(source, /handleMessageAction\(message, \$event\)/)
})

test('reduces secondary account information in the mobile drawer', async () => {
  const source = await userDockSource()

  assert.match(source, /v-if="!isMobile" class="truncate text-xs/)
  assert.match(source, /'dock-trigger-mobile': isMobile/)
})

test('keeps collapsed account actions inside the sidebar as icon controls', async () => {
  const source = await userDockSource()

  assert.match(source, /'dock-menu-collapsed': isCollapsed/)
  assert.match(source, /'dock-link-collapsed': isCollapsed/)
  assert.match(source, /const isCollapsed = computed\(\(\) => props\.collapsed/)
  assert.match(source, /<Share2[\s\S]*<Settings[\s\S]*<LogOut/)
  assert.match(source, /v-if="isAdmin"\s+class="dock-section"/)
  assert.match(source, /<Shield[\s\S]*platforms\.adminConsole/)
  assert.match(
    source,
    /v-if="!collapsed \|\| isMobile" class="dock-build-info"/
  )
  assert.match(source, /.dock-menu-collapsed\s*\{[^}]*left-0 w-full px-0/)
  assert.equal(
    (source.match(/v-if="!collapsed \|\| isMobile" class="truncate"/g) || [])
      .length,
    4
  )
})

test('creates the first session only when the user submits a prompt', async () => {
  const source = await chatSource()
  const submit = source.indexOf('async function submit()')
  const createFirstSession = source.indexOf('await createNewSession(', submit)
  const bindSession = source.indexOf(
    'const sessionAtSubmit = selectedSessionUuid.value',
    submit
  )
  const submitGuard = source.indexOf(
    'submittingSessionUuids.value.has(selectedSessionUuid.value)',
    submit
  )
  const markCreating = source.indexOf(
    'sessionCreationInProgress.value = true',
    submit
  )

  assert.notEqual(createFirstSession, -1)
  assert.notEqual(bindSession, -1)
  assert.notEqual(submitGuard, -1)
  assert.ok(submitGuard < createFirstSession)
  assert.ok(markCreating < createFirstSession)
  assert.ok(createFirstSession < bindSession)
})

test('lets Smart Collaboration select assistants before its first session', async () => {
  const source = await chatSource()
  const composerShell = source.indexOf('<div class="composer-shell relative">')
  const scopeButton = source.indexOf(
    '<ParticipatingAssistantsPicker',
    composerShell
  )
  const composer = source.indexOf('<div class="composer">', scopeButton)
  const scopeCandidates = source.indexOf('const routingScopeAssistantUuids')
  const sessionPayload = source.indexOf(
    'allowed_assistant_uuids: allowedAssistantUuids'
  )
  const selectedScope = source.indexOf(
    'await createNewSession(\n        false,\n        routingScopeAssistantUuids.value',
    scopeCandidates
  )

  assert.notEqual(scopeButton, -1)
  assert.ok(composerShell < scopeButton)
  assert.ok(scopeButton < composer)
  assert.notEqual(scopeCandidates, -1)
  assert.notEqual(selectedScope, -1)
  assert.notEqual(sessionPayload, -1)
})

test('shows the current delegated assistant above Smart Collaboration progress', async () => {
  const source = await chatSource()
  const liveAnswer = source.indexOf('<!-- Live answer:')
  const currentAssistant = source.indexOf(
    'class="runtime-assistant-names"',
    liveAnswer
  )
  const liveProgress = source.indexOf(
    'class="runtime-progress-card runtime-progress-live"',
    liveAnswer
  )

  assert.notEqual(currentAssistant, -1)
  assert.ok(liveProgress < currentAssistant)
  assert.match(source, /assistantNamesLabel\(runtimeState\)/)
  assert.match(source, /activity\.assistantName/)
})

test('shows each delegated task inside its assistant activity group', async () => {
  const source = await chatSource()
  const component = await readFile(
    new URL(
      '../src/pages/lens/components/AssistantActivityGroups.vue',
      import.meta.url
    ),
    'utf8'
  )

  assert.match(source, /<AssistantActivityGroups/)
  assert.match(component, /group\.tasks/)
  assert.match(component, /class="runtime-assistant-full-task"/)
  assert.doesNotMatch(source, /lens\.chat\.runtime\.delegatedTask/)
})

test('keeps browser scroll anchoring enabled while activities grow', async () => {
  const source = await chatSource()

  assert.match(source, /\.thread-scroll[\s\S]*overflow-anchor: auto/)
  assert.doesNotMatch(source, /\.thread-scroll[\s\S]*overflow-anchor: none/)
})

test('keeps every conversation layout free of repeated message avatars', async () => {
  const source = await chatSource()

  assert.doesNotMatch(source, /class="message-avatar/)
  assert.doesNotMatch(source, /\.message-avatar/)
  assert.doesNotMatch(source, /const avatarBgColor = computed/)
})

test('keeps the mobile composer docked below its prompt suggestions', async () => {
  const source = await chatSource()
  const promptSuggestions = source.indexOf('class="prompt-suggestions"')
  const composer = source.indexOf('class="composer"', promptSuggestions)

  assert.notEqual(promptSuggestions, -1)
  assert.notEqual(composer, -1)
  assert.ok(promptSuggestions < composer)
  assert.match(
    source,
    /\.main-shell \.composer-wrap,\s*\.main-shell \.composer-wrap-empty \{[\s\S]*?position: absolute;[\s\S]*?top: auto;[\s\S]*?bottom: calc\(0\.75rem \+ env\(safe-area-inset-bottom\)\);[\s\S]*?transform: none;/
  )
  assert.match(source, /\.prompt-suggestions \{[^}]*margin: 0 auto 0\.75rem;/)
  assert.doesNotMatch(source, /top: 49dvh;/)
})

test('tracks the visual viewport so keyboard panning keeps the thread top reachable', async () => {
  const source = await chatSource()

  assert.match(
    source,
    /class="lens-chat-page qa-screen-view"[\s\S]*?'visual-viewport-constrained': visualViewportConstrained[\s\S]*?:style="mobileViewportStyle"/
  )
  assert.match(source, /resolveChatViewport\(\{[\s\S]*?layoutHeight:/)
  assert.doesNotMatch(source, /window\.innerWidth >= 1024/)
  assert.match(source, /'--chat-viewport-height': `\$\{resolved\.height\}px`/)
  assert.match(
    source,
    /'--chat-viewport-offset-top': `\$\{resolved\.offsetTop\}px`/
  )
  assert.match(
    source,
    /window\.visualViewport\?\.addEventListener\(\s*'resize',\s*syncMobileViewport\s*\)/
  )
  assert.match(
    source,
    /window\.visualViewport\?\.addEventListener\(\s*'scroll',\s*syncMobileViewport\s*\)/
  )
  assert.match(
    source,
    /@media \(max-width: 1023px\) \{[\s\S]*?\.lens-chat-page \{[\s\S]*?position: fixed;[\s\S]*?top: var\(--chat-viewport-offset-top, 0px\);[\s\S]*?right: 0;[\s\S]*?bottom: auto;[\s\S]*?left: 0;[\s\S]*?height: var\(--chat-viewport-height, 100dvh\);[\s\S]*?overflow: hidden;[\s\S]*?overscroll-behavior: none;/
  )
  assert.match(
    source,
    /\.lens-chat-page\.visual-viewport-constrained \{[\s\S]*?position: fixed;[\s\S]*?top: var\(--chat-viewport-offset-top, 0px\);[\s\S]*?height: var\(--chat-viewport-height, 100dvh\);/
  )
})

test('keeps the mobile AI disclaimer in the scrolling thread', async () => {
  const source = await chatSource()
  const mobileDisclaimer = source.indexOf(
    'class="disclaimer mobile-disclaimer"'
  )
  const composerWrap = source.indexOf('class="composer-wrap"')

  assert.notEqual(mobileDisclaimer, -1)
  assert.notEqual(composerWrap, -1)
  assert.ok(mobileDisclaimer < composerWrap)
  assert.match(source, /v-if="!isMobile" class="disclaimer"/)
  assert.match(
    source,
    /\.main-shell \.composer-wrap,\s*\.main-shell \.composer-wrap-empty \{[^}]*background: transparent;/
  )
  assert.match(
    source,
    /\.main-shell \.composer-wrap,\s*\.main-shell \.composer-wrap-empty \{[^}]*background: transparent;[\s\S]*?\.composer \{[\s\S]*?box-shadow:[\s\S]*?0 10px 30px/
  )
  assert.doesNotMatch(source, /\.mobile-disclaimer \{[^}]*position: absolute;/)
})

test('keeps the mobile welcome copy clear of the docked composer', async () => {
  const source = await chatSource()

  assert.match(source, /\.chat-welcome \{[\s\S]*?padding: 0 1\.5rem 13\.5rem;/)
})

test('keeps live agent activity expanded on mobile while allowing collapse', async () => {
  const source = await chatSource()

  assert.match(
    source,
    /v-if="[\s\S]*?isRunActive &&[\s\S]*?liveStructuredProgress\.items\.length[\s\S]*?open\s+class="runtime-progress-card runtime-progress-live"/
  )
  assert.match(
    source,
    /class="runtime-progress-summary runtime-progress-live-summary"[\s\S]*?\{\{ liveProgressText \}\}/
  )
  assert.match(source, /class="runtime-progress-content"/)
  assert.match(
    source,
    /\.runtime-progress-live > \.runtime-progress-summary \{[\s\S]*?display: none;/
  )
  assert.match(
    source,
    /\.runtime-progress-card \{[\s\S]*?border: 0;[\s\S]*?background: transparent;[\s\S]*?padding: 0;/
  )
  assert.match(
    source,
    /\.runtime-progress-live > \.runtime-progress-summary \{[\s\S]*?display: flex;[\s\S]*?min-height: 2\.75rem;/
  )
})

test('keeps completed agent activity visible until the user collapses it', async () => {
  const source = await chatSource()
  const completedProgress = source.indexOf(
    'structuredProgress(message._runtimeState).items.length ||'
  )
  const completedDetails = source.lastIndexOf('<details', completedProgress)

  assert.notEqual(completedProgress, -1)
  assert.notEqual(completedDetails, -1)
  assert.match(
    source.slice(completedDetails, completedProgress + 240),
    /<details[\s\S]*?open[\s\S]*?class="runtime-progress-card"/
  )
})

test('renders delegated assistants as bounded progressive-disclosure cards', async () => {
  const source = await chatSource()
  const component = await readFile(
    new URL(
      '../src/pages/lens/components/AssistantActivityGroups.vue',
      import.meta.url
    ),
    'utf8'
  )

  assert.match(source, /<AssistantActivityGroups/)
  assert.match(source, /:live="true"/)
  assert.match(source, /:live="false"/)
  assert.match(component, /<details[\s\S]*?:open="live"/)
  assert.match(component, /class="runtime-assistant-task-summary"/)
  assert.match(component, /class="runtime-assistant-full-task"/)
  assert.match(component, /group\.summaryItems/)
  assert.match(
    component,
    /\.runtime-assistant-task-summary \{[\s\S]*?-webkit-line-clamp: 2;/
  )
})
