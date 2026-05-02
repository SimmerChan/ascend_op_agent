#!/usr/bin/env python3
# Copyright 2026 SimmerChan
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Python 后端入口

通过 stdin/stdout 与前端通信，支持 JSON-RPC 2.0 协议。
"""

import asyncio
import logging
import os
import sys
import threading
from pathlib import Path

# 添加项目根目录到 Python 路径
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from ascend_op_agent.backend.rpc.server import JSONRPCServer
from ascend_op_agent.backend.rpc.agent_service import AgentAsyncWrapper, AgentResponse
from ascend_op_agent.config import load_config


# 全局变量
_server: JSONRPCServer | None = None
_agent_wrapper: AgentAsyncWrapper | None = None


def _setup_logging() -> None:
    """配置日志"""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        stream=sys.stderr,
    )


def _consume_stderr(stderr_file, log_path: str) -> None:
    """后台线程消费 stderr，避免 pipe 阻塞

    Args:
        stderr_file: stderr 文件对象
        log_path: 日志文件路径
    """
    try:
        with open(log_path, "w") as f:
            for line in stderr_file:
                f.write(line)
                f.flush()
    except Exception as e:
        logging.error(f"Error consuming stderr: {e}")


async def _handle_run_conversation(user_input: str) -> AgentResponse:
    """处理 agent.run 请求

    Args:
        user_input: 用户输入

    Returns:
        Agent 响应
    """
    if _agent_wrapper is None:
        return AgentResponse(
            status="error",
            response=None,
            data={"message": "Agent not initialized"}
        )

    return await _agent_wrapper.run_conversation_async(user_input)


async def _handle_session_reset() -> dict:
    """处理 session.reset 请求

    Returns:
        重置结果
    """
    if _agent_wrapper is None:
        return {"status": "error", "message": "Agent not initialized"}

    _agent_wrapper.agent.reset_conversation()
    return {"status": "completed"}


def _setup_agent(config_path: str | None = None) -> None:
    """初始化 Agent

    Args:
        config_path: 配置文件路径，从环境变量读取
    """
    global _agent_wrapper

    # 加载配置
    if config_path is None:
        config_path = os.getenv("ASCEND_OP_AGENT_CONFIG")

    if config_path:
        config = load_config(config_path)
    else:
        config = load_config()

    logging.info(f"Loaded config from: {config_path or 'default'}")

    # 初始化 Agent 相关组件
    from ascend_op_agent.agent.core import AIAgent
    from ascend_op_agent.agent.context import ContextEngine
    from ascend_op_agent.agent.memory import MemoryStore
    from ascend_op_agent.agent.prompt_builder import PromptBuilder
    from ascend_op_agent.agent.tool_registry import ToolRegistry

    tool_registry = ToolRegistry()
    prompt_builder = PromptBuilder()
    context_engine = ContextEngine()
    memory_store = MemoryStore()

    agent = AIAgent(
        config=config,
        tool_registry=tool_registry,
        prompt_builder=prompt_builder,
        context_engine=context_engine,
        memory_store=memory_store,
    )

    _agent_wrapper = AgentAsyncWrapper(agent)
    logging.info("Agent initialized")


async def main() -> None:
    """主入口"""
    global _server

    _setup_logging()
    logging.info("Starting backend RPC service")

    # 初始化 Agent
    config_path = os.getenv("ASCEND_OP_AGENT_CONFIG")
    _setup_agent(config_path)

    # 创建 RPC 服务器
    _server = JSONRPCServer()

    # 注册处理方法
    _server.register_method("agent.run", _handle_run_conversation)
    _server.register_method("session.reset", _handle_session_reset)

    logging.info("Backend ready, starting RPC server")

    # 启动服务
    await _server.run()


if __name__ == "__main__":
    # 后台线程消费 stderr，避免 pipe 阻塞
    # 注意：此代码在作为子进程启动时会被父进程接管 stderr
    # 这里主要确保日志配置正确
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("Backend shutdown by keyboard interrupt")
    except Exception as e:
        logging.error(f"Backend error: {e}")
        sys.exit(1)
