import { promptSuggestionKeys } from './chatPromptSuggestions.js'

const GENERIC_TOPICS = new Set([
  '结论',
  '总结',
  '摘要',
  '概览',
  '概述',
  '背景',
  '说明',
  '核心结论',
  '检索步骤概览',
  '下一步',
  'summary',
  'overview',
  'conclusion',
  'background',
  'introduction',
  'next steps'
])

function cleanTopic(value) {
  return String(value || '')
    .replace(/\[\[[^\]]*\]\]/g, '')
    .replace(/^[#*_`\s]+|[#*_`\s]+$/g, '')
    .replace(/^[一二三四五六七八九十]+[、.．]\s*/, '')
    .replace(/\s+/g, ' ')
    .trim()
}

function normalizeQuestion(value) {
  return String(value || '')
    .toLocaleLowerCase()
    .replace(/[\s\p{P}\p{S}]+/gu, '')
}

function bigramSimilarity(left, right) {
  if (!left || !right) return 0
  const leftPairs = new Set()
  const rightPairs = new Set()
  for (let index = 0; index < left.length - 1; index += 1) {
    leftPairs.add(left.slice(index, index + 2))
  }
  for (let index = 0; index < right.length - 1; index += 1) {
    rightPairs.add(right.slice(index, index + 2))
  }
  if (!leftPairs.size || !rightPairs.size) return 0
  let overlap = 0
  for (const pair of leftPairs) {
    if (rightPairs.has(pair)) overlap += 1
  }
  return (2 * overlap) / (leftPairs.size + rightPairs.size)
}

function wasAlreadyAsked(suggestion, askedQuestions) {
  const normalized = normalizeQuestion(suggestion)
  if (!normalized) return true
  for (const asked of askedQuestions) {
    if (!asked) continue
    if (asked === normalized) return true
    if (
      Math.min(asked.length, normalized.length) >= 8 &&
      (asked.includes(normalized) || normalized.includes(asked))
    ) {
      return true
    }
    if (bigramSimilarity(asked, normalized) >= 0.8) return true
  }
  return false
}

function collectTopics(content, pattern, seen) {
  const topics = []
  for (const match of String(content || '').matchAll(pattern)) {
    const topic = cleanTopic(match[1] || match[2]).slice(0, 40)
    const key = topic.toLocaleLowerCase()
    if (
      !topic ||
      topic.length > 32 ||
      /[：:]/.test(topic) ||
      GENERIC_TOPICS.has(key) ||
      seen.has(key)
    ) {
      continue
    }
    seen.add(key)
    topics.push(topic)
  }
  return topics
}

function answerTopics(content) {
  const seen = new Set()
  const topics = collectTopics(content, /^##\s+(.+)\s*$/gm, seen)
  if (topics.length < 2) {
    topics.push(...collectTopics(content, /^###\s+(.+)\s*$/gm, seen))
  }
  if (topics.length < 2) {
    topics.push(...collectTopics(content, /^\*\*(.+)\*\*\s*$/gm, seen))
  }
  return topics.slice(0, 3)
}

export function buildFollowUpSuggestions({
  content = '',
  citations = [],
  task = '',
  askedQuestions = [],
  translate
}) {
  const suggestions = []
  const asked = (Array.isArray(askedQuestions) ? askedQuestions : [])
    .map(normalizeQuestion)
    .filter(Boolean)
    .slice(-50)
  const add = (value) => {
    const suggestion = String(value || '').trim()
    if (!suggestion || suggestions.includes(suggestion)) return
    if (wasAlreadyAsked(suggestion, asked)) return
    suggestions.push(suggestion)
  }
  const topics = answerTopics(content)
  if (topics[0]) {
    add(translate('lens.chat.followUps.detail', { topic: topics[0] }))
  }
  if (topics[1]) {
    add(translate('lens.chat.followUps.details', { topic: topics[1] }))
  }
  if (Array.isArray(citations) && citations.length) {
    add(translate('lens.chat.followUps.sources'))
  } else if (topics[2]) {
    add(translate('lens.chat.followUps.details', { topic: topics[2] }))
  }
  for (const key of promptSuggestionKeys(task)) {
    if (suggestions.length >= 3) break
    add(translate(key))
  }
  return suggestions.slice(0, 3)
}
