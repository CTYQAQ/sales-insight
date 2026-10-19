import { http } from './client'

/* ---------------- 类型 ---------------- */
export interface UserInfo {
  id: number
  username: string
  email?: string
  role: 'admin' | 'analyst' | 'viewer'
  region_scope: string[]
  is_active: boolean
  created_at: string
}

export interface LoginResp {
  access_token: string
  token_type: string
  expires_in: number
  user: UserInfo
}

export interface SummaryMetric {
  revenue: number
  gross_profit: number
  quantity: number
  orders: number
  margin: number
}

export interface SummaryResp {
  period: { start: string; end: string }
  previous_period: { start: string; end: string }
  current: SummaryMetric
  previous: SummaryMetric
  changes: Record<string, number | null>
}

export interface TrendItem {
  date: string
  value: number
}

export interface BreakItem {
  key: string
  name: string
  revenue: number
  gross_profit: number
  share_pct: number
}

export interface Mover {
  key: string
  current: number
  previous: number
  change_pct: number
  delta_amount: number
}

export interface DataSourceItem {
  id: string
  name: string
  type: 'file' | 'database' | 'api'
  config: Record<string, unknown>
  field_mapping: Record<string, string>
  enabled: boolean
  last_run_at: string | null
  last_status: string | null
}

export interface IngestResult {
  job_id: string
  status: string
  rows_read: number
  rows_written: number
  rows_skipped: number
  datasource_id?: string
}

export interface PreviewResp {
  temp_path: string
  filename: string
  columns: string[]
  rows_total: number
  preview: Record<string, unknown>[]
  inferred_mapping: Record<string, string>
}

export interface ReportItem {
  id: string
  title: string
  period_start?: string
  period_end?: string
  scope: Record<string, unknown>
  model?: string
  created_at: string
  summary_md?: string
  excerpt?: string
}

export interface AlertRuleItem {
  id: string
  name: string
  metric: string
  dimension: string
  comparator: string
  threshold: number
  severity: string
  channels: string[]
  enabled: boolean
  last_triggered_at: string | null
}

export interface AlertEventItem {
  id: string
  rule_name: string
  dimension_key: string
  actual_value: number
  message: string
  acknowledged: boolean
  created_at: string
}

export interface ChatResp {
  sql: string
  row_count: number
  columns: string[]
  rows: Record<string, unknown>[]
  answer: string
  elapsed_ms: number
}

/** Agent 单步执行记录 */
export interface AgentTraceItem {
  node: string
  status: 'ok' | 'retry' | 'failed' | 'blocked'
  detail: string
  elapsed_ms: number
}

export interface AgentChatResp extends ChatResp {
  rewritten: string
  attempts: number
  trace: AgentTraceItem[]
}

/** 对话 Agent：意图路由 + RAG + 多轮记忆 */
export type DialogIntent = 'knowledge' | 'data' | 'chat'

export interface DialogSource {
  source: string
  title: string
}

export interface RetrievedChunk {
  chunk_id: string
  text: string
  source: string
  title: string
  score: number
  vector_score: number
  bm25_score: number
  vector_rank: number
  bm25_rank: number
  coverage: number
  topic_score: number
}

export interface DialogResp {
  answer: string
  intent: DialogIntent
  intent_reason: string
  standalone_question: string
  sources: DialogSource[]
  retrieved: RetrievedChunk[]
  sql: string
  row_count: number
  columns: string[]
  rows: Record<string, unknown>[]
  trace: AgentTraceItem[]
  elapsed_ms: number
}

export interface DialogMsg {
  role: 'user' | 'assistant'
  content: string
}

export interface KnowledgeStats {
  enabled: boolean
  embedding: string
  dim: number
  hybrid: boolean
  mode: 'lite' | 'standalone'
  chunks: number
  documents: number
  ok: boolean
  error: string
}

export interface KnowledgeDoc {
  source: string
  title: string
  chunks: number
}

/* ---------------- 认证 ---------------- */
export const authApi = {
  login: (username: string, password: string) =>
    http.post<LoginResp>('/auth/login', { username, password }),
  logout: () => http.post('/auth/logout'),
  me: () => http.get<UserInfo>('/auth/me'),
  users: () => http.get<UserInfo[]>('/auth/users'),
  createUser: (payload: Record<string, unknown>) => http.post<UserInfo>('/auth/users', payload),
  updateUser: (id: number, payload: Record<string, unknown>) =>
    http.patch<UserInfo>(`/auth/users/${id}`, payload),
}

