import { watch, type Ref } from 'vue'
import { aiWorkspace } from './aiWorkspace'

export interface OrbBindingOptions {
  /** Max pupil travel in pixels from center. */
  range?: number
  /** Whether this orb should blink periodically. */
  blink?: boolean
  /** Optional reactive busy flag for calm thinking gaze. */
  busy?: Ref<boolean>
}

interface RegisteredOrb {
  getEl: () => HTMLElement | null | undefined
  range: number
  blink: boolean
  busy?: Ref<boolean>
}

const orbs = new Set<RegisteredOrb>()
let reducedQuery: MediaQueryList | null = null
let pointerX = 0
let pointerY = 0
let hasPointer = false
let rafId = 0
let blinkTimer: ReturnType<typeof setTimeout> | null = null
let blinkReleaseTimer: ReturnType<typeof setTimeout> | null = null
let listenersAttached = false

export function isMotionReduced(): boolean {
  if (aiWorkspace.reduced) return true
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return false
  if (!reducedQuery) reducedQuery = window.matchMedia('(prefers-reduced-motion: reduce)')
  return reducedQuery.matches
}

function resetOrbVars(el: HTMLElement | null | undefined): void {
  if (!el) return
  el.style.setProperty('--ex', '0px')
  el.style.setProperty('--ey', '0px')
  el.style.setProperty('--blink', '1')
}

function resetAllOrbs(): void {
  for (const entry of orbs) resetOrbVars(entry.getEl())
}

function applyGaze(): void {
  rafId = 0
  if (isMotionReduced()) {
    resetAllOrbs()
    return
  }
  for (const entry of orbs) {
    const el = entry.getEl()
    if (!el) continue
    if (entry.busy?.value) {
      el.style.setProperty('--ex', '0px')
      el.style.setProperty('--ey', `${(-entry.range * 0.35).toFixed(2)}px`)
      continue
    }
    if (!hasPointer) {
      el.style.setProperty('--ex', '0px')
      el.style.setProperty('--ey', '0px')
      continue
    }
    const rect = el.getBoundingClientRect()
    if (!rect.width || !rect.height) continue
    const cx = rect.left + rect.width * 0.5
    const cy = rect.top + rect.height * 0.5
    const dx = pointerX - cx
    const dy = pointerY - cy
    const dist = Math.hypot(dx, dy)
    if (dist < 1) {
      el.style.setProperty('--ex', '0px')
      el.style.setProperty('--ey', '0px')
      continue
    }
    const falloff = 1 - Math.exp(-dist / 135)
    const tx = (dx / dist) * entry.range * falloff
    const ty = (dy / dist) * entry.range * 0.72 * falloff
    el.style.setProperty('--ex', `${tx.toFixed(2)}px`)
    el.style.setProperty('--ey', `${ty.toFixed(2)}px`)
  }
}

function scheduleGaze(): void {
  if (rafId || typeof requestAnimationFrame === 'undefined') return
  rafId = requestAnimationFrame(applyGaze)
}

function clearBlinkTimers(): void {
  if (blinkTimer !== null) {
    clearTimeout(blinkTimer)
    blinkTimer = null
  }
  if (blinkReleaseTimer !== null) {
    clearTimeout(blinkReleaseTimer)
    blinkReleaseTimer = null
  }
}

function setBlinkScale(value: string): void {
  for (const entry of orbs) {
    if (!entry.blink || entry.busy?.value) continue
    const el = entry.getEl()
    if (el) el.style.setProperty('--blink', value)
  }
}

function scheduleNextBlink(): void {
  clearBlinkTimers()
  if (typeof window === 'undefined' || !orbs.size || isMotionReduced() || document.hidden) return
  const delay = 2600 + Math.random() * 2800
  blinkTimer = setTimeout(() => {
    blinkTimer = null
    if (isMotionReduced() || document.hidden || !orbs.size) return
    setBlinkScale('0.08')
    const doubleBlink = Math.random() < 0.2
    blinkReleaseTimer = setTimeout(() => {
      setBlinkScale('1')
      if (doubleBlink) {
        blinkReleaseTimer = setTimeout(() => {
          if (isMotionReduced() || document.hidden || !orbs.size) return
          setBlinkScale('0.08')
          blinkReleaseTimer = setTimeout(() => {
            setBlinkScale('1')
            scheduleNextBlink()
          }, 105)
        }, 95)
      } else {
        scheduleNextBlink()
      }
    }, 115)
  }, delay)
}

function onPointerMove(e: PointerEvent): void {
  pointerX = e.clientX
  pointerY = e.clientY
  hasPointer = true
  scheduleGaze()
}

function onPointerLeave(): void {
  hasPointer = false
  scheduleGaze()
}

function onVisibilityChange(): void {
  if (document.hidden) {
    clearBlinkTimers()
    resetAllOrbs()
  } else if (orbs.size && !isMotionReduced()) {
    scheduleNextBlink()
  }
}

function onReducedChange(): void {
  if (isMotionReduced()) {
    clearBlinkTimers()
    resetAllOrbs()
  } else if (orbs.size) {
    scheduleGaze()
    scheduleNextBlink()
  }
}

function attachListeners(): void {
  if (listenersAttached || typeof window === 'undefined') return
  listenersAttached = true
  if (typeof window.matchMedia === 'function') {
    reducedQuery = window.matchMedia('(prefers-reduced-motion: reduce)')
    reducedQuery.addEventListener?.('change', onReducedChange)
  }
  window.addEventListener('pointermove', onPointerMove, { passive: true })
  document.addEventListener('mouseleave', onPointerLeave)
  window.addEventListener('blur', onPointerLeave)
  document.addEventListener('visibilitychange', onVisibilityChange)
  scheduleNextBlink()
}

function detachListeners(): void {
  if (!listenersAttached || typeof window === 'undefined') return
  listenersAttached = false
  clearBlinkTimers()
  if (rafId) {
    cancelAnimationFrame(rafId)
    rafId = 0
  }
  reducedQuery?.removeEventListener?.('change', onReducedChange)
  window.removeEventListener('pointermove', onPointerMove)
  document.removeEventListener('mouseleave', onPointerLeave)
  window.removeEventListener('blur', onPointerLeave)
  document.removeEventListener('visibilitychange', onVisibilityChange)
}

watch(
  () => aiWorkspace.reduced,
  () => onReducedChange(),
)

/**
 * Registers an AI orb host so its `--ex`, `--ey`, and `--blink` CSS variables
 * receive gentle pointer gaze tracking and natural idle blinking.
 */
export function registerOrbMotion(
  getEl: () => HTMLElement | null | undefined,
  options: OrbBindingOptions = {},
): () => void {
  const entry: RegisteredOrb = {
    getEl,
    range: options.range ?? 2.6,
    blink: options.blink ?? true,
    busy: options.busy,
  }
  orbs.add(entry)
  attachListeners()
  if (options.busy) {
    const stopWatch = watch(options.busy, () => scheduleGaze())
    return () => {
      stopWatch()
      orbs.delete(entry)
      resetOrbVars(entry.getEl())
      if (!orbs.size) detachListeners()
    }
  }
  return () => {
    orbs.delete(entry)
    resetOrbVars(entry.getEl())
    if (!orbs.size) detachListeners()
  }
}
