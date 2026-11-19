<template>
  <div class="diagnosis-panel">
    <div class="space-y-4">
      <BaseLoading v-if="loading && !latest" />

      <div
        v-else-if="loadError"
        class="rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-700"
        role="alert"
      >
        {{ loadError }}
      </div>

      <section
        v-else-if="!latest"
        class="diagnosis-card flex flex-col gap-4 sm:flex-row sm:items-center"
        data-testid="diagnosis-empty"
      >
        <span
          class="inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-primary-50 text-primary-600"
        >
          <Sparkles :size="18" aria-hidden="true" />
        </span>
        <div data-testid="diagnosis-empty-content" class="min-w-0 flex-1">
          <h3 class="text-sm font-semibold text-gray-900">
            {{ t('lensRuns.diagnosisEmptyTitle') }}
          </h3>
          <p class="mt-1 text-sm leading-6 text-gray-500">
            {{ t('lensRuns.diagnosisEmptyDescription') }}
          </p>
          <p
            v-if="!readonly && !canGenerate"
            class="mt-1 text-xs text-amber-700"
          >
            {{ t('lensRuns.diagnosisTerminalOnly') }}
          </p>
        </div>
        <BaseButton
          v-if="!readonly"
          class="shrink-0"
          size="sm"
          :disabled="!canGenerate"
          :loading="generating"
          @click="generate"
        >
          {{ t('lensRuns.generateDiagnosis') }}
        </BaseButton>
      </section>

      <template v-else>
        <section
          v-if="isPending"
          class="runtime-progress-live rounded-xl border p-4"
          aria-live="polite"
        >
          <div class="flex items-center justify-between gap-3">
            <div class="flex min-w-0 items-baseline gap-2">
              <span class="runtime-card-title text-sm">{{ runningTitle }}</span>
              <span class="runtime-progress-summary-text">
                {{ elapsedText }}
              </span>
            </div>
            <BaseButton
              v-if="!readonly && isStalePending"
              variant="outline"
              size="sm"
              :disabled="!canGenerate"
              :loading="generating"
              @click="generate"
            >
              {{ t('lensRuns.retryDiagnosis') }}
            </BaseButton>
          </div>
          <div class="mt-2 space-y-0.5">
            <div
              v-for="(stage, index) in diagnosisStages"
              :key="stage"
              class="runtime-plan-step"
            >
              <span
                class="runtime-plan-status"
                :class="stepStatusClass(index)"
                aria-hidden="true"
              >
                {{ stepStatusIcon(index) }}
              </span>
              <span :class="stepLabelClass(index)">
                {{ stageLabel(stage) }}
              </span>
            </div>
          </div>
          <p v-if="progressDetail" class="mt-2 text-xs text-slate-500">
            {{ progressDetail }}
          </p>
        </section>

        <section
          v-else-if="latest.status === 'failed'"
          class="rounded-xl border border-red-200 bg-red-50 p-5"
          role="alert"
        >
          <div class="flex items-start gap-3">
            <AlertTriangle
              :size="20"
              class="mt-0.5 text-red-600"
              aria-hidden="true"
            />
            <div class="flex-1">
              <h3 class="text-sm font-semibold text-red-900">
                {{ t('lensRuns.diagnosisFailed') }}
              </h3>
              <p class="mt-1 text-xs text-red-700">
                {{ diagnosisErrorLabel }}
              </p>
              <BaseButton
                v-if="!readonly"
                class="mt-3"
                variant="outline"
                size="sm"
                :disabled="!canGenerate"
                :loading="generating"
                @click="generate"
              >
                {{ t('lensRuns.retryDiagnosis') }}
              </BaseButton>
            </div>
          </div>
        </section>

        <template v-else-if="latest.status === 'completed'">
          <section class="diagnosis-card">
            <div class="flex flex-wrap items-start justify-between gap-3">
              <div class="min-w-0 flex-1">
                <p
                  class="text-xs font-medium uppercase tracking-wide text-gray-500"
                >
                  {{ t('lensRuns.diagnosisSummary') }}
                </p>
                <h3
                  class="mt-2 whitespace-pre-wrap break-words text-sm font-medium leading-6 text-gray-700"
                >
                  {{ latest.result.summary }}
                </h3>
              </div>
              <div class="flex shrink-0 items-center gap-2">
                <span :class="severityClass(latest.result.severity)">
                  {{ severityLabel(latest.result.severity) }}
                </span>
                <span
                  class="rounded-full bg-gray-100 px-2.5 py-1 text-xs text-gray-600"
                >
                  {{ confidenceText(latest.result.confidence) }}
                </span>
              </div>
            </div>
            <p class="mt-3 text-xs text-gray-500">
              {{ t('lensRuns.evidenceSnapshot') }}
              <span class="font-mono">{{
                shortHash(latest.evidence_hash)
              }}</span>
            </p>
          </section>

          <!-- Timeline: what happened during the Run -->
          <section v-if="latest.result.events?.length" class="diagnosis-card">
            <h3 class="text-sm font-semibold text-gray-900">
              {{ t('lensRuns.timelineTitle') }}
            </h3>
            <ol class="mt-4">
              <li
                v-for="(event, index) in latest.result.events"
                :key="`event-${index}`"
                class="relative flex gap-3 pb-5 last:pb-0"
              >
                <span
                  v-if="index < latest.result.events.length - 1"
                  class="absolute bottom-0 left-3 top-6 w-px bg-gray-200"
                />
                <span
                  class="timeline-node"
                  :class="eventNodeClass(event.status)"
                  aria-hidden="true"
                >
                  {{ eventNodeIcon(event.status) }}
                </span>
                <div class="min-w-0 flex-1">
                  <div class="flex flex-wrap items-center gap-2">
                    <p class="text-sm font-medium text-gray-900">
                      {{ event.title }}
                    </p>
                    <span
                      class="rounded-full px-2 py-0.5 text-xs font-semibold"
                      :class="eventStatusClass(event.status)"
                    >
                      {{ eventStatusLabel(event.status) }}
                    </span>
                  </div>
                  <p
                    v-if="event.description"
                    class="mt-1 whitespace-pre-wrap break-words text-xs leading-5 text-gray-600"
                  >
                    {{ event.description }}
                  </p>
                  <EvidenceLinks
                    :refs="event.evidence_refs"
                    @navigate="navigateToEvidence"
                  />
                </div>
              </li>
            </ol>
          </section>

          <!-- Root cause: where the Run went wrong -->
          <section
            v-if="latest.result.root_cause"
            class="rounded-xl border border-red-200 bg-red-50/60 p-5"
            data-testid="diagnosis-root-cause"
          >
            <h3
              class="flex items-center gap-2 text-sm font-semibold text-red-900"
            >
              <AlertTriangle :size="16" aria-hidden="true" />
              {{ t('lensRuns.rootCauseTitle') }}
            </h3>
            <p class="mt-2 text-sm font-medium text-red-900">
              {{ latest.result.root_cause.title }}
            </p>
            <p
              class="mt-1 whitespace-pre-wrap break-words text-sm leading-6 text-red-800"
            >
              {{ latest.result.root_cause.description }}
            </p>
            <EvidenceLinks
              :refs="latest.result.root_cause.evidence_refs"
              @navigate="navigateToEvidence"
            />
          </section>
          <section
            v-else-if="latest.result.events?.length"
            class="rounded-xl border border-green-200 bg-green-50/60 p-5"
          >
            <h3
              class="flex items-center gap-2 text-sm font-semibold text-green-900"
            >
              <CheckCircle2 :size="16" aria-hidden="true" />
              {{ t('lensRuns.noRootCauseTitle') }}
            </h3>
            <p class="mt-1 text-sm text-green-800">
              {{ t('lensRuns.noRootCauseDescription') }}
            </p>
          </section>

          <!-- Fallback for diagnoses created before the timeline schema -->
          <section
            v-if="
              !latest.result.events?.length && latest.result.findings?.length
            "
            class="diagnosis-card"
          >
            <h3 class="text-sm font-semibold text-gray-900">
              {{ t('lensRuns.findings') }}
            </h3>
            <div class="mt-4 space-y-4">
              <article
                v-for="(finding, index) in latest.result.findings"
                :key="`finding-${index}`"
                class="rounded-lg border border-gray-100 bg-gray-50/40 p-4"
              >
                <div class="flex items-center gap-2">
                  <span :class="kindClass(finding.kind)">
                    {{ kindLabel(finding.kind) }}
                  </span>
                  <span class="text-xs text-gray-400">
                    {{ confidenceText(finding.confidence) }}
                  </span>
                </div>
                <h4 class="mt-2.5 text-sm font-semibold text-gray-900">
                  {{ finding.title }}
                </h4>
                <p
                  class="mt-1.5 whitespace-pre-wrap break-words text-sm leading-6 text-gray-600"
                >
                  {{ finding.statement }}
                </p>
                <EvidenceLinks
                  :refs="finding.evidence_refs"
                  @navigate="navigateToEvidence"
                />
              </article>
            </div>
          </section>

          <section
            v-if="latest.result.recommendations?.length"
            class="rounded-xl border border-emerald-200 bg-emerald-50/50 p-5"
          >
            <h3 class="text-sm font-semibold text-emerald-900">
              {{ t('lensRuns.recommendations') }}
            </h3>
            <div class="mt-3 space-y-3">
              <article
                v-for="(item, index) in latest.result.recommendations"
                :key="`recommendation-${index}`"
              >
                <p class="text-sm font-medium text-emerald-900">
                  {{ item.title }}
                </p>
                <p
                  class="mt-1 whitespace-pre-wrap text-sm leading-6 text-emerald-800"
                >
                  {{ item.action }}
                </p>
                <EvidenceLinks
                  :refs="item.evidence_refs"
                  @navigate="navigateToEvidence"
                />
              </article>
            </div>
          </section>

          <section
            v-if="latest.result.unknowns?.length"
            class="rounded-xl border border-amber-200 bg-amber-50/50 p-5"
          >
            <h3
              class="flex items-center gap-2 text-sm font-semibold text-amber-900"
            >
              <HelpCircle :size="16" aria-hidden="true" />
              {{ t('lensRuns.unknowns') }}
            </h3>
            <ul class="mt-3 space-y-2 text-sm leading-6 text-amber-900">
              <li
                v-for="(item, index) in latest.result.unknowns"
                :key="`unknown-${index}`"
              >
                {{ item.statement }}
                <EvidenceLinks
                  :refs="item.evidence_refs"
                  @navigate="navigateToEvidence"
                />
              </li>
            </ul>
          </section>

          <!-- Deterministic evidence checks -->
          <section
            v-if="latest.deterministic_findings?.length"
            class="diagnosis-card"
          >
            <h3 class="text-sm font-semibold text-gray-900">
              {{ t('lensRuns.deterministicChecks') }}
            </h3>
            <div class="mt-3 space-y-3">
              <article
                v-for="(finding, index) in latest.deterministic_findings"
                :key="`deterministic-${index}`"
                class="rounded-lg bg-gray-50 p-3"
              >
                <div class="flex items-start gap-2">
                  <CheckCircle2
                    :size="16"
                    class="mt-0.5 shrink-0 text-green-600"
                    aria-hidden="true"
                  />
                  <div class="min-w-0">
                    <p class="text-sm font-medium text-gray-900">
                      {{ finding.title }}
                    </p>
                    <p class="mt-1 text-sm leading-6 text-gray-600">
                      {{ finding.statement }}
                    </p>
                    <EvidenceLinks
                      :refs="finding.evidence_refs"
                      @navigate="navigateToEvidence"
                    />
                  </div>
                </div>
              </article>
            </div>
          </section>

          <section
            v-if="latest.turns?.length"
            class="diagnosis-card"
            aria-live="polite"
          >
            <h3 class="text-sm font-semibold text-gray-900">
              {{ t('lensRuns.followUpConversation') }}
            </h3>
            <div class="mt-4 space-y-4">
              <article v-for="turn in latest.turns" :key="turn.uuid">
                <p
                  class="ml-auto max-w-[85%] rounded-xl bg-primary-600 px-4 py-3 text-sm text-white"
                >
                  {{ turn.question }}
                </p>
                <div
                  class="mt-2 max-w-[90%] rounded-xl bg-gray-100 px-4 py-3 text-sm leading-6 text-gray-700"
                >
                  <span v-if="turn.status === 'completed'">
                    {{ turn.answer }}
                  </span>
                  <span
                    v-else-if="turn.status === 'failed'"
                    class="text-red-700"
                  >
                    {{ t('lensRuns.followUpFailed') }}
                  </span>
                  <span v-else class="text-gray-500">
                    {{ t('lensRuns.followUpRunning') }}
                  </span>
                  <EvidenceLinks
                    :refs="turn.evidence_refs"
                    @navigate="navigateToEvidence"
                  />
                </div>
              </article>
            </div>
          </section>
        </template>
      </template>
    </div>

    <form
      v-if="!readonly && canGenerate && latest?.status === 'completed'"
      class="diagnosis-card mt-4"
      data-testid="diagnosis-follow-up"
      @submit.prevent="submitFollowUp"
    >
      <label
        for="run-diagnosis-question"
        class="text-xs font-medium text-gray-700"
      >
        {{ t('lensRuns.askAboutRun') }}
      </label>
      <div class="mt-2 flex gap-2">
        <input
          id="run-diagnosis-question"
          v-model="question"
          maxlength="2000"
          class="min-w-0 flex-1 rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-primary-500 focus:outline-none focus:ring-2 focus:ring-primary-100"
          :placeholder="t('lensRuns.askAboutRunPlaceholder')"
          :disabled="submitting"
        />
        <BaseButton
          type="submit"
          :disabled="!question.trim()"
          :loading="submitting"
          :aria-label="t('lensRuns.sendFollowUp')"
        >
          <Send :size="16" aria-hidden="true" />
          <span class="sr-only">{{ t('lensRuns.sendFollowUp') }}</span>
        </BaseButton>
      </div>
      <p class="mt-2 text-xs text-gray-500">
        {{ t('lensRuns.followUpBoundary') }}
      </p>
    </form>
  </div>
