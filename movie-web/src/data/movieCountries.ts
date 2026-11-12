// Fallback only: the live source is GET /api/catalog/production-countries
// (parsed from the stored TMDB payload). This static map keeps the globe usable
// when that endpoint is unavailable (e.g. offline dev).
export const MOVIE_COUNTRIES: Record<number, string[]> = {
  1: ['USA'],
  2: ['USA', 'GBR'],
  1002: ['NZL', 'USA'],
  1003: ['JPN'],
  1004: ['USA', 'GBR'],
  1005: ['USA'],
  1006: ['NZL', 'USA'],
  1007: ['USA'],
  1008: ['USA'],
  1009: ['NZL', 'USA'],
  1010: ['USA'],
  1011: ['USA'],
  1012: ['KOR'],
  1013: ['JPN'],
  1014: ['USA'],
  1015: ['USA', 'DEU'],
  1016: ['JPN'],
  1017: ['FRA', 'DEU'],
  1018: ['USA'],
  1019: ['USA'],
  1020: ['USA', 'GBR'],
  1021: ['USA', 'GBR']
}
