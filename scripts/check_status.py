from __future__ import annotations

import argparse
import json
from pathlib import Path

from contract_extraction.storage import ReviewStore


def main() -> None:
    parser = argparse.ArgumentParser(description="查看合同审查后台任务和每份合同解析状态")
    parser.add_argument("--output", type=Path, default=Path("data/review_output"), help="审查结果目录")
    parser.add_argument("--project", help="只查看指定项目编码")
    args = parser.parse_args()
    store = ReviewStore(args.output / "contract_review.db")
    tasks = [x for x in store.list_tasks(20) if not args.project or x.get("project_code") == args.project]
    projects = [x for x in store.list_projects() if not args.project or x.get("project_code") == args.project]
    print("最近后台任务：")
    print(json.dumps([{k: x.get(k, "") for k in ("task_id", "project_code", "status", "stage", "progress", "completed_at", "error")}
                      for x in tasks], ensure_ascii=False, indent=2))
    print("\n合同解析状态：")
    print(json.dumps([{"项目编码": x["project_code"], "项目状态": x["status"],
                       "合同状态": x["payload"].get("contract_parse_statuses", [])} for x in projects],
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