</template>

<script setup>
import {
  AlertTriangle,
  CheckCircle2,
  HelpCircle,
  Send,
  Sparkles
} from '@lucide/vue'
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'

import {
  createAdminRunDiagnosticTurn,
  generateAdminRunDiagnosis,
  getAdminRunDiagnostics
} from '@/api/lens'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseLoading from '@/components/ui/BaseLoading.vue'
import { extractErrorMessage } from '@/utils/api'

import EvidenceLinks from './RunDiagnosisEvidenceLinks.vue'

const props = defineProps({
  runUuid: { type: String, required: true },
  active: { type: Boolean, default: false },
  canGenerate: { type: Boolean, default: true },
  readonly: { type: Boolean, default: false }
})
const emit = defineEmits(['navigate'])
const { t } = useI18n()

const diagnostics = ref([])
const loading = ref(false)
const generating = ref(false)
const submitting = ref(false)
const loadError = ref('')
const question = ref('')
let pollTimer = null
let requestVersion = 0

const latest = computed(() => diagnostics.value[0] || null)
const isPending = computed(() =>
  ['queued', 'running'].includes(latest.value?.status)
)
const isStalePending = computed(() => {
  if (!isPending.value) return false
  const start = latest.value?.started_at || latest.value?.created_at
  if (!start) return false
  return Date.now() - new Date(start).getTime() > 10 * 60 * 1000
})

