<script setup lang="ts">
import { ref } from 'vue'
import SessionTreeView from './views/SessionTreeView.vue'
import CheckpointView from './views/CheckpointView.vue'

const activeView = ref<'sessions' | 'checkpoints'>('sessions')
</script>

<template>
  <div class="app">
    <nav class="app-nav">
      <button :class="{ active: activeView === 'sessions' }" @click="activeView = 'sessions'">
        会话(聊天)
      </button>
      <button
        :class="{ active: activeView === 'checkpoints' }"
        @click="activeView = 'checkpoints'"
      >
        检查点(算子开发)
      </button>
    </nav>
    <main class="app-main">
      <SessionTreeView v-show="activeView === 'sessions'" />
      <CheckpointView v-show="activeView === 'checkpoints'" />
    </main>
  </div>
</template>

<style>
* {
  margin: 0;
  padding: 0;
  box-sizing: border-box;
}
body {
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, Cantarell, sans-serif;
}
.app {
  height: 100vh;
  display: flex;
  flex-direction: column;
}
.app-nav {
  display: flex;
  background: #1f2329;
  flex-shrink: 0;
}
.app-nav button {
  padding: 10px 20px;
  border: none;
  background: transparent;
  color: #a8abb2;
  cursor: pointer;
  font-size: 13px;
  border-bottom: 2px solid transparent;
}
.app-nav button:hover {
  color: #fff;
}
.app-nav button.active {
  color: #fff;
  border-bottom-color: #409eff;
}
.app-main {
  flex: 1;
  overflow: hidden;
}
</style>
