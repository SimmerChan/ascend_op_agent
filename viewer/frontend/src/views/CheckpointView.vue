<script setup lang="ts">
import { onMounted } from 'vue'
import {
  ElTable,
  ElTableColumn,
  ElButton,
  ElRadioGroup,
  ElRadioButton,
  ElEmpty,
  ElLoading,
  ElTag,
} from 'element-plus'
import { useCheckpointStore } from '../stores/checkpoint'
import CheckpointDetail from '../components/CheckpointDetail.vue'

const store = useCheckpointStore()

onMounted(() => {
  store.fetchCheckpoints()
})

const formatTime = (iso: string) => (iso ? new Date(iso).toLocaleString() : '—')

const statusType = (s: string) => {
  if (['done', 'success'].includes(s)) return 'success' as const
  if (['failed', 'error'].includes(s)) return 'danger' as const
  if (['running', 'waiting_confirm'].includes(s)) return 'warning' as const
  return 'info' as const
}
</script>

<template>
  <div class="checkpoint-view">
    <!-- Header with source filter -->
    <div class="view-header">
      <h2>算子开发检查点 (CheckpointStore)</h2>
      <div class="header-controls">
        <ElRadioGroup
          :model-value="store.sourceFilter"
          @update:model-value="store.setSourceFilter"
          size="small"
        >
          <ElRadioButton value="all">全部</ElRadioButton>
          <ElRadioButton value="production">生产 op:</ElRadioButton>
          <ElRadioButton value="spike">spike</ElRadioButton>
          <ElRadioButton value="e2e">e2e</ElRadioButton>
        </ElRadioGroup>
        <ElButton size="small" @click="store.fetchCheckpoints">刷新</ElButton>
      </div>
    </div>

    <!-- Split View Layout -->
    <div class="split-view">
      <!-- Left: checkpoint list -->
      <div class="cp-list-panel">
        <h3>检查点列表 ({{ store.filteredCheckpoints.length }})</h3>
        <div v-if="store.loading && store.checkpoints.length === 0" class="loading-state">
          <ElLoading loading />
        </div>
        <ElEmpty v-else-if="store.filteredCheckpoints.length === 0" description="无检查点" />
        <ElTable
          v-else
          :data="store.filteredCheckpoints"
          highlight-current-row
          @row-click="(row: any) => store.selectCheckpoint(row.db_file, row.thread_id)"
          style="width: 100%"
        >
          <ElTableColumn prop="thread_id" label="Thread" width="120">
            <template #default="{ row }">
              <code>{{ String(row.thread_id).substring(0, 12) }}</code>
            </template>
          </ElTableColumn>
          <ElTableColumn prop="source" label="来源" width="80">
            <template #default="{ row }">
              <span :class="['src-badge', `src-${row.source}`]">{{ row.source }}</span>
            </template>
          </ElTableColumn>
          <ElTableColumn prop="current_phase" label="Phase" width="90">
            <template #default="{ row }">{{ row.current_phase ?? '—' }}</template>
          </ElTableColumn>
          <ElTableColumn prop="status" label="Status" width="110">
            <template #default="{ row }">
              <ElTag size="small" :type="statusType(row.status)">{{ row.status }}</ElTag>
            </template>
          </ElTableColumn>
          <ElTableColumn prop="updated_at" label="更新时间">
            <template #default="{ row }">{{ formatTime(row.updated_at) }}</template>
          </ElTableColumn>
        </ElTable>
      </div>

      <!-- Right: detail -->
      <div class="cp-detail-panel">
        <div v-if="!store.currentDetail && !store.loading" class="empty-state">
          <ElEmpty description="选择左侧检查点查看完整 state(messages/代码/编译/精度)" />
        </div>
        <div v-else-if="store.loading" class="loading-state">
          <ElLoading loading />
        </div>
        <div v-else-if="store.error" class="error-state">
          <ElEmpty description="加载失败" />
          <p class="error-text">{{ store.error }}</p>
        </div>
        <CheckpointDetail v-else-if="store.currentDetail" :detail="store.currentDetail" />
      </div>
    </div>
  </div>
</template>

<style scoped>
.checkpoint-view {
  height: 100%;
  display: flex;
  flex-direction: column;
  background: #f5f7fa;
}
.view-header {
  padding: 16px 24px;
  background: #fff;
  border-bottom: 1px solid #e4e7ed;
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.view-header h2 {
  margin: 0;
  font-size: 18px;
  font-weight: 600;
}
.header-controls {
  display: flex;
  gap: 12px;
  align-items: center;
}
.split-view {
  flex: 1;
  display: flex;
  overflow: hidden;
}
.cp-list-panel {
  width: 520px;
  background: #fff;
  border-right: 1px solid #e4e7ed;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
.cp-list-panel h3 {
  padding: 12px 16px;
  margin: 0;
  font-size: 14px;
  font-weight: 600;
  border-bottom: 1px solid #e4e7ed;
}
.cp-list-panel .el-table {
  flex: 1;
}
.cp-detail-panel {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  background: #fff;
}
.empty-state,
.loading-state,
.error-state {
  flex: 1;
  display: flex;
  flex-direction: column;
  justify-content: center;
  align-items: center;
}
.error-text {
  color: #f56c6c;
  margin: 8px 0;
}
.src-badge {
  padding: 2px 6px;
  border-radius: 3px;
  font-size: 10px;
  font-weight: 600;
}
.src-badge.src-production {
  background: #ecf5ff;
  color: #409eff;
}
.src-badge.src-spike {
  background: #fdf6ec;
  color: #e6a23c;
}
.src-badge.src-e2e {
  background: #f0f9eb;
  color: #67c23a;
}
.src-badge.src-other {
  background: #f4f4f5;
  color: #909399;
}
</style>
