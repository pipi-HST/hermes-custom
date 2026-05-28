export interface Capture {
  id: string
  title: string
  source: string
  source_url: string
  source_type: string
  file_path: string
  raw_path: string
  summary: string
  tags: string[]
  word_count: number
  status: string
  created_at: string
  updated_at: string
  review_count: number
}

export async function fetchCaptures(params?: {
  tag?: string
  search?: string
  folder?: string
  limit?: number
}): Promise<Capture[]> {
  const qs = new URLSearchParams()
  if (params?.tag) qs.set('tag', params.tag)
  if (params?.search) qs.set('search', params.search)
  if (params?.folder) qs.set('folder', params.folder)
  if (params?.limit) qs.set('limit', String(params.limit))

  const res = await fetch(`http://localhost:8000/api/captures?${qs}`)
  if (!res.ok) return []
  const data = await res.json()
  return Array.isArray(data.items) ? data.items : []
}
