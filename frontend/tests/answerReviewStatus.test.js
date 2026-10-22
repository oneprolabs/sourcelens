import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

const componentSource = () =>
  readFile(
    new URL('../src/components/lens/AnswerReviewStatus.vue', import.meta.url),
    'utf8'
  )

test('keeps unknown coverage out of expandable review details', async () => {
  const contents = await componentSource()

  assert.doesNotMatch(
    contents,
    /unknown: 'lens\.chat\.answerReview\.coverage\.unknown'/
  )
  assert.match(
    contents,
    /:aria-expanded="details\.length \? expanded : undefined"/
  )
  assert.match(contents, /@click="details\.length && \(expanded = !expanded\)"/)
})

test('chat hides answers whose review was not requested', async () => {
  const contents = await componentSource()
  const chat = await readFile(
    new URL('../src/pages/lens/Chat.vue', import.meta.url),
    'utf8'
  )

  assert.match(contents, /showNotRequested:/)
  assert.match(
    contents,
    /value === 'not_requested' && !props\.showNotRequested/
  )
  assert.match(chat, /show-not-requested="false"/)
})
