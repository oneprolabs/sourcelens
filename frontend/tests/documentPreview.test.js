import assert from 'node:assert/strict'
import test from 'node:test'

import { normalizeMarkdownTables } from '../src/utils/documentPreview.js'

test('drops empty rows, extra separators, and blank columns', () => {
  const input = [
    '| 项目 | | 金额 | |',
    '| --- | --- | --- | --- |',
    '| 收入 | | 100 | |',
    '| --- | --- | --- | --- |',
    '| | | | |',
    '| 利润 | | 200 | |'
  ].join('\n')

  const output = normalizeMarkdownTables(input)

  assert.match(output, /\| 项目 \| 金额 \|/)
  assert.match(output, /\| --- \| --- \|/)
  assert.match(output, /\| 收入 \| 100 \|/)
  assert.match(output, /\| 利润 \| 200 \|/)
  assert.doesNotMatch(output, /\| \| \|/)
  assert.equal((output.match(/---/g) || []).length, 2)
})

test('collapses long runs of blank lines', () => {
  const output = normalizeMarkdownTables('a\n\n\n\n\nb')

  assert.equal(output, 'a\n\nb')
})

test('leaves prose without tables unchanged', () => {
  const input = '七、公司主要会计数据\n\n(一) 主要会计数据'

  assert.equal(normalizeMarkdownTables(input), input)
})

test('keeps a table whose header and delimiter are the only structure', () => {
  const output = normalizeMarkdownTables(
    '| 名称 | 数值 |\n| --- | --- |\n| 收入 | 100 |'
  )

  assert.equal(output, '| 名称 | 数值 |\n| --- | --- |\n| 收入 | 100 |')
})
