import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import vm from 'node:vm'
import { computed, reactive, ref } from 'vue'
import { extractErrorMessage } from '../src/utils/api.js'

const source = await readFile(
  new URL('../src/admin/pages/Notifications/Config.vue', import.meta.url),
  'utf8'
)
const script = source.split('<script setup>')[1].split('</script>')[0].replace(/^import .*$/gm, '')
const config = { retention_days: 180, cleanup_enabled: true, cleanup_crontab: '0 2 * * *' }

function setup(api) {
  const context = vm.createContext({
    computed, reactive, ref, extractErrorMessage,
    onMounted() {}, onBeforeUnmount() {}, onBeforeRouteLeave() {}, setTimeout() {},
    useI18n: () => ({ t: key => key }),
    useToast: () => ({ showSuccess() {}, showError() {} }),
    notificationsAdminApi: api
  })
  vm.runInContext(script, context)
  return code => vm.runInContext(code, context)
}

test('notification configuration rejection preserves the draft and displays field validation', async () => {
  const run = setup({ putGlobalConfig: async () => {
    throw { response: { data: { code: 400, message: 'failed', data: { cleanup_crontab: ['Invalid cron'] } } } }
  } })
  run(`assignFromValue(${JSON.stringify(config)})`)
  run("form.cleanup_crontab = '99 99 * * *'")
  await run('saveConfig()')
  assert.equal(run('saveError.value'), 'Invalid cron')
  assert.equal(run('dirty.value'), true)
  assert.equal(run('saving.value'), false)
  assert.equal(run('initialValues.cleanup_crontab'), config.cleanup_crontab)
  run('resetForm()')
  assert.equal(run('dirty.value'), false)
  assert.equal(run('saveError.value'), '')
})

test('notification configuration sends changed fields and accepts the persisted state', async () => {
  let payload
  const run = setup({ putGlobalConfig: async value => {
    payload = JSON.parse(JSON.stringify(value))
    return { value: { ...config, ...value } }
  } })
  run(`assignFromValue(${JSON.stringify(config)})`)
  run('form.cleanup_enabled = false')
  await run('saveConfig()')
  assert.deepEqual(payload, { cleanup_enabled: false })
  assert.equal(run('form.cleanup_crontab'), config.cleanup_crontab)
  assert.equal(run('dirty.value'), false)
})
