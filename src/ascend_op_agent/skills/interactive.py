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

"""InteractiveSelector - 交互式Skill选择

参考Hermes curses_checklist实现，支持:
- TUI模式: 使用方向键导航，空格切换，Enter确认
- 纯文本回退: 显示编号列表，用户输入编号切换选择
"""

import logging
import sys
from typing import Callable, Optional

from ascend_op_agent.skills.models import SkillInfo

logger = logging.getLogger(__name__)


class InteractiveSelector:
    """交互式Skill选择器

    支持TUI和纯文本两种模式。
    """

    def __init__(
        self,
        skills: list[SkillInfo],
        title: str = "可安装的 Skills",
        allow_cancel: bool = True,
    ):
        """
        Args:
            skills: SkillInfo列表
            title: 选择标题
            allow_cancel: 是否允许取消
        """
        self.skills = skills
        self.title = title
        self.allow_cancel = allow_cancel
        self._selected: set[int] = set()

    def select(self) -> Optional[list[int]]:
        """显示交互式选择界面

        Returns:
            选中的索引列表，None表示取消
        """
        # 检测是否支持TTY模式
        if hasattr(sys.stdin, "isatty") and sys.stdin.isatty():
            # 尝试TUI模式
            try:
                return self._select_tui()
            except Exception as e:
                logger.debug(f"TUI mode failed, falling back to text mode: {e}")

        # 回退到纯文本模式
        return self._select_text()

    def _select_tui(self) -> Optional[list[int]]:
        """TUI模式选择"""
        try:
            import curses
        except ImportError:
            raise RuntimeError("curses module not available")

        def run_curses(stdscr):
            curses.curs_set(0)
            stdscr.nodelay(True)
            stdscr.timeout(100)

            current_row = 0
            key = 0

            while key != curses.KEY_ENTER and key != 10 and key != 13:
                stdscr.clear()
                height, width = stdscr.getmaxyx()

                # 显示标题
                stdscr.addstr(0, 0, self.title, curses.A_BOLD)
                stdscr.addstr(1, 0, "(输入编号切换选择, ↑↓导航, SPACE切换, ENTER确认)", curses.A_DIM)

                # 显示skills列表
                for i, skill in enumerate(self.skills):
                    row = 3 + i
                    if row >= height - 2:
                        break

                    if i == current_row:
                        stdscr.addstr(row, 0, "> ", curses.A_REVERSE)
                    else:
                        stdscr.addstr(row, 0, "  ")

                    checkbox = "[✓]" if i in self._selected else "[ ]"
                    stdscr.addstr(row, 2, checkbox)

                    # 显示skill信息
                    display_line = f"[{i + 1}] {skill.name}"
                    if skill.tags:
                        display_line += f" ({', '.join(skill.tags[:2])})"
                    stdscr.addstr(row, 7, display_line[:width - 10])

                    # 显示描述（截断）
                    if skill.description:
                        desc = skill.description[:width - 15]
                        stdscr.addstr(row + 1, 4, f"    {desc}", curses.A_DIM)

                # 显示底部状态
                if self.allow_cancel:
                    stdscr.addstr(height - 2, 0, "ESC/q: 取消 | ENTER: 确认", curses.A_DIM)
                else:
                    stdscr.addstr(height - 2, 0, "ENTER: 确认", curses.A_DIM)

                selected_count = len(self._selected)
                stdscr.addstr(height - 1, 0, f"已选择: {selected_count} 个", curses.A_BOLD)

                stdscr.refresh()

                key = stdscr.getch()

                if key == curses.KEY_UP:
                    current_row = max(0, current_row - 1)
                elif key == curses.KEY_DOWN:
                    current_row = min(len(self.skills) - 1, current_row + 1)
                elif key == curses.KEY_HOME:
                    current_row = 0
                elif key == curses.KEY_END:
                    current_row = len(self.skills) - 1
                elif key == ord(" "):
                    # 切换选择
                    if current_row in self._selected:
                        self._selected.remove(current_row)
                    else:
                        self._selected.add(current_row)
                elif key == ord("\n"):
                    break
                elif key in (curses.KEY_EXIT, 27):  # ESC
                    if self.allow_cancel:
                        return None
                elif key in (ord("q"), ord("Q")):
                    if self.allow_cancel:
                        return None
                elif key >= 49 and key <= 57:  # 数字键 1-9
                    idx = key - 49
                    if idx < len(self.skills):
                        current_row = idx
                elif key >= 256:  # 特殊键
                    pass

            return list(self._selected)

        return curses.wrapper(run_curses)

    def _select_text(self) -> Optional[list[int]]:
        """纯文本模式选择"""
        print(f"\n{self.title} (输入编号切换选择，按 Enter 确认)")
        print("-" * 60)

        for i, skill in enumerate(self.skills):
            checkbox = "[✓]" if i in self._selected else "[ ]"
            tags_str = f" ({', '.join(skill.tags[:2])})" if skill.tags else ""
            print(f"{checkbox} [{i + 1}] {skill.name}{tags_str}")
            if skill.description:
                print(f"      {skill.description[:60]}")

        print("-" * 60)

        if self.allow_cancel:
            print("按 ENTER 确认安装所选 Skill (ESC 或 q 取消)")

        # 等待输入
        while True:
            try:
                key = input("\n请输入编号: ").strip()

                if key.lower() in ("q", "quit", "esc"):
                    if self.allow_cancel:
                        return None
                    continue

                if key == "":
                    # 确认选择
                    return list(self._selected)

                # 切换对应编号的选择状态
                idx = int(key) - 1
                if 0 <= idx < len(self.skills):
                    if idx in self._selected:
                        self._selected.remove(idx)
                        print(f"  取消选择: {self.skills[idx].name}")
                    else:
                        self._selected.add(idx)
                        print(f"  已选择: {self.skills[idx].name}")
                else:
                    print(f"  无效编号: {key}")

            except ValueError:
                print("  请输入有效编号")
            except EOFError:
                return None


def format_skills_for_selection(skills: list[SkillInfo]) -> str:
    """格式化skills列表为选择文本

    Args:
        skills: SkillInfo列表

    Returns:
        格式化后的文本
    """
    lines = ["可安装的 Skills (输入编号切换选择，按 Enter 确认):\n"]

    for i, skill in enumerate(skills):
        tags_str = f" ({', '.join(skill.tags[:2])})" if skill.tags else ""
        lines.append(f"[ ] [{i + 1}] {skill.name}{tags_str}")
        if skill.description:
            lines.append(f"      {skill.description[:60]}")

    lines.append("\n按 Enter 确认安装所选 Skill (ESC 取消)")
    return "\n".join(lines)


def handle_skill_input(
    key: str,
    selected: set[int],
    skills: list[SkillInfo],
) -> Optional[list[int]]:
    """处理用户键盘输入（用于TUI模式）

    Args:
        key: 键盘输入
        selected: 当前已选择的索引集合
        skills: skill列表

    Returns:
        None表示继续等待，[]表示确认，[-1]表示取消
    """
    if key in ("Enter", "\n"):
        return list(selected)
    elif key in ("Escape", "q", "Q"):
        return None
    elif key.isdigit():
        idx = int(key) - 1
        if 0 <= idx < len(skills):
            if idx in selected:
                selected.remove(idx)
            else:
                selected.add(idx)
    return None
