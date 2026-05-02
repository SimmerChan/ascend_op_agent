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

import { useEffect, useRef, useState } from 'react';
import { spawn, ChildProcess } from 'child_process';

export interface BackendProcessReturn {
  backend: ChildProcess | null;
  isConnected: boolean;
  error: string | null;
}

export const useBackendProcess = (
  backendModule: string,
  configPath?: string
): BackendProcessReturn => {
  const [isConnected, setIsConnected] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const backendRef = useRef<ChildProcess | null>(null);

  useEffect(() => {
    const pythonPath = process.platform === 'win32'
      ? (process.env.PYTHON_PATH || 'python.exe')
      : (process.env.PYTHON_PATH || 'python3');

    const env: Record<string, string> = {
      ...process.env,
      PYTHONUNBUFFERED: '1',
      TERM: process.env.TERM || 'xterm-256color',
    };

    if (configPath) {
      env.ASCEND_OP_AGENT_CONFIG = configPath;
    }

    const backend = spawn(pythonPath, ['-m', backendModule], {
      env,
      stdio: ['pipe', 'pipe', 'pipe', 'pipe'],
    });

    backendRef.current = backend;

    if (backend.stdin && !backend.stdin.destroyed) {
      backend.stdin.cork();
    }

    backend.stdout.on('data', (data: Buffer) => {
      const lines = data.toString().split('\n').filter(Boolean);
      for (const line of lines) {
        try {
          const msg = JSON.parse(line);
          if (msg.method === 'backend.ready') {
            setIsConnected(true);
          }
        } catch {}
      }
    });

    backend.stderr.on('data', (data: Buffer) => {
      console.error('[backend stderr]', data.toString());
    });

    backend.on('error', (err) => {
      setError(err.message);
      setIsConnected(false);
    });

    backend.on('exit', () => {
      setIsConnected(false);
    });

    return () => {
      if (backend && !backend.killed) {
        backend.kill();
      }
    };
  }, [backendModule, configPath]);

  return { backend: backendRef.current, isConnected, error };
};