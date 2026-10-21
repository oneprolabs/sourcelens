import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import test from 'node:test'

import { createPinia, setActivePinia } from 'pinia'
import { createServer } from 'vite'

import {
  THEME_MODES,
  getNextThemeBoundary,
  isManagementPath,
  normalizeThemeMode,
  resolveTheme
} from '../src/utils/theme.js'
import { inspectThemedLogoPair } from './helpers/pngPixels.js'

const frontendRoot = fileURLToPath(new URL('..', import.meta.url))
const mainSource = readFileSync(
  new URL('../src/main.js', import.meta.url),
  'utf8'
)
const appSource = readFileSync(
  new URL('../src/App.vue', import.meta.url),
  'utf8'
)
const cssSource = readFileSync(
  new URL('../src/assets/css/main.css', import.meta.url),
  'utf8'
)
const tailwindSource = readFileSync(
  new URL('../tailwind.config.js', import.meta.url),
  'utf8'
)

const getCssBlock = (selector) => {
  const escapedSelector = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  const match = cssSource.match(
    new RegExp(`${escapedSelector}\\s*\\{([^}]*)\\}`)
  )

  assert.ok(match, `Missing CSS block for ${selector}`)
  return match[1]
}

const assertTokenValues = (source, tokens) => {
  for (const [name, value] of Object.entries(tokens)) {
    assert.match(
      source,
      new RegExp(`--${name}:\\s*${value.replace('#', '\\#')}`)
    )
  }
}

const createBrowserHarness = () => {
  const values = new Map()
  const timers = new Map()
  const mediaListeners = new Set()
  let addEventListenerCalls = 0
  let nextTimerId = 1
  let removeEventListenerCalls = 0

  const localStorage = {
    clear() {
      values.clear()
    },
    getItem(key) {
      return values.has(key) ? values.get(key) : null
    },
    removeItem(key) {
      values.delete(key)
    },
    setItem(key, value) {
      values.set(key, String(value))
    }
  }
  const mediaQuery = {
    matches: false,
    addEventListener(event, listener) {
      assert.equal(event, 'change')
      addEventListenerCalls += 1
      mediaListeners.add(listener)
    },
    removeEventListener(event, listener) {
      assert.equal(event, 'change')
      removeEventListenerCalls += 1
      mediaListeners.delete(listener)
    }
  }
  const window = {
    clearTimeout(timerId) {
      timers.delete(timerId)
    },
    matchMedia() {
      return mediaQuery
    },
    setTimeout(callback, delay) {
      const timerId = nextTimerId
      nextTimerId += 1
      timers.set(timerId, { callback, delay })
      return timerId
    }
  }
  const document = {
    documentElement: {
      dataset: {},
      lang: '',
      style: {}
    }
  }

  return {
    document,
    localStorage,
    mediaQuery,
    timers,
    window,
    get addEventListenerCalls() {
      return addEventListenerCalls
    },
    get removeEventListenerCalls() {
      return removeEventListenerCalls
    },
    fireMediaChange() {
      for (const listener of [...mediaListeners]) {
        listener({ matches: mediaQuery.matches })
      }
    },
    getOnlyTimer() {
      assert.equal(timers.size, 1)
      return timers.values().next().value
    },
    runOnlyTimer() {
      assert.equal(timers.size, 1)
      const [timerId, timer] = timers.entries().next().value
      timers.delete(timerId)
      timer.callback()
    }
  }
}

const installFixedNow = (now) => {
  const NativeDate = globalThis.Date

  class FixedDate extends NativeDate {
    constructor(...args) {
      super(...(args.length > 0 ? args : [now.getTime()]))
    }

    static now() {
      return now.getTime()
    }
  }

  globalThis.Date = FixedDate
  return () => {
    globalThis.Date = NativeDate
  }
}

const installBrowserGlobals = (browser) => {
  const replacements = {
    document: browser.document,
    localStorage: browser.localStorage,
    navigator: { language: 'en' },
    window: browser.window
  }
  const descriptors = new Map()

  for (const [name, value] of Object.entries(replacements)) {
    descriptors.set(name, Object.getOwnPropertyDescriptor(globalThis, name))
    Object.defineProperty(globalThis, name, {
      configurable: true,
      value,
      writable: true
    })
  }

  return () => {
    for (const [name, descriptor] of descriptors) {
      if (descriptor) {
        Object.defineProperty(globalThis, name, descriptor)
      } else {
        delete globalThis[name]
      }
    }
  }
}

