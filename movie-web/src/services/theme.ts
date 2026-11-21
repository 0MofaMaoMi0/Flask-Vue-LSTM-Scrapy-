import { computed, ref } from 'vue'

export type SiteTheme = 'light' | 'dark'

export const THEME_STORAGE_KEY = 'mi2_site_theme'

function readStoredTheme(): SiteTheme {
  if (typeof localStorage === 'undefined') return 'dark'
  try {
    const raw = localStorage.getItem(THEME_STORAGE_KEY)
    return raw === 'light' ? 'light' : 'dark'
  } catch {
    return 'dark'
  }
}

export function applySiteTheme(theme: SiteTheme): void {
  if (typeof document === 'undefined') return
  const root = document.documentElement
  root.setAttribute('data-theme', theme)
  root.style.colorScheme = theme
}

export const siteTheme = ref<SiteTheme>(readStoredTheme())
export const isLightTheme = computed(() => siteTheme.value === 'light')

applySiteTheme(siteTheme.value)

export function setSiteTheme(theme: SiteTheme): void {
  const normalized: SiteTheme = theme === 'light' ? 'light' : 'dark'
  siteTheme.value = normalized
  applySiteTheme(normalized)
  if (typeof localStorage !== 'undefined') {
    try {
      localStorage.setItem(THEME_STORAGE_KEY, normalized)
    } catch {}
  }
}
