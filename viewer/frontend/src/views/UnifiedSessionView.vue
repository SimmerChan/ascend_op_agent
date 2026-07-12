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
import { useUnifiedStore } from '../stores/unified'
import { useSessionStore } from '../stores/session'
import { useCheckpointStore } from '../stores/checkpoint'
import TreeNode from '../components/TreeNode.vue'
import CheckpointDetail from '../components/CheckpointDetail.vue'

const store = useUnifiedStore()
const sessionStore = useSessionStore()
const checkpointStore = useCheckpointStore()

onMounted(() => {
  store.fetchSessions()
})

const statusType = (s: string | null) => {
  if (!s) return 'info' as const
  if (['done', 'success'].includes(s)) return 'success' as const
  if (['failed', 'error'].includes(s)) return 'danger' as const
  if (['running', 'waiting_confirm'].includes(s)) return 'warning' as const
  return 'info' as const
}
</script>

<template>
  <div class="unified-view">
    <!-- Header with source filter -->
    <div class="view-header">
      <h2>统一会话列表(聊天 + 工作流任务)</h2>
      <div class="header-controls">
        <ElRadioGroup
          :model-value="store.sourceFilter"
          @update:model-value="store.setSourceFilter"
          size="small"
        >
          <ElRadioButton value="all">全部</ElRadioButton>
          <ElRadioButton value="chat">聊天</ElRadioButton>
          <ElRadioButton value="production">生产 op:</ElRadioButton>
          <ElRadioButton value="spike">spike</ElRadioButton>
          <ElRadioButton value="e2e">e2e</ElRadioButton>
        </ElRadioGroup>
        <ElButton size="small" @click="store.fetchSessions">刷新</ElButton>
      </div>
    </div>

    <!-- Split View -->
    <div class="split-view">
      <!-- Left: 统一会话列表 -->
      <div class="list-panel">
        <h3>会话 ({{ store.filteredSessions.length }})</h3>
        <div v-if="store.loading && store.sessions.length === 0" class="loading-state">
          <ElLoading loading />
        </div>
        <ElEmpty v-else-if="store.filteredSessions.length === 0" description="无会话" />
        <ElTable
          v-else
          :data="store.filteredSessions"
          highlight-current-row
          @row-click="(row: any) => store.selectSession(row)"
          style="width: 100%"
        >
          <ElTableColumn prop="label" label="ID" width="120">
            <template #default="{ row }">
              <code>{{ row.label }}</code>
            </template>
          </ElTableColumn>
          <ElTableColumn prop="source" label="来源" width="90">
            <template #default="{ row }">
              <span :class="['src-badge', `src-${row.source}`]">{{ row.source }}</span>
            </template>
          </ElTableColumn>
          <ElTableColumn prop="phase" label="Phase" width="85">
            <template #default="{ row }">{{ row.phase || '-' }}</template>
          </ElTableColumn>
          <ElTableColumn prop="status" label="Status" width="105">
            <template #default="{ row }">
              <ElTag v-if="row.status" size="small" :type="statusType(row.status)">{{ row.status }}</ElTag>
              <span v-else>-</span>
            </template>
          </ElTableColumn>
          <ElTableColumn prop="updated_at_display" label="更新时间">
            <template #default="{ row }">{{ row.updated_at_display }}</template>
          </ElTableColumn>
        </ElTable>
      </div>

      <!-- Right: 详情(按 source 路由)-->
      <div class="detail-panel">
        <div v-if="!store.selectedId" class="empty-state">
          <ElEmpty description="选择左侧会话查看详情(聊天->对话树,工作流->state)" />
        </div>
        <div v-else-if="store.loading" class="loading-state">
          <ElLoading loading />
        </div>
        <div v-else-if="store.error" class="error-state">
          <ElEmpty description="加载失败" />
          <p class="error-text">{{ store.error }}</p>
        </div>
        <!-- chat -> entry tree -->
        <div
          v-else-if="store.selectedType === 'chat' && sessionStore.currentTree"
          class="detail-content"
        >
          <div class="detail-header">
            <h3>聊天会话: {{ store.selectedId?.substring(0, 8) }}...</h3>
          </div>
          <div class="tree-view">
            <TreeNode :node="sessionStore.currentTree" />
          </div>
        </div>
        <!-- workflow -> CheckpointDetail(state 面板)-->
        <CheckpointDetail
          v-else-if="store.selectedType === 'workflow' && checkpointStore.currentDetail"
          :detail="checkpointStore.currentDetail"
        />
      </div>
    </div>
  </div>
</template>

<style scoped>
.unified-view {
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
.list-panel {
  width: 560px;
  background: #fff;
  border-right: 1px solid #e4e7ed;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
.list-panel h3 {
  padding: 12px 16px;
  margin: 0;
  font-size: 14px;
  font-weight: 600;
  border-bottom: 1px solid #e4e7ed;
}
.list-panel .el-table {
  flex: 1;
}
.detail-panel {
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
.detail-content {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
.detail-header {
  padding: 12px 16px;
  border-bottom: 1px solid #e4e7ed;
}
.detail-header h3 {
  margin: 0;
  font-size: 14px;
  font-weight: 600;
}
.tree-view {
  flex: 1;
  overflow-y: auto;
  padding: 16px;
}
.src-badge {
  padding: 2px 6px;
  border-radius: 3px;
  font-size: 10px;
  font-weight: 600;
}
.src-badge.src-chat {
  background: #f4f4f5;
  color: #909399;
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
