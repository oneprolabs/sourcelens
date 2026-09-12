import { SUPPORTED_UI_LANGUAGES } from '@/i18n'

const LANGUAGE_FLAGS = {
  en: '🇺🇸',
  'zh-CN': '🇨🇳',
  es: '🇪🇸'
}

const NATIVE_LANGUAGE_NAMES = {
  en: 'English',
  'zh-CN': '简体中文',
  es: 'Español'
}

export function getUiLanguageOptions() {
  return SUPPORTED_UI_LANGUAGES.map((language) => ({
    value: language,
    label: NATIVE_LANGUAGE_NAMES[language] || language,
    flag: LANGUAGE_FLAGS[language] || language.toUpperCase()
  }))
}

export function getUiLanguageLabel(language, t) {
  return t(`settings.preferences.languages.${language}`)
}
