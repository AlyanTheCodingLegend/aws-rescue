import { useEffect, useMemo, useState } from 'react'
import {
  Activity,
  AlertTriangle,
  CheckCircle2,
  Clock3,
  Cloud,
  Database,
  Download,
  LayoutDashboard,
  Boxes,
  RefreshCw,
  ScrollText,
  Search,
  Server,
  SlidersHorizontal,
  Sparkles,
  TriangleAlert,
  UploadCloud,
  XCircle,
} from 'lucide-react'
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import './App.css'
import { api } from './api'

const NAV_ITEMS = [
  { key: 'overview', label: 'Overview', icon: LayoutDashboard },
  { key: 'objects', label: 'Objects', icon: Boxes },
  { key: 'logs', label: 'Logs', icon: ScrollText },
  { key: 'controls', label: 'Controls', icon: SlidersHorizontal },
]

const LOG_FILTERS = ['ALL', 'SUCCESS', 'FAILED', 'DRIFT_DETECTED', 'DELETED', 'HEALTH_CHECK_OK']

const STATUS_TONES = {
  SUCCESS: 'ok',
  HEALTH_CHECK_OK: 'ok',
  DELETED: 'neutral',
  DRIFT_DETECTED: 'warn',
  FAILED: 'danger',
  IN_SYNC: 'ok',
  MISSING: 'danger',
  MISMATCH: 'warn',
  HEALTHY: 'ok',
  UNAVAILABLE: 'danger',
}

