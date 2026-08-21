/**
 * Global setup for access-control E2E: seed deterministic backend fixtures
 * (users, group, public/private assistants, grants, shared Q&A), then log in
 * each role and persist tokens to fixtures.json for the specs.
 */
const { execSync } = require('node:child_process')
const fs = require('node:fs')
const path = require('node:path')

const BASE =
  process.env.BASE_URL ||
  process.env.PLAYWRIGHT_BASE_URL ||
  'http://localhost:8000'
const SEED_EXEC =
  process.env.E2E_SEED_EXEC || 'docker exec sourcelens-api-dev python manage.py'
const FIXTURES = path.join(__dirname, 'fixtures.json')

module.exports = async () => {
  const raw = execSync(`${SEED_EXEC} seed_e2e_access`, { encoding: 'utf-8' })
  const jsonLine = raw.trim().split('\n').filter(Boolean).pop()
  const seed = JSON.parse(jsonLine)

  const tokens = {}
  for (const [role, username] of Object.entries(seed.users)) {
    const res = await fetch(`${BASE}/api/v1/auth/login`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        email: `${username}@example.com`,
        password: seed.password
      })
    })
    const body = await res.json()
    const token = body?.data?.access
    if (!token) {
      throw new Error(`Login failed for ${username}: ${JSON.stringify(body)}`)
    }
    tokens[role] = token
  }

  fs.writeFileSync(
    FIXTURES,
    JSON.stringify({ baseURL: BASE, ...seed, tokens }, null, 2)
  )
  console.log(`[e2e access] seeded roles: ${Object.keys(tokens).join(', ')}`)
}
