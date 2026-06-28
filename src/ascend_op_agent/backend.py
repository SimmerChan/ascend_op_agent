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

from __future__ import annotations

import asyncio
import logging
import os
import signal
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
_session_manager = None
_checkpoint_store = None  # U7: CheckpointStore 实例(op.* RPC 用)
_orchestrator = None  # U7: Orchestrator 实例(U9 才有真实 graph,U7 期间为 None)


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

    U6: 输入以 ``op:`` 开头 → 走 Orchestrator PhaseRunner(算子开发任务);
    否则 fallback 老 AIAgent path(保持 TUI 兼容)。

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

    # U6: op: 前缀路由 → Orchestrator
    if _orchestrator is not None and isinstance(user_input, str) and user_input.startswith("op:"):
        import uuid as _uuid
        thread_id = _uuid.uuid4().hex[:12]
        try:
            state = _orchestrator.invoke(user_input, thread_id=thread_id)
        except Exception as e:
            logging.exception(f"op.run orchestrator.invoke failed (thread={thread_id})")
            return AgentResponse(
                status="error",
                response=None,
                data={"message": str(e), "thread_id": thread_id},
            )
        # U8: 把 thread 的 skill 跟踪(SignalUsageRegistry 累积)推给前端
        # 复用 agent.progress 通知方法,加 discriminator event='skill_usage' 让前端区分
        # (见 frontend/src/hooks/parseProgress.ts)
        self._push_skill_usage_to_frontend(thread_id)
        pending = state.get("pending_confirmation")
        return AgentResponse(
            status="interrupted" if pending is not None else "completed",
            response=None,
            data={
                "thread_id": thread_id,
                "current_phase": state.get("current_phase"),
                "pending_confirmation": pending,
                "messages_count": len(state.get("messages", [])),
            },
        )

    return await _agent_wrapper.run_conversation_async(user_input)


def _push_skill_usage_to_frontend(thread_id: str) -> None:
    """U8: 把 SkillUsageRegistry 里该 thread 的所有 phase 记录推给前端。

    前端 (App.tsx + parseProgress.ts) 看到 event='skill_usage' discriminator
    → 渲染 chips (loaded: X, used: Y)。ephemeral(registry 不持久化),
    done 阶段一次推送足够。
    """
    try:
        from ascend_op_agent.orchestrator.cannbot_loader import SkillUsageRegistry
        loads = SkillUsageRegistry.instance().get_loads(thread_id)
        for sl in loads:
            _server.send_notification(
                "agent.progress",
                {
                    "phase": sl.phase,
                    "event": "skill_usage",
                    "payload": {
                        "phase": sl.phase,
                        "thread_id": thread_id,
                        "skill_names": sl.skill_names,
                        "used_skills": sl.used_skills,
                    },
                },
            )
    except Exception as e:
        logging.warning(f"_push_skill_usage_to_frontend failed: {e}")


async def _handle_session_reset() -> dict:
    """处理 session.reset 请求

    Returns:
        重置结果
    """
    if _agent_wrapper is None:
        return {"status": "error", "message": "Agent not initialized"}

    _agent_wrapper.agent.reset_conversation()
    return {"status": "reset_completed"}


async def _handle_session_get_history(session_id: str = None, limit: int = None) -> dict:
    """处理 session.get_history 请求

    Args:
        session_id: 会话 ID（None 表示当前会话）
        limit: 返回记录数限制

    Returns:
        会话历史记录
    """
    if _agent_wrapper is None:
        return {"status": "error", "message": "Agent not initialized"}

    from ascend_op_agent.agent.session_manager import read_session_history, list_sessions
    from ascend_op_agent.config import load_config

    config = load_config()
    persist_dir = config.session.persist_dir

    if session_id:
        # 获取指定会话的历史
        entries = read_session_history(session_id, persist_dir, limit)
        return {
            "status": "success",
            "session_id": session_id,
            "entries": [e.to_dict() for e in entries],
            "count": len(entries),
        }
    else:
        # 列出所有会话
        sessions = list_sessions(persist_dir, limit or 100)
        return {
            "status": "success",
            "sessions": sessions,
            "count": len(sessions),
        }


async def _handle_session_shutdown() -> dict:
    """处理 session.shutdown 请求

    Returns:
        关闭结果
    """
    if _session_manager is not None:
        _session_manager.shutdown()
        return {"status": "shutdown_completed"}
    return {"status": "no_session_manager"}


async def _handle_session_list_pending() -> dict:
    """处理 session.list_pending 请求(U7)。

    列出所有 status != done 的 checkpoint(供前端展示"可恢复"列表)。

    Returns:
        ``{"status": "success", "pending": [...], "count": N}``
    """
    if _checkpoint_store is None:
        return {"status": "error", "message": "CheckpointStore not initialized"}

    pending = _checkpoint_store.list_pending()
    return {
        "status": "success",
        "pending": [
            {
                "thread_id": p.thread_id,
                "current_phase": p.current_phase,
                "status": p.status,
                "updated_at": p.updated_at,
            }
            for p in pending
        ],
        "count": len(pending),
    }


