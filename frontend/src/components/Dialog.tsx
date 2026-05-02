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
import { useInput } from 'ink';
import React, { useState } from 'react';

interface DialogProps {
  title: string;
  message?: string;
  options: string[];
  onSelect: (option: string) => void;
  onCancel?: () => void;
}

export const Dialog: React.FC<DialogProps> = ({ title, message, options, onSelect, onCancel }) => {
  const [selectedIndex, setSelectedIndex] = useState(0);

  useInput((input, key) => {
    if (key.upArrow) {
      setSelectedIndex(i => Math.max(0, i - 1));
    } else if (key.downArrow) {
      setSelectedIndex(i => Math.min(options.length - 1, i + 1));
    } else if (key.return) {
      onSelect(options[selectedIndex]);
    } else if (key.escape && onCancel) {
      onCancel();
    }
  });

  return (
    <Box flexDirection="column" borderStyle="round" padding={1}>
      <Text bold>{title}</Text>
      {message && <Text>{message}</Text>}
      {options.map((option, i) => (
        <Text key={option} color={i === selectedIndex ? 'cyan' : undefined}>
          {i === selectedIndex ? '> ' : '  '}{option}
        </Text>
      ))}
      <Text dimColor>↑↓ 选择，Enter 确认，ESC 取消</Text>
    </Box>
  );
};