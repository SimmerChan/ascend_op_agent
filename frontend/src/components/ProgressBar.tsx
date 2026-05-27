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

interface ProgressBarProps {
  label?: string;
  stage?: 'thinking' | 'tool_executing' | 'completed' | 'waiting';
  tool_name?: string;
  error_message?: string;
}

export const ProgressBar: React.FC<ProgressBarProps> = ({ label, stage, tool_name, error_message }) => {
  const getStageText = () => {
    if (stage === 'thinking') {
      return '思考中...';
    } else if (stage === 'tool_executing' && error_message) {
      return `⚠️ ${tool_name || 'tool'} - ${error_message}`;
    } else if (stage === 'tool_executing' && tool_name) {
      return `正在执行 ${tool_name}...`;
    } else if (stage === 'tool_executing') {
      return '执行中...';
    } else if (stage === 'waiting') {
      return '等待响应...';
    } else if (stage === 'completed') {
      return '完成';
    }
    return '';
  };

  return (
    <Box flexDirection="column">
      {label && <Text>{label}</Text>}
      <Text dimColor>{getStageText()}</Text>
    </Box>
  );
};