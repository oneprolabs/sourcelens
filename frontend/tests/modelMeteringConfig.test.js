import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import vm from 'node:vm'
import { computed, reactive, ref } from 'vue'
import { extractErrorMessage } from '../src/utils/api.js'

const source = await readFile(
  new URL('../src/admin/pages/LLM/DataSettings.vue', import.meta.url),
  'utf8'
)
const script = source
  .split('<script setup>')[1]
  .split('</script>')[0]
  .replace(/^import .*$/gm, '')
const config = {
  retention_days: 365,
  cleanup_enabled: true,
  cleanup_crontab: '0 2 * * *',
  aggregation_crontab: '5 * * * *'
}

function setup(api) {
  const context = vm.createContext({
    computed,
    reactive,
    ref,
    extractErrorMessage,
    onMounted() {},
    onBeforeUnmount() {},
    onBeforeRouteLeave() {},
    useI18n: () => ({ t: (key) => key }),
    useToast: () => ({ showSuccess() {}, showError() {} }),
    llmAdminApi: api
  })
  vm.runInContext(script, context)
  return (code) => vm.runInContext(code, context)
}

test('metering settings reject invalid edits without changing the persisted state', async () => {
  const run = setup({
    updateMeteringConfig: async () => {
      throw { response: { data: { cleanup_crontab: ['Invalid cron'] } } }
    }
  })
  run(`assignFromValue(${JSON.stringify(config)})`)
  run("form.cleanup_crontab = '99 99 * * *'; form.cleanup_enabled = false")
  await run('saveConfig()')
  assert.match(run('saveError.value'), /Invalid cron/)
  assert.equal(run('dirty.value'), true)
  run('resetForm()')
  assert.equal(run('form.cleanup_enabled'), true)
  assert.equal(run('form.cleanup_crontab'), config.cleanup_crontab)
  assert.equal(run('dirty.value'), false)
})

test('metering settings only submit changed fields and accept actual scheduler state', async () => {
  let payload
  const run = setup({
    updateMeteringConfig: async (value) => {
      payload = JSON.parse(JSON.stringify(value))
      return { ...config, ...value }
    }
  })
  run(`assignFromValue(${JSON.stringify(config)})`)
  run('form.cleanup_enabled = false')
  await run('saveConfig()')
  assert.deepEqual(payload, { cleanup_enabled: false })
  assert.equal(run('form.cleanup_crontab'), config.cleanup_crontab)
  assert.equal(run('dirty.value'), false)
})
