import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

const componentSource = () =>
  readFile(
    new URL('../src/admin/pages/lens/RunDiagnosisPanel.vue', import.meta.url),
    'utf8'
  )

const apiSource = () =>
  readFile(new URL('../src/api/lens.js', import.meta.url), 'utf8')

test('diagnosis panel exposes generation and evidence-bound follow-up', async () => {
  const contents = await componentSource()

  assert.match(contents, /defineExpose\(\{ generate, load \}\)/)
  assert.match(contents, /data-testid="diagnosis-follow-up"/)
  assert.match(contents, /maxlength="2000"/)
  assert.match(contents, /createAdminRunDiagnosticTurn/)
  assert.match(contents, /emit\('navigate', evidenceRef\)/)
  assert.match(contents, /aria-live="polite"/)
})

test('diagnosis panel uses a compact single-surface layout', async () => {
  const contents = await componentSource()

  assert.match(contents, /data-testid="diagnosis-empty-content"/)
  assert.match(contents, /:disabled="!canGenerate"/)
  assert.match(contents, /class="diagnosis-card"/)
  assert.doesNotMatch(contents, /min-h-\[32rem\]/)
  assert.doesNotMatch(contents, /sticky bottom-0/)
  assert.doesNotMatch(contents, /shadow-sm/)
})

test('lens API includes diagnosis list, generate, and follow-up calls', async () => {
  const contents = await apiSource()

  assert.match(contents, /getAdminRunDiagnostics/)
  assert.match(contents, /generateAdminRunDiagnosis/)
  assert.match(contents, /createAdminRunDiagnosticTurn/)
  assert.match(contents, /\/diagnostics\//)
  assert.match(contents, /\/turns\//)
})

test('readonly diagnosis inspection cannot submit a model-backed follow-up', async () => {
  const { default: vm } = await import('node:vm')
  const contents = await componentSource()
  const start = contents.indexOf('async function submitFollowUp(')
  const script = contents.slice(start, contents.indexOf('\n}', start) + 2)
  const context = vm.createContext({
    props: { readonly: true, canGenerate: true, runUuid: 'run' },
    question: { value: 'Why did this fail?' },
    latest: { value: { uuid: 'diagnosis', status: 'completed' } },
    submitting: { value: false },
    createAdminRunDiagnosticTurn: () =>
      assert.fail('readonly follow-up reached the API')
  })
  vm.runInContext(script, context)
  await context.submitFollowUp()
  assert.equal(context.submitting.value, false)
  assert.equal(context.question.value, 'Why did this fail?')
})
