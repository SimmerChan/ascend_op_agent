// Copyright 2026 SimmerChan
// Apache 2.0 License
// https://www.apache.org/licenses/LICENSE-2.0

export interface RPCMessage {
  jsonrpc: "2.0";
  id?: number;
  method?: string;
  params?: Record<string, unknown>;
  result?: unknown;
  error?: { code: number; message: string };
}

export type AppState = 'idle' | 'running' | 'waiting_confirm' | 'completed' | 'error';

export interface ConfirmData {
  title: string;
  message?: string;
  options: string[];
  data?: unknown;
}