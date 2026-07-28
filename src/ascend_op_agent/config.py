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

支持 ${ENV_VAR} 和 ${ENV_VAR:-default} 格式的环境变量引用解析。
采用双文件架构：config.yaml（行为配置）+ .env（敏感凭据）。
"""

import os
import re
from pathlib import Path
from typing import Any, Optional, Union

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field, field_validator, model_validator


class LLMConfig(BaseModel):
    """LLM 配置"""

    provider: str = "openai"
    api_base: str = "https://api.openai.com/v1"
    api_key: str = Field(default="", description="API密钥（敏感）")
    auth_token: str = Field(
        default="",
        description="Bearer auth token for Ark-like providers; takes precedence over api_key when non-empty",
    )
    model: str = "gpt-4o"
    max_retries: int = 3
    timeout: int = 120
    disable_thinking: bool = Field(
        default=False,
        description="禁用推理模型 extended thinking。glm-5.2/Ark 等推理模型在 SOUL 等"
        "长 system_prompt 下 thinking 会膨胀吃光 max_tokens(实测 12k-15k 字符 vs "
        "max_tokens=4096),导致 visible text block 输出为空。设 True 传 "
        "thinking={type:disabled}。非推理模型(MiniMax-M3)保持 False(其兼容端点"
        "可能不认 thinking 参数)。",
    )

    @field_validator("provider")
    @classmethod
    def validate_provider(cls, v: str) -> str:
        """验证 LLM provider"""
        if not v or not v.strip():
            raise ValueError("LLM provider 不能为空")
        return v.strip().lower()


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
    password: Optional[str] = None  # 敏感字段
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


class EmbeddingConfig(BaseModel):
    """Embedding 模型配置"""

    model: str = "sentence-transformers/all-MiniLM-L6-v3"


class VectorStoreConfig(BaseModel):
    """向量存储配置"""

    persist_dir: str = "~/.ascend_op_agent/vector_db"


class SessionConfig(BaseModel):
    """会话记录配置"""

    persist_dir: str = "~/.ascend_op_agent/sessions"
    max_history: Optional[int] = None
    flush_interval_ms: int = 100


class CheckpointConfig(BaseModel):
    """编排器 checkpoint 配置(自研 sqlite3)。

    支撑崩溃恢复(R4):节点每步落 checkpoint,backend 启动时按 auto_resume
    检测 pending thread 并续跑。
    """

    db_path: str = "~/.ascend_op_agent/checkpoints.db"
    auto_resume: bool = True


class LoggingConfig(BaseModel):
    """日志配置"""

    level: str = "INFO"
    format: str = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    file: Optional[str] = None

    @field_validator("level")
    @classmethod
    def validate_level(cls, v: str) -> str:
        """验证日志级别"""
        valid_levels = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        if v.upper() not in valid_levels:
            raise ValueError(f"无效的日志级别: {v}，支持的选项: {valid_levels}")
        return v.upper()


class Config(BaseModel):
    """主配置类"""

    llm: LLMConfig = Field(default_factory=LLMConfig)
    mcp: MCPConfig = Field(default_factory=MCPConfig)
    skill_repositories: list[SkillRepositoryConfig] = Field(default_factory=list)
    remote: Optional[RemoteConfig] = None
    local: LocalConfig = Field(default_factory=LocalConfig)
    embedding: EmbeddingConfig = Field(default_factory=EmbeddingConfig)
    vector_store: VectorStoreConfig = Field(default_factory=VectorStoreConfig)
    session: SessionConfig = Field(default_factory=SessionConfig)
    checkpoint: CheckpointConfig = Field(default_factory=CheckpointConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)

    # 配置文件路径（仅在从文件加载时设置）
    config_path: Optional[Path] = Field(default=None, exclude=True)

    @classmethod
    def from_file(cls, path: Union[str, Path]) -> "Config":
        """从文件加载配置

        Args:
            path: 配置文件路径

        Returns:
            Config 对象

        Raises:
            FileNotFoundError: 配置文件不存在
            ValueError: 配置文件格式错误
        """
        path = Path(path).expanduser()
        if not path.exists():
            raise FileNotFoundError(f"配置文件不存在: {path}")

        with open(path, "r", encoding="utf-8") as f:
            raw_config = yaml.safe_load(f)

        # 解析环境变量引用
        resolved_config = cls._resolve_env_vars(raw_config)

        config = cls(**resolved_config)
        config.config_path = path
        return config

    @classmethod
    def _resolve_env_vars(cls, obj: Any) -> Any:
        """递归解析配置中的 ${ENV_VAR} 和 ${ENV_VAR:-default} 引用

        解析顺序（优先级从高到低）：
        1. .env 文件中的值（通过 load_dotenv 加载到环境变量）
        2. 系统环境变量
        3. 默认值（${VAR:-default} 中的 default 部分）

        解析规则：
        - ${VAR} - 环境变量不存在时替换为空字符串
        - ${VAR:-default} - 环境变量不存在时使用 default 作为默认值
        """
        if isinstance(obj, str):
            # 匹配 ${ENV_VAR} 或 ${ENV_VAR:-default} 格式
            # 支持 ${VAR:-default} 默认值语法
            pattern = r"\$\{([^}:-]+)(?::-([^}]*))?\}"
            matches = re.findall(pattern, obj)

            for env_var, default_value in matches:
                env_value = os.getenv(env_var)

                if env_value:
                    # 环境变量存在，使用其值
                    # 替换两种形式：${VAR} 和 ${VAR:-default}
                    obj = obj.replace(f"${{{env_var}}}", env_value)
                    obj = obj.replace(f"${{{env_var}:-{default_value}}}", env_value)
                elif default_value:
                    # 环境变量不存在，但有默认值（${VAR:-default} 形式）
                    # 只替换 ${VAR:-default} 形式
                    obj = obj.replace(f"${{{env_var}:-{default_value}}}", default_value)
                else:
                    # 环境变量不存在且无默认值（${VAR} 形式）
                    # 只替换 ${VAR} 形式，${VAR:-default} 保持不变
                    obj = obj.replace(f"${{{env_var}}}", "")

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

    def save(self, path: Union[str, Path]) -> None:
        """保存配置到文件"""
        path = Path(path).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)

        with open(path, "w", encoding="utf-8") as f:
            yaml.dump(self.model_dump(), f, default_flow_style=False, allow_unicode=True)


def _load_env_file() -> None:
    """加载 .env 文件到环境变量

    .env 文件路径: ~/.ascend_op_agent/.env
    如果文件不存在，静默忽略。
    """
    env_path = Path("~/.ascend_op_agent/.env").expanduser()
    if env_path.exists():
        load_dotenv(env_path)


def load_config(config_path: Optional[Union[str, Path]] = None) -> Config:
    """加载配置的便捷函数

    加载顺序：
    1. 加载 ~/.ascend_op_agent/.env 文件到环境变量（自动进行）
    2. 解析 config.yaml（支持 ${ENV_VAR} 和 ${ENV_VAR:-default} 语法）
    3. 环境变量值覆盖 YAML 中的引用

    Args:
        config_path: 配置文件路径，默认为 ~/.ascend_op_agent/config.yaml

    Returns:
        Config 对象
    """
    # 自动加载 .env 文件
    _load_env_file()

    if config_path is None:
        config_path = Config.default_config_path()
    else:
        config_path = Path(config_path)

    if not config_path.exists():
        # 如果默认配置文件不存在，返回默认配置
        config = Config()
        config.config_path = config_path
        return config

    return Config.from_file(config_path)
