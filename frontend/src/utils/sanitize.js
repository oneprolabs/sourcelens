/**
 * HTML sanitization for safe v-html output (e.g. Markdown-rendered content).
 * Uses DOMPurify to prevent XSS while allowing common content tags.
 */

import DOMPurify from 'dompurify'

/**
 * Sanitize HTML string for safe insertion into DOM
 * @param {string} dirty - Raw HTML string (e.g. from marked.parse())
 * @returns {string} - Sanitized HTML
 */
export function sanitizeHtml(dirty) {
  if (typeof dirty !== 'string') return ''
  return DOMPurify.sanitize(dirty, {
    ALLOWED_TAGS: [
      'p',
      'br',
      'strong',
      'em',
      'u',
      's',
      'code',
      'pre',
      'blockquote',
      'h1',
      'h2',
      'h3',
      'h4',
      'h5',
      'h6',
      'ul',
      'ol',
      'li',
      'hr',
      'a',
      'img',
      'table',
      'thead',
      'tbody',
      'tr',
      'th',
      'td',
      'span',
      'div',
      'button',
      'svg',
      'g',
      'path',
      'circle',
      'text',
      'tspan'
    ],
    ALLOWED_ATTR: [
      'href',
      'src',
      'alt',
      'title',
      'class',
      'target',
      'rel',
      'type',
      'aria-label',
      'data-markdown-code-copy',
      'data-mindmap-root',
      'data-mindmap-action',
      'data-mindmap-toggle',
      'data-mindmap-path',
      'data-mindmap-has-children',
      'data-mindmap-collapsed',
      'data-mindmap-link',
      'data-mindmap-width',
      'data-mindmap-height',
      'viewBox',
      'preserveAspectRatio',
      'aria-hidden',
      'role',
      'tabindex',
      'cx',
      'cy',
      'r',
      'd',
      'fill',
      'stroke',
      'stroke-width',
      'x',
      'y',
      'dx',
      'dy',
      'text-anchor'
    ]
  })
}

/**
 * Escape raw text for safe display in HTML (e.g. error fallback)
 * @param {string} text - Raw text
 * @returns {string} - Escaped HTML string
 */
export function escapeHtml(text) {
  if (typeof text !== 'string') return ''
  const map = {
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    '"': '&quot;',
    "'": '&#39;'
  }
  return text.replace(/[&<>"']/g, (ch) => map[ch])
}
