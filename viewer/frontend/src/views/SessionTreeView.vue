<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ElCard, ElTable, ElTableColumn, ElButton, ElRadioGroup, ElRadioButton, ElEmpty, ElLoading } from 'element-plus'
import { useSessionStore } from '../stores/session'
import TreeNodeComponent from '../components/TreeNode.vue'

const store = useSessionStore()

const activeTab = ref('tree')

onMounted(() => {
  store.fetchSessions()
})

const formatDate = (timestamp: number) => {
  return new Date(timestamp * 1000).toLocaleString()
}

const getRowClass = ({ row }: { row: { source: string } }) => {
  return row.source === 'acp' ? 'acp-row' : 'cli-row'
}
</script>

<template>
  <div class="session-tree-view">
    <!-- Header with source filter -->
    <div class="view-header">
      <h2>Agent Conversation Visualizer</h2>
      <div class="header-controls">
        <ElRadioGroup
          :model-value="store.sourceFilter"
          @update:model-value="store.setSourceFilter"
          size="small"
        >
          <ElRadioButton value="all">全部</ElRadioButton>
          <ElRadioButton value="acp">ACP</ElRadioButton>
          <ElRadioButton value="cli">CLI</ElRadioButton>
        </ElRadioGroup>
        <ElButton size="small" @click="store.fetchSessions">刷新</ElButton>
      </div>
    </div>

    <!-- Split View Layout -->
    <div class="split-view">
      <!-- Left Panel: Session List -->
      <div class="session-list-panel">
        <h3>会话列表</h3>
        <div v-if="store.loading && store.sessions.length === 0" class="loading-state">
          <ElLoading loading />
        </div>
        <ElEmpty v-else-if="store.filteredSessions.length === 0" description="暂无会话" />
        <ElTable
          v-else
          :data="store.filteredSessions"
          highlight-current-row
          @row-click="(row: any) => store.selectSession(row.session_id)"
          :row-class-name="getRowClass"
          style="width: 100%"
        >
          <ElTableColumn prop="session_id" label="Session ID" width="180">
            <template #default="{ row }">
              <span class="session-id">{{ row.session_id.substring(0, 8) }}...</span>
            </template>
          </ElTableColumn>
          <ElTableColumn prop="source" label="来源" width="60">
            <template #default="{ row }">
              <span :class="['source-badge', row.source]">{{ row.source.toUpperCase() }}</span>
            </template>
          </ElTableColumn>
          <ElTableColumn prop="entry_count" label="Entries" width="80" />
          <ElTableColumn prop="created_at" label="创建时间">
            <template #default="{ row }">
              {{ formatDate(row.created_at) }}
            </template>
          </ElTableColumn>
        </ElTable>
      </div>

      <!-- Right Panel: Session Detail -->
      <div class="session-detail-panel">
        <div v-if="!store.selectedSessionId" class="empty-state">
          <ElEmpty description="请选择左侧会话查看详情" />
        </div>
        <div v-else-if="store.loading" class="loading-state">
          <ElLoading loading />
        </div>
        <div v-else-if="store.error" class="error-state">
          <ElEmpty description="加载失败" />
          <p class="error-text">{{ store.error }}</p>
          <ElButton @click="store.selectSession(store.selectedSessionId!)">重试</ElButton>
        </div>
        <div v-else class="detail-content">
          <div class="detail-header">
            <h3>会话详情: {{ store.selectedSessionId?.substring(0, 8) }}...</h3>
          </div>

          <!-- Tree View -->
          <div v-if="activeTab === 'tree' && store.currentTree" class="tree-view">
            <TreeNodeComponent :node="store.currentTree" />
          </div>

          <!-- Entries List View -->
          <div v-else-if="activeTab === 'entries'" class="entries-view">
            <div v-for="entry in store.currentEntries" :key="entry.id" class="entry-item">
              <div class="entry-header">
                <span class="entry-type">{{ entry.type }}</span>
                <span class="entry-time">{{ formatDate(entry.timestamp as number) }}</span>
              </div>
              <pre class="entry-content">{{ JSON.stringify(entry, null, 2) }}</pre>
            </div>
          </div>

          <!-- Tab Switcher -->
          <div class="detail-tabs">
            <ElButton
              :type="activeTab === 'tree' ? 'primary' : 'default'"
              size="small"
              @click="activeTab = 'tree'"
            >
              树形视图
            </ElButton>
            <ElButton
              :type="activeTab === 'entries' ? 'primary' : 'default'"
              size="small"
              @click="activeTab = 'entries'"
            >
              扁平 Entries
            </ElButton>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.session-tree-view {
  height: 100vh;
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

.session-list-panel {
  width: 400px;
  background: #fff;
  border-right: 1px solid #e4e7ed;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.session-list-panel h3 {
  padding: 12px 16px;
  margin: 0;
  font-size: 14px;
  font-weight: 600;
  border-bottom: 1px solid #e4e7ed;
}

.session-list-panel .el-table {
  flex: 1;
}

.session-id {
  font-family: monospace;
  font-size: 12px;
}

.source-badge {
  padding: 2px 6px;
  border-radius: 3px;
  font-size: 10px;
  font-weight: 600;
}

.source-badge.acp {
  background: #ecf5ff;
  color: #409eff;
}

.source-badge.cli {
  background: #f0f9eb;
  color: #67c23a;
}

.session-detail-panel {
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

.detail-tabs {
  padding: 8px 16px;
  border-top: 1px solid #e4e7ed;
  display: flex;
  gap: 8px;
}

.tree-view,
.entries-view {
  flex: 1;
  overflow-y: auto;
  padding: 16px;
}

.entry-item {
  margin-bottom: 12px;
  padding: 12px;
  background: #f5f7fa;
  border-radius: 4px;
}

.entry-header {
  display: flex;
  justify-content: space-between;
  margin-bottom: 8px;
}

.entry-type {
  font-weight: 600;
  color: #409eff;
}

.entry-time {
  color: #909399;
  font-size: 12px;
}

.entry-content {
  font-size: 12px;
  white-space: pre-wrap;
  overflow-x: auto;
}
</style>