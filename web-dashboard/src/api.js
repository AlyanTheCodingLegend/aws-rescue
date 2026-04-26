const API_BASE = import.meta.env.VITE_API_BASE_URL ?? '/api'

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

  getLogsCsvUrl({ status = 'ALL', fromDate = '', keyQuery = '', limit = 1000 } = {}) {
    return buildUrl('/logs.csv', {
      status,
      from_date: fromDate,
      key_query: keyQuery,
      limit,
    })
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
}