const diagnosisStages = [
  'queued',
  'deterministic_checks',
  'model_analysis',
  'validating'
]
const stageIndex = computed(() => {
  const idx = diagnosisStages.indexOf(latest.value?.progress?.stage)
  return idx === -1 ? 0 : idx
})
const runningTitle = computed(() =>
  t(
    `lensRuns.diagnosisStageTitles.${diagnosisStages[stageIndex.value]}`,
    t('lensRuns.diagnosisRunning')
  )
)
const progressDetail = computed(() => {
  const progress = latest.value?.progress || {}
  if (
    progress.stage === 'model_analysis' &&
    progress.deterministic_findings_count != null
  ) {
    return t('lensRuns.diagnosisModelAnalysisDetail', {
      n: progress.deterministic_findings_count
    })
  }
  return ''
})
const elapsedText = ref('')
let elapsedTimer = null

function stageLabel(stage) {
  return t(`lensRuns.diagnosisStages.${stage}`, stage)
}

function stepStatusClass(index) {
  if (index < stageIndex.value) return 'is-completed'
  if (index === stageIndex.value) return 'is-in_progress'
  return ''
}

function stepStatusIcon(index) {
  if (index < stageIndex.value) return '✓'
  if (index === stageIndex.value) return '●'
  return '○'
}

