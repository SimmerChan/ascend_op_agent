<script setup lang="ts">
import { computed, ref, defineOptions } from 'vue'

// Enable recursive component
defineOptions({ name: 'TreeNode' })

interface TreeNode {
  id: string
  entry: Record<string, unknown>
  children: TreeNode[]
}

const props = defineProps<{
  node: TreeNode
  depth?: number
}>()

const depth = computed(() => props.depth ?? 0)
const entry = computed(() => props.node.entry)

// 控制 input_messages 展开/收起
const showInputMessages = ref(false)

const inputMessages = computed(() => entry.value.input_messages as Array<{role: string, content: string}> || [])
const hasInputMessages = computed(() => inputMessages.value.length > 0)

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

const typeIcon = computed(() => {
  switch (entry.value.type) {
    case 'turn': return '📝'
    case 'user': return '👤'
    case 'assistant': return '🤖'
    case 'system': return '⚙️'
    case 'tool': return '🔧'
    default: return '📄'
  }
})

const typeLabel = computed(() => {
  switch (entry.value.type) {
    case 'turn': return '会话'
    case 'user': return 'User'
    case 'assistant': return 'LLM'
    case 'system': return 'System'
    case 'tool': return 'Tool'
    default: return 'Unknown'
  }
})
</script>

<template>
  <div class="tree-node" :style="{ paddingLeft: `${depth * 24}px` }">
    <div class="node-header">
      <span class="node-icon">{{ typeIcon }}</span>
      <span class="node-type">{{ typeLabel }}</span>
      <span class="node-time">{{ formatTime(entry.timestamp as number) }}</span>
    </div>

    <div class="node-content">
      <!-- LLM Entry -->
      <template v-if="entry.type === 'assistant'">
        <!-- 输入消息可折叠区域 -->
        <div class="entry-section">
          <div class="section-label" style="display: flex; align-items: center; gap: 8px;">
            <span>输入消息 ({{ inputMessages.length }})</span>
            <button
              v-if="hasInputMessages"
              class="toggle-btn"
              @click="showInputMessages = !showInputMessages"
            >
              {{ showInputMessages ? '收起' : '展开' }}
            </button>
          </div>
          <!-- 折叠时显示简要信息 -->
          <div v-if="!showInputMessages && hasInputMessages" class="messages-preview">
            <div v-for="(msg, idx) in inputMessages.slice(0, 2)" :key="idx" class="message-brief">
              <span class="msg-role">{{ msg.role }}:</span>
              <span class="msg-content">{{ msg.content.substring(0, 80) }}{{ msg.content.length > 80 ? '...' : '' }}</span>
            </div>
            <div v-if="inputMessages.length > 2" class="messages-more">
              还有 {{ inputMessages.length - 2 }} 条消息...
            </div>
          </div>
          <!-- 展开时显示完整消息列表 -->
          <div v-if="showInputMessages" class="messages-list">
            <div v-for="(msg, idx) in inputMessages" :key="idx" class="message-item">
              <span class="msg-role">{{ msg.role }}</span>
              <pre class="msg-content">{{ msg.content }}</pre>
            </div>
          </div>
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

    <!-- Recursively render children -->
    <TreeNode
      v-for="child in node.children"
      :key="child.id"
      :node="child"
      :depth="depth + 1"
    />
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

.toggle-btn {
  background: #ecf5ff;
  border: 1px solid #409eff;
  color: #409eff;
  cursor: pointer;
  font-size: 11px;
  padding: 2px 8px;
  border-radius: 3px;
}

.toggle-btn:hover {
  background: #409eff;
  color: white;
}

.messages-preview {
  padding: 8px;
  background: #f5f7fa;
  border-radius: 4px;
  margin-top: 4px;
}

.messages-list {
  padding: 8px;
  background: #f5f7fa;
  border-radius: 4px;
  margin-top: 4px;
}

.message-brief {
  font-size: 12px;
  margin-bottom: 4px;
}

.messages-more {
  font-size: 12px;
  color: #909399;
  font-style: italic;
}

.msg-role {
  font-weight: 600;
  color: #606266;
  margin-right: 8px;
}

.msg-content {
  color: #303133;
  margin: 0;
  white-space: pre-wrap;
  word-break: break-all;
}

.message-item {
  margin-bottom: 8px;
}

.message-item .msg-content {
  background: white;
  padding: 4px 8px;
  border-radius: 3px;
  margin-top: 4px;
  font-size: 12px;
}
</style>