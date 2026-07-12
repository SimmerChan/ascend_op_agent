import axios from 'axios'

const api = axios.create({
  baseURL: '/api',
  timeout: 10000,
})

/** 统一会话来源:chat=聊天,production=op: 生产,spike/e2e=脚本,migration=模型迁移 */
export type UnifiedSource = 'chat' | 'production' | 'spike' | 'e2e' | 'migration' | 'other'

export interface UnifiedSessionInfo {
  id: string // session_id(聊天)或 thread_id(工作流)
  source: UnifiedSource
  label: string
  updated_at: number
  updated_at_display: string
  phase: string | null
  status: string | null
  db_file: string | null // 工作流详情查 checkpoint 用
  entry_count: number
}

export const unifiedApi = {
  list: async (source?: string) => {
    const params = source && source !== 'all' ? { source } : {}
    const response = await api.get('/unified-sessions', { params })
    return response.data as { sessions: UnifiedSessionInfo[]; total: number }
  },
}