function formatBytes(value) {
  const bytes = Number(value || 0)
  if (bytes < 1024) {
    return `${bytes} B`
  }
  if (bytes < 1024 * 1024) {
    return `${(bytes / 1024).toFixed(1)} KB`
  }
  if (bytes < 1024 * 1024 * 1024) {
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
  }
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`
}

function formatTimestamp(ts) {
  if (!ts || ts === 'Never') {
    return ts || 'Never'
  }

  const parsed = new Date(ts)
  if (Number.isNaN(parsed.getTime())) {
    return ts
  }

  return parsed.toISOString().replace('T', ' ').slice(0, 19) + ' UTC'
}

function hourLabel(ts) {
  const parsed = new Date(ts)
  if (Number.isNaN(parsed.getTime())) {
    return ts
  }
  return parsed.toISOString().slice(11, 16)
}

function App() {
  const [activeView, setActiveView] = useState('overview')
  const [toast, setToast] = useState(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)

  const [configData, setConfigData] = useState(null)
  const [overviewData, setOverviewData] = useState(null)
  const [objectsData, setObjectsData] = useState({ primary: [], backup: [], summary: {} })
  const [logsData, setLogsData] = useState({ items: [], failed_details: [], count: 0 })

  const [selectedObjectKey, setSelectedObjectKey] = useState('')
  const [selectedObjectSource, setSelectedObjectSource] = useState('primary')
  const [objectHistory, setObjectHistory] = useState([])

  const [objectQuery, setObjectQuery] = useState('')
  const [logQuery, setLogQuery] = useState('')
  const [logFilter, setLogFilter] = useState('ALL')
  const [logFromDate, setLogFromDate] = useState('')

  const [autoRefresh, setAutoRefresh] = useState(false)
  const [outageMode, setOutageMode] = useState(false)
  const [runningActionKey, setRunningActionKey] = useState('')
  const [actionResult, setActionResult] = useState(null)
  const [uploadFileChoice, setUploadFileChoice] = useState(null)
  const [uploadingFile, setUploadingFile] = useState(false)
  const [uploadInputKey, setUploadInputKey] = useState(0)

  function showToast(text, tone = 'neutral') {
    setToast({ id: Date.now(), text, tone })
  }

  async function refreshConfig() {
    const data = await api.getConfig()
    setConfigData(data)
  }

  async function refreshOverview() {
    const data = await api.getOverview()
    setOverviewData(data)
    setOutageMode(Boolean(data.outage_active))
  }

  async function refreshObjects(search = objectQuery) {
    const data = await api.getObjects(search)
    setObjectsData(data)
  }

  async function refreshLogs() {
    const data = await api.getLogs({
      status: logFilter,
      fromDate: logFromDate,
      keyQuery: logQuery,
      limit: 2000,
    })
    setLogsData(data)
  }

  async function refreshOutageState() {
    const state = await api.getOutageState()
    setOutageMode(Boolean(state.outage_active))
  }

  async function refreshAll() {
    await Promise.all([
      refreshConfig(),
      refreshOverview(),
      refreshObjects(),
      refreshLogs(),
      refreshOutageState(),
    ])
  }

  useEffect(() => {
    let mounted = true

    const load = async () => {
      setLoading(true)
      setError('')
      try {
        await refreshAll()
      } catch (loadError) {
        if (!mounted) {
          return
        }
        setError(loadError.message || 'Failed to load dashboard data.')
      } finally {
        if (mounted) {
          setLoading(false)
        }
      }
    }

    load()

    return () => {
      mounted = false
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    const timer = window.setTimeout(() => {
      setToast(null)
    }, 3200)

    if (!toast) {
      window.clearTimeout(timer)
    }

    return () => {
      window.clearTimeout(timer)
    }
  }, [toast])

  useEffect(() => {
    const timer = window.setTimeout(() => {
      refreshObjects().catch((loadError) => {
        setError(loadError.message || 'Failed to refresh objects.')
      })
    }, 250)

    return () => {
      window.clearTimeout(timer)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [objectQuery])

  useEffect(() => {
    const timer = window.setTimeout(() => {
      refreshLogs().catch((loadError) => {
        setError(loadError.message || 'Failed to refresh logs.')
      })
    }, 250)

    return () => {
      window.clearTimeout(timer)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [logFilter, logQuery, logFromDate])

  useEffect(() => {
    if (!autoRefresh) {
      return undefined
    }

    const timer = window.setInterval(() => {
      refreshAll().catch((loadError) => {
        setError(loadError.message || 'Auto-refresh failed.')
      })
    }, 30000)

    return () => {
      window.clearInterval(timer)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autoRefresh, objectQuery, logFilter, logQuery, logFromDate])

  const allObjects = useMemo(
    () => [...objectsData.primary, ...objectsData.backup],
    [objectsData.primary, objectsData.backup],
  )

  const effectiveSelectedObjectKey = useMemo(() => {
    if (allObjects.length === 0) {
      return ''
    }
    const exists = allObjects.some((item) => item.key === selectedObjectKey)
    return exists ? selectedObjectKey : allObjects[0].key
  }, [allObjects, selectedObjectKey])

  useEffect(() => {
    let mounted = true

    if (!effectiveSelectedObjectKey) {
      return undefined
    }

    api
      .getObjectHistory(effectiveSelectedObjectKey, 20)
      .then((result) => {
        if (mounted) {
          setObjectHistory(result.history || [])
        }
      })
      .catch((historyError) => {
        if (mounted) {
          setError(historyError.message || 'Failed to load object history.')
        }
      })

    return () => {
      mounted = false
    }
  }, [effectiveSelectedObjectKey])

  const selectedObject = useMemo(() => {
    if (!effectiveSelectedObjectKey) {
      return null
    }

    const preferred =
      selectedObjectSource === 'backup'
        ? objectsData.backup.find((item) => item.key === effectiveSelectedObjectKey)
        : objectsData.primary.find((item) => item.key === effectiveSelectedObjectKey)

    return (
      preferred ||
      objectsData.primary.find((item) => item.key === effectiveSelectedObjectKey) ||
      objectsData.backup.find((item) => item.key === effectiveSelectedObjectKey) ||
      null
    )
  }, [effectiveSelectedObjectKey, selectedObjectSource, objectsData])

  const timelineData = useMemo(
    () =>
      (overviewData?.timeline || []).map((entry) => ({
        slot: hourLabel(entry.hour),
        replications: entry.replications,
      })),
    [overviewData],
  )

  const driftChartData = useMemo(() => {
    const grouped = new Map()
    const rows = overviewData?.drift_history || []

    rows.forEach((entry) => {
      if (!grouped.has(entry.day)) {
        grouped.set(entry.day, {
          day: entry.day,
          SUCCESS: 0,
          FAILED: 0,
          DRIFT_DETECTED: 0,
          DELETED: 0,
          HEALTH_CHECK_OK: 0,
        })
      }
      const bucket = grouped.get(entry.day)
      if (Object.prototype.hasOwnProperty.call(bucket, entry.status)) {
        bucket[entry.status] = entry.count
      }
    })

    return [...grouped.values()]
  }, [overviewData])

  async function runControlAction(actionKey) {
    if (runningActionKey) {
      return
    }

    setRunningActionKey(actionKey)
    setError('')

    try {
      let response
      if (actionKey === 'full-sync') {
        response = await api.triggerFullSync()
        showToast('Full sync completed.', 'ok')
      } else if (actionKey === 'health-check') {
        response = await api.runHealthCheck()
        showToast('Health check completed.', 'ok')
      } else if (actionKey === 'seed-data') {
        response = await api.seedData()
        showToast('Seed data uploaded successfully.', 'ok')
      } else if (actionKey === 'simulate-outage') {
        response = outageMode ? await api.endOutage() : await api.startOutage()
        showToast(response?.result?.message || 'Outage state updated.', outageMode ? 'ok' : 'warn')
      }

      setActionResult({ action: actionKey, payload: response?.result ?? response })
      await refreshAll()
    } catch (actionError) {
      setError(actionError.message || 'Action failed.')
      showToast(actionError.message || 'Action failed.', 'warn')
    } finally {
      setRunningActionKey('')
    }
  }

  async function runUploadFile() {
    if (!uploadFileChoice || uploadingFile) {
      return
    }

    setUploadingFile(true)
    setError('')

    try {
      const response = await api.uploadFile(uploadFileChoice)
      const result = response?.result ?? response
      const targetKey = result?.key || uploadFileChoice.name
      const targetBucket = result?.bucket || ''
      showToast(`Uploaded ${targetKey} to ${targetBucket}`, 'ok')
      setActionResult({ action: 'upload-file', payload: result })
      setUploadFileChoice(null)
      setUploadInputKey((current) => current + 1)
      await refreshAll()
      // Replicator Lambda writes the DynamoDB log entry a beat after the
      // S3 PUT returns, so the immediate refresh above can miss it. Re-pull
      // logs + overview shortly after to capture the new row.
      window.setTimeout(() => {
        Promise.all([refreshLogs(), refreshOverview()]).catch((followError) => {
          setError(followError.message || 'Follow-up refresh failed.')
        })
      }, 2500)
    } catch (uploadError) {
      setError(uploadError.message || 'Upload failed.')
      showToast(uploadError.message || 'Upload failed.', 'warn')
    } finally {
      setUploadingFile(false)
    }
  }

  function exportVisibleLogs() {
    const url = api.getLogsCsvUrl({
      status: logFilter,
      fromDate: logFromDate,
      keyQuery: logQuery,
      limit: 5000,
    })

    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = `rescue_logs_${Date.now()}.csv`
    anchor.click()
    showToast('CSV download started.', 'neutral')
  }

  const metrics = overviewData?.metrics || {
    total_objects_primary: 0,
    total_objects_backup: 0,
    sync_percentage: 0,
    last_successful_sync: 'Never',
    active_drift_alerts: 0,
    primary_size_bytes: 0,
    backup_size_bytes: 0,
  }

  const activeTitle = NAV_ITEMS.find((item) => item.key === activeView)?.label ?? 'Overview'

  return (
    <div className="app-shell">
      <aside className="nav-rail">
        <div className="brand-block">
          <div className="brand-chip">
            <Cloud size={18} />
            <span>AWS-RESCUE</span>
          </div>
          <h1>Command Deck</h1>
          <p>Live operational dashboard with real AWS-backed controls.</p>
        </div>

        <nav className="nav-stack" aria-label="Dashboard Sections">
          {NAV_ITEMS.map((item) => {
            const Icon = item.icon
            const selected = activeView === item.key
            return (
              <button
                key={item.key}
                type="button"
                className={`nav-item ${selected ? 'selected' : ''}`}
                onClick={() => setActiveView(item.key)}
              >
                <Icon size={16} />
                <span>{item.label}</span>
              </button>
            )
          })}
        </nav>

        <div className="rail-footer">
          <div className="rail-kpi">
            <span>Sync Percentage</span>
            <strong>{metrics.sync_percentage}%</strong>
          </div>
          <div className={`mode-chip ${outageMode ? 'warn' : 'ok'}`}>
            {outageMode ? <TriangleAlert size={14} /> : <CheckCircle2 size={14} />}
            <span>{outageMode ? 'Outage Simulation On' : 'Normal Mode'}</span>
          </div>
        </div>
      </aside>

      <main className="main-surface">
        <header className="topbar panel" data-stagger="1">
          <div>
            <p className="eyebrow">Resilient Emergency Storage and Cross-region Upload Engine</p>
            <h2>{activeTitle}</h2>
          </div>
          <div className="topbar-meta">
            <div>
              <Clock3 size={14} />
              <span>Last Successful Sync: {formatTimestamp(metrics.last_successful_sync)}</span>
            </div>
            <div>
              <Server size={14} />
              <span>
                Project: {configData?.project_id || 'team01'} • {configData?.failover_state || 'NORMAL'}
              </span>
            </div>
          </div>
        </header>

        {toast && (
          <div className={`toast ${toast.tone}`} key={toast.id}>
            <Sparkles size={15} />
            <span>{toast.text}</span>
          </div>
        )}

        {error && (
          <div className="panel" data-stagger="2">
            <div className="mode-banner warn">
              <AlertTriangle size={16} />
              <span>{error}</span>
            </div>
          </div>
        )}

        {loading ? (
          <div className="panel" data-stagger="2">
            <div className="mode-banner ok">
              <RefreshCw className="spin" size={16} />
              <span>Loading live dashboard data...</span>
            </div>
          </div>
        ) : null}

        {!loading && activeView === 'overview' && (
          <OverviewSection
            metrics={metrics}
            regions={overviewData?.regions || []}
            timelineData={timelineData}
            driftChartData={driftChartData}
            autoRefresh={autoRefresh}
            setAutoRefresh={setAutoRefresh}
          />
        )}

        {!loading && activeView === 'objects' && (
          <ObjectsSection
            configData={configData}
            objectQuery={objectQuery}
            setObjectQuery={setObjectQuery}
            objectsData={objectsData}
            selectedObject={selectedObject}
            selectedObjectKey={effectiveSelectedObjectKey}
            objectHistory={objectHistory}
            onSelectObject={(key, source) => {
              setSelectedObjectKey(key)
              setSelectedObjectSource(source)
            }}
          />
        )}

        {!loading && activeView === 'logs' && (
          <LogsSection
            logsData={logsData}
            logFilter={logFilter}
            setLogFilter={setLogFilter}
            logQuery={logQuery}
            setLogQuery={setLogQuery}
            logFromDate={logFromDate}
            setLogFromDate={setLogFromDate}
            exportVisibleLogs={exportVisibleLogs}
          />
        )}

        {!loading && activeView === 'controls' && (
          <ControlsSection
            configData={configData}
            outageMode={outageMode}
            runningActionKey={runningActionKey}
            actionResult={actionResult}
            runControlAction={runControlAction}
            uploadFileChoice={uploadFileChoice}
            setUploadFileChoice={setUploadFileChoice}
            uploadingFile={uploadingFile}
            runUploadFile={runUploadFile}
            uploadInputKey={uploadInputKey}
          />
        )}
      </main>
    </div>
  )
}

function OverviewSection({
  metrics,
  regions,
  timelineData,
  driftChartData,
  autoRefresh,
  setAutoRefresh,
}) {
  const lastSyncDisplay = formatTimestamp(metrics.last_successful_sync)
  const lastSyncValue = lastSyncDisplay === 'Never' ? 'Never' : lastSyncDisplay.slice(11, 19)
  const lastSyncHint = lastSyncDisplay === 'Never' ? 'No successful sync yet' : lastSyncDisplay.slice(0, 10)

  return (
    <section className="view-section overview-grid">
      <div className="metric-grid panel" data-stagger="2">
        <MetricCard
          icon={Database}
          title="Total Objects (Primary)"
          value={metrics.total_objects_primary}
          hint={`Primary size: ${formatBytes(metrics.primary_size_bytes)}`}
        />
        <MetricCard
          icon={Activity}
          title="Sync Percentage"
          value={`${metrics.sync_percentage}%`}
          hint={`Backup objects: ${metrics.total_objects_backup}`}
        />
        <MetricCard
          icon={Clock3}
          title="Last Successful Sync"
          value={lastSyncValue}
          hint={lastSyncHint}
        />
        <MetricCard
          icon={AlertTriangle}
          title="Active Drift Alerts"
          value={metrics.active_drift_alerts}
          hint="DynamoDB status = DRIFT_DETECTED"
        />
      </div>

      <div className="panel chart-card" data-stagger="3">
        <div className="card-head">
          <h3>Replication Timeline</h3>
          <span>Successful replications per hour (UTC)</span>
        </div>
        <ResponsiveContainer width="100%" height={300}>
          <AreaChart data={timelineData} margin={{ top: 12, right: 14, left: 0, bottom: 0 }}>
            <defs>
              <linearGradient id="replicatedFill" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor="#0f766e" stopOpacity={0.35} />
                <stop offset="100%" stopColor="#0f766e" stopOpacity={0} />
              </linearGradient>
            </defs>
            <CartesianGrid strokeDasharray="3 3" stroke="rgba(39, 65, 56, 0.15)" />
            <XAxis dataKey="slot" tick={{ fill: '#36594d', fontSize: 12 }} />
            <YAxis tick={{ fill: '#36594d', fontSize: 12 }} />
            <Tooltip />
            <Area
              type="monotone"
              dataKey="replications"
              stroke="#0f766e"
              fillOpacity={1}
              fill="url(#replicatedFill)"
              strokeWidth={2.2}
            />
          </AreaChart>
        </ResponsiveContainer>
      </div>

      <div className="panel chart-card" data-stagger="4">
        <div className="card-head">
          <h3>Drift History</h3>
          <span>Daily replication events by status</span>
        </div>
        <ResponsiveContainer width="100%" height={300}>
          <BarChart data={driftChartData} margin={{ top: 12, right: 14, left: 0, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="rgba(39, 65, 56, 0.15)" />
            <XAxis dataKey="day" tick={{ fill: '#36594d', fontSize: 12 }} />
            <YAxis tick={{ fill: '#36594d', fontSize: 12 }} />
            <Tooltip />
            <Bar dataKey="SUCCESS" stackId="a" fill="#22c55e" />
            <Bar dataKey="FAILED" stackId="a" fill="#ef4444" />
            <Bar dataKey="DRIFT_DETECTED" stackId="a" fill="#f59e0b" />
            <Bar dataKey="DELETED" stackId="a" fill="#94a3b8" />
            <Bar dataKey="HEALTH_CHECK_OK" stackId="a" fill="#3b82f6" />
          </BarChart>
        </ResponsiveContainer>
      </div>

      <div className="panel region-grid" data-stagger="5">
        {regions.map((entry) => (
          <article key={entry.region} className="region-card">
            <header>
              <h4>{entry.region}</h4>
              <StatusBadge status={entry.health.toUpperCase()} label={entry.health} />
            </header>
            <p>
              {entry.role} bucket: {entry.bucket}
            </p>
            <dl>
              <div>
                <dt>Latency</dt>
                <dd>{entry.latency_ms ? `${entry.latency_ms} ms` : 'N/A'}</dd>
              </div>
              <div>
                <dt>Reachability</dt>
                <dd>{entry.health}</dd>
              </div>
            </dl>
          </article>
        ))}
      </div>

      <div className="panel overview-refresh" data-stagger="5">
        <div className="mode-banner ok">
          <CheckCircle2 size={16} />
          <span>
            Auto-refresh every 30 seconds is {autoRefresh ? 'enabled' : 'disabled'}.
          </span>
        </div>
        <div style={{ marginTop: '10px' }}>
          <button
            type="button"
            className="ghost-button"
            onClick={() => setAutoRefresh((current) => !current)}
          >
            {autoRefresh ? 'Disable Auto-refresh' : 'Enable Auto-refresh'}
          </button>
        </div>
      </div>
    </section>
  )
}

function ObjectsSection({
  configData,
  objectQuery,
  setObjectQuery,
  objectsData,
  selectedObject,
  selectedObjectKey,
  objectHistory,
  onSelectObject,
}) {
  return (
    <section className="view-section split-layout">
      <div className="panel" data-stagger="2">
        <div className="card-head inline">
          <div>
            <h3>Object Browser</h3>
            <span>Primary and backup inventory comparison</span>
          </div>
          <label className="search-field" htmlFor="objectSearch">
            <Search size={16} />
            <input
              id="objectSearch"
              type="search"
              placeholder="Filter by key prefix or substring"
              value={objectQuery}
              onChange={(event) => setObjectQuery(event.target.value)}
            />
          </label>
        </div>

        <div className="objects-columns">
          <div className="object-column">
            <h4>
              Primary — {configData?.primary_bucket || 'primary'} ({objectsData.summary?.primary_count || 0})
            </h4>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th className="col-key">Object Key</th>
                    <th className="col-size">Size</th>
                    <th className="col-status">Status</th>
                  </tr>
                </thead>
                <tbody>
                  {objectsData.primary.map((item) => (
                    <tr
                      key={`p-${item.key}`}
                      className={selectedObjectKey === item.key ? 'table-row-active' : ''}
                      onClick={() => onSelectObject(item.key, 'primary')}
                    >
                      <td className="col-key" title={item.key}>
                        {item.key}
                      </td>
                      <td className="col-size">{item.size_hr}</td>
                      <td className="col-status">
                        <StatusBadge
                          status={item.in_backup ? 'IN_SYNC' : 'MISSING'}
                          label={item.in_backup ? 'In Backup' : 'Missing'}
                        />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          <div className="object-column">
            <h4>
              Backup — {configData?.backup_bucket || 'backup'} ({objectsData.summary?.backup_count || 0})
            </h4>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th className="col-key">Object Key</th>
                    <th className="col-size">Size</th>
                    <th className="col-status">Status</th>
                  </tr>
                </thead>
                <tbody>
                  {objectsData.backup.map((item) => (
                    <tr
                      key={`b-${item.key}`}
                      className={selectedObjectKey === item.key ? 'table-row-active' : ''}
                      onClick={() => onSelectObject(item.key, 'backup')}
                    >
                      <td className="col-key" title={item.key}>
                        {item.key}
                      </td>
                      <td className="col-size">{item.size_hr}</td>
                      <td className="col-status">
                        <StatusBadge
                          status={item.matches_primary ? 'IN_SYNC' : 'MISMATCH'}
                          label={item.matches_primary ? 'Matches Primary' : 'Mismatch/Orphan'}
                        />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      </div>

      <div className="panel object-details" data-stagger="3">
        <div className="card-head">
          <h3>Object Details</h3>
          <span>Selected object metadata and replication history</span>
        </div>

        {selectedObject ? (
          <>
            <div className="detail-list">
              <div>
                <strong>Object Key</strong>
                <span>{selectedObject.key}</span>
              </div>
              <div>
                <strong>Last Modified</strong>
                <span>{selectedObject.last_modified || 'N/A'}</span>
              </div>
              <div>
                <strong>ETag</strong>
                <span>{selectedObject.etag || 'N/A'}</span>
              </div>
              <div>
                <strong>Size</strong>
                <span>{selectedObject.size_hr || formatBytes(selectedObject.size_bytes || 0)}</span>
              </div>
            </div>

            <h4>Replication History</h4>
            <ul className="history-list">
              {objectHistory.length > 0 ? (
                objectHistory.map((entry) => (
                  <li key={`${entry.timestamp}-${entry.status}-${entry.object_key}`}>
                    <StatusBadge status={entry.status} label={entry.status.replaceAll('_', ' ')} />
                    <p>{formatTimestamp(entry.timestamp)}</p>
                    <small>
                      {entry.source_region || '-'} to {entry.dest_region || '-'} • {entry.size_bytes || 0} bytes
                    </small>
                    {entry.error_message ? <small>{entry.error_message}</small> : null}
                  </li>
                ))
              ) : (
                <li className="empty">No replication history found for this object.</li>
              )}
            </ul>
          </>
        ) : (
          <div className="empty-state">Select an object to view details.</div>
        )}
      </div>
    </section>
  )
}

function LogsSection({
  logsData,
  logFilter,
  setLogFilter,
  logQuery,
  setLogQuery,
  logFromDate,
  setLogFromDate,
  exportVisibleLogs,
}) {
  return (
    <section className="view-section" data-stagger="2">
      <div className="panel">
        <div className="card-head inline">
          <div>
            <h3>Replication Logs</h3>
            <span>Filter by status, date, and object key</span>
          </div>
          <div className="controls-inline">
            <label className="field compact" htmlFor="logFilter">
              <span>Status</span>
              <select
                id="logFilter"
                value={logFilter}
                onChange={(event) => setLogFilter(event.target.value)}
              >
                {LOG_FILTERS.map((item) => (
                  <option key={item} value={item}>
                    {item}
                  </option>
                ))}
              </select>
            </label>

            <label className="field" htmlFor="logFromDate">
              <span>From date</span>
              <input
                id="logFromDate"
                type="date"
                className="date-input"
                value={logFromDate}
                onChange={(event) => setLogFromDate(event.target.value)}
              />
            </label>

            <label className="search-field" htmlFor="logSearch">
              <Search size={16} />
              <input
                id="logSearch"
                type="search"
                placeholder="Object key contains"
                value={logQuery}
                onChange={(event) => setLogQuery(event.target.value)}
              />
            </label>

            <button type="button" className="ghost-button" onClick={exportVisibleLogs}>
              <Download size={15} />
              Download CSV
            </button>
          </div>
        </div>

        <div className="table-wrap">
          <table className="logs-table">
            <thead>
              <tr>
                <th className="col-time">Timestamp</th>
                <th className="col-key">Object Key</th>
                <th className="col-status">Status</th>
                <th className="col-size">Size (bytes)</th>
                <th className="col-source">Source</th>
                <th className="col-dest">Destination</th>
              </tr>
            </thead>
            <tbody>
              {logsData.items.map((entry) => (
                <tr key={`${entry.timestamp}-${entry.object_key}-${entry.status}`}>
                  <td className="col-time">{formatTimestamp(entry.timestamp)}</td>
                  <td className="col-key" title={entry.object_key}>
                    {entry.object_key}
                  </td>
                  <td className="col-status">
                    <StatusBadge status={entry.status} label={entry.status.replaceAll('_', ' ')} />
                  </td>
                  <td className="col-size">{entry.size_bytes}</td>
                  <td className="col-source">{entry.source_region || '-'}</td>
                  <td className="col-dest">{entry.dest_region || '-'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <div className="card-head" style={{ marginTop: '12px' }}>
          <h4>Failure Details</h4>
          <span>{logsData.failed_details.length} failed events in current filter</span>
        </div>

        <ul className="history-list">
          {logsData.failed_details.length > 0 ? (
            logsData.failed_details.map((entry) => (
              <li key={`${entry.timestamp}-${entry.object_key}`}>
                <StatusBadge status="FAILED" label="FAILED" />
                <p>{entry.object_key}</p>
                <small>{formatTimestamp(entry.timestamp)}</small>
                <small>{entry.error_message || 'No error message'}</small>
              </li>
            ))
          ) : (
            <li className="empty">No failures for the selected filters.</li>
          )}
        </ul>
      </div>
    </section>
  )
}

function ControlsSection({
  configData,
  outageMode,
  runningActionKey,
  actionResult,
  runControlAction,
  uploadFileChoice,
  setUploadFileChoice,
  uploadingFile,
  runUploadFile,
  uploadInputKey,
}) {
  const actionCards = [
    {
      key: 'full-sync',
      title: 'Trigger Full Sync',
      description: 'Force primary to backup sync for all objects.',
      buttonLabel: 'Trigger Full Sync',
    },
    {
      key: 'health-check',
      title: 'Run Health Check',
      description: 'Invoke HealthChecker Lambda and return diagnostics.',
      buttonLabel: 'Run Health Check',
    },
    {
      key: 'simulate-outage',
      title: outageMode ? 'End Outage Simulation' : 'Start Outage Simulation',
      description: outageMode
        ? 'Restore S3 to Lambda trigger to resume replication.'
        : 'Disable S3 to Lambda trigger so new uploads are not replicated.',
      buttonLabel: outageMode ? 'End Outage' : 'Start Outage',
    },
    {
      key: 'seed-data',
      title: 'Seed Test Data',
      description: 'Generate fake NGO files and upload to primary bucket.',
      buttonLabel: 'Seed Test Data',
    },
  ]

  return (
    <section className="view-section controls-layout">
      <div className="panel" data-stagger="2">
        <div className="card-head">
          <h3>Controls</h3>
          <span>Same operational commands as the Streamlit control page</span>
        </div>

        <div className="control-grid">
          {actionCards.map((action) => {
            const busy = runningActionKey === action.key
            return (
              <article key={action.key} className="control-card">
                <h4>{action.title}</h4>
                <p>{action.description}</p>
                <button
                  type="button"
                  onClick={() => runControlAction(action.key)}
                  disabled={Boolean(runningActionKey)}
                >
                  {busy ? <RefreshCw className="spin" size={15} /> : <Activity size={15} />}
                  {busy ? 'Running...' : action.buttonLabel}
                </button>
              </article>
            )
          })}
        </div>

        <div className="card-head" style={{ marginTop: '18px' }}>
          <h4>Upload Your Own File</h4>
          <span>
            Pick any document from your computer; it will be uploaded to the active primary bucket.
            {outageMode ? ' (Outage active — file goes to the promoted backup region.)' : ''}
          </span>
        </div>

        <article className="control-card upload-card">
          <label className="field" htmlFor="uploadFileInput">
            <span>File</span>
            <input
              key={uploadInputKey}
              id="uploadFileInput"
              type="file"
              onChange={(event) => {
                const next = event.target.files && event.target.files[0]
                setUploadFileChoice(next || null)
              }}
              disabled={uploadingFile}
            />
          </label>

          <p>
            {uploadFileChoice
              ? `Ready: ${uploadFileChoice.name} (${formatBytes(uploadFileChoice.size)})`
              : 'No file selected yet.'}
            {' '}
            Target: <code>{configData?.primary_bucket || 'primary bucket'}</code>
          </p>

          <button
            type="button"
            onClick={runUploadFile}
            disabled={!uploadFileChoice || uploadingFile || Boolean(runningActionKey)}
          >
            {uploadingFile ? <RefreshCw className="spin" size={15} /> : <UploadCloud size={15} />}
            {uploadingFile ? 'Uploading...' : 'Upload File'}
          </button>
        </article>
      </div>

      <div className="panel" data-stagger="3">
        <div className="card-head">
          <h3>Runtime State</h3>
          <span>Primary: {configData?.primary_bucket || 'N/A'} • Backup: {configData?.backup_bucket || 'N/A'}</span>
        </div>

        <div className={`mode-banner ${outageMode ? 'warn' : 'ok'}`}>
          {outageMode ? <TriangleAlert size={16} /> : <CheckCircle2 size={16} />}
          <span>
            {outageMode
              ? 'Outage simulation active: replication trigger disabled.'
              : 'Normal mode: replication trigger enabled.'}
          </span>
        </div>

        <div className="card-head" style={{ marginTop: '14px' }}>
          <h4>Last Action Result</h4>
        </div>

        {actionResult ? (
          <pre className="data-pre">{JSON.stringify(actionResult, null, 2)}</pre>
        ) : (
          <div className="empty-state">Run any control action to view detailed result payload.</div>
        )}
      </div>
    </section>
  )
}

function MetricCard({ icon: Icon, title, value, hint }) {
  return (
    <article className="metric-card">
      <span className="metric-icon">
        <Icon size={16} />
      </span>
      <h4>{title}</h4>
      <strong>{value}</strong>
      <p>{hint}</p>
    </article>
  )
}

function StatusBadge({ status, label }) {
  const normalized = String(status || 'neutral').toUpperCase()
  const tone = STATUS_TONES[normalized] ?? 'neutral'

  const Icon =
    tone === 'ok' ? CheckCircle2 : tone === 'warn' ? TriangleAlert : tone === 'danger' ? XCircle : Activity

  return (
    <span className={`status-badge ${tone}`}>
      <Icon size={13} />
      {label}
    </span>
  )
}

export default App
