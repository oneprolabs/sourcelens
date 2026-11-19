import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import vm from 'node:vm'

const pages = {
  Evaluation: ['openCreate', 'submitCreate', 'askDelete', 'confirmDelete'],
  EvaluationDataset: ['openCaseForm', 'submitCaseForm', 'confirmDeleteCase', 'openImport', 'submitImport', 'launchRun'],
  EvaluationReport: ['saveCalibration', 'cancelRun', 'pauseRun', 'resumeRun', 'retryRun']
}

test('leaving evaluation invalidates pending requests without throwing', async () => {
  const source = await readFile(new URL('../src/admin/pages/lens/Evaluation.vue', import.meta.url), 'utf8')
  const start = source.indexOf('onBeforeUnmount(() => {')
  const end = source.indexOf('\n})', start) + 3
  assert.ok(start >= 0 && end > start)
  let cleanup
  let clearedTimer
  const context = vm.createContext({
    onBeforeUnmount: (callback) => { cleanup = callback },
    requestVersion: 4,
    searchTimer: 123,
    lifecycleVersion: { value: 7 },
    clearTimeout: (timer) => { clearedTimer = timer }
  })
  vm.runInContext(source.slice(start, end), context)
  cleanup()
  assert.equal(context.requestVersion, 5)
  assert.equal(context.lifecycleVersion.value, 8)
  assert.equal(clearedTimer, 123)
})

for (const [page, actions] of Object.entries(pages)) {
  test(`${page} blocks writes after a delegation becomes readonly`, async () => {
    const source = await readFile(new URL(`../src/admin/pages/lens/${page}.vue`, import.meta.url), 'utf8')
    for (const name of actions) {
      let start = source.indexOf(`function ${name}(`)
      assert.ok(start >= 0, name)
      if (source.slice(start - 6, start) === 'async ') start -= 6
      const script = source.slice(start, source.indexOf('\n}', start) + 2)
      const context = vm.createContext({ canWriteEvaluations: { value: false } })
      vm.runInContext(script, context)
      await context[name]()
    }
  })
}

async function renderRegion(page, select, values) {
  const [{ parse, compileScript }, { parse: parseTemplate }, { renderToString }, vue] = await Promise.all([
    import('@vue/compiler-sfc'), import('@vue/compiler-dom'), import('@vue/server-renderer'), import('vue')
  ])
  const source = await readFile(new URL(`../src/admin/pages/lens/${page}.vue`, import.meta.url), 'utf8')
  const { descriptor } = parse(source)
  const root = parseTemplate(descriptor.template.content)
  const nodes = []
  const visit = (node) => {
    if (select(node)) nodes.push(node.loc.source)
    else (node.children || []).forEach(visit)
  }
  visit(root)
  assert.ok(nodes.length)
  const { descriptor: fragment } = parse(`<template>${nodes.join('')}</template><script setup>
    defineProps(${JSON.stringify(Object.keys(values))})
    const t = (key) => key
    const formatDate = (value) => value
    const shortUuid = (value) => value
    const runStatusClass = () => ''
  </script>`)
  const helpers = {}
  const script = compileScript(fragment, { id: page, inlineTemplate: true }).content
    .replace(/import \{([^}]+)\} from "vue"/g, (_, names) => {
      for (const item of names.split(',')) {
        const [name, alias] = item.trim().split(/\s+as\s+/)
        helpers[alias || name] = vue[name]
      }
      return ''
    }).replace('export default', 'globalThis.component =')
  const context = vm.createContext(helpers)
  vm.runInContext(script, context)
  const app = vue.createSSRApp(context.component, values)
  app.component('BaseButton', { render() { return vue.h('button', this.$slots.default?.()) } })
  for (const name of ['BaseModal', 'BaseDrawer', 'ConfirmDeleteModal']) {
    app.component(name, { props: ['show'], render() { return this.show ? vue.h('dialog') : null } })
  }
  app.component('router-link', { render() { return vue.h('a', this.$slots.default?.()) } })
  return renderToString(app)
}

const headerActions = (node) => node.tag === 'div' && node.props?.some(
  (prop) => prop.name === 'class' && prop.value?.content === 'flex flex-wrap items-center gap-2'
)

for (const [page, labels] of [
  ['Evaluation', ['evaluation.datasets.create']],
  ['EvaluationDataset', ['evaluation.runs.launch']],
  ['EvaluationReport', ['evaluation.report.pause', 'evaluation.report.resume', 'evaluation.report.cancel', 'evaluation.report.retry']]
]) {
  test(`${page} renders reading actions and hides mutation controls for readonly delegation`, async () => {
    const values = { canWriteEvaluations: true, loading: false, launching: false, caseTotal: 1,
      canPause: true, isPaused: true, isActive: true, isTerminal: true, actionLoading: false }
    const writable = await renderRegion(page, headerActions, values)
    const readonly = await renderRegion(page, headerActions, { ...values, canWriteEvaluations: false })
    for (const label of labels) {
      assert.ok(writable.includes(label), label)
      assert.ok(!readonly.includes(label), label)
    }
    assert.ok(readonly.includes('common.refresh'))
    if (page === 'EvaluationDataset') assert.ok(readonly.includes('evaluation.revisions.title'))
    if (page === 'EvaluationReport') assert.ok(readonly.includes('evaluation.comparison.action'))
  })
}

test('open evaluation mutation dialogs disappear when delegation changes to readonly', async () => {
  for (const page of ['Evaluation', 'EvaluationDataset']) {
    const dialogs = (node) => ['BaseModal', 'BaseDrawer', 'ConfirmDeleteModal'].includes(node.tag) &&
      !node.loc.source.includes(':show="revisionsOpen"')
    const values = { canWriteEvaluations: true, createOpen: true, deleteTarget: { name: 'Dataset' },
      caseFormOpen: true, importOpen: true, deleteCaseTarget: { input: 'Question' },
      editingCase: null, saving: false, importing: false }
    assert.match(await renderRegion(page, dialogs, values), /<dialog/)
    assert.doesNotMatch(await renderRegion(page, dialogs, { ...values, canWriteEvaluations: false }), /<dialog/)
  }
})

test('case edit and import controls disappear while calibration history remains readable', async () => {
  const caseControls = (node) => node.tag === 'div' && node.props?.some(
    (prop) => prop.name === 'if' && prop.exp?.content?.includes('canWriteEvaluations')
  )
  const values = { canWriteEvaluations: true, activeTab: 'cases', row: {} }
  const writable = await renderRegion('EvaluationDataset', caseControls, values)
  const readonly = await renderRegion('EvaluationDataset', caseControls, { ...values, canWriteEvaluations: false })
  for (const label of ['evaluation.cases.add', 'evaluation.cases.import', 'common.edit', 'common.delete']) {
    assert.ok(writable.includes(label))
    assert.ok(!readonly.includes(label))
  }
  const calibration = (node) => node.tag === 'section' && node.loc.source.includes('evaluation.calibration.title')
  const history = await renderRegion('EvaluationReport', calibration, {
    canWriteEvaluations: false, userStore: { delegation: { mode: 'readonly' } },
    calibrations: [{ id: 1, reviewer: 'Private reviewer', explanation: 'Review retained', created_at: 'Today',
      answer_verdict: 'pass', evidence_verdict: 'pass' }]
  })
  assert.ok(history.includes('Review retained'))
  assert.doesNotMatch(history, /Private reviewer|<select|<textarea|evaluation.calibration.save/)
})
