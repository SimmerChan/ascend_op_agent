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

"""CLI 入口模块

支持以下命令:
- init: 初始化项目配置
- run: 运行 Agent 对话
- skill: Skill 管理
- mcp: MCP 服务器管理
- sync: 同步远程文件
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

import click
from rich.console import Console
from rich.panel import Panel

from ascend_op_agent import __version__
from ascend_op_agent.config import Config, load_config

console = Console()


@click.group()
@click.version_option(version=__version__)
@click.option(
    "-c", "--config",
    type=click.Path(exists=True),
    help="配置文件路径"
)
@click.pass_context
def main(ctx: click.Context, config: Optional[str]) -> None:
    """Ascend Op Agent - 昇腾算子开发Agent

    支持从0开发算子和GPU迁移两种场景，兼容本地/远程开发模式。
    """
    # 将配置存入 context，供子命令使用
    ctx.ensure_object(dict)

    if config:
        ctx.obj["config"] = load_config(config)
    else:
        # 尝试加载默认配置
        try:
            ctx.obj["config"] = load_config()
        except FileNotFoundError:
            ctx.obj["config"] = Config()


@main.command()
@click.option(
    "--workspace",
    type=click.Path(),
    default="./workspace",
    help="工作目录路径"
)
@click.option(
    "--remote",
    is_flag=True,
    help="启用远程开发模式"
)
@click.pass_context
def init(ctx: click.Context, workspace: str, remote: bool) -> None:
    """初始化项目配置

    创建必要的工作目录和配置文件。
    """
    workspace_path = Path(workspace).expanduser().absolute()
    workspace_path.mkdir(parents=True, exist_ok=True)

    console.print(f"[green]✓[/green] 创建工作目录: {workspace_path}")

    # 创建 .ascend_op_agent 目录
    agent_dir = Path("~/.ascend_op_agent").expanduser()
    agent_dir.mkdir(parents=True, exist_ok=True)

    config_path = agent_dir / "config.yaml"
    if not config_path.exists():
        # 创建默认配置
        default_config = Config(
            local={"workspace": str(workspace_path)}
        )
        default_config.save(config_path)
        console.print(f"[green]✓[/green] 创建配置文件: {config_path}")
    else:
        console.print(f"[yellow]⚠[/yellow] 配置文件已存在: {config_path}")

    if remote:
        console.print("[yellow]⚠[/yellow] 远程模式需要在 config.yaml 中配置 remote 项")
        console.print("    请编辑配置文件添加远程连接信息")

    console.print("\n[bold green]初始化完成！[/bold green]")
    console.print("使用 [cyan]ascend-op-agent run[/cyan] 启动 Agent 对话")


@main.command()
@click.option(
    "--local",
    is_flag=True,
    help="强制使用本地模式"
)
@click.pass_context
def run(ctx: click.Context, local: bool) -> None:
    """启动 Agent 对话

    前端基于 Node.js + Ink 构建，后端是独立的 Python 进程。
    支持推理期间的响应式渲染和复杂审批交互。
    """
    config: Config = ctx.obj["config"]

    # 检查 Node.js 是否可用
    if not shutil.which("node"):
        console.print("[red]错误: Agent 对话需要 Node.js[/red]")
        console.print("请安装 Node.js: https://nodejs.org/")
        return

    # 获取前端路径 (项目根目录下的 frontend)
    frontend_path = Path(__file__).parent.parent.parent / "frontend"
    dist_path = frontend_path / "dist"

    # 检查前端是否已构建
    if not dist_path.exists():
        console.print("[yellow]前端未构建，正在构建...[/yellow]")
        subprocess.run(["npm", "install"], cwd=frontend_path, check=True)
        subprocess.run(["npm", "run", "build"], cwd=frontend_path, check=True)

    # 构建环境变量，传递配置路径
    env = {
        **os.environ,
        "ASCEND_OP_AGENT_CONFIG": str(config.config_path),
        "PYTHONUNBUFFERED": "1",
    }

    # 启动 Node.js 前端
    try:
        subprocess.run(
            ["node", "dist/index.js"],
            cwd=frontend_path,  # 从 frontend 目录运行
            env=env,
        )
    except Exception as e:
        console.print(f"[red]前端启动失败: {e}[/red]")


@main.command()
@click.argument("action", type=click.Choice(["list", "add", "remove", "install"]))
@click.argument("repo_url", required=False)
@click.pass_context
def skill(ctx: click.Context, action: str, repo_url: Optional[str]) -> None:
    """Skill 管理命令

    ACTIONS:
        list:   列出已安装的 skills
        add:    添加外部 skill 仓库
        remove: 移除外部 skill 仓库
        install:从仓库安装 skill（交互式选择）

    EXAMPLES:
        ascend-op-agent skill list
        ascend-op-agent skill install https://gitcode.com/ascend/agent-skills
    """
    config: Config = ctx.obj["config"]

    if action == "list":
        console.print("[bold]已安装的 Skills:[/bold]")
        skills_path = Path(config.local.skills_path).expanduser()
        if skills_path.exists():
            for skill_dir in skills_path.iterdir():
                if skill_dir.is_dir():
                    console.print(f"  - [cyan]{skill_dir.name}[/cyan]")
        else:
            console.print("  [dim]暂无已安装的 skills[/dim]")

    elif action == "add":
        if not repo_url:
            console.print("[red]错误: 需要提供仓库 URL[/red]")
            console.print("用法: ascend-op-agent skill add <repo_url>")
            return

        # 添加仓库到配置
        from ascend_op_agent.config import SkillRepositoryConfig
        new_repo = SkillRepositoryConfig(
            name=repo_url.split("/")[-1].replace(".git", ""),
            url=repo_url
        )
        config.skill_repositories.append(new_repo)

        config_path = Config.default_config_path()
        config.save(config_path)
        console.print(f"[green]✓[/green] 添加仓库: {repo_url}")

    elif action == "install":
        if not repo_url:
            # 无 URL 时，检查是否有已配置的仓库
            if not config.skill_repositories:
                console.print("[red]错误: 需要提供仓库 URL[/red]")
                console.print("请先添加仓库: ascend-op-agent skill add <repo_url>")
                console.print("或直接指定仓库 URL 安装")
                console.print("用法: ascend-op-agent skill install <repo_url>")
                return
            else:
                # 有已配置仓库，提示用户选择
                console.print("[bold]已配置的仓库:[/bold]")
                for i, repo in enumerate(config.skill_repositories, 1):
                    console.print(f"  [{i}] {repo.name} ({repo.url})")
                console.print()
                selected = input("请输入仓库编号或 URL: ").strip()
                if not selected:
                    console.print("[yellow]取消安装[/yellow]")
                    return
                # 尝试解析为编号
                if selected.isdigit() and 1 <= int(selected) <= len(config.skill_repositories):
                    repo_url = config.skill_repositories[int(selected) - 1].url
                else:
                    repo_url = selected  # 用户可能直接输入了 URL

        console.print(f"[bold]正在获取仓库中的 Skills...[/bold] {repo_url}")

        try:
            from ascend_op_agent.skills.repository import SkillRepositoryDiscovery
            from ascend_op_agent.skills.interactive import InteractiveSelector
            from ascend_op_agent.skills.installer import SkillInstaller
            from ascend_op_agent.skills.index import SkillIndex
            from ascend_op_agent.skills.storage import SkillStorage

            discovery = SkillRepositoryDiscovery()
            repo_path = discovery.clone_or_update(repo_url)
            skills = discovery.fetch_skill_list(repo_url)

            if not skills:
                console.print("[yellow]仓库中未找到任何 Skill[/yellow]")
                return

            console.print(f"\n[bold]找到 {len(skills)} 个 Skill，请选择要安装的:[/bold]")
            selector = InteractiveSelector(skills, title="选择要安装的 Skills")
            selected_indices = selector.select()

            if selected_indices is None:
                console.print("[yellow]取消安装[/yellow]")
                return

            if not selected_indices:
                console.print("[yellow]未选择任何 Skill[/yellow]")
                return

            selected_skills = [skills[i] for i in selected_indices]

            # 安装选中的 skills
            installer = SkillInstaller(storage=SkillStorage())
            index = SkillIndex()
            results = installer.install_skills(selected_skills, repo_path, index)

            # 打印结果
            console.print("\n[bold]安装结果:[/bold]")
            success_count = sum(1 for v in results.values() if v)
            for name, success in results.items():
                status = "[green]✓[/green]" if success else "[red]✗[/red]"
                console.print(f"  {status} {name}")
            console.print(f"\n成功安装 {success_count}/{len(results)} 个 Skill")

        except Exception as e:
            console.print(f"[red]安装失败: {e}[/red]")

    elif action == "remove":
        if not repo_url:
            console.print("[red]错误: 需要提供仓库 URL 或名称[/red]")
            return

        # 从配置中移除仓库
        config.skill_repositories = [
            r for r in config.skill_repositories
            if r.url != repo_url and r.name != repo_url
        ]
        config_path = Config.default_config_path()
        config.save(config_path)
        console.print(f"[green]✓[/green] 移除仓库: {repo_url}")


@main.command()
@click.argument("action", type=click.Choice(["list", "start", "stop", "status"]))
@click.argument("server_name", required=False)
@click.pass_context
def mcp(ctx: click.Context, action: str, server_name: Optional[str]) -> None:
    """MCP 服务器管理命令

    ACTIONS:
        list:   列出已配置的 MCP 服务器
        start:  启动 MCP 服务器
        stop:   停止 MCP 服务器
        status: 查看服务器状态

    EXAMPLES:
        ascend-op-agent mcp list
        ascend-op-agent mcp start code-search
    """
    config: Config = ctx.obj["config"]

    if action == "list":
        console.print("[bold]已配置的 MCP 服务器:[/bold]")
        if not config.mcp.servers:
            console.print("  [dim]暂无配置的服务器[/dim]")
        else:
            for server in config.mcp.servers:
                console.print(f"  - [cyan]{server.name}[/cyan] ({server.type})")

    elif action == "start":
        if not server_name:
            console.print("[red]错误: 需要提供服务器名称[/red]")
            console.print("用法: ascend-op-agent mcp start <server_name>")
            return

        console.print(f"[bold]启动 MCP 服务器:[/bold] {server_name}")
        # TODO: 实现 MCPLifecycleManager.start_server()
        console.print("[dim]MCP 服务器启动功能即将到来...[/dim]")

    elif action == "stop":
        if not server_name:
            console.print("[red]错误: 需要提供服务器名称[/red]")
            return

        console.print(f"[bold]停止 MCP 服务器:[/bold] {server_name}")
        # TODO: 实现 MCPLifecycleManager.stop_server()
        console.print("[dim]MCP 服务器停止功能即将到来...[/dim]")

    elif action == "status":
        console.print("[bold]MCP 服务器状态:[/bold]")
        # TODO: 实现状态检查
        console.print("[dim]MCP 服务器状态功能即将到来...[/dim]")


@main.command()
@click.pass_context
def acp(ctx: click.Context) -> None:
    """ACP 编辑器集成模式

    通过 stdio 与编辑器（如 VS Code、Zed、JetBrains）通信，
    使 Ascend Op Agent 可以作为编辑器的 AI 后端运行。

    EXAMPLES:
        ascend-op-agent acp
    """
    import asyncio

    from ascend_op_agent.acp.adapter import ACPAdapter
    from ascend_op_agent.agent.core import AIAgent
    from ascend_op_agent.agent.context import ContextEngine
    from ascend_op_agent.agent.memory import MemoryStore
    from ascend_op_agent.agent.prompt_builder import PromptBuilder
    from ascend_op_agent.agent.tool_registry import ToolRegistry
    from ascend_op_agent.config import Config

    config: Config = ctx.obj["config"]

    # 初始化 Agent 组件
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

    # 创建 ACP 适配器
    adapter = ACPAdapter(
        agent_runner=agent.run_conversation,
        tool_registry=tool_registry,
        session_timeout=30 * 60,  # 30 分钟
    )

    # 运行 ACP 适配器
    console.print("[bold green]ACP 模式已启动，等待编辑器连接...[/bold green]")
    asyncio.run(adapter.run())


@main.command()
@click.argument("direction", type=click.Choice(["push", "pull"]))
@click.option("--files", "-f", multiple=True, help="指定要同步的文件或目录")
@click.pass_context
def sync(ctx: click.Context, direction: str, files: tuple[str, ...]) -> None:
    """同步本地与远程文件

    DIRECTIONS:
        push: 将本地文件同步到远程
        pull: 将远程文件同步到本地

    EXAMPLES:
        ascend-op-agent sync push
        ascend-op-agent sync pull -f src/ops
    """
    config: Config = ctx.obj["config"]

    if not config.remote:
        console.print("[red]错误: 未配置远程开发环境[/red]")
        console.print("请在配置文件中添加 remote 项")
        return

    if direction == "push":
        console.print("[bold]同步本地文件到远程...[/bold]")
        # TODO: 实现 SSHManager.sync_files()
        console.print("[dim]文件同步功能即将到来...[/dim]")

    elif direction == "pull":
        console.print("[bold]从远程同步文件到本地...[/bold]")
        # TODO: 实现 SSHManager.sync_files()
        console.print("[dim]文件同步功能即将到来...[/dim]")


if __name__ == "__main__":
    main()