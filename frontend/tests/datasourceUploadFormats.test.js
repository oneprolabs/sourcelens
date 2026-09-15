import assert from 'node:assert/strict'
import test from 'node:test'

import {
  DATASOURCE_UPLOAD_ACCEPT,
  isSupportedUploadFile
} from '../src/utils/lens.js'

test('manual upload accepts document, text, image, and archive formats', () => {
  for (const name of [
    'report.pdf',
    'brief.docx',
    'legacy.doc',
    'deck.pptx',
    'legacy.ppt',
    'sheet.xlsx',
    'legacy.xls',
    'notes.txt',
    'readme.md',
    'data.csv',
    'manifest.json',
    'page.html',
    'feed.xml',
    'photo.PNG',
    'bundle.zip'
  ]) {
    assert.equal(isSupportedUploadFile(name), true, name)
  }
})

test('manual upload rejects unknown or executable formats', () => {
  for (const name of ['installer.exe', 'script.sh', 'archive.rar', '']) {
    assert.equal(isSupportedUploadFile(name), false, name)
  }
})

test('the file input accept list covers every supported format', () => {
  for (const extension of ['.pdf', '.docx', '.txt', '.csv', '.zip']) {
    assert.ok(DATASOURCE_UPLOAD_ACCEPT.includes(extension), extension)
  }
})
