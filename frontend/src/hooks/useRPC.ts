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

import { useState, useEffect, useCallback, useRef } from 'react';
import { spawn, ChildProcess } from 'child_process';

export interface RPCMessage {
  jsonrpc: "2.0";
  id?: number;
  method?: string;
  params?: Record<string, unknown>;
  result?: unknown;
  error?: { code: number; message: string };
}

export interface UseRPCReturn {
  send: (method: string, params?: Record<string, unknown>) => number;
  messages: RPCMessage[];
  lastResponse: RPCMessage | null;
  isConnected: boolean;
  reset: () => void;
}

export const useRPC = (backendModule: string = 'ascend_op_agent.backend'): UseRPCReturn => {
  const [messages, setMessages] = useState<RPCMessage[]>([]);
  const [lastResponse, setLastResponse] = useState<RPCMessage | null>(null);
  const [isConnected, setIsConnected] = useState(false);
  const backendRef = useRef<ChildProcess | null>(null);

  useEffect(() => {
    // Python 路径检测
    const pythonPath = process.platform === 'win32'
      ? (process.env.PYTHON_PATH || 'python.exe')
      : (process.env.PYTHON_PATH || 'python3');

    const backend = spawn(pythonPath, ['-m', backendModule], {
      env: {
        ...process.env,
        PYTHONUNBUFFERED: '1',
        TERM: process.env.TERM || 'xterm-256color',
      },
      stdio: ['pipe', 'pipe', 'pipe', 'pipe'],
    });

    backendRef.current = backend;

    backend.stdout.on('data', (data: Buffer) => {
      const lines = data.toString().split('\n').filter(Boolean);
      for (const line of lines) {
        try {
          const msg: RPCMessage = JSON.parse(line);
          setMessages(prev => [...prev, msg]);
          setLastResponse(msg);
          if (msg.method === 'backend.ready') {
            setIsConnected(true);
          }
        } catch (e) {
          console.error('[RPC parse error]', e, 'line:', line);
        }
      }
    });

    backend.stderr.on('data', (data: Buffer) => {
      console.error('[backend stderr]', data.toString());
    });

    backend.on('exit', (code) => {
      setIsConnected(false);
      console.log(`[backend exited with code ${code}]`);
    });

    return () => {
      if (backend && !backend.killed) {
        backend.kill();
      }
    };
  }, [backendModule]);

  const send = useCallback((method: string, params?: Record<string, unknown>): number => {
    const backend = backendRef.current;
    if (!backend || !backend.stdin || backend.stdin.destroyed) {
      throw new Error('Backend not connected');
    }
    const id = Date.now();
    const msg: RPCMessage = { jsonrpc: "2.0", id, method, params };
    backend.stdin.write(JSON.stringify(msg) + '\n');
    return id;
  }, []);

  const reset = useCallback(() => {
    send('session.reset', {});
    setMessages([]);
    setLastResponse(null);
  }, [send]);

  return { send, messages, lastResponse, isConnected, reset };
};