test('exports all supported theme modes', () => {
  assert.deepEqual(THEME_MODES, ['light', 'dark', 'system', 'scheduled'])
})

test('normalizes invalid and null modes to system', () => {
  assert.equal(normalizeThemeMode('unknown'), 'system')
  assert.equal(normalizeThemeMode(null), 'system')
  assert.equal(normalizeThemeMode('dark'), 'dark')
})

test('resolves fixed and system theme modes', () => {
  const now = new Date(2026, 7, 4, 12)

  assert.equal(resolveTheme('light', now, true), 'light')
  assert.equal(resolveTheme('dark', now, false), 'dark')
  assert.equal(resolveTheme('system', now, true), 'dark')
  assert.equal(resolveTheme('system', now, false), 'light')
})

test('resolves invalid modes using the system preference', () => {
  const now = new Date(2026, 7, 4, 12)

  assert.equal(resolveTheme('unknown', now, true), 'dark')
  assert.equal(resolveTheme('unknown', now, false), 'light')
})

test('resolves scheduled theme at local time boundaries', () => {
  assert.equal(
    resolveTheme('scheduled', new Date(2026, 7, 4, 19, 59, 59), true),
    'light'
  )
  assert.equal(
    resolveTheme('scheduled', new Date(2026, 7, 4, 20), false),
    'dark'
  )
  assert.equal(
    resolveTheme('scheduled', new Date(2026, 7, 5, 6, 59, 59), false),
    'dark'
  )
  assert.equal(
    resolveTheme('scheduled', new Date(2026, 7, 5, 7), true),
    'light'
  )
})

test('returns 07:00 today before the morning boundary', () => {
  const boundary = getNextThemeBoundary(new Date(2026, 7, 4, 6, 59, 59))

  assert.deepEqual(boundary, new Date(2026, 7, 4, 7))
})

test('returns 20:00 today during daytime', () => {
  const boundary = getNextThemeBoundary(new Date(2026, 7, 4, 19, 59, 59))

  assert.deepEqual(boundary, new Date(2026, 7, 4, 20))
})

test('returns 20:00 today at the morning boundary', () => {
  const boundary = getNextThemeBoundary(new Date(2026, 7, 4, 7))

  assert.deepEqual(boundary, new Date(2026, 7, 4, 20))
})

test('returns 07:00 tomorrow after the evening boundary', () => {
  const boundary = getNextThemeBoundary(new Date(2026, 7, 4, 20))

  assert.deepEqual(boundary, new Date(2026, 7, 5, 7))
})

test('does not modify the date passed to boundary calculation', () => {
  const now = new Date(2026, 7, 4, 22, 30)
  const timestamp = now.getTime()

  getNextThemeBoundary(now)

  assert.equal(now.getTime(), timestamp)
})

test('identifies only canonical administration paths', () => {
  assert.equal(isManagementPath('/management'), true)
  assert.equal(isManagementPath('/management/users'), true)
  assert.equal(isManagementPath('/management/lens/runs?status=done'), true)
  assert.equal(isManagementPath('/management-tools'), false)
  assert.equal(isManagementPath('/lens/assistants/demo/chat'), false)
  assert.equal(isManagementPath(null), false)
})

