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

"""一期-a falsifier baseline + R16 go/no-go gate 自动判定。

报告 task_store 当前状态(任务/线程/active)+ 自动汇总 dogfood 两个 metric +
输出 R16 gate 判定(GO/NO-GO)。metric 由 task 层埋点自动采集(非手动):

  1. spontaneous_task_switches — TaskCommands.select 从另一 active 切走时自动记
  2. context_juggling_complaints — 用户 `task complain` 一键标记

gate(R16):GO = switches>=threshold OR complaints>0;NO-GO(唯一) = switches<threshold
AND complaints==0 → task 层痛点未验证 → abandon(本 gate 是 task 层 kill-switch)。
threshold 默认 5 是占位,dogfood 后按真实分布定。

用法:
  PYTHONPATH=src python scripts/falsifier_baseline.py [--db PATH] [--since ISO] [--threshold N]
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Optional

from ascend_op_agent.task_store import TaskStore

METRIC_SWITCH = "spontaneous_task_switch"
METRIC_COMPLAINT = "context_juggling_complaint"


def build_report(
    store: TaskStore,
    since: Optional[str] = None,
    threshold: int = 5,
) -> dict:
    tasks = store.list_tasks()
    n_sw = len(store.list_metrics(METRIC_SWITCH, since=since))
    n_cp = len(store.list_metrics(METRIC_COMPLAINT, since=since))
    go = n_sw >= threshold or n_cp > 0
    return {
        "task_count": len(tasks),
        "active_task_id": store.get_active(),
        "tasks": [
            {
                "id": t.id,
                "type": t.type,
                "created_at": t.created_at,
                "state_native": t.state_native,
                "thread_count": len(store.get_task_threads(t.id)),
            }
            for t in tasks
        ],
        "dogfood_metrics": {
            "spontaneous_task_switches": n_sw,
            "context_juggling_complaints": n_cp,
        },
        "go_no_go": {
            "switches": n_sw,
            "complaints": n_cp,
            "threshold": threshold,
            "since": since,
            "decision": "GO" if go else "NO-GO (abandon task layer)",
            "rule": "GO = switches>=threshold OR complaints>0 (threshold 占位,dogfood 后定)",
        },
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="一期-a falsifier baseline + R16 gate 自动判定")
    ap.add_argument("--db", default=TaskStore.DEFAULT_DB_PATH, help="tasks.db 路径")
    ap.add_argument(
        "--since",
        default=None,
        help="ISO 时间戳,只计 created_at >= since 的 metric(默认全计)",
    )
    ap.add_argument(
        "--threshold",
        type=int,
        default=5,
        help="spontaneous_task_switches GO 阈值(默认 5,占位;dogfood 后定)",
    )
    args = ap.parse_args()
    store = TaskStore(args.db)
    report = build_report(store, since=args.since, threshold=args.threshold)
    json.dump(report, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
