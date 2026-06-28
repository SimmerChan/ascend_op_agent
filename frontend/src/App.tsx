// Copyright 2026 SimmerChan
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
// http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

import React, { useState, useEffect } from 'react';
import { Box, Text } from 'ink';
import { useRPC } from './hooks/useRPC';
import { Dialog } from './components/Dialog';
import { ProgressBar } from './components/ProgressBar';
import { MessageList } from './components/MessageList';
import { StatusBar } from './components/StatusBar';
import { Spacer } from './components/Spacer';
import { parseProgressNotification } from './hooks/parseProgress';
import TextInput from 'ink-text-input';

type AppState = 'idle' | 'running' | 'waiting_confirm' | 'completed' | 'error';

interface ProgressState {
  stage: 'thinking' | 'tool_executing' | 'completed' | 'waiting';
  tool_name?: string;
  error_code?: string;
  error_message?: string;
}

// U8: skill 跟踪(cannbot skill 加载/使用记录)前端 state
interface SkillLoad {
  phase: string;
  skill_names: string[];
  used_skills: string[];
}

interface ConfirmData {
  title: string;
  message?: string;
  options: string[];
}

export const App: React.FC = () => {
  const [state, setState] = useState<AppState>('idle');
  const [input, setInput] = useState('');
  const [progress, setProgress] = useState<ProgressState>({ stage: 'thinking' });
  const [skillLoads, setSkillLoads] = useState<SkillLoad[]>([]);
  const [messages, setMessages] = useState<string[]>([]);
  const [confirmData, setConfirmData] = useState<ConfirmData | null>(null);

  const { send, lastResponse, isConnected, reset } = useRPC();

  // 处理后端响应
  useEffect(() => {
    if (!lastResponse) return;

    // 处理通知消息
    if (lastResponse.method === 'agent.thinking') {
      const msg = lastResponse.params?.message as string;
      setMessages(prev => [...prev, `[${new Date().toLocaleTimeString()}] ${msg}`]);
    } else if (lastResponse.method === 'agent.progress') {
      // U8: discriminated 解析(skill.usage 路由到 SkillLoad,其他到 ProgressBar)
      const parsed = parseProgressNotification(
        lastResponse.params as Record<string, unknown>
      );
      if (parsed.kind === 'skill_usage') {
        setSkillLoads(prev => {
          // 合并同 phase 的累积记录(后端可能分多次推 skill.usage)
          const existing = prev.find(s => s.phase === parsed.phase);
          if (existing) {
            return prev.map(s =>
              s.phase === parsed.phase
                ? {
                    phase: s.phase,
                    skill_names: parsed.skill_names.length ? parsed.skill_names : s.skill_names,
                    used_skills: parsed.used_skills.length ? parsed.used_skills : s.used_skills,
                  }
                : s
            );
          }
          return [
            ...prev,
            {
              phase: parsed.phase,
              skill_names: parsed.skill_names,
              used_skills: parsed.used_skills,
            },
          ];
        });
      } else {
        setProgress({
          stage: parsed.stage ?? 'thinking',
          tool_name: parsed.tool_name,
          error_code: parsed.error_code,
          error_message: parsed.error_message,
        });
      }
    } else if (lastResponse.method === 'agent.error') {
      const err = lastResponse.params?.message as string;
      setMessages(prev => [...prev, `[ERROR] ${err}`]);
      setState('error');
    }

    // 处理 RPC 响应结果
    if (lastResponse.result) {
      const result = lastResponse.result as { status?: string; response?: string; data?: ConfirmData };
      if (result.status === 'waiting_confirmation' && result.data) {
        setConfirmData(result.data);
        setState('waiting_confirm');
      } else if (result.status === 'reset_completed') {
        // Reset completed, UI will transition to idle via handleNewConversation
      } else if (result.status === 'completed') {
        // 显示 Agent 的回复
        if (result.response) {
          setMessages(prev => [...prev, `[${new Date().toLocaleTimeString()}] Agent: ${result.response}`]);
        }
        setState('completed');
      } else if (result.status === 'error') {
        setState('error');
      }
    }
  }, [lastResponse]);

  const handleSubmit = () => {
    if (!input.trim()) return;
    // Allow submit from idle, completed, or error state
    if (state !== 'idle' && state !== 'completed' && state !== 'error') return;
    setMessages(prev => [...prev, `[${new Date().toLocaleTimeString()}] User: ${input}`]);
    send('agent.run', { user_input: input });
    setState('running');
    // Clear input after submission to prevent stale value showing in completed state
    setInput('');
  };

  const handleConfirm = (choice: string) => {
    send('agent.respond', { choice });
    setConfirmData(null);
    setState('running');
  };

  const handleCancel = () => {
    send('agent.cancel', {});
    setConfirmData(null);
    setState('idle');
  };

  const handleNewConversation = () => {
    // Immediately clear all UI state to prevent stale input display
    setInput('');
    setMessages([]);
    setProgress({ stage: 'thinking' });
    setConfirmData(null);
    setState('idle');
    // Then reset backend
    reset();
  };

  if (!isConnected) {
    return (
      <Box>
        <Text dimColor>正在连接后端...</Text>
      </Box>
    );
  }

  return (
    <Box flexDirection="column" padding={1}>
      <StatusBar state={state} isConnected={isConnected} />
      <Spacer height={1} />
      <MessageList messages={messages} />

      {state === 'idle' && (
        <Box>
          <Text dimColor>请输入需求: </Text>
          <TextInput
            value={input}
            onChange={setInput}
            onSubmit={handleSubmit}
          />
        </Box>
      )}

      {state === 'waiting_confirm' && confirmData && (
        <Dialog
          title={confirmData.title}
          message={confirmData.message}
          options={confirmData.options}
          onSelect={handleConfirm}
          onCancel={handleCancel}
        />
      )}

      {state === 'running' && (
        <ProgressBar stage={progress.stage} tool_name={progress.tool_name} error_message={progress.error_message} />
      )}

      {/* U8: skill 加载/使用 chips(cannbot skill 跟踪,只在非空时显示) */}
      {skillLoads.length > 0 && (
        <Box flexDirection="column" marginTop={1}>
          <Text dimColor>skills (cannbot)</Text>
          {skillLoads.map(s => (
            <Box key={s.phase} flexDirection="column" marginLeft={2}>
              <Text>
                <Text color="cyan">[{s.phase}]</Text> loaded: {s.skill_names.join(', ') || '(none)'}
              </Text>
              {s.used_skills.length > 0 && (
                <Text dimColor>used: {s.used_skills.join(', ')}</Text>
              )}
            </Box>
          ))}
        </Box>
      )}

      {state === 'completed' && (
        <Box flexDirection="column">
          <Text bold color="green">对话完成</Text>
          <Text dimColor>输入新需求继续，或按 Ctrl+C 退出</Text>
          <Spacer height={1} />
          <Box>
            <Text dimColor>请输入需求: </Text>
            <TextInput
              value={input}
              onChange={setInput}
              onSubmit={handleSubmit}
            />
          </Box>
        </Box>
      )}

      {state === 'error' && (
        <Box flexDirection="column">
          <Text bold color="red">发生错误</Text>
          <Text dimColor>输入新需求继续，或按 Ctrl+C 退出</Text>
          <Spacer height={1} />
          <Box>
            <Text dimColor>请输入需求: </Text>
            <TextInput
              value={input}
              onChange={setInput}
              onSubmit={handleSubmit}
            />
          </Box>
        </Box>
      )}
    </Box>
  );
};