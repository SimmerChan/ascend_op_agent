import axios from 'axios'

const api = axios.create({
  baseURL: '/api',
  timeout: 10000,
})

/** checkpoint 来源:production=op: 生产路径, spike/e2e=脚本, other=未知 */
export type CheckpointSource = 'production' | 'spike' | 'e2e' | 'other'

export interface CheckpointInfo {
  db_file: string
  thread_id: string
  current_phase: string | null
  status: string
  updated_at: string
  source: CheckpointSource
}

export interface CheckpointStateResponse {
  db_file: string
  thread_id: string
  current_phase: string | null
  status: string | null
  updated_at: string | null
  source: CheckpointSource
  op_info: Record<string, unknown> | null
  messages: Record<string, unknown>[]
  code_result: Record<string, unknown> | null
  compile_result: Record<string, unknown> | null
  precision_report: Record<string, unknown> | null
  phase_history: unknown[]
  skill_loads: Record<string, unknown> | null
  pending_confirmation: Record<string, unknown> | null
}

export const checkpointsApi = {
  list: async (source?: string) => {
    const params = source && source !== 'all' ? { source } : {}
    const response = await api.get('/checkpoints', { params })
    return response.data as { checkpoints: CheckpointInfo[]; total: number }
  },

  getDetail: async (dbFile: string, threadId: string) => {
    // dbFile 含 '.',threadId 是 hex,均不含 '/',无需 encode
    const response = await api.get(`/checkpoints/${dbFile}/${threadId}`)
    return response.data as CheckpointStateResponse
  },
}
