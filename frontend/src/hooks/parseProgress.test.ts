// U8: parseProgress 单测(用 Node 22 内置 --test + --experimental-strip-types)
//
// 覆盖 discriminator 路由:
// - payload.event='skill_usage' → kind='skill_usage'
// - 其他 → kind='phase' + stage/tool_name/error_* 字段透传
// - 字段缺失的容错(全 undefined、stage 不在 KNOWN_STAGES 等)
// - 字段位置兼容(payload.* vs params.*,后端 U6 实际发 payload.* 包一层)

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { parseProgressNotification } from './parseProgress.ts';

test('payload.event=skill_usage → kind=skill_usage', () => {
  const parsed = parseProgressNotification({
    phase: 'design',
    payload: {
      event: 'skill_usage',
      phase: 'design',
      thread_id: 't-abc',
      skill_names: ['cuda2ascend-simt', 'npu-arch'],
      used_skills: ['cuda2ascend-simt'],
    },
  });
  assert.equal(parsed.kind, 'skill_usage');
  if (parsed.kind === 'skill_usage') {
    assert.equal(parsed.phase, 'design');
    assert.equal(parsed.thread_id, 't-abc');
    assert.deepEqual(parsed.skill_names, ['cuda2ascend-simt', 'npu-arch']);
    assert.deepEqual(parsed.used_skills, ['cuda2ascend-simt']);
  }
});

test('payload.stage=completed → kind=phase, stage=completed', () => {
  const parsed = parseProgressNotification({
    phase: 'compile',
    payload: { stage: 'completed', event: 'completed' },
  });
  assert.equal(parsed.kind, 'phase');
  if (parsed.kind === 'phase') {
    assert.equal(parsed.stage, 'completed');
    assert.equal(parsed.event, 'completed');
  }
});

test('tool_executing + tool_name + error_message 透传', () => {
  const parsed = parseProgressNotification({
    payload: {
      stage: 'tool_executing',
      tool_name: 'file_write',
      error_code: 'EACCES',
      error_message: 'permission denied',
    },
  });
  assert.equal(parsed.kind, 'phase');
  if (parsed.kind === 'phase') {
    assert.equal(parsed.stage, 'tool_executing');
    assert.equal(parsed.tool_name, 'file_write');
    assert.equal(parsed.error_code, 'EACCES');
    assert.equal(parsed.error_message, 'permission denied');
  }
});

test('stage 不在 KNOWN_STAGES → fallback thinking(不抛)', () => {
  const parsed = parseProgressNotification({
    payload: { stage: 'totally_made_up' },
  });
  assert.equal(parsed.kind, 'phase');
  if (parsed.kind === 'phase') {
    assert.equal(parsed.stage, 'thinking');
  }
});

test('payload 完全为空 → kind=phase, stage=thinking', () => {
  const parsed = parseProgressNotification({});
  assert.equal(parsed.kind, 'phase');
  if (parsed.kind === 'phase') {
    assert.equal(parsed.stage, 'thinking');
  }
});

test('params 直接在顶层(老后端兼容)→ payload.* 优先', () => {
  // 老格式(无 payload 包):stage 直接在 params 上
  const parsed = parseProgressNotification({
    stage: 'tool_executing',
    tool_name: 'shell_exec',
  });
  assert.equal(parsed.kind, 'phase');
  if (parsed.kind === 'phase') {
    assert.equal(parsed.stage, 'tool_executing');
    assert.equal(parsed.tool_name, 'shell_exec');
  }
});

test('skill_usage with missing arrays → empty arrays(不抛)', () => {
  const parsed = parseProgressNotification({
    payload: { event: 'skill_usage', phase: 'codegen' },
  });
  assert.equal(parsed.kind, 'skill_usage');
  if (parsed.kind === 'skill_usage') {
    assert.deepEqual(parsed.skill_names, []);
    assert.deepEqual(parsed.used_skills, []);
  }
});

test('skill_usage 字段非 string 数组 → 过滤掉(只留 string)', () => {
  const parsed = parseProgressNotification({
    payload: {
      event: 'skill_usage',
      skill_names: ['ok', 123, null, 'also_ok'],
      used_skills: [{}, 'used1'],
    },
  });
  assert.equal(parsed.kind, 'skill_usage');
  if (parsed.kind === 'skill_usage') {
    assert.deepEqual(parsed.skill_names, ['ok', 'also_ok']);
    assert.deepEqual(parsed.used_skills, ['used1']);
  }
});
