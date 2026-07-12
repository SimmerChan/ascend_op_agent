<script setup lang="ts">
import { computed } from 'vue'
import {
  ElCollapse,
  ElCollapseItem,
  ElTag,
  ElEmpty,
  ElDescriptions,
  ElDescriptionsItem,
} from 'element-plus'
import type { CheckpointStateResponse } from '../api/checkpoints'

const props = defineProps<{ detail: CheckpointStateResponse }>()

interface CodeFile {
  path?: string
  content?: string
  dir?: string
}

const codeFiles = computed<CodeFile[]>(() => {
  const cr = props.detail.code_result as { files?: CodeFile[] } | null
  return cr?.files ?? []
})
const messages = computed(() => props.detail.messages ?? [])
const compile = computed(() => props.detail.compile_result)
const precision = computed(() => props.detail.precision_report)
const phaseHistory = computed<unknown[]>(() => props.detail.phase_history ?? [])

const statusType = (s: string | null | undefined) => {
  if (!s) return 'info' as const
  if (['done', 'success', 'passed'].includes(s)) return 'success' as const
  if (['failed', 'error'].includes(s)) return 'danger' as const
  if (['running', 'waiting_confirm'].includes(s)) return 'warning' as const
  return 'info' as const
}

const roleClass = (role: unknown) => `msg-${String(role ?? 'unknown').toLowerCase()}`

const messageContent = (m: Record<string, unknown>) => {
  const c = m.content
  if (typeof c === 'string') return c
  return JSON.stringify(c, null, 2)
}

const formatTime = (iso: string | null) => (iso ? new Date(iso).toLocaleString() : '—')
</script>

<template>
  <div class="checkpoint-detail">
    <!-- 元数据头 -->
    <div class="cp-header">
      <h3>
        <code>{{ detail.thread_id }}</code>
        <ElTag size="small" :type="statusType(detail.status)">{{ detail.status ?? '—' }}</ElTag>
        <ElTag size="small" type="info">{{ detail.source }}</ElTag>
      </h3>
      <ElDescriptions :column="2" border size="small">
        <ElDescriptionsItem label="current_phase">{{ detail.current_phase ?? '—' }}</ElDescriptionsItem>
        <ElDescriptionsItem label="updated">{{ formatTime(detail.updated_at) }}</ElDescriptionsItem>
        <ElDescriptionsItem label="db_file" :span="2"><code>{{ detail.db_file }}</code></ElDescriptionsItem>
        <ElDescriptionsItem v-if="detail.op_info" label="op_info" :span="2">
          <pre class="inline-pre">{{ JSON.stringify(detail.op_info, null, 2) }}</pre>
        </ElDescriptionsItem>
      </ElDescriptions>
    </div>

    <!-- 折叠面板:默认展开 messages/compile/code -->
    <ElCollapse :model-value="['messages', 'compile', 'code']">
      <ElCollapseItem name="messages">
        <template #title>
          <span class="section-title">对话历史 (messages)</span>
          <ElTag size="small">{{ messages.length }}</ElTag>
        </template>
        <ElEmpty v-if="messages.length === 0" description="无 messages" :image-size="60" />
        <div v-else class="msg-list">
          <div v-for="(m, i) in messages" :key="i" :class="['msg-item', roleClass(m.role)]">
            <div class="msg-role">{{ String(m.role ?? 'unknown') }} <span class="msg-idx">#{{ i }}</span></div>
            <pre class="msg-content">{{ messageContent(m) }}</pre>
          </div>
        </div>
      </ElCollapseItem>

      <ElCollapseItem name="compile">
        <template #title>
          <span class="section-title">编译 (compile_result)</span>
          <ElTag
            v-if="compile"
            size="small"
            :type="compile.success === true ? 'success' : 'danger'"
          >{{ compile.success === true ? 'success' : 'failed' }}</ElTag>
          <ElTag v-else size="small" type="info">未编译</ElTag>
        </template>
        <ElEmpty v-if="!compile" description="未到编译阶段或无数据" :image-size="60" />
        <div v-else class="compile-block">
          <div v-if="compile.stderr" class="stderr-block">
            <div class="block-label">stderr</div>
            <pre class="stderr">{{ String(compile.stderr) }}</pre>
          </div>
          <div v-if="compile.stdout" class="stdout-block">
            <div class="block-label stdout-label">stdout (前 2000 字符)</div>
            <pre class="stdout">{{ String(compile.stdout).slice(0, 2000) }}</pre>
          </div>
        </div>
      </ElCollapseItem>

      <ElCollapseItem name="code">
        <template #title>
          <span class="section-title">产出代码 (code_result)</span>
          <ElTag size="small">{{ codeFiles.length }} 文件</ElTag>
        </template>
        <ElEmpty v-if="codeFiles.length === 0" description="无代码产物(codegen 阶段后才有)" :image-size="60" />
        <div v-else class="code-list">
          <div v-for="(f, i) in codeFiles" :key="i" class="code-file">
            <div class="code-path"><code>{{ f.path || f.dir || `file-${i}` }}</code></div>
            <pre class="code-content">{{ f.content ?? '(无 content)' }}</pre>
          </div>
        </div>
      </ElCollapseItem>

      <ElCollapseItem name="precision">
        <template #title>
          <span class="section-title">精度 (precision_report)</span>
          <ElTag
            v-if="precision"
            size="small"
            :type="precision.success === true ? 'success' : 'danger'"
          >{{ precision.passed_cases ?? 0 }}/{{ precision.total_cases ?? 0 }}</ElTag>
        </template>
        <ElEmpty v-if="!precision" description="未到精度阶段或无数据" :image-size="60" />
        <pre v-else class="json-pre">{{ JSON.stringify(precision, null, 2) }}</pre>
      </ElCollapseItem>

      <ElCollapseItem name="phases">
        <template #title>
          <span class="section-title">阶段轨迹 (phase_history)</span>
          <ElTag size="small">{{ phaseHistory.length }}</ElTag>
        </template>
        <pre v-if="phaseHistory.length" class="json-pre">{{ JSON.stringify(phaseHistory, null, 2) }}</pre>
        <ElEmpty v-else description="无" :image-size="60" />
      </ElCollapseItem>
    </ElCollapse>
  </div>
