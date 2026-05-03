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

"""CLI 模块测试"""

import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml
from click.testing import CliRunner

from ascend_op_agent.cli import main
from ascend_op_agent.config import Config


class TestMain:
    """主命令测试"""

    def test_help(self):
        """测试 --help 输出"""
        runner = CliRunner()
        result = runner.invoke(main, ["--help"])
        assert result.exit_code == 0
        assert "Ascend Op Agent" in result.output
        assert "--version" in result.output

    def test_version(self):
        """测试 --version 输出"""
        runner = CliRunner()
        result = runner.invoke(main, ["--version"])
        assert result.exit_code == 0
        assert "0.1.0" in result.output

    def test_default_config_not_exists(self):
        """默认配置不存在时使用空配置"""
        with patch("ascend_op_agent.cli.load_config") as mock_load:
            mock_load.side_effect = FileNotFoundError()
            runner = CliRunner()
            result = runner.invoke(main, ["run", "--help"])
            # run 子命令存在即可
            assert result.exit_code == 0


class TestInitCommand:
    """init 命令测试"""

    def test_init_creates_workspace(self):
        """测试 init 创建工作目录"""
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "test_workspace"
            runner = CliRunner()
            result = runner.invoke(main, ["init", "--workspace", str(workspace)])
            assert result.exit_code == 0
            assert workspace.exists()

    def test_init_creates_config_dir(self):
        """测试 init 创建配置目录"""
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "test_workspace"
            runner = CliRunner()
            result = runner.invoke(main, ["init", "--workspace", str(workspace)])
            assert result.exit_code == 0
            agent_dir = Path("~/.ascend_op_agent").expanduser()
            assert agent_dir.exists()

    def test_init_remote_flag_warning(self):
        """测试 remote 标志给出警告"""
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "test_workspace"
            runner = CliRunner()
            result = runner.invoke(main, ["init", "--workspace", str(workspace), "--remote"])
            assert "远程模式需要在 config.yaml 中配置 remote 项" in result.output


class TestRunCommand:
    """run 命令测试"""

    def test_run_help(self):
        """测试 run --help"""
        runner = CliRunner()
        result = runner.invoke(main, ["run", "--help"])
        assert result.exit_code == 0

    @patch("ascend_op_agent.cli.load_config")
    def test_run_local_mode(self, mock_load):
        """测试本地模式运行"""
        mock_config = MagicMock(spec=Config)
        mock_config.remote = None
        mock_load.return_value = mock_config
        runner = CliRunner()
        # 使用输入 "exit" 退出循环
        result = runner.invoke(main, ["run", "--local"], input="exit\n")
        assert result.exit_code == 0


class TestSkillCommand:
    """skill 命令测试"""

    def test_skill_list(self):
        """测试 skill list 命令"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump({"local": {"skills_path": "/tmp/nonexistent"}}, f)
            config_path = f.name

        try:
            runner = CliRunner()
            result = runner.invoke(main, ["-c", config_path, "skill", "list"])
            assert result.exit_code == 0
            assert "Skills" in result.output
        finally:
            os.unlink(config_path)

    def test_skill_add_requires_url(self):
        """测试 skill add 需要 URL"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump({}, f)
            config_path = f.name

        try:
            runner = CliRunner()
            result = runner.invoke(main, ["-c", config_path, "skill", "add"])
            assert result.exit_code == 0
            assert "需要提供仓库 URL" in result.output
        finally:
            os.unlink(config_path)

    def test_skill_remove_requires_url(self):
        """测试 skill remove 需要 URL"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump({}, f)
            config_path = f.name

        try:
            runner = CliRunner()
            result = runner.invoke(main, ["-c", config_path, "skill", "remove"])
            assert result.exit_code == 0
            assert "需要提供仓库 URL" in result.output
        finally:
            os.unlink(config_path)

    def test_skill_install_no_url_no_repos(self):
        """测试 install 无 URL 且无已配置仓库时报错"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump({"skill_repositories": []}, f)
            config_path = f.name

        try:
            runner = CliRunner()
            result = runner.invoke(main, ["-c", config_path, "skill", "install"])
            assert result.exit_code == 0
            assert "需要提供仓库 URL" in result.output
            assert "请先添加仓库" in result.output
        finally:
            os.unlink(config_path)

    def test_skill_install_no_url_with_repos(self):
        """测试 install 无 URL 但有已配置仓库时显示仓库列表"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(
                {
                    "skill_repositories": [
                        {"name": "test-repo", "url": "https://gitcode.com/test/repo"}
                    ]
                },
                f,
            )
            config_path = f.name

        try:
            runner = CliRunner()
            result = runner.invoke(main, ["-c", config_path, "skill", "install"], input="\n")
            assert result.exit_code == 0
            assert "test-repo" in result.output
            assert "取消安装" in result.output
        finally:
            os.unlink(config_path)


class TestMCPCommand:
    """mcp 命令测试"""

    def test_mcp_list_empty(self):
        """测试 mcp list 空列表"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump({"mcp": {"servers": []}}, f)
            config_path = f.name

        try:
            runner = CliRunner()
            result = runner.invoke(main, ["-c", config_path, "mcp", "list"])
            assert result.exit_code == 0
            assert "暂无配置的服务器" in result.output
        finally:
            os.unlink(config_path)

    def test_mcp_start_requires_name(self):
        """测试 mcp start 需要服务器名"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump({}, f)
            config_path = f.name

        try:
            runner = CliRunner()
            result = runner.invoke(main, ["-c", config_path, "mcp", "start"])
            assert result.exit_code == 0
            assert "需要提供服务器名称" in result.output
        finally:
            os.unlink(config_path)

    def test_mcp_stop_requires_name(self):
        """测试 mcp stop 需要服务器名"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump({}, f)
            config_path = f.name

        try:
            runner = CliRunner()
            result = runner.invoke(main, ["-c", config_path, "mcp", "stop"])
            assert result.exit_code == 0
            assert "需要提供服务器名称" in result.output
        finally:
            os.unlink(config_path)


class TestSyncCommand:
    """sync 命令测试"""

    def test_sync_requires_remote(self):
        """测试 sync 需要远程配置"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump({"remote": None}, f)
            config_path = f.name

        try:
            runner = CliRunner()
            result = runner.invoke(main, ["-c", config_path, "sync", "push"])
            assert result.exit_code == 0
            assert "未配置远程开发环境" in result.output
        finally:
            os.unlink(config_path)
