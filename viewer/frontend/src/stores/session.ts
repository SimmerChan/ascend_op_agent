import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { sessionsApi, type SessionInfo, type TreeNode } from '../api/sessions'

export const useSessionStore = defineStore('session', () => {
  const sessions = ref<SessionInfo[]>([])
  const selectedSessionId = ref<string | null>(null)
  const currentTree = ref<TreeNode | null>(null)
  const currentEntries = ref<Record<string, unknown>[]>([])
  const loading = ref(false)
  const error = ref<string | null>(null)
  const sourceFilter = ref<'all' | 'acp' | 'cli'>('all')

  const filteredSessions = computed(() => {
    if (sourceFilter.value === 'all') return sessions.value
    return sessions.value.filter(s => s.source === sourceFilter.value)
  })

  async function fetchSessions() {
    loading.value = true
    error.value = null
    try {
      const source = sourceFilter.value === 'all' ? undefined : sourceFilter.value
      const data = await sessionsApi.list(source)
      sessions.value = data.sessions
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'Failed to fetch sessions'
    } finally {
      loading.value = false
    }
  }

  async function selectSession(sessionId: string) {
    selectedSessionId.value = sessionId
    loading.value = true
    error.value = null
    try {
      const data = await sessionsApi.getTree(sessionId)
      currentTree.value = data.tree
      currentEntries.value = data.entries
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'Failed to fetch session'
    } finally {
      loading.value = false
    }
  }

  function setSourceFilter(source: 'all' | 'acp' | 'cli') {
    sourceFilter.value = source
    fetchSessions()
  }

  return {
    sessions,
    selectedSessionId,
    currentTree,
    currentEntries,
    loading,
    error,
    sourceFilter,
    filteredSessions,
    fetchSessions,
    selectSession,
    setSourceFilter,
  }
})