async def _handle_session_resume_with_input(
    thread_id: str,
    payload: dict | None = None,
) -> AgentResponse:
    """处理 session.resume_with_input 请求(U7 + U6)。

    HITL 恢复:把用户确认 payload 注入 pending_confirmation 并续跑。
    U6 修复:orchestrator 真接进去,不再有 None 短路。

    Args:
        thread_id: 要恢复的 thread
        payload: 用户确认内容(如 ``{"approved": True}``)

    Returns:
        AgentResponse:``status="completed"/"interrupted"/"failed"``
        + ``data={"current_phase": ..., "pending_confirmation": ...}``
    """
    if _orchestrator is None:
        return AgentResponse(
            status="error",
            response=None,
            data={
                "message": (
                    "Orchestrator not wired (build_new_dev_graph init 失败或未 import). "
                    "检查 config.remote / NpuExecutor SSH 状态"
                )
            },
        )
    try:
        state = _orchestrator.resume(thread_id, payload=payload)
    except Exception as e:
        logging.exception(f"op.resume failed for thread={thread_id}")
        return AgentResponse(
            status="error",
            response=None,
            data={"message": str(e), "thread_id": thread_id},
        )

    status = "completed"
    if state.get("pending_confirmation") is not None:
        status = "interrupted"
    return AgentResponse(
        status=status,
        response=None,
        data={
            "thread_id": thread_id,
            "current_phase": state.get("current_phase"),
            "pending_confirmation": state.get("pending_confirmation"),
            "messages_count": len(state.get("messages", [])),
        },
    )


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
    from ascend_op_agent.agent.session_manager import SessionRecordManager
    from ascend_op_agent.agent.tool_registry import tool_registry

    global _session_manager

    prompt_builder = PromptBuilder()
    context_engine = ContextEngine()
    memory_store = MemoryStore()

    # 创建 SessionRecordManager
    _session_manager = SessionRecordManager(
        persist_dir=config.session.persist_dir,
        flush_interval_ms=config.session.flush_interval_ms,
        max_history=config.session.max_history,
    )

    agent = AIAgent(
        config=config,
        tool_registry=tool_registry,
        prompt_builder=prompt_builder,
        context_engine=context_engine,
        memory_store=memory_store,
        session_manager=_session_manager,
    )

    _agent_wrapper = AgentAsyncWrapper(agent, send_notification_fn=_server.send_notification)
    logging.info("Agent initialized")

    # U7: 初始化 CheckpointStore(崩溃恢复 + HITL 持久化底层)
    global _checkpoint_store
    from ascend_op_agent.orchestrator import CheckpointStore
    _checkpoint_store = CheckpointStore.from_config(config.checkpoint)
    logging.info(
        f"CheckpointStore initialized at {_checkpoint_store.db_path} "
        f"(auto_resume={config.checkpoint.auto_resume})"
    )

    # U6: 实例化 Orchestrator(_orchestrator 从 None 转为 PhaseRunner)。
    # 输入前缀 ``op:`` 触发新 path(算子开发任务);TUI 老输入仍走 _agent_wrapper。
    # 不重建 _agent_wrapper,不打破 TUI 兼容。
    global _orchestrator
    _orchestrator = _build_orchestrator(
        config=config,
        tool_registry=tool_registry,
        prompt_builder=prompt_builder,
        context_engine=context_engine,
        memory_store=memory_store,
        checkpoint_store=_checkpoint_store,
    )
    if _orchestrator is not None:
        logging.info("Orchestrator initialized (op: prefix → PhaseRunner path)")

    # 启动时检测 pending(pending != done 的 checkpoint)
    _resume_pending_check(config.checkpoint.auto_resume)


