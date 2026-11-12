let csrf = ''
async function token() {
  if (csrf) return csrf
  const r = await fetch('/api/v1/auth/csrf', { credentials: 'same-origin' })
  const j = await r.json()
  csrf = j?.data?.csrf_token || j?.data?.token || ''
  return csrf
}

export async function trackImpressions(payload: {
  channel: string
  period?: string | null
  boardVersion?: string
  context: string
  items: any[]
}): Promise<string> {
  if (!payload.items?.length) return ''
  const requestId = crypto.randomUUID()
  try {
    const t = await token()
    await fetch('/api/catalog/recommendation-impressions', {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': t },
      body: JSON.stringify({
        request_id: requestId,
        channel: payload.channel,
        period: payload.period || null,
        board_version: payload.boardVersion || 'boards-v1',
        context: payload.context,
        items: payload.items.map((m, i) => ({
          position: i + 1,
          movie_id: m.movie_id ?? null,
          tmdb_id: m.movie_id ? null : (m.tmdb_id ?? null),
        })),
      }),
    })
  } catch { /* feedback must never block rendering */ }
  return requestId
}

export async function trackClick(requestId: string, position: number) {
  if (!requestId || !position) return
  try {
    const t = await token()
    await fetch('/api/catalog/recommendation-clicks', {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': t },
      body: JSON.stringify({ request_id: requestId, position }),
    })
  } catch { /* ignore */ }
}
