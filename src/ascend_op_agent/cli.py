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
    "--session",
    type=str,
    help="会话名称"
)
@click.option(
    "--local",
    is_flag=True,
    help="强制使用本地模式"
)
@click.pass_context
def run(ctx: click.Context, session: Optional[str], local: bool) -> None:
    """运行 Agent 对话

    启动交互式会话进行算子开发。
    """
    config: Config = ctx.obj["config"]

    # 检查远程配置
    if config.remote and not local:
        env_type = config.remote.get_environment_type()
        console.print(Panel(
            f"[yellow]即将在远程环境开发[/yellow]\n\n"
            f"环境类型: {env_type}\n"
            f"主机: {config.remote.host}\n"
            f"用户: {config.remote.user}",
            title="远程环境确认"
        ))

        if config.remote.requires_confirmation():
            confirm = click.confirm("是否确认在此环境继续开发？")
            if not confirm:
                console.print("[yellow]已取消操作[/yellow]")
                return

    console.print("[bold]Ascend Op Agent[/bold] - 算子开发会话")
    console.print("输入 'exit' 或 'quit' 退出会话\n")

    # 简单的交互式循环（后续会替换为真正的 Agent）
    while True:
        try:
            user_input = console.input("[bold blue]>>>[/bold blue] ")
        except (KeyboardInterrupt, EOFError):
            console.print("\n[yellow]退出会话[/yellow]")
            break

        if user_input.strip().lower() in ("exit", "quit", "q"):
            console.print("[yellow]退出会话[/yellow]")
            break

        if not user_input.strip():
            continue

        # TODO: 调用 Agent 处理输入
        console.print(f"[dim]暂不支持: {user_input}[/dim]")


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
            console.print("[red]错误: 需要提供仓库 URL[/red]")
            console.print("用法: ascend-op-agent skill install <repo_url>")
            return

        console.print(f"[bold]正在获取仓库中的 Skills...[/bold] {repo_url}")

        # TODO: 实现 SkillRepositoryDiscovery.fetch_skill_list()
        console.print("[dim]Skill 安装功能即将到来...[/dim]")

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