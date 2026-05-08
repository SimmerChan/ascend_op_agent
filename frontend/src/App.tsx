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
import TextInput from 'ink-text-input';

type AppState = 'idle' | 'running' | 'waiting_confirm' | 'completed' | 'error';

interface ConfirmData {
  title: string;
  message?: string;
  options: string[];
}

export const App: React.FC = () => {
  const [state, setState] = useState<AppState>('idle');
  const [input, setInput] = useState('');
  const [progress, setProgress] = useState({ phase: 0, percent: 0 });
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
      const p = lastResponse.params as { phase?: number; percent?: number } | undefined;
      setProgress({
        phase: p?.phase ?? 0,
        percent: p?.percent ?? 0
      });
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
    setProgress({ phase: 0, percent: 0 });
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
        <ProgressBar percent={progress.percent} phase={progress.phase} />
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