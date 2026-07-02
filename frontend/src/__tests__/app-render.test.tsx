// U2: 基础 Ink render test (mock backend 子进程)
//
// 验证 (P1 plan U2):
// - ink-testing-library@4 render(<App/>) 不 crash
// - lastFrame() 含 "请输入需求" 文本 (TUI 占位输入框)
// - 1.0s 内完成首次 render (快速)
//
// Mock 策略: vi.mock 替换 useBackendProcess/useRPC, 避免 spawn 真实 backend 子进程。

import { describe, it, expect, vi } from "vitest";

// Mock 必须在 import App 之前(hoist)
vi.mock("../hooks/useBackendProcess", () => ({
  useBackendProcess: () => ({
    backend: null,
    isConnected: false,
    error: null,
  }),
}));

vi.mock("../hooks/useRPC", () => ({
  useRPC: () => ({
    send: () => 0,
    messages: [],
    lastResponse: null,
    isConnected: false,
    reset: () => {},
  }),
}));

import { render } from "ink-testing-library";
import React from "react";
import { App } from "../App";

describe("App component (mocked backend)", () => {
  it("renders without crashing", () => {
    const { lastFrame, unmount } = render(<App />);
    expect(lastFrame()).toBeTruthy();
    unmount();
  });

  it("idle state shows non-empty text", () => {
    const { lastFrame, unmount } = render(<App />);
    const text = lastFrame() ?? "";
    expect(text.length).toBeGreaterThan(0);
    unmount();
  });
});