test('preferences store applies isolated theme lifecycles', async (t) => {
  const bootstrapBrowser = createBrowserHarness()
  const restoreBootstrapGlobals = installBrowserGlobals(bootstrapBrowser)
  let vite = null

  try {
    vite = await createServer({
      appType: 'custom',
      logLevel: 'silent',
      root: frontendRoot,
      server: { middlewareMode: true }
    })
    const { usePreferencesStore } = await vite.ssrLoadModule(
      '/src/store/preferences.js'
    )

    const withStore = async (callback) => {
      const browser = createBrowserHarness()
      const restoreGlobals = installBrowserGlobals(browser)
      setActivePinia(createPinia())
      const store = usePreferencesStore()

      try {
        await callback({ browser, store })
      } finally {
        store.$dispose()
        browser.timers.clear()
        restoreGlobals()
      }
    }

    await t.test('load normalizes and applies an invalid saved mode', () => {
      return withStore(({ browser, store }) => {
        browser.localStorage.setItem('userThemeMode', 'invalid')
        browser.mediaQuery.matches = false

        store.loadFromLocalStorage()

        assert.equal(browser.localStorage.getItem('userThemeMode'), 'system')
        assert.equal(store.themeMode, 'system')
        assert.equal(store.resolvedTheme, 'light')
        assert.equal(browser.document.documentElement.dataset.theme, 'light')
        assert.equal(
          browser.document.documentElement.style.colorScheme,
          'light'
        )
      })
    })

    await t.test(
      'citation display defaults on and remembers toggles across stores',
      () => {
        return withStore(({ store }) => {
          assert.equal(store.showCitations, true)
          store.setShowCitations(false)
          assert.equal(store.showCitations, false)
          const restored = usePreferencesStore(createPinia())
          try {
            restored.loadFromLocalStorage()
            assert.equal(restored.showCitations, false)
            restored.setShowCitations(true)
            store.loadFromLocalStorage()
            assert.equal(store.showCitations, true)
            store.setShowCitations(false)
            store.reset()
            assert.equal(store.showCitations, true)
            restored.loadFromLocalStorage()
            assert.equal(restored.showCitations, true)
          } finally {
            restored.$dispose()
          }
        })
      }
    )

    await t.test('load registers one system-only media listener', () => {
      return withStore(({ browser, store }) => {
        store.loadFromLocalStorage()
        store.loadFromLocalStorage()
        assert.equal(browser.addEventListenerCalls, 1)

        browser.mediaQuery.matches = true
        browser.fireMediaChange()
        assert.equal(store.resolvedTheme, 'dark')

        store.setThemeMode('dark')
        browser.mediaQuery.matches = false
        browser.fireMediaChange()
        assert.equal(store.resolvedTheme, 'dark')
      })
    })

    await t.test('mode changes persist and keep only one timer', () => {
      return withStore(({ browser, store }) => {
        store.setThemeMode('scheduled')
        store.setThemeMode('scheduled')

        assert.equal(browser.localStorage.getItem('userThemeMode'), 'scheduled')
        assert.equal(browser.timers.size, 1)

        store.setThemeMode('light')
        assert.equal(browser.timers.size, 0)
        assert.equal(store.resolvedTheme, 'light')
      })
    })

    await t.test(
      'administration override applies light without changing mode',
      () => {
        return withStore(({ browser, store }) => {
          store.setThemeMode('dark')
          store.setThemeOverride('light')

          assert.equal(store.themeMode, 'dark')
          assert.equal(store.themeOverride, 'light')
          assert.equal(store.resolvedTheme, 'light')
          assert.equal(browser.localStorage.getItem('userThemeMode'), 'dark')
          assert.equal(browser.document.documentElement.dataset.theme, 'light')
          assert.equal(
            browser.document.documentElement.style.colorScheme,
            'light'
          )
        })
      }
    )

    await t.test(
      'clearing administration override restores the user theme',
      () => {
        return withStore(({ browser, store }) => {
          store.setThemeMode('dark')
          store.setThemeOverride('light')
          store.setThemeOverride(null)

          assert.equal(store.themeMode, 'dark')
          assert.equal(store.themeOverride, null)
          assert.equal(store.resolvedTheme, 'dark')
          assert.equal(browser.document.documentElement.dataset.theme, 'dark')
        })
      }
    )

    await t.test('scheduled timer uses the exact boundary delay', () => {
      return withStore(({ browser, store }) => {
        const now = new Date(2026, 7, 4, 12, 30)
        const restoreDate = installFixedNow(now)

        try {
          store.setThemeMode('scheduled')
          const expectedDelay =
            getNextThemeBoundary(now).getTime() - now.getTime()
          assert.equal(browser.getOnlyTimer().delay, expectedDelay)
        } finally {
          restoreDate()
        }
      })
    })

    await t.test('scheduled timer applies and schedules again', () => {
      return withStore(({ browser, store }) => {
        const now = new Date(2026, 7, 4, 12, 30)
        const restoreDate = installFixedNow(now)

        try {
          store.setThemeMode('scheduled')
          store.resolvedTheme = 'sentinel'
          browser.runOnlyTimer()

          assert.equal(store.resolvedTheme, 'light')
          assert.equal(browser.timers.size, 1)
        } finally {
          restoreDate()
        }
      })
    })

    await t.test('reset clears scheduling and reapplies system mode', () => {
      return withStore(({ browser, store }) => {
        store.setThemeMode('scheduled')
        browser.mediaQuery.matches = false
        store.reset()

        assert.equal(browser.localStorage.getItem('userThemeMode'), null)
        assert.equal(browser.timers.size, 0)
        assert.equal(store.themeMode, 'system')
        assert.equal(store.resolvedTheme, 'light')
        assert.equal(browser.document.documentElement.dataset.theme, 'light')
        assert.equal(
          browser.document.documentElement.style.colorScheme,
          'light'
        )
      })
    })

    await t.test('applyTheme is safe without window or document', () => {
      return withStore(({ store }) => {
        const windowDescriptor = Object.getOwnPropertyDescriptor(
          globalThis,
          'window'
        )
        const documentDescriptor = Object.getOwnPropertyDescriptor(
          globalThis,
          'document'
        )
        delete globalThis.window
        delete globalThis.document

        try {
          store.themeMode = 'system'
          assert.doesNotThrow(() => {
            store.applyTheme(new Date(2026, 7, 4, 12))
          })
          assert.equal(store.resolvedTheme, 'light')
        } finally {
          Object.defineProperty(globalThis, 'window', windowDescriptor)
          Object.defineProperty(globalThis, 'document', documentDescriptor)
        }
      })
    })

    await t.test('stores own and dispose independent controllers', () => {
      return withStore(({ browser, store: firstStore }) => {
        setActivePinia(createPinia())
        const secondStore = usePreferencesStore()

        try {
          firstStore.loadFromLocalStorage()
          secondStore.loadFromLocalStorage()
          assert.equal(browser.addEventListenerCalls, 2)

          firstStore.setThemeMode('scheduled')
          secondStore.setThemeMode('scheduled')
          assert.equal(browser.timers.size, 2)

          firstStore.setThemeMode('light')
          assert.equal(browser.timers.size, 1)
          assert.equal(secondStore.themeMode, 'scheduled')

          firstStore.setThemeMode('scheduled')
          firstStore.$dispose()
          assert.equal(browser.removeEventListenerCalls, 1)
          assert.equal(browser.timers.size, 1)

          firstStore.resolvedTheme = 'sentinel'
          secondStore.setThemeMode('system')
          browser.mediaQuery.matches = true
          browser.fireMediaChange()
          assert.equal(firstStore.resolvedTheme, 'sentinel')
          assert.equal(secondStore.resolvedTheme, 'dark')
        } finally {
          secondStore.$dispose()
        }

        assert.equal(browser.removeEventListenerCalls, 2)
        assert.equal(browser.timers.size, 0)
      })
    })

    await t.test('theme changes tolerate unavailable storage', () => {
      return withStore(({ store }) => {
        const storageDescriptor = Object.getOwnPropertyDescriptor(
          globalThis,
          'localStorage'
        )
        Object.defineProperty(globalThis, 'localStorage', {
          configurable: true,
          get() {
            throw new Error('storage unavailable')
          }
        })

        try {
          assert.doesNotThrow(() => store.setThemeMode('dark'))
          assert.equal(store.themeMode, 'dark')
          assert.equal(store.resolvedTheme, 'dark')
        } finally {
          Object.defineProperty(globalThis, 'localStorage', storageDescriptor)
        }
      })
    })
  } finally {
    bootstrapBrowser.timers.clear()
    try {
      if (vite) {
        await vite.close()
      }
    } finally {
      restoreBootstrapGlobals()
    }
  }
})

