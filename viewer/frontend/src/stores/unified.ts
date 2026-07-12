import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import {
  unifiedApi,
  type UnifiedSessionInfo,
  type UnifiedSource,
} from '../api/unified'
import { useSessionStore } from './session'
import { useCheckpointStore } from './checkpoint'

type SourceFilter = 'all' | UnifiedSource

export const useUnifiedStore = defineStore('unified', () => {
  const sessions = ref<UnifiedSessionInfo[]>([])
  const selectedId = ref<string | null>(null)
  /** 详情类型:chat -> entry tree,workflow -> CheckpointDetail */
  const selectedType = ref<'chat' | 'workflow' | null>(null)
  const loading = ref(false)
  const error = ref<string | null>(null)
  const sourceFilter = ref<SourceFilter>('all')

  const filteredSessions = computed(() => {
    if (sourceFilter.value === 'all') return sessions.value
    return sessions.value.filter((s) => s.source === sourceFilter.value)
  })

  async function fetchSessions() {
    loading.value = true
    error.value = null
    try {
      const source = sourceFilter.value === 'all' ? undefined : sourceFilter.value
      const data = await unifiedApi.list(source)
      sessions.value = data.sessions
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'Failed to fetch sessions'
    } finally {
      loading.value = false
    }
  }

  /** 按 source 路由详情:chat -> sessionStore(getTree),工作流 -> checkpointStore(getDetail) */
  async function selectSession(row: UnifiedSessionInfo) {
    selectedId.value = row.id
    selectedType.value = null
    loading.value = true
    error.value = null
    try {
      if (row.source === 'chat') {
        const ss = useSessionStore()
        await ss.selectSession(row.id)
        selectedType.value = 'chat'
      } else {
        const cs = useCheckpointStore()
        await cs.selectCheckpoint(row.db_file || '', row.id)
        selectedType.value = 'workflow'
      }
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'Failed to fetch detail'
    } finally {
      loading.value = false
    }
  }

  function setSourceFilter(s: SourceFilter) {
    sourceFilter.value = s
    fetchSessions()
  }

  return {
    sessions,
    selectedId,
    selectedType,
    loading,
    error,
    sourceFilter,
    filteredSessions,
    fetchSessions,
    selectSession,
    setSourceFilter,
  }
})
