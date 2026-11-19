// Shared metrics used by the globe engine.
export function metricValue(stat,metric) {
  if (!stat) return null;
  const v=stat[metric];
  return Number.isFinite(v)?v:null;
}
export function escapeHtml(s) {
  return String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}