test('loads preferences before mounting the Vue application', () => {
  const loadIndex = mainSource.indexOf(
    'preferencesStore.loadFromLocalStorage()'
  )
  const mountIndex = mainSource.indexOf("app.mount('#app')")

  assert.notEqual(loadIndex, -1)
  assert.notEqual(mountIndex, -1)
  assert.ok(loadIndex < mountIndex)
})

test('defines light and exact Cursor dark semantic tokens', () => {
  assertTokenValues(getCssBlock(':root'), {
    'sl-bg-canvas': '#f9fafb',
    'sl-bg-surface': '#ffffff',
    'sl-bg-raised': '#ffffff',
    'sl-bg-hover': '#f3f4f6',
    'sl-bg-selected': '#e5e7eb',
    'sl-border-default': '#e5e7eb',
    'sl-border-strong': '#d1d5db',
    'sl-text-primary': '#171512',
    'sl-text-secondary': '#4b5563',
    'sl-text-muted': '#6b7280',
    'sl-text-subtle': '#9ca3af',
    'sl-neutral-text-400-rgb': '156 163 175',
    'sl-neutral-text-500-rgb': '107 114 128',
    'sl-neutral-text-600-rgb': '75 85 99',
    'sl-neutral-text-700-rgb': '55 65 81',
    'sl-neutral-text-800-rgb': '31 41 55',
    'sl-neutral-text-900-rgb': '17 24 39'
  })
  assertTokenValues(getCssBlock(":root[data-theme='dark']"), {
    'sl-bg-canvas': '#1e1e1e',
    'sl-bg-surface': '#252526',
    'sl-bg-raised': '#2a2d2e',
    'sl-bg-hover': '#2a2d2e',
    'sl-bg-selected': '#333333',
    'sl-border-default': '#3c3c3c',
    'sl-border-strong': '#454545',
    'sl-text-primary': '#cccccc',
    'sl-text-secondary': '#b4b4b4',
    'sl-text-muted': '#858585',
    'sl-text-subtle': '#6e6e6e',
    'sl-neutral-text-400-rgb': '133 133 133',
    'sl-neutral-text-500-rgb': '133 133 133',
    'sl-neutral-text-600-rgb': '180 180 180',
    'sl-neutral-text-700-rgb': '180 180 180',
    'sl-neutral-text-800-rgb': '204 204 204',
    'sl-neutral-text-900-rgb': '204 204 204'
  })
})

