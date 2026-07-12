import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import {
  checkpointsApi,
  type CheckpointInfo,
  type CheckpointStateResponse,
  type CheckpointSource,
} from '../api/checkpoints'

type SourceFilter = 'all' | CheckpointSource

export const useCheckpointStore = defineStore('checkpoint', () => {
  const checkpoints = ref<CheckpointInfo[]>([])
  /** "db_file/thread_id" 复合 key */
  const selectedKey = ref<string | null>(null)
  const currentDetail = ref<CheckpointStateResponse | null>(null)
  const loading = ref(false)
  const error = ref<string | null>(null)
  const sourceFilter = ref<SourceFilter>('all')

  const filteredCheckpoints = computed(() => {
    if (sourceFilter.value === 'all') return checkpoints.value
    return checkpoints.value.filter((c) => c.source === sourceFilter.value)
  })

  async function fetchCheckpoints() {
    loading.value = true
    error.value = null
    try {
      const source = sourceFilter.value === 'all' ? undefined : sourceFilter.value
      const data = await checkpointsApi.list(source)
      checkpoints.value = data.checkpoints
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'Failed to fetch checkpoints'
    } finally {
      loading.value = false
    }
  }

  async function selectCheckpoint(dbFile: string, threadId: string) {
    selectedKey.value = `${dbFile}/${threadId}`
    currentDetail.value = null
    loading.value = true
    error.value = null
    try {
      currentDetail.value = await checkpointsApi.getDetail(dbFile, threadId)
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'Failed to fetch checkpoint'
    } finally {
      loading.value = false
    }
  }

  function setSourceFilter(source: SourceFilter) {
    sourceFilter.value = source
    fetchCheckpoints()
  }

  return {
    checkpoints,
    selectedKey,
    currentDetail,
    loading,
    error,
    sourceFilter,
    filteredCheckpoints,
    fetchCheckpoints,
    selectCheckpoint,
    setSourceFilter,
  }
})
