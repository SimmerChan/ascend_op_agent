<script setup lang="ts">
import { computed } from 'vue'

interface TreeNode {
  id: string
  entry: Record<string, unknown>
  children: TreeNode[]
}

const props = defineProps<{
  node: TreeNode
}>()

const entry = computed(() => props.node.entry)

const typeIcon = computed(() => {
  switch (entry.value.type) {
    case 'user': return '👤'
    case 'assistant': return '🤖'
    case 'system': return '⚙️'
    case 'tool': return '🔧'
    default: return '📄'
  }
})

const typeLabel = computed(() => {
  switch (entry.value.type) {
    case 'user': return 'User'
    case 'assistant': return 'LLM'
    case 'system': return 'System'
    case 'tool': return 'Tool'
    default: return 'Unknown'
  }
})

const truncatedContent = computed(() => {
  const content = entry.value.output_content || entry.value.content || ''
  const maxLen = 500
  if (content.length <= maxLen) return content
  return content.substring(0, maxLen) + '...'
})

const hasMore = computed(() => {
  const content = entry.value.output_content || entry.value.content || ''
  return content.length > 500
})

const toolName = computed(() => entry.value.tool_name as string || '')
const toolSuccess = computed(() => entry.value.success as boolean ?? true)
const toolError = computed(() => entry.value.error as string | null)

const formatTime = (ts: number) => {
  return new Date(ts * 1000).toLocaleTimeString()
}
</script>

<template>
  <div class="tree-node">
    <div class="node-header">
      <span class="node-icon">{{ typeIcon }}</span>
      <span class="node-type">{{ typeLabel }}</span>
      <span class="node-time">{{ formatTime(entry.timestamp as number) }}</span>
    </div>

    <div class="node-content">
      <!-- LLM Entry -->
      <template v-if="entry.type === 'assistant'">
        <div class="entry-section">
          <div class="section-label">输入消息 ({{ (entry.input_messages as unknown[])?.length || 0 }})</div>
        </div>
        <div class="entry-section">
          <div class="section-label">输出内容</div>
          <div class="content-text">{{ truncatedContent }}</div>
          <button v-if="hasMore" class="expand-btn">展开全部</button>
        </div>
        <div v-if="(entry.tool_calls as unknown[])?.length > 0" class="entry-section">
          <div class="section-label">工具调用 ({{ (entry.tool_calls as unknown[]).length }})</div>
        </div>
      </template>

      <!-- Tool Entry -->
      <template v-if="entry.type === 'tool'">
        <div class="entry-section">
          <div class="tool-name" :class="{ 'tool-error': !toolSuccess }">
            {{ toolName }}
          </div>
        </div>
        <div v-if="toolError" class="error-message">{{ toolError }}</div>
        <div v-else class="entry-section">
          <div class="section-label">结果</div>
          <div class="content-text">{{ truncatedContent }}</div>
        </div>
      </template>

      <!-- User/System Entry -->
      <template v-if="entry.type === 'user' || entry.type === 'system'">
        <div class="content-text">{{ truncatedContent }}</div>
        <button v-if="hasMore" class="expand-btn">展开全部</button>
      </template>
    </div>
  </div>
</template>

<style scoped>
.tree-node {
  padding: 8px;
  border: 1px solid #e4e7ed;
  border-radius: 4px;
  margin-bottom: 8px;
  background: #fafafa;
}

.node-header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
}

.node-icon {
  font-size: 16px;
}

.node-type {
  font-weight: 600;
  color: #409eff;
}

.node-time {
  color: #909399;
  font-size: 12px;
  margin-left: auto;
}

.node-content {
  padding-left: 24px;
}

.entry-section {
  margin-bottom: 8px;
}

.section-label {
  font-size: 12px;
  color: #909399;
  margin-bottom: 4px;
}

.content-text {
  font-size: 13px;
  color: #303133;
  white-space: pre-wrap;
  word-break: break-all;
}

.tool-name {
  font-weight: 600;
  color: #67c23a;
}

.tool-name.tool-error {
  color: #f56c6c;
}

.error-message {
  color: #f56c6c;
  font-size: 13px;
}

.expand-btn {
  background: none;
  border: none;
  color: #409eff;
  cursor: pointer;
  font-size: 12px;
  padding: 4px 0;
}
</style>