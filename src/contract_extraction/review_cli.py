from __future__ import annotations

import argparse
import json
import logging
from datetime import datetime
from pathlib import Path

from .review_export import export_review
from .review_service import ReviewService


def main() -> int:
    parser = argparse.ArgumentParser(description="前后向合同智能解析与履约风险审查")
    parser.add_argument("--input", type=Path, required=True, help="项目根目录，每个一级目录包含前向合同.pdf和后向合同.pdf")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--projects", nargs="*", help="只处理指定项目编码")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--config", type=Path)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    service = ReviewService(args.input, args.output, args.config)
    summary = service.run(set(args.projects or []) or None, args.force)
    excel = export_review(service.store, args.output / f"前后向合同履约风险审查_{datetime.now():%Y%m%d_%H%M%S}.xlsx")
    summary["Excel"] = str(excel)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
