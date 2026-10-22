import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

import { buildFollowUpSuggestions } from '../src/pages/lens/chatFollowUps.js'

const translate = (key, params = {}) =>
  params.topic ? `${key}:${params.topic}` : key

test('builds follow-up questions from answer sections and citations', () => {
  const suggestions = buildFollowUpSuggestions({
    content: [
      '## 检索步骤概览',
      '过程说明',
      '## 五个产品模块',
      '模块列表',
      '**逐项核心能力**',
      '能力列表'
    ].join('\n'),
    citations: [{ id: 'src-1' }],
    task: 'knowledge_qa',
    translate
  })

  assert.deepEqual(suggestions, [
    'lens.chat.followUps.detail:五个产品模块',
    'lens.chat.followUps.details:逐项核心能力',
    'lens.chat.followUps.sources'
  ])
})

test('filters suggestions that were already asked in the conversation', () => {
  const textTranslate = (key, params = {}) => {
    if (key === 'lens.chat.followUps.detail') {
      return `请详细说明「${params.topic}」`
    }
    if (key === 'lens.chat.followUps.details') {
      return `「${params.topic}」还有哪些关键细节或边界？`
    }
    if (key === 'lens.chat.followUps.sources') {
      return '这些结论分别来自哪些来源？'
    }
    return key
  }
  const suggestions = buildFollowUpSuggestions({
    content: '## 五个产品模块\n模块列表\n## 逐项核心能力\n能力列表',
    citations: [{ id: 'src-1' }],
    task: 'knowledge_qa',
    askedQuestions: ['请详细说明「五个产品模块」'],
    translate: textTranslate
  })

  assert.deepEqual(suggestions, [
    '「逐项核心能力」还有哪些关键细节或边界？',
    '这些结论分别来自哪些来源？',
    'lens.chat.suggestions.knowledgeSummary'
  ])
})

test('skips sentence-like headings and near-duplicate asked questions', () => {
  const textTranslate = (key, params = {}) => {
    if (key === 'lens.chat.followUps.detail') {
      return `请详细说明「${params.topic}」`
    }
    if (key === 'lens.chat.followUps.details') {
      return `「${params.topic}」还有哪些关键细节或边界？`
    }
    if (key === 'lens.chat.followUps.sources') {
      return '这些结论分别来自哪些来源？'
    }
    return key
  }
  const suggestions = buildFollowUpSuggestions({
    content:
      '## 郑伟（GitHub: fengren）：当日三仓库范围内唯一有可核实产出的成员\n正文',
    citations: [{ id: 'src-1' }],
    task: 'knowledge_qa',
    askedQuestions: [
      '请详细说明「郑伟（GitHub: fengren）——当日唯一有可核实产出的成员」'
    ],
    translate: textTranslate
  })

  assert.equal(
    suggestions.some((item) => item.includes('郑伟')),
    false
  )
  assert.equal(suggestions[0], '这些结论分别来自哪些来源？')
})

test('falls back to subsections when the top heading is a sentence', () => {
  const textTranslate = (key, params = {}) => {
    if (key === 'lens.chat.followUps.detail') {
      return `请详细说明「${params.topic}」`
    }
    if (key === 'lens.chat.followUps.details') {
      return `「${params.topic}」还有哪些关键细节或边界？`
    }
    if (key === 'lens.chat.followUps.sources') {
      return '这些结论分别来自哪些来源？'
    }
    return key
  }
  const suggestions = buildFollowUpSuggestions({
    content: [
      '## 郑伟（GitHub: fengren）：当日三仓库范围内唯一有可核实产出的成员',
      '### 一、可核实完成的事实（证据链可验证）',
      '正文',
      '### 二、提出并推进，但当日无法核实最终合入',
      '正文'
    ].join('\n'),
    citations: [{ id: 'src-1' }],
    task: 'knowledge_qa',
    askedQuestions: [
      '请详细说明「郑伟（GitHub: fengren）——当日唯一有可核实产出的成员」'
    ],
    translate: textTranslate
  })

  assert.deepEqual(suggestions, [
    '请详细说明「可核实完成的事实（证据链可验证）」',
    '「提出并推进，但当日无法核实最终合入」还有哪些关键细节或边界？',
    '这些结论分别来自哪些来源？'
  ])
})

test('filters near-duplicate topics with extra wording', () => {
  const textTranslate = (key, params = {}) => {
    if (key === 'lens.chat.followUps.detail') {
      return `请详细说明「${params.topic}」`
    }
    if (key === 'lens.chat.followUps.details') {
      return `「${params.topic}」还有哪些关键细节或边界？`
    }
    return key
  }
  const suggestions = buildFollowUpSuggestions({
    content: '## 五个产品模块的定位与边界\n正文',
    task: 'knowledge_qa',
    askedQuestions: ['请详细说明「五个产品模块的定位」'],
    translate: textTranslate
  })

  assert.equal(
    suggestions.some((item) => item.includes('五个产品模块')),
    false
  )
})

test('uses a third section when the answer has no citations', () => {
  const suggestions = buildFollowUpSuggestions({
    content: [
      '### 一、可核实完成的事实',
      '正文',
      '### 二、提出并推进',
      '正文',
      '### 三、问题上报',
      '正文'
    ].join('\n'),
    task: 'knowledge_qa',
    translate
  })

  assert.deepEqual(suggestions, [
    'lens.chat.followUps.detail:可核实完成的事实',
    'lens.chat.followUps.details:提出并推进',
    'lens.chat.followUps.details:问题上报'
  ])
})

test('preserves technical names when extracting answer topics', () => {
  const suggestions = buildFollowUpSuggestions({
    content: '## C# 与 .NET\n对比说明',
    citations: [{ id: 'src-1' }],
    task: 'knowledge_qa',
    translate
  })

  assert.equal(suggestions[0], 'lens.chat.followUps.detail:C# 与 .NET')
})

test('falls back to task prompts when the answer has no usable sections', () => {
  const suggestions = buildFollowUpSuggestions({
    content: 'A short answer without headings.',
    task: 'knowledge_qa',
    translate
  })

  assert.deepEqual(suggestions, [
    'lens.chat.suggestions.knowledgeSummary',
    'lens.chat.suggestions.knowledgeCompare',
    'lens.chat.suggestions.knowledgeActions'
  ])
})

test('keeps follow-up chips fixed above the composer', async () => {
  const source = await readFile(
    new URL('../src/pages/lens/Chat.vue', import.meta.url),
    'utf8'
  )
  const followUps = source.indexOf('data-testid="follow-up-suggestions"')
  const composer = source.indexOf('class="composer-shell relative"')

  assert.notEqual(followUps, -1)
  assert.notEqual(composer, -1)
  assert.ok(followUps < composer)
  assert.match(source, /v-if="followUpSuggestions\.length"/)
  assert.match(source, /const lastFollowUpMessage = computed/)
  assert.match(source, /isRunActive\.value \|\| showLiveAnswer\.value/)
  assert.match(source, /status !== 'awaiting_user_input'/)
  assert.match(source, /isFailedRunStatus\(message\?\.thinking\?\.status\)/)
})