</template>

<style scoped>
.checkpoint-detail {
  padding: 16px;
  overflow-y: auto;
  height: 100%;
}
.cp-header {
  margin-bottom: 16px;
}
.cp-header h3 {
  margin: 0 0 12px;
  font-size: 15px;
  display: flex;
  align-items: center;
  gap: 8px;
}
.inline-pre {
  font-size: 12px;
  margin: 0;
  white-space: pre-wrap;
}
.section-title {
  font-weight: 600;
  margin-right: 8px;
}
.msg-list,
.code-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.msg-item {
  padding: 8px 12px;
  border-left: 3px solid #dcdfe6;
  background: #f5f7fa;
  border-radius: 0 4px 4px 0;
}
.msg-item.msg-user {
  border-left-color: #409eff;
}
.msg-item.msg-assistant {
  border-left-color: #67c23a;
}
.msg-item.msg-system {
  border-left-color: #909399;
}
.msg-item.msg-tool {
  border-left-color: #e6a23c;
}
.msg-role {
  font-size: 12px;
  font-weight: 600;
  color: #606266;
  margin-bottom: 4px;
}
.msg-idx {
  color: #909399;
  font-weight: 400;
}
.msg-content {
  font-size: 12px;
  white-space: pre-wrap;
  word-break: break-word;
  max-height: 400px;
  overflow-y: auto;
  margin: 0;
}
.stderr-block {
  margin-bottom: 12px;
}
.block-label {
  font-size: 12px;
  font-weight: 600;
  color: #f56c6c;
  margin-bottom: 4px;
}
.stdout-label {
  color: #909399;
}
.stderr {
  font-size: 12px;
  white-space: pre-wrap;
  background: #fef0f0;
  color: #f56c6c;
  padding: 8px;
  border-radius: 4px;
  max-height: 400px;
  overflow-y: auto;
  margin: 0;
}
.stdout {
  font-size: 12px;
  white-space: pre-wrap;
  background: #f5f7fa;
  padding: 8px;
  border-radius: 4px;
  max-height: 300px;
  overflow-y: auto;
  margin: 0;
}
.code-file {
  margin-bottom: 12px;
}
.code-path {
  font-size: 12px;
  background: #ecf5ff;
  padding: 4px 8px;
  border-radius: 4px 4px 0 0;
}
.code-content {
  font-size: 12px;
  background: #1e1e1e;
  color: #d4d4d4;
  padding: 8px;
  border-radius: 0 0 4px 4px;
  max-height: 500px;
  overflow-y: auto;
  margin: 0;
}
.json-pre {
  font-size: 12px;
  white-space: pre-wrap;
  margin: 0;
}
</style>