function stepLabelClass(index) {
  if (index < stageIndex.value) return 'text-xs font-medium text-slate-600'
  if (index === stageIndex.value) return 'text-xs font-semibold text-slate-800'
  return 'text-xs text-slate-400'
}

function updateElapsed() {
  const start = latest.value?.started_at || latest.value?.created_at
  if (!start) {
    elapsedText.value = ''
    return
  }
  const seconds = Math.max(
    0,
    Math.floor((Date.now() - new Date(start).getTime()) / 1000)
  )
  elapsedText.value = t('lensRuns.diagnosisElapsed', { n: seconds })
}

function startElapsed() {
  stopElapsed()
  updateElapsed()
  elapsedTimer = window.setInterval(updateElapsed, 1000)
}

function stopElapsed() {
  if (elapsedTimer !== null) {
    window.clearInterval(elapsedTimer)
    elapsedTimer = null
  }
  elapsedText.value = ''
}
const hasPendingTurn = computed(() =>
  (latest.value?.turns || []).some((turn) =>
    ['queued', 'running'].includes(turn.status)
  )
)
const diagnosisErrorLabel = computed(() => {
  const code = latest.value?.error_code
  if (!code) return t('lensRuns.diagnosisFailedDescription')
  const knownCodes = new Set([
    'MODEL_RESPONSE_INVALID',
    'INVALID_EVIDENCE_REFERENCE',
    'MODEL_CALL_FAILED'
  ])
  if (knownCodes.has(code)) return t(`lensRuns.diagnosisErrors.${code}`)
  return `${t('lensRuns.diagnosisFailedDescription')} (${code})`
})