def _build_orchestrator(
    config,
    tool_registry,
    prompt_builder,
    context_engine,
    memory_store,
    checkpoint_store,
):
    """构造 Orchestrator PhaseRunner(U6)。

    失败优雅:NpuExecutor SSH 不可用时返回 None(回退 op: 路由失败报错)。
    """
    try:
        from ascend_op_agent.orchestrator import (
            NpuExecutor,
            build_new_dev_graph,
        )
        from ascend_op_agent.orchestrator.nodes.validation import (
            make_real_compile_node,
            make_real_precision_node,
        )
        from ascend_op_agent.ssh.manager import SSHEnvironment
    except Exception as e:
        logging.warning(f"_build_orchestrator: import failed ({e}), op: 路由不可用")
        return None

    # NpuExecutor:从 config.remote 配 SSH(可选)
    ssh_env = None
    remote_env_setup = ""
    container_name = ""
    if getattr(config, "remote", None):
        r = config.remote
        if r.host and r.user:
            try:
                ssh_env = SSHEnvironment(
                    host=r.host, user=r.user, port=int(getattr(r, "port", 22) or 22),
                    timeout=60,
                )
            except Exception as e:
                logging.warning(f"NpuExecutor SSH 初始化失败({e}),NPU 跑算子将不可用")
            if getattr(r, "env_setup", None):
                remote_env_setup = f"source {r.env_setup} > /dev/null 2>&1 && "
            container_name = getattr(r, "container_name", "") or ""

    from pathlib import Path
    npu = NpuExecutor(
        ssh_env=ssh_env,
        remote_env_setup=remote_env_setup,
        container_name=container_name,
        archive_dir=Path("/tmp/e2e_ops_archive"),
    )

    def _orchestrator_agent_factory():
        """Orchestrator 用的 fresh AIAgent —— session_manager=None(编排器 owns 持久化)。"""
        from ascend_op_agent.agent.core import AIAgent
        return AIAgent(
            config=config,
            tool_registry=tool_registry,
            prompt_builder=prompt_builder,
            context_engine=context_engine,
            memory_store=memory_store,
            session_manager=None,
        )

    def _operator_path_resolver(state: dict) -> str:
        """算子工程根目录:从 state 读,fallback e2e 默认路径。"""
        op = state.get("operator_path")
        if op:
            return op
        return "/tmp/e2e_ops_local/op_add"

    def _operator_name_resolver(state: dict) -> str:
        """算子名:op_info.name → unknown。"""
        info = state.get("op_info") or {}
        return info.get("name", "add_example")

    def _phase_callback(phase: str, event: str, payload):
        """PhaseRunner → 推 agent.progress 通知(U8 既有通知桥复用)。"""
        try:
            _server.send_notification(
                "agent.progress",
                {"phase": phase, "event": event, "payload": payload or {}},
            )
        except Exception as e:
            logging.warning(f"orchestrator phase_callback send failed: {e}")

    try:
        return build_new_dev_graph(
            store=checkpoint_store,
            agent_factory=_orchestrator_agent_factory,
            phase_callback=_phase_callback,
            use_real_skill_bundles=True,
            use_scaffold_codegen=True,
            compile_node_factory=lambda: make_real_compile_node(
                executor=npu,
                operator_path_resolver=_operator_path_resolver,
            ),
            precision_node_factory=lambda: make_real_precision_node(
                executor=npu,
                operator_path_resolver=_operator_path_resolver,
                operator_name_resolver=_operator_name_resolver,
            ),
        )
    except Exception as e:
        logging.warning(f"build_new_dev_graph 失败({e}),orchestrator 不可用")
        return None


def _resume_pending_check(auto_resume: bool) -> None:
    """启动时检测未完成的 thread(U7)。

    列出 status != done 的 checkpoint,日志记录。

    - ``auto_resume=True``:日志提示"可自动恢复,等待 op.resume RPC 触发"
      (P0 阶段不自动 invoke,避免误启动 LLM;真正自动 resume 留 P1)
    - ``auto_resume=False``:仅日志记录,等待用户显式调用

    Fallback 设计:此处只检测不自动续跑,确保现有 agent.run UX 不受影响。
    """
    if _checkpoint_store is None:
        return
    pending = _checkpoint_store.list_pending()
    if not pending:
        logging.info("No pending checkpoints found at startup")
        return
    logging.warning(
        f"Found {len(pending)} pending checkpoint(s): "
        + ", ".join(f"{p.thread_id}({p.status}@{p.current_phase})" for p in pending)
    )
    if auto_resume:
        logging.info(
            "auto_resume=true: use 'op.resume' RPC with thread_id to resume. "
            "P0 does not auto-invoke to avoid accidental LLM calls."
        )


async def main() -> None:
    """主入口"""
    global _server

    _setup_logging()
    logging.info("Starting backend RPC service")

    # 创建 RPC 服务器
    _server = JSONRPCServer()

    # 注册处理方法
    _server.register_method("agent.run", _handle_run_conversation)
    _server.register_method("session.reset", _handle_session_reset)
    _server.register_method("session.get_history", _handle_session_get_history)
    _server.register_method("session.shutdown", _handle_session_shutdown)
    # U7: checkpoint/resume 相关 RPC
    _server.register_method("session.list_pending", _handle_session_list_pending)
    _server.register_method("session.resume_with_input", _handle_session_resume_with_input)

    # 初始化 Agent（需要 _server 已创建）
    config_path = os.getenv("ASCEND_OP_AGENT_CONFIG")
    _setup_agent(config_path)

    logging.info("Backend ready, starting RPC server")

    # 启动服务
    await _server.run()


if __name__ == "__main__":
    # 后台线程消费 stderr，避免 pipe 阻塞
    # 注意：此代码在作为子进程启动时会被父进程接管 stderr
    # 这里主要确保日志配置正确

    # 信号处理
    def handle_signal(signum, frame):
        logging.info(f"Received signal {signum}, initiating shutdown...")
        if _session_manager is not None:
            _session_manager.shutdown()
        sys.exit(0)

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("Backend shutdown by keyboard interrupt")
    except Exception as e:
        logging.error(f"Backend error: {e}")
        sys.exit(1)
    finally:
        # 确保关闭 session manager
        if _session_manager is not None:
            _session_manager.shutdown()
