const API_BASE = import.meta.env.VITE_API_BASE_URL ?? '/api'

const CREDS_KEY = 'aws_rescue_creds'

export function getStoredCreds() {
  try {
    const raw = localStorage.getItem(CREDS_KEY)
    return raw ? JSON.parse(raw) : null
  } catch {
    return null
  }
}

export function saveStoredCreds(creds) {
  localStorage.setItem(CREDS_KEY, JSON.stringify(creds))
}

export function clearStoredCreds() {
  localStorage.removeItem(CREDS_KEY)
}

function credHeaders() {
  const creds = getStoredCreds()
  if (!creds) return {}
  return {
    'X-AWS-Access-Key-Id': creds.accessKeyId || '',
    'X-AWS-Secret-Access-Key': creds.secretAccessKey || '',
    'X-AWS-Region': creds.region || '',
    'X-Project-Id': creds.projectId || '',
  }
}

function buildUrl(path, params = {}) {
  const url = new URL(`${API_BASE}${path}`, window.location.origin)
  Object.entries(params).forEach(([key, value]) => {
    if (value === undefined || value === null || value === '') {
      return
    }
    url.searchParams.set(key, String(value))
  })
  return `${url.pathname}${url.search}`
}

async function parseResponse(response) {
  const contentType = response.headers.get('content-type') || ''
  if (contentType.includes('application/json')) {
    return response.json()
  }
  return response.text()
}

async function request(path, options = {}) {
  const response = await fetch(`${API_BASE}${path}`, {
    headers: {
      'Content-Type': 'application/json',
      ...credHeaders(),
      ...(options.headers || {}),
    },
    ...options,
  })

  const body = await parseResponse(response)
  if (!response.ok) {
    const detail = typeof body === 'object' && body?.detail ? body.detail : `Request failed (${response.status})`
    throw new Error(detail)
  }
  return body
}

export const api = {
  getConfig() {
    return request('/config')
  },

  getOverview() {
    return request('/overview')
  },

  getObjects(search = '') {
    const url = buildUrl('/objects', { search })
    return request(url.replace(API_BASE, ''))
  },

  getObjectHistory(objectKey, limit = 20) {
    const encoded = objectKey
      .split('/')
      .map((part) => encodeURIComponent(part))
      .join('/')
    const url = buildUrl(`/objects/${encoded}/history`, { limit })
    return request(url.replace(API_BASE, ''))
  },

  getLogs({ status = 'ALL', fromDate = '', keyQuery = '', limit = 1000 } = {}) {
    const url = buildUrl('/logs', {
      status,
      from_date: fromDate,
      key_query: keyQuery,
      limit,
    })
    return request(url.replace(API_BASE, ''))
  },

  async downloadLogsCsv({ status = 'ALL', fromDate = '', keyQuery = '', limit = 5000 } = {}) {
    const params = new URLSearchParams({ status, limit: String(limit) })
    if (fromDate) params.set('from_date', fromDate)
    if (keyQuery) params.set('key_query', keyQuery)

    const response = await fetch(`${API_BASE}/logs.csv?${params}`, {
      headers: credHeaders(),
    })
    if (!response.ok) throw new Error(`CSV download failed (${response.status})`)
    return response.blob()
  },

  getOutageState() {
    return request('/outage-state')
  },

  triggerFullSync() {
    return request('/actions/full-sync', { method: 'POST' })
  },

  runHealthCheck() {
    return request('/actions/health-check', { method: 'POST' })
  },

  startOutage() {
    return request('/actions/outage/start', { method: 'POST' })
  },

  endOutage() {
    return request('/actions/outage/end', { method: 'POST' })
  },

  seedData() {
    return request('/actions/seed', { method: 'POST' })
  },

  getInfraStatus() {
    return request('/infra-status')
  },

  provision() {
    return request('/actions/provision', { method: 'POST' })
  },

  async uploadFile(file, prefix = 'uploads') {
    const formData = new FormData()
    formData.append('file', file)
    formData.append('prefix', prefix ?? '')

    const response = await fetch(`${API_BASE}/actions/upload-file`, {
      method: 'POST',
      headers: credHeaders(),
      body: formData,
    })

    const body = await parseResponse(response)
    if (!response.ok) {
      const detail =
        typeof body === 'object' && body?.detail ? body.detail : `Upload failed (${response.status})`
      throw new Error(detail)
    }
    return body
  },
}
