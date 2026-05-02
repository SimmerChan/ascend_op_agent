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

import { Box, Text } from 'ink';
import React from 'react';

type AppState = 'idle' | 'running' | 'waiting_confirm' | 'completed' | 'error';

interface StatusBarProps {
  state: AppState;
  isConnected?: boolean;
}

const stateLabels: Record<AppState, string> = {
  idle: '就绪',
  running: '推理中',
  waiting_confirm: '等待确认',
  completed: '完成',
  error: '错误'
};

export const StatusBar: React.FC<StatusBarProps> = ({ state, isConnected = false }) => {
  return (
    <Box>
      <Text dimColor>[</Text>
      <Text color={isConnected ? 'green' : 'red'}>{isConnected ? '已连接' : '未连接'}</Text>
      <Text dimColor>] </Text>
      <Text bold>Ascend Op Agent</Text>
      <Text dimColor> | </Text>
      <Text color="yellow">{stateLabels[state]}</Text>
    </Box>
  );
};