async function load({ quiet = false } = {}) {
  if (!props.runUuid) return
  const version = ++requestVersion
  if (!quiet) loading.value = true
  try {
    const result = await getAdminRunDiagnostics(props.runUuid)
    if (version === requestVersion) {
      diagnostics.value = result
      loadError.value = ''
    }
  } catch (error) {
    if (!quiet && version === requestVersion) {
      loadError.value = extractErrorMessage(error, t('common.error'))
    }
  } finally {
    if (version === requestVersion) loading.value = false
  }
  if (version === requestVersion) schedulePoll()
}

async function generate() {
  if (
    props.readonly ||
    !props.runUuid ||
    !props.canGenerate ||
    generating.value
  )
    return
  const version = ++requestVersion
  generating.value = true
  loadError.value = ''
  try {
    const diagnostic = await generateAdminRunDiagnosis(props.runUuid)
    if (version === requestVersion) {
      diagnostics.value = [
        diagnostic,
        ...diagnostics.value.filter((item) => item.uuid !== diagnostic.uuid)
      ]
    }
  } catch (error) {
    if (version === requestVersion) {
      loadError.value = extractErrorMessage(error, t('common.error'))
    }
  } finally {
    if (version === requestVersion) generating.value = false
  }
  if (version === requestVersion) schedulePoll()
}

async function submitFollowUp() {
  const value = question.value.trim()
  if (
    props.readonly ||
    !props.canGenerate ||
    !value ||
    !latest.value ||
    submitting.value
  )
    return
  const version = ++requestVersion
  submitting.value = true
  try {
    const turn = await createAdminRunDiagnosticTurn(
      props.runUuid,
      latest.value.uuid,
      value
    )
    if (version === requestVersion) {
      latest.value.turns = [
        ...(latest.value.turns || []).filter((item) => item.uuid !== turn.uuid),
        turn
      ]
      question.value = ''
      loadError.value = ''
    }
  } catch (error) {
    if (version === requestVersion) {
      loadError.value = extractErrorMessage(error, t('common.error'))
    }
  } finally {
    if (version === requestVersion) submitting.value = false
  }
  if (version === requestVersion) schedulePoll()
}

function schedulePoll() {
  clearPoll()
  if (!props.active || (!isPending.value && !hasPendingTurn.value)) return
  pollTimer = window.setTimeout(() => load({ quiet: true }), 1500)
}

function clearPoll() {
  if (pollTimer !== null) {
    window.clearTimeout(pollTimer)
    pollTimer = null
  }
}

function navigateToEvidence(evidenceRef) {
  emit('navigate', evidenceRef)
}

function shortHash(value) {
  return value ? value.slice(0, 12) : '-'
}

function confidenceText(value) {
  const confidence = Number(value)
  if (!Number.isFinite(confidence)) return t('lensRuns.confidenceUnknown')
  return t('lensRuns.confidence', { value: Math.round(confidence * 100) })
}

