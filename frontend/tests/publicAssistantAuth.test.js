import assert from 'node:assert/strict'
import fs from 'node:fs'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

const chatPath = fileURLToPath(
  new URL('../src/pages/lens/Chat.vue', import.meta.url)
)
const chatSource = fs.readFileSync(chatPath, 'utf8')

test('reloads assistant access when authentication state changes', () => {
  assert.match(
    chatSource,
    /watch\(\s*\(\) => userStore\.isAuthenticated,[\s\S]*?bootstrap\(\)/
  )
})
