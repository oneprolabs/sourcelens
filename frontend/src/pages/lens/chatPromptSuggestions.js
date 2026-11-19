const GENERAL_SUGGESTIONS = [
  'lens.chat.suggestions.generalCapabilities',
  'lens.chat.suggestions.generalAnalysis',
  'lens.chat.suggestions.generalPlan'
]

const SUGGESTIONS_BY_TASK = {
  code_analysis: [
    'lens.chat.suggestions.codeArchitecture',
    'lens.chat.suggestions.codeFlow',
    'lens.chat.suggestions.codeRisks'
  ],
  knowledge_qa: [
    'lens.chat.suggestions.knowledgeSummary',
    'lens.chat.suggestions.knowledgeCompare',
    'lens.chat.suggestions.knowledgeActions'
  ],
  qa: [
    'lens.chat.suggestions.knowledgeSummary',
    'lens.chat.suggestions.knowledgeCompare',
    'lens.chat.suggestions.knowledgeActions'
  ]
}

export function promptSuggestionKeys(task) {
  return SUGGESTIONS_BY_TASK[task] || GENERAL_SUGGESTIONS
}

export function assistantCapability(assistant) {
  return assistant?.capability || assistant?.selected_task
}
