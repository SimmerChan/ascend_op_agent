// build.js - 使用 esbuild 打包 TypeScript/JSX
import * as esbuild from 'esbuild';
import { typescriptPlugin } from 'esbuild-plugin-typescript';
import { rmSync, existsSync } from 'fs';

// 清理 dist 目录
if (existsSync('dist')) {
  rmSync('dist', { recursive: true });
}

// 构建
await esbuild.build({
  entryPoints: ['src/index.tsx'],
  bundle: true,
  outfile: 'dist/index.js',
  platform: 'node',
  format: 'esm',
  external: ['react', 'ink', 'react/jsx-runtime', 'ink-text-input'],
  plugins: [typescriptPlugin()],
});

console.log('Build completed successfully');
