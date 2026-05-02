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
  percent: number; // 0-100
  phase?: number;
}

export const ProgressBar: React.FC<ProgressBarProps> = ({ label, percent, phase }) => {
  const filled = Math.floor(percent / 2.5);
  const empty = 40 - filled;
  const bar = '█'.repeat(filled) + '░'.repeat(empty);
  return (
    <Box flexDirection="column">
      {label && <Text>{label}</Text>}
      {phase !== undefined && <Text dimColor>Phase {phase}</Text>}
      <Text>[{bar}] {percent}%</Text>
    </Box>
  );
};