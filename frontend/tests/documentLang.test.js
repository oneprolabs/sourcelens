import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

import { toDocumentLang } from '../src/utils/documentLang.js'

test('toDocumentLang maps UI locales to HTML/date BCP-47 tags', () => {
  assert.equal(toDocumentLang('en'), 'en-US')
  assert.equal(toDocumentLang('zh-CN'), 'zh-CN')
  assert.equal(toDocumentLang('fr'), 'en-US')
})

test('BaseDateInput hides native empty chrome and shows i18n placeholder', () => {
  const source = readFileSync(
    new URL('../src/components/ui/BaseDateInput.vue', import.meta.url),
    'utf8'
  )
  assert.match(source, /common\.datePlaceholder/)
  assert.match(source, /whitespace-nowrap/)
  assert.match(source, /compact/)
  assert.match(source, /md:w-36/)
  assert.match(source, /text-transparent is-empty/)
  assert.match(source, /z-10 flex items-center/)
  assert.match(source, /text-transparent is-empty/)
  assert.match(source, /toDocumentLang\(locale\)/)
})

test('History filters use BaseDateInput', () => {
  const source = readFileSync(
    new URL('../src/admin/pages/lens/RunObservation.vue', import.meta.url),
    'utf8'
  )
  assert.match(source, /<BaseDateInput/)
  assert.match(source, /compact/)
  assert.match(source, /md:grid-cols-/)
  assert.doesNotMatch(
    source,
    /type="date"[\s\S]{0,80}:lang="toDocumentLang\(locale\)"/
  )
})

test('main.css positions BaseDateInput calendar and hides native empty text', () => {
  const source = readFileSync(
    new URL('../src/assets/css/main.css', import.meta.url),
    'utf8'
  )
  assert.match(
    source,
    /input\.base-date-input\.is-empty::-webkit-datetime-edit/
  )
  assert.match(
    source,
    /input\.base-date-input::-webkit-calendar-picker-indicator/
  )
})
test('common locales define datePlaceholder', () => {
  const en = JSON.parse(
    readFileSync(new URL('../src/locales/en.json', import.meta.url), 'utf8')
  )
  const zh = JSON.parse(
    readFileSync(new URL('../src/locales/zh-CN.json', import.meta.url), 'utf8')
  )
  assert.equal(en.common.datePlaceholder, 'YYYY-MM-DD')
  assert.equal(zh.common.datePlaceholder, '年 / 月 / 日')
})