test('global controls and light datepicker read color variables', () => {
  const baseLayer = cssSource.slice(
    cssSource.indexOf('@layer base'),
    cssSource.indexOf('@layer components')
  )
  const datepicker = getCssBlock('.dp__theme_light')
  const fixedColors = /(?:#ffffff|#171512|#ded7ca|#f5f2eb|#736b5d)/

  assert.match(baseLayer, /background-color:\s*var\(--sl-form-bg\)/)
  assert.match(baseLayer, /color:\s*var\(--sl-form-text\)/)
  assert.match(baseLayer, /border:\s*1px solid var\(--sl-form-border\)/)
  assert.doesNotMatch(baseLayer, fixedColors)
  assert.match(datepicker, /var\(--sl-form-border\)/)
  assert.match(datepicker, /var\(--sl-text-primary\)/)
  assert.doesNotMatch(datepicker, /#[0-9a-f]{6}/i)
})

test('Tailwind semantic roles use opacity-compatible variables', () => {
  assert.match(
    tailwindSource,
    /DEFAULT:\s*'rgb\(var\(--sl-bg-surface-rgb\) \/ <alpha-value>\)'/
  )
  assert.match(
    tailwindSource,
    /raised:\s*'rgb\(var\(--sl-bg-raised-rgb\) \/ <alpha-value>\)'/
  )
  assert.match(
    tailwindSource,
    /strong:\s*'rgb\(var\(--sl-border-strong-rgb\) \/ <alpha-value>\)'/
  )
  assert.match(tailwindSource, /ink:\s*\{[\s\S]*?950:\s*'#030712'/)
  assert.match(
    tailwindSource,
    /textColor:\s*\{[\s\S]*?theme:\s*\{[\s\S]*?DEFAULT:\s*'rgb\(var\(--sl-text-primary-rgb\) \/ <alpha-value>\)'/
  )
  for (const palette of ['gray', 'ink']) {
    assert.match(
      tailwindSource,
      new RegExp(
        `${palette}:\\s*\\{[\\s\\S]*?900:\\s*` +
          `'rgb\\(var\\(--sl-neutral-text-900-rgb\\) \\/ ` +
          `<alpha-value>\\)'`
      )
    )
  }
  assert.match(
    tailwindSource,
    /backgroundColor:\s*\{[\s\S]*?white:\s*'rgb\(var\(--sl-bg-surface-rgb\) \/ <alpha-value>\)'/
  )
})

test('document root uses semantic canvas and theme text', () => {
  const baseLayer = cssSource.slice(
    cssSource.indexOf('@layer base'),
    cssSource.indexOf('@layer components')
  )
  assert.match(baseLayer, /body\s*\{[\s\S]*?\btext-theme\b/)
  assert.doesNotMatch(baseLayer, /body\s*\{[\s\S]*?\btext-ink-900\b/)
  assert.match(appSource, /\bbg-surface-sunken\b/)
  assert.doesNotMatch(appSource, /\bbg-gray-50\b/)
})

const settingsSource = readFileSync(
  new URL('../src/components/settings/UserSettingsModal.vue', import.meta.url),
  'utf8'
)
const en = JSON.parse(
  readFileSync(new URL('../src/locales/en.json', import.meta.url), 'utf8')
)
const zh = JSON.parse(
  readFileSync(new URL('../src/locales/zh-CN.json', import.meta.url), 'utf8')
)
test('settings expose four mutually exclusive appearance modes', () => {
  for (const mode of ['light', 'dark', 'system', 'scheduled']) {
    assert.match(settingsSource, new RegExp(`value: '${mode}'`))
  }
  assert.match(settingsSource, /setThemeMode/)
})

test('settings modal preserves light colors and softens dark dividers', () => {
  assert.match(settingsSource, /bg-surface-sunken/)
  assert.match(
    settingsSource,
    /settings-nav[\s\S]*?border-b border-line[\s\S]*?sm:border-b-0[\s\S]*?sm:border-r/
  )
  assert.match(settingsSource, /border-b border-line/)
  assert.match(settingsSource, /border-t border-line/)
  assert.match(settingsSource, /hover:bg-red-50/)
  assert.match(
    settingsSource,
    /:global\(:root\[data-theme='dark'\] \.settings-header\)/
  )
  assert.match(settingsSource, /border-color: transparent/)
  assert.match(settingsSource, /\.settings-logout:hover/)
  assert.match(settingsSource, /background: var\(--sl-bg-hover\)/)
})

test('appearance choices use responsive cards and semantic hover colors', () => {
  assert.match(settingsSource, /sm:grid-cols-2/)
  assert.match(settingsSource, /bg-surface-selected/)
  assert.match(settingsSource, /hover:border-line-strong/)
  assert.match(settingsSource, /hover:bg-surface-hover/)
})

test('appearance labels exist in both locales', () => {
  for (const locale of [en, zh]) {
    assert.ok(locale.settings.modal.appearance)
    assert.ok(locale.settings.modal.themeScheduled)
    assert.ok(locale.settings.modal.themeScheduleDescription)
    assert.ok(locale.settings.modal.themeLightDesc)
    assert.ok(locale.settings.modal.themeDarkDesc)
    assert.ok(locale.settings.modal.themeSystemDesc)
    assert.ok(locale.settings.modal.themeScheduledDesc)
    assert.ok(locale.settings.modal.themeAdminNote)
  }
})

const logoSource = readFileSync(
  new URL('../src/components/layout/BrandLogo.vue', import.meta.url),
  'utf8'
)
const chatLogoSource = readFileSync(
  new URL('../src/pages/lens/Chat.vue', import.meta.url),
  'utf8'
)
const emptyStateSource = readFileSync(
  new URL('../src/components/lens/AssistantEmptyState.vue', import.meta.url),
  'utf8'
)
const activityStackSource = readFileSync(
  new URL(
    '../src/components/ui/ActivityNotificationStack.vue',
    import.meta.url
  ),
  'utf8'
)
const adminSidebarSource = readFileSync(
  new URL('../src/admin/layout/AdminSidebar.vue', import.meta.url),
  'utf8'
)
const userMenuSources = [
  '../src/components/layout/SidebarQuickMenu.vue',
  '../src/components/lens/UserDock.vue'
].map((relativePath) =>
  readFileSync(new URL(relativePath, import.meta.url), 'utf8')
)
const appSidebarSource = readFileSync(
  new URL('../src/components/layout/AppSidebar.vue', import.meta.url),
  'utf8'
)
const appHeaderSource = readFileSync(
  new URL('../src/components/layout/AppHeader.vue', import.meta.url),
  'utf8'
)

test('user theme switches transparent logo colors without geometry hacks', () => {
  assert.match(logoSource, /logo_with_text_transparent\.png/)
  assert.match(logoSource, /logo_transparent\.png/)
  assert.match(logoSource, /logo_with_text_dark_transparent\.png/)
  assert.match(logoSource, /logo_dark_transparent\.png/)
  assert.doesNotMatch(logoSource, /brightness-0 invert/)
  assert.doesNotMatch(logoSource, /mix-blend/)
  assert.doesNotMatch(logoSource, /object-cover/)
})

test('dark transparent logo pairs preserve the pixel contract', () => {
  const pairs = [
    {
      dark: new URL(
        '../public/brand/logo_dark_transparent.png',
        import.meta.url
      ),
      light: new URL('../public/brand/logo_transparent.png', import.meta.url),
      name: 'mark'
    },
    {
      dark: new URL(
        '../public/brand/logo_with_text_dark_transparent.png',
        import.meta.url
      ),
      light: new URL(
        '../public/brand/logo_with_text_transparent.png',
        import.meta.url
      ),
      name: 'wordmark'
    }
  ]

  for (const pair of pairs) {
    const result = inspectThemedLogoPair(pair.light, pair.dark)
    const prefix = `${pair.name} logo`

    assert.equal(result.darkWidth, result.lightWidth, `${prefix} width`)
    assert.equal(result.darkHeight, result.lightHeight, `${prefix} height`)
    assert.equal(result.alphaMismatchCount, 0, `${prefix} alpha plane`)
    assert.ok(result.accentPixelCount > 0, `${prefix} accent pixels`)
    assert.equal(result.accentMismatchCount, 0, `${prefix} accent RGBA`)
    assert.ok(result.visibleBodyPixelCount > 0, `${prefix} visible body pixels`)
    assert.equal(
      result.visibleBodyMismatchCount,
      0,
      `${prefix} visible dark body RGB`
    )
  }
})

test('user logo consumers avoid dark assets and blend geometry hacks', () => {
  for (const source of [
    chatLogoSource,
    emptyStateSource,
    activityStackSource
  ]) {
    assert.doesNotMatch(source, /logo_with_text_dark/)
    assert.doesNotMatch(source, /logo_dark/)
    assert.doesNotMatch(source, /mix-blend/)
  }
  assert.match(chatLogoSource, /BrandLogo[\s\S]*variant="mark"/)
  assert.match(emptyStateSource, /BrandLogo[\s\S]*variant="mark"/)
  assert.match(activityStackSource, /BrandLogo[\s\S]*variant="mark"/)
})

test('chat composer gradient uses semantic surface opacity tokens', () => {
  const composerWrap = chatLogoSource.match(/\.composer-wrap\s*\{([^}]*)\}/)

  assert.ok(composerWrap, 'Missing .composer-wrap CSS block')
  assert.match(composerWrap[1], /rgb\(var\(--sl-bg-surface-rgb\)\s*\/\s*98%\)/)
  assert.match(composerWrap[1], /rgb\(var\(--sl-bg-surface-rgb\)\s*\/\s*78%\)/)
  assert.doesNotMatch(composerWrap[1], /rgba\(255,\s*255,\s*255/)
})

test('administration keeps its existing explicit logo treatment', () => {
  assert.match(adminSidebarSource, /tone="dark"/)
  assert.match(adminSidebarSource, /\[&_img\]:!w-48/)
  assert.match(adminSidebarSource, /mix-blend-screen/)
})

test('user menus preserve light interactions and override only dark colors', () => {
  for (const source of userMenuSources) {
    assert.match(source, /bg-primary-(?:50|100)\b/)
    assert.match(source, /border-[bt] border-line/)
    assert.match(source, /:global\(:root\[data-theme='dark'\] \./)
    assert.match(source, /background: var\(--sl-bg-hover\)/)
  }
})

test('scoped dark selectors keep their component targets', () => {
  const sources = [
    settingsSource,
    appSidebarSource,
    appHeaderSource,
    chatLogoSource,
    ...userMenuSources
  ]
  const truncatedSelector = /:global\(:root\[data-theme='dark'\]\)\s+\./

  for (const source of sources) {
    assert.doesNotMatch(source, truncatedSelector)
  }
})

test('user shells keep light dividers and remove them only in dark mode', () => {
  assert.match(appSidebarSource, /border-r border-line/)
  assert.match(appSidebarSource, /border-b border-line/)
  assert.match(appHeaderSource, /border-b border-line/)
  assert.match(appSidebarSource, /:root\[data-theme='dark'\]/)
  assert.match(appHeaderSource, /:root\[data-theme='dark'\]/)

  const chatSidebar = chatLogoSource.match(/\.sidebar\s*\{([^}]*)\}/)
  const chatHeader = chatLogoSource.match(/\.chat-header\s*\{([^}]*)\}/)
  const sidebarFooter = chatLogoSource.match(/\.sidebar-footer\s*\{([^}]*)\}/)

  assert.ok(chatSidebar)
  assert.ok(chatHeader)
  assert.ok(sidebarFooter)
  assert.match(chatSidebar[1], /border-r/)
  assert.match(chatSidebar[1], /--sl-bg-surface/)
  assert.match(chatHeader[1], /border-b/)
  assert.match(sidebarFooter[1], /border-t/)
  assert.match(chatLogoSource, /box-shadow: inset 1px 0 0 #e5e7eb/)
  assert.match(chatLogoSource, /:root\[data-theme='dark'\]/)
  assert.match(chatLogoSource, /background: var\(--sl-bg-canvas\)/)
  assert.match(chatLogoSource, /box-shadow: none/)
})

const sharedThemeFiles = [
  '../src/pages/Auth.vue',
  '../src/components/ui/BaseModal.vue',
  '../src/components/ui/PaginationBar.vue',
  '../src/components/ui/BaseLoading.vue',
  '../src/components/ui/LanguageSwitcher.vue',
  '../src/components/layout/AppLayout.vue',
  '../src/components/layout/AppSidebar.vue',
  '../src/components/layout/AppHeader.vue',
  '../src/components/lens/PublicLensHeader.vue',
  '../src/components/layout/SidebarQuickMenu.vue',
  '../src/components/auth/LoginModal.vue',
  '../src/components/ui/Toast.vue',
  '../src/components/ui/BaseDrawer.vue',
  '../src/components/ui/BaseInput.vue',
  '../src/components/ui/ErrorBoundary.vue',
  '../src/components/ui/ActivityNotificationStack.vue',
  '../src/components/lens/UserDock.vue',
  '../src/components/lens/AssistantSwitcher.vue',
  '../src/components/ui/BaseSelect.vue',
  '../src/components/ui/BaseButton.vue',
  '../src/components/ui/StatusBadge.vue',
  '../src/components/ui/RowActionMenu.vue',
  '../src/components/ui/BaseCard.vue',
  '../src/components/ui/MarkdownRenderer.vue'
]

test('shared theme surfaces use semantic classes', () => {
  for (const relativePath of sharedThemeFiles) {
    const source = readFileSync(new URL(relativePath, import.meta.url), 'utf8')
    assert.doesNotMatch(
      source,
      /\bbg-white\b/,
      `${relativePath} still uses bg-white`
    )
    assert.doesNotMatch(
      source,
      /\btext-gray-(?:400|500|600|700|900)\b/,
      `${relativePath} still uses hard-coded gray text`
    )
    assert.doesNotMatch(
      source,
      /\bborder-gray-(?:100|200|300)\b/,
      `${relativePath} still uses hard-coded gray borders`
    )
  }
})

test('chat neutral surfaces use theme tokens', () => {
  const fixedNeutralDeclaration = new RegExp(
    String.raw`(?:background(?:-color)?|border(?:-color)?):\s*` +
      String.raw`#(?:ffffff|f9fafb|f8fafc|f3f4f6|e5e7eb|e2e8f0|` +
      String.raw`d1d5db)\b|color:\s*` +
      String.raw`#(?:111827|374151|4b5563|475569|64748b|6b7280|` +
      String.raw`94a3b8|9ca3af)\b`,
    'i'
  )

  assert.doesNotMatch(chatLogoSource, fixedNeutralDeclaration)
})