/* ---------------- 指标 ---------------- */
export const metricsApi = {
  summary: (days = 30, end?: string) =>
    http.get<SummaryResp>('/metrics/summary', { params: { days, end } }),
  range: () => http.get<{ has_data: boolean; min_date?: string; max_date?: string }>('/metrics/range'),
  trend: (start: string, end: string, metric = 'revenue') =>
    http.get<TrendItem[]>('/metrics/trend', { params: { start, end, metric } }),
  breakdown: (start: string, end: string, dimension = 'region', limit = 10) =>
    http.get<BreakItem[]>('/metrics/breakdown', { params: { start, end, dimension, limit } }),
  movers: (days = 30, limit = 5) => http.get<Record<string, Mover[]>>('/metrics/movers', { params: { days, limit } }),
}

/* ---------------- 数据接入 ---------------- */
export const ingestApi = {
  sources: () => http.get<DataSourceItem[]>('/datasources'),
  createSource: (payload: Record<string, unknown>) => http.post<DataSourceItem>('/datasources', payload),
  deleteSource: (id: string) => http.delete(`/datasources/${id}`),
  preview: (file: File) => {
    const fd = new FormData()
    fd.append('file', file)
    return http.post<PreviewResp>('/ingest/preview', fd, { headers: { 'Content-Type': 'multipart/form-data' } })
  },
  upload: (file: File, name: string, replace = false) => {
    const fd = new FormData()
    fd.append('file', file)
    fd.append('source_name', name)
    fd.append('replace_existing', String(replace))
    return http.post<IngestResult>('/ingest/upload', fd, { headers: { 'Content-Type': 'multipart/form-data' } })
  },
  run: (id: string) => http.post<IngestResult>(`/ingest/run/${id}`),
  jobs: (limit = 20) => http.get<Record<string, unknown>[]>('/ingest/jobs', { params: { limit } }),
}

/* ---------------- AI 报告 / 对话 ---------------- */
export const aiApi = {
  generateReport: (days = 30, end?: string) => http.post<ReportItem>('/reports/generate', { days, end }),
  reports: (limit = 20) => http.get<ReportItem[]>('/reports', { params: { limit } }),
  report: (id: string) => http.get<ReportItem>(`/reports/${id}`),
  chat: (question: string) => http.post<ChatResp>('/chat/query', { question }),
  /** Agent 版：改写→生成→校验→执行→（失败则反思重来）→解读，返回完整 trace */
  agentChat: (question: string) => http.post<AgentChatResp>('/chat/agent/query', { question }),
  /** 对话 Agent：自动判断问知识/问数据/闲聊，支持多轮追问 */
  dialog: (question: string, history: DialogMsg[] = []) =>
    http.post<DialogResp>('/chat/dialog', { question, history }),
  chatHistory: (limit = 10) => http.get<Record<string, unknown>[]>('/chat/history', { params: { limit } }),
}

/* ---------------- 知识库 ---------------- */
export const kbApi = {
  stats: () => http.get<KnowledgeStats>('/knowledge/stats'),
  documents: () => http.get<KnowledgeDoc[]>('/knowledge/documents'),
  seed: () => http.post<{ chunks: number; documents: number; embedding: string }>('/knowledge/seed'),
  rebuild: () => http.post<{ chunks: number; documents: number; embedding: string }>('/knowledge/rebuild'),
  reset: () => http.post<{ dropped: string }>('/knowledge/reset'),
  upload: (file: File) => {
    const fd = new FormData()
    fd.append('file', file)
    return http.post<{ chunks: number; documents: number; embedding: string }>('/knowledge/upload', fd, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })
  },
  /** 检索调试：能看到向量分 / BM25 分 / 覆盖率，用来验证混合检索是否生效 */
  search: (q: string, topK = 5, hybrid = true) =>
    http.get<RetrievedChunk[]>('/knowledge/search', { params: { q, top_k: topK, hybrid } }),
}

/* ---------------- 告警 ---------------- */
export const alertApi = {
  rules: () => http.get<AlertRuleItem[]>('/alerts/rules'),
  createRule: (payload: Record<string, unknown>) => http.post<AlertRuleItem>('/alerts/rules', payload),
  patchRule: (id: string, payload: Record<string, unknown>) =>
    http.patch<AlertRuleItem>(`/alerts/rules/${id}`, payload),
  deleteRule: (id: string) => http.delete(`/alerts/rules/${id}`),
  check: (windowDays = 7) => http.post<{ evaluated: number; triggered: number }>('/alerts/check', { params: { window_days: windowDays } }),
  events: (limit = 30) => http.get<AlertEventItem[]>('/alerts/events', { params: { limit } }),
  ack: (id: string) => http.post(`/alerts/events/${id}/ack`),
}
