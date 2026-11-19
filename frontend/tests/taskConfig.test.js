import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import vm from 'node:vm'
import { computed, reactive, ref } from 'vue'

const component = await readFile(
  new URL(
    '../src/components/task-management/TaskConfigSection.vue',
    import.meta.url
  ),
  'utf8'
)
const script = component
  .split('<script setup>')[1]
  .split('</script>')[0]
  .replace(/^import .*$/gm, '')
const config = {
  timeout_minutes: 10,
  retention_days: 180,
  cleanup_crontab: '0 2 * * *',
  mark_timeout_crontab: '*/30 * * * *',
  cleanup_enabled: true,
  mark_timeout_enabled: true,
  cleanup_available: true,
  mark_timeout_available: true
}
function setup(api) {
  const context = vm.createContext({
    computed,
    reactive,
    ref,
    onMounted() {},
    onBeforeUnmount() {},
    onBeforeRouteLeave() {},
    useI18n: () => ({ t: (key) => key }),
    useToast: () => ({ showSuccess() {} }),
    taskManagementApi: api,
    extractResponseData: (response) => response.data,
    extractErrorMessage: (error) => error.message
  })
  vm.runInContext(script, context)
  return (code) => vm.runInContext(code, context)
}

test('reset restores scheduler switches and expressions together', async () => {
  const run = setup({ getConfig: async () => ({ data: config }) })
  await run('loadConfig()')
  run("form.cleanup_enabled = false; form.cleanup_crontab = '15 4 * * *'")
  assert.equal(run('dirty.value'), true)
  run('resetForm()')
  assert.equal(run('form.cleanup_enabled'), true)
  assert.equal(run('form.cleanup_crontab'), config.cleanup_crontab)
  assert.equal(run('dirty.value'), false)
})

test('saving a disabled schedule preserves its cron and marks returned settings clean', async () => {
  let sent
  const run = setup({
    getConfig: async () => ({ data: config }),
    updateConfig: async (payload) => {
      sent = payload
      return { data: { ...config, ...payload } }
    }
  })
  await run('loadConfig()')
  run('form.cleanup_enabled = false')
  await run('saveConfig()')
  assert.equal(sent.cleanup_enabled, false)
  assert.equal(Object.keys(sent).join(','), 'cleanup_enabled')
  assert.equal(run('form.cleanup_crontab'), '0 2 * * *')
  assert.equal(run('dirty.value'), false)
})

test('load failure remains retryable and never presents loaded defaults', async () => {
  let calls = 0
  const run = setup({
    getConfig: async () => {
      if (++calls === 1) throw new Error('offline')
      return { data: config }
    }
  })
  await run('loadConfig()')
  assert.equal(run('loadError.value'), 'offline')
  assert.equal(run('Object.keys(form).length'), 0)
  await run('loadConfig()')
  assert.equal(run('loadError.value'), '')
  assert.equal(run('form.timeout_minutes'), 10)
})

test('failed save keeps edited inputs and warns on page close', async () => {
  const run = setup({
    getConfig: async () => ({ data: config }),
    updateConfig: async () => {
      throw new Error('Invalid cron')
    }
  })
  await run('loadConfig()')
  run("form.cleanup_crontab = 'invalid'")
  await run('saveConfig()')
  assert.equal(run('saveError.value'), 'Invalid cron')
  assert.equal(run('dirty.value'), true)
  assert.equal(run('saving.value'), false)
  assert.equal(
    run(
      'let prevented = false; beforeUnload({ preventDefault() { prevented = true } }); prevented'
    ),
    true
  )
})

test('task statistics loads user options without depending on notification or model APIs', async () => {
  const source = await readFile(
    new URL('../src/admin/pages/TaskManagement/Stats.vue', import.meta.url),
    'utf8'
  )
  const start = source.indexOf('async function fetchUserOptions()')
  const end = source.indexOf('\n}', start) + 2
  const userOptions = ref([])
  const context = vm.createContext({
    userOptions,
    managementApi: {
      getAllUsers: async () => [
        { id: 7, username: 'operator', display_name: 'Operator' }
      ]
    }
  })
  vm.runInContext(source.slice(start, end), context)
  await vm.runInContext('fetchUserOptions()', context)
  assert.equal(userOptions.value[0].user_id, 7)
  assert.equal(userOptions.value[0].display, 'Operator')
})
