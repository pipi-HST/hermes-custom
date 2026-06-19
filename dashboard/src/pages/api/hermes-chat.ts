import type { NextApiRequest, NextApiResponse } from 'next'
import { existsSync, readFileSync } from 'fs'
import { join } from 'path'

export const config = {
  api: {
    bodyParser: {
      sizeLimit: '10mb',
    },
    responseLimit: false,
  },
}

const DEFAULT_GATEWAY_URL = 'http://127.0.0.1:8642/v1/chat/completions'
let hermesEnvCache: Record<string, string> | null = null

function gatewayUrl(): string {
  return process.env.HERMES_GATEWAY_URL || hermesEnv().HERMES_GATEWAY_URL || DEFAULT_GATEWAY_URL
}

function gatewayApiKey(): string {
  const env = hermesEnv()
  return (
    process.env.HERMES_GATEWAY_API_KEY ||
    process.env.API_SERVER_KEY ||
    env.HERMES_GATEWAY_API_KEY ||
    env.API_SERVER_KEY ||
    ''
  )
}

function hermesEnv(): Record<string, string> {
  if (hermesEnvCache) return hermesEnvCache
  const envPath = join(process.env.HOME || '', '.hermes', '.env')
  if (!envPath || !existsSync(envPath)) {
    hermesEnvCache = {}
    return hermesEnvCache
  }

  hermesEnvCache = readFileSync(envPath, 'utf-8')
    .split(/\r?\n/)
    .reduce<Record<string, string>>((acc, line) => {
      const trimmed = line.trim()
      if (!trimmed || trimmed.startsWith('#')) return acc
      const eq = trimmed.indexOf('=')
      if (eq <= 0) return acc
      const key = trimmed.slice(0, eq).trim()
      const value = trimmed.slice(eq + 1).trim().replace(/^['"]|['"]$/g, '')
      if (key) acc[key] = value
      return acc
    }, {})
  return hermesEnvCache
}

function sessionIdFromBody(body: unknown): string {
  if (!body || typeof body !== 'object') return ''
  const raw = (body as { session_id?: unknown }).session_id
  return typeof raw === 'string' ? raw.trim() : ''
}

export default async function handler(req: NextApiRequest, res: NextApiResponse) {
  if (req.method !== 'POST') {
    res.setHeader('Allow', 'POST')
    res.status(405).json({ error: 'Method not allowed' })
    return
  }

  const body = req.body && typeof req.body === 'object' ? req.body : {}
  const apiKey = gatewayApiKey()
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
  }

  if (apiKey) {
    headers.Authorization = `Bearer ${apiKey}`
    const sessionId = sessionIdFromBody(body)
    if (sessionId) {
      headers['X-Hermes-Session-Id'] = sessionId
    }
  }

  let upstream: Response
  try {
    upstream = await fetch(gatewayUrl(), {
      method: 'POST',
      headers,
      body: JSON.stringify({
        model: (body as { model?: unknown }).model || 'hermes-agent',
        messages: Array.isArray((body as { messages?: unknown }).messages)
          ? (body as { messages: unknown[] }).messages
          : [],
        stream: (body as { stream?: unknown }).stream !== false,
      }),
    })
  } catch (error) {
    res.status(502).json({
      error: error instanceof Error ? error.message : 'Hermes gateway is unavailable',
    })
    return
  }

  if (!upstream.ok) {
    const text = await upstream.text()
    res.status(upstream.status).json({
      error: text || `Hermes gateway returned ${upstream.status}`,
    })
    return
  }

  if (!upstream.body) {
    res.status(502).json({ error: 'Hermes gateway returned an empty response body' })
    return
  }

  res.writeHead(200, {
    'Content-Type': upstream.headers.get('content-type') || 'text/event-stream',
    'Cache-Control': 'no-cache, no-transform',
    Connection: 'keep-alive',
  })

  const reader = upstream.body.getReader()
  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      if (value) {
        res.write(Buffer.from(value))
      }
    }
  } finally {
    res.end()
  }
}