function severityLabel(value) {
  return t(`lensRuns.severity.${value}`, value || '-')
}

function kindLabel(value) {
  return t(`lensRuns.findingKind.${value}`, value || '-')
}

function severityClass(value) {
  const base = 'rounded-full px-2.5 py-1 text-xs font-semibold'
  const classes = {
    low: 'bg-green-100 text-green-800',
    medium: 'bg-amber-100 text-amber-800',
    high: 'bg-orange-100 text-orange-800',
    critical: 'bg-red-100 text-red-800'
  }
  return `${base} ${classes[value] || 'bg-gray-100 text-gray-700'}`
}

function kindClass(value) {
  const base = 'rounded-full px-2 py-0.5 text-xs font-semibold'
  return value === 'fact'
    ? `${base} bg-blue-100 text-blue-800`
    : `${base} bg-purple-100 text-purple-800`
}

const EVENT_STATUS_STYLES = {
  ok: {
    icon: '✓',
    node: 'bg-green-100 text-green-700 border-green-300',
    pill: 'bg-green-100 text-green-800'
  },
  failed: {
    icon: '✕',
    node: 'bg-red-100 text-red-700 border-red-300',
    pill: 'bg-red-100 text-red-800'
  },
  recovered: {
    icon: '↻',
    node: 'bg-amber-100 text-amber-700 border-amber-300',
    pill: 'bg-amber-100 text-amber-800'
  },
  unknown: {
    icon: '?',
    node: 'bg-gray-100 text-gray-500 border-gray-300',
    pill: 'bg-gray-100 text-gray-600'
  }
}

function eventStatusLabel(status) {
  return t(`lensRuns.timelineEventStatus.${status}`, status)
}

function eventNodeClass(status) {
  return `timeline-node ${EVENT_STATUS_STYLES[status]?.node || EVENT_STATUS_STYLES.unknown.node}`
}

function eventNodeIcon(status) {
  return EVENT_STATUS_STYLES[status]?.icon || '?'
}

function eventStatusClass(status) {
  return EVENT_STATUS_STYLES[status]?.pill || EVENT_STATUS_STYLES.unknown.pill
}

watch(
  () => [props.runUuid, props.active],
  ([runUuid, active], previous) => {
    const previousRunUuid = previous?.[0]
    if (runUuid !== previousRunUuid) {
      requestVersion += 1
      diagnostics.value = []
      question.value = ''
      loadError.value = ''
    }
    if (active && runUuid) load()
    else clearPoll()
  },
  { immediate: true }
)

watch(
  () => [latest.value?.status, latest.value?.progress],
  () => {
    if (isPending.value) startElapsed()
    else stopElapsed()
  },
  { immediate: true }
)

onBeforeUnmount(() => {
  clearPoll()
  stopElapsed()
})

defineExpose({ generate, load })
</script>

<style scoped>
.diagnosis-panel {
  @apply space-y-4;
}
.diagnosis-card {
  @apply rounded-lg border border-gray-200 bg-white p-4;
}
/* Mirrors the chat runtime-progress visual language from Chat.vue so the
   diagnosis progress stays consistent with the user Q&A experience. */
.runtime-progress-live {
  border-color: #d8dce8;
  background: #f8f9fc;
}
.runtime-card-title {
  color: #334155;
  font-weight: 600;
}
.runtime-progress-summary-text {
  min-width: 0;
  color: #64748b;
  font-size: 0.72rem;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.runtime-plan-step {
  display: flex;
  align-items: flex-start;
  gap: 0.5rem;
  padding: 0.18rem 0;
}
.runtime-plan-status {
  display: inline-flex;
  width: 1rem;
  height: 1rem;
  align-items: center;
  justify-content: center;
  flex: 0 0 auto;
  margin-top: 0.08rem;
  color: #8b8378;
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
.runtime-plan-status.is-completed {
  color: #3f7a5c;
}
.runtime-plan-status.is-failed {
  color: #b64949;
}
@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}
.timeline-node {
  display: inline-flex;
  width: 1.5rem;
  height: 1.5rem;
  align-items: center;
  justify-content: center;
  flex: 0 0 auto;
  margin-top: 0.1rem;
  border: 1px solid;
  border-radius: 9999px;
  font-size: 0.7rem;
  font-weight: 700;
  line-height: 1;
}
</style>
