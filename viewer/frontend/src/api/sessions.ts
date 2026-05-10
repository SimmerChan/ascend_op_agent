import axios from 'axios'

const api = axios.create({
  baseURL: '/api',
  timeout: 10000,
})

export interface SessionInfo {
  session_id: string
  source: 'acp' | 'cli'
  created_at: number
  entry_count: number
}

export interface TreeNode {
  id: string
  entry: Record<string, unknown>
  children: TreeNode[]
}

export interface SessionDetailResponse {
  session_id: string
  entries: Record<string, unknown>[]
  tree: TreeNode | null
}

export const sessionsApi = {
  list: async (source?: string) => {
    const params = source && source !== 'all' ? { source } : {}
    const response = await api.get('/sessions', { params })
    return response.data
  },

  getTree: async (sessionId: string) => {
    const response = await api.get(`/sessions/${sessionId}/tree`)
    return response.data
  },

  getEntries: async (sessionId: string) => {
    const response = await api.get(`/sessions/${sessionId}/entries`)
    return response.data
  },
}