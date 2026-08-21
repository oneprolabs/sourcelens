/**
 * Shared helpers for access-control E2E specs.
 *
 * Fixtures are produced by global-setup.cjs (which seeds the backend and
 * logs in each role). Auth is injected into localStorage so the SPA boots
 * already authenticated as the chosen role — no fragile UI login.
 */
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const here = path.dirname(fileURLToPath(import.meta.url))
const FIXTURES = path.join(here, 'fixtures.json')

export function fixtures() {
  return JSON.parse(fs.readFileSync(FIXTURES, 'utf-8'))
}

/** Inject a role's JWT into localStorage before any page script runs. */
export async function asRole(page, role) {
  const token = fixtures().tokens[role]
  await page.addInitScript((t) => {
    window.localStorage.setItem('access_token', t)
    window.localStorage.setItem('userLanguage', 'en')
  }, token)
  return token
}

export function authHeader(role) {
  return { Authorization: `Bearer ${fixtures().tokens[role]}` }
}

/** Pull list rows out of the {code,message,data:{results}} envelope. */
export function listRows(body) {
  const data = body?.data ?? body
  if (Array.isArray(data)) return data
  return data?.results ?? []
}

export function listSlugs(body) {
  return listRows(body).map((row) => row.slug)
}
