import {computed, ref, watch, onBeforeUnmount} from 'vue'

/** Balance existing cards into full rows without changing API pagination. */
export function usePosterGrid(itemCount: () => number) {
  const gridRef = ref<HTMLElement>()
  const width = ref(0), gap = ref(20)
  let observer: ResizeObserver | undefined
  watch(gridRef, element => {
    observer?.disconnect()
    if (!element) return
    const measure = () => {
      width.value = element.clientWidth
      gap.value = parseFloat(getComputedStyle(element).columnGap) || 0
    }
    observer = new ResizeObserver(measure)
    observer.observe(element)
    measure()
  }, {flush: 'post'})
  onBeforeUnmount(() => observer?.disconnect())
  const gridStyle = computed(() => {
    const count = itemCount(), w = width.value, g = gap.value
    if (!count || !w) return {}
    const minimum = w < 540 ? 130 : 180
    const maxColumns = Math.max(1, Math.min(count, Math.floor((w + g) / (minimum + g))))
    const minColumns = Math.min(maxColumns, Math.max(1, Math.ceil((w + g) / (280 + g))))
    let best = maxColumns, score = Infinity
    for (let columns = minColumns; columns <= maxColumns; columns++) {
      const empty = (columns - count % columns) % columns
      // Prefer complete rows; among equally complete options use denser cards.
      const next = empty * 100 + maxColumns - columns
      if (next < score) { best = columns; score = next }
    }
    return {gridTemplateColumns: `repeat(${best}, minmax(0, 1fr))`}
  })
  return {gridRef, gridStyle}
}
