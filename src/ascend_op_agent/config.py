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

"""配置管理模块

支持 ${ENV_VAR} 格式的环境变量引用解析。
"""

import os
import re
from pathlib import Path
from typing import Any, Optional

import yaml
from pydantic import BaseModel, Field


class LLMConfig(BaseModel):
    """LLM 配置"""
    provider: str = "openai"
    api_base: str = "https://api.openai.com/v1"
    api_key: str = ""
    model: str = "gpt-4o"
    max_retries: int = 3
    timeout: int = 120


class MCPServerConfig(BaseModel):
    """MCP 服务器配置"""
    name: str
    type: str = "stdio"  # stdio / http / streamable-http
    command: Optional[str] = None
    args: list[str] = Field(default_factory=list)
    url: Optional[str] = None
    token: Optional[str] = None
    headers: Optional[dict[str, str]] = None
    oauth: Optional[dict[str, Any]] = None


class MCPConfig(BaseModel):
    """MCP 配置"""
    servers: list[MCPServerConfig] = Field(default_factory=list)


class SkillRepositoryConfig(BaseModel):
    """Skill 仓库配置"""
    name: str
    url: str


class RemoteConfig(BaseModel):
    """远程开发环境配置"""
    host: str
    user: str
    port: int = 22
    key_path: Optional[str] = None
    password: Optional[str] = None
    image_name: Optional[str] = None  # 可选，无此配置则在宿主机环境
    container_name: Optional[str] = None  # 可选，无此配置则自动创建容器

    def get_environment_type(self) -> str:
        """获取环境类型描述"""
        if self.image_name and self.container_name:
            return f"容器内开发 (镜像: {self.image_name}, 容器: {self.container_name})"
        elif self.image_name:
            return f"自动创建容器开发 (镜像: {self.image_name})"
        else:
            return "宿主机环境开发"

    def requires_confirmation(self) -> bool:
        """是否需要用户确认"""
        return True


class LocalConfig(BaseModel):
    """本地模式配置"""
    workspace: str = "./workspace"
    skills_path: str = "~/.ascend_op_agent/skills"


class LoggingConfig(BaseModel):
    """日志配置"""
    level: str = "INFO"
    format: str = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    file: Optional[str] = None


class Config(BaseModel):
    """主配置类"""
    llm: LLMConfig = Field(default_factory=LLMConfig)
    mcp: MCPConfig = Field(default_factory=MCPConfig)
    skill_repositories: list[SkillRepositoryConfig] = Field(default_factory=list)
    remote: Optional[RemoteConfig] = None
    local: LocalConfig = Field(default_factory=LocalConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)

    @classmethod
    def from_file(cls, path: str | Path) -> "Config":
        """从文件加载配置"""
        path = Path(path).expanduser()
        if not path.exists():
            raise FileNotFoundError(f"配置文件不存在: {path}")

        with open(path, "r", encoding="utf-8") as f:
            raw_config = yaml.safe_load(f)

        # 解析环境变量引用
        resolved_config = cls._resolve_env_vars(raw_config)

        return cls(**resolved_config)

    @classmethod
    def _resolve_env_vars(cls, obj: Any) -> Any:
        """递归解析配置中的 ${ENV_VAR} 引用"""
        if isinstance(obj, str):
            # 匹配 ${ENV_VAR} 格式
            pattern = r"\$\{([^}]+)\}"
            matches = re.findall(pattern, obj)
            for env_var in matches:
                env_value = os.getenv(env_var, "")
                obj = obj.replace(f"${{{env_var}}}", env_value)
            return obj
        elif isinstance(obj, dict):
            return {k: cls._resolve_env_vars(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [cls._resolve_env_vars(item) for item in obj]
        return obj

    @classmethod
    def default_config_path(cls) -> Path:
        """获取默认配置路径"""
        return Path("~/.ascend_op_agent/config.yaml").expanduser()

    def save(self, path: str | Path) -> None:
        """保存配置到文件"""
        path = Path(path).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)

        with open(path, "w", encoding="utf-8") as f:
            yaml.dump(self.model_dump(), f, default_flow_style=False, allow_unicode=True)


def load_config(config_path: Optional[str | Path] = None) -> Config:
    """加载配置的便捷函数

    Args:
        config_path: 配置文件路径，默认为 ~/.ascend_op_agent/config.yaml

    Returns:
        Config 对象
    """
    if config_path is None:
        config_path = Config.default_config_path()
    else:
        config_path = Path(config_path)

    if not config_path.exists():
        # 如果默认配置文件不存在，返回默认配置
        return Config()

    return Config.from_file(config_path)