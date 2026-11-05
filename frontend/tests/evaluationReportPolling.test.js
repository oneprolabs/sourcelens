import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import vm from 'node:vm'

const source = await readFile(new URL('../src/admin/pages/lens/EvaluationReport.vue', import.meta.url), 'utf8')
const ref = (value) => ({ value })

function functionSource(name) {
  let start = source.indexOf(`function ${name}(`)
  if (source.slice(start - 6, start) === 'async ') start -= 6
  return source.slice(start, source.indexOf('\n}', start) + 2)
}

function createContext(fetchResults) {
  let callback
  const context = vm.createContext({
    runUuid: ref('run'), page: ref(1), pageSize: ref(20), verdictFilter: ref('all'),
    results: ref([]), resultsTotal: ref(0), resultsLoading: ref(false),
    resultsRequestVersion: 0, disposed: false, pollTimer: null, isActive: ref(true),
    listEvaluationResults: fetchResults, showError: () => {}, errorMessage: String,
    loadRun: async () => {}, loadReport: async () => {},
    window: { setTimeout: (fn) => { callback = fn; return 1 }, clearTimeout: () => {} }
  })
  for (const name of ['loadResults', 'stopPoll', 'schedulePoll']) {
    vm.runInContext(functionSource(name), context)
  }
  return { context, tick: () => callback() }
}

test('polling refreshes case results while the batch is running and once more on completion', async () => {
  let calls = 0
  const { context, tick } = createContext(async () => ({ results: [{ passed: ++calls > 1 ? true : false }], count: 1 }))
  vm.runInContext('schedulePoll()', context)
  await tick()
  assert.equal(context.results.value[0].passed, false)
  context.loadRun = async () => { context.isActive.value = false }
  await tick()
  assert.equal(context.results.value[0].passed, true)
  assert.equal(calls, 2)
})

test('older poll results cannot overwrite a newly selected filter', async () => {
  let resolveOld
  const { context } = createContext((uuid, params) => params.passed
    ? Promise.resolve({ results: [{ passed: false }], count: 1 })
    : new Promise((resolve) => { resolveOld = resolve }))
  const old = vm.runInContext('loadResults({ quiet: true })', context)
  context.verdictFilter.value = 'false'
  await vm.runInContext('loadResults()', context)
  resolveOld({ results: [{ passed: true }], count: 1 })
  await old
  assert.equal(context.results.value[0].passed, false)
})

test('temporary polling failures preserve the displayed results', async () => {
  const { context } = createContext(async () => { throw new Error('offline') })
  context.results.value = [{ passed: false }]
  await vm.runInContext('loadResults({ quiet: true })', context)
  assert.equal(context.results.value[0].passed, false)
})
