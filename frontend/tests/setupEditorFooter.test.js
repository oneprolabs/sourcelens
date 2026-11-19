import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import vm from 'node:vm'
import { compileScript, parse } from '@vue/compiler-sfc'
import { parse as parseTemplate } from '@vue/compiler-dom'
import { renderToString } from '@vue/server-renderer'
import * as vue from 'vue'

async function renderFooter(file, values) {
  const source = await readFile(
    new URL(`../src/pages/lens/${file}`, import.meta.url),
    'utf8'
  )
  const { descriptor } = parse(source)
  const ast = parseTemplate(descriptor.template.content)
  const drawer =
    file === 'Connections.vue'
      ? ast.children[0].children.find(
          (node) =>
            node.tag === 'BaseDrawer' &&
            node.props.some(
              (prop) =>
                prop.arg?.content === 'show' &&
                prop.exp?.content === 'drawerOpen'
            )
        )
      : ast.children.find((node) => node.tag === 'BaseDrawer')
  const footer = drawer.children.find(
    (node) =>
      node.tag === 'template' &&
      node.props.some(
        (prop) => prop.name === 'slot' && prop.arg?.content === 'footer'
      )
  )
  const markup = footer.children.map((node) => node.loc.source).join('')
  const body = `<template>${markup}</template><script setup>
    defineProps(${JSON.stringify(Object.keys(values))})
    const t = (key) => key
    const prevWizardStep = () => {}
    const nextWizardStep = () => {}
    const useStepByStep = () => {}
    const submitAssistant = () => {}
    const closeDrawer = () => {}
    const save = () => {}
  </script>`
  const { descriptor: component } = parse(body)
  const helpers = {}
  const script = compileScript(component, { id: file, inlineTemplate: true })
    .content.replace(/import \{([^}]+)\} from "vue"/g, (_, names) => {
      for (const item of names.split(',')) {
        const [name, alias] = item.trim().split(/\s+as\s+/)
        helpers[alias || name] = vue[name]
      }
      return ''
    })
    .replace('export default', 'globalThis.component =')
  const context = vm.createContext(helpers)
  vm.runInContext(script, context)
  const app = vue.createSSRApp(context.component, values)
  app.component('BaseButton', {
    props: ['disabled', 'loading'],
    render() {
      return vue.h(
        'button',
        { disabled: this.disabled || this.loading },
        this.$slots.default()
      )
    }
  })
  return renderToString(app)
}

for (const file of [
  'AssistantFormDrawerDirectEnvironment.vue',
  'DataSourceFormDrawer.vue'
]) {
  const defaults = {
    wizardStep: 1,
    typePickerOpen: false,
    quickCreate: false,
    canSaveAssistant: true,
    wizardStepCount: 4,
    mode: 'create',
    saving: false,
    canProceedWizard: true,
    isManagedWorkspace: false,
    connectionResult: { status: 'success' },
    isFileUpload: false,
    uploadFiles: [],
    continueLabel: 'Save and continue to next stage',
    skipLabel: ''
  }
  test(`${file} offers continuation only on the final step and retains ordinary save`, async () => {
    const first = await renderFooter(file, defaults)
    assert.match(first, /lensAdmin.wizard.next/)
    assert.doesNotMatch(first, /Save and continue|lensAdmin.wizard.finish/)
    const final = await renderFooter(file, { ...defaults, wizardStep: 4 })
    assert.match(final, /Save and continue to next stage/)
    assert.match(final, /lensAdmin.wizard.finish/)
    const standalone = await renderFooter(file, {
      ...defaults,
      wizardStep: 4,
      continueLabel: ''
    })
    assert.doesNotMatch(standalone, /Save and continue/)
    assert.equal((standalone.match(/<button/g) || []).length, 2)
    const saving = await renderFooter(file, {
      ...defaults,
      wizardStep: 4,
      saving: true
    })
    assert.match(saving, /<button disabled[^>]*>Save and continue/)
  })
}

test('datasource continuation is disabled by the same connection validation as ordinary save', async () => {
  const html = await renderFooter('DataSourceFormDrawer.vue', {
    wizardStep: 3,
    wizardStepCount: 3,
    mode: 'create',
    saving: false,
    canProceedWizard: true,
    isManagedWorkspace: false,
    connectionResult: { status: 'error' },
    isFileUpload: false,
    uploadFiles: [],
    continueLabel: 'Save and continue',
    skipLabel: ''
  })
  assert.match(html, /<button disabled[^>]*>lensAdmin.wizard.finish/)
  assert.match(html, /<button disabled[^>]*>Save and continue/)
})

test('optional knowledge can be skipped inside the drawer until saving begins', async () => {
  const values = {
    wizardStep: 1,
    wizardStepCount: 3,
    mode: 'create',
    saving: false,
    canProceedWizard: false,
    continueLabel: '',
    skipLabel: 'Skip knowledge'
  }
  const first = await renderFooter('DataSourceFormDrawer.vue', values)
  assert.match(first, /<button(?![^>]*disabled)[^>]*>Skip knowledge/)
  const saving = await renderFooter('DataSourceFormDrawer.vue', {
    ...values,
    saving: true
  })
  assert.match(saving, /<button disabled[^>]*>Skip knowledge/)
  const required = await renderFooter('DataSourceFormDrawer.vue', {
    ...values,
    skipLabel: ''
  })
  assert.doesNotMatch(required, /Skip knowledge/)
})

test('connection footer preserves standalone save and offers setup continuation with the same guard', async () => {
  const values = {
    embedded: true,
    form: { status: 'active' },
    saving: false,
    canSave: false,
    continueLabel: 'Continue to knowledge'
  }
  const invalid = await renderFooter('Connections.vue', values)
  assert.match(
    invalid,
    /<button disabled[^>]*>lensAdmin.connections.saveAndValidate/
  )
  assert.match(invalid, /<button disabled[^>]*>Continue to knowledge/)
  const standalone = await renderFooter('Connections.vue', {
    ...values,
    embedded: false,
    canSave: true,
    continueLabel: ''
  })
  assert.match(standalone, /<button(?![^>]*disabled)[^>]*>common.save/)
  assert.doesNotMatch(standalone, /Continue to knowledge|saveAndValidate/)
})

test('assistant footer handles three steps, the picker and quick creation with one save guard', async () => {
  const file = 'AssistantFormDrawerDirectEnvironment.vue'
  const values = {
    typePickerOpen: false,
    quickCreate: false,
    wizardStep: 3,
    wizardStepCount: 3,
    mode: 'create',
    saving: false,
    canProceedWizard: true,
    canSaveAssistant: false,
    continueLabel: 'Continue setup'
  }
  const final = await renderFooter(file, values)
  assert.match(final, /<button disabled[^>]*>lensAdmin.wizard.finish/)
  assert.match(final, /<button disabled[^>]*>Continue setup/)
  assert.doesNotMatch(final, /lensAdmin.wizard.next/)
  const picker = await renderFooter(file, { ...values, typePickerOpen: true })
  assert.doesNotMatch(
    picker,
    /lensAdmin.wizard.finish|Continue setup|lensAdmin.wizard.next/
  )
  const quick = await renderFooter(file, {
    ...values,
    wizardStep: 1,
    quickCreate: true,
    canSaveAssistant: true
  })
  assert.match(quick, /lensAdmin.creation.stepByStep/)
  assert.match(quick, /<button(?![^>]*disabled)[^>]*>Continue setup/)
  assert.doesNotMatch(quick, /lensAdmin.wizard.next/)
})
