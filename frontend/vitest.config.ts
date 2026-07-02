// U2: vitest config for Ink testing
//
// - environment='jsdom' (Ink 4 无 DOM, 但 vitest runtime 需要 DOM polyfill)
// - ink-testing-library@4 默认 TTY mock (F-P1-FEAS-22 round 3)
// - testTimeout 60_000 (F-P1-FEAS-06 round 3: 默认 5s 不够, backend RPC 需 30s+)
// - hookTimeout 60_000 (同)
// - include '**/__tests__/**/*.test.{ts,tsx}'

import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    environment: "jsdom",
    include: ["src/**/__tests__/**/*.test.{ts,tsx}"],
    testTimeout: 60_000,
    hookTimeout: 60_000,
  },
});
