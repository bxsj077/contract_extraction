from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from .pipeline import run


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="中文合同PDF批量OCR与结构化抽取")
    parser.add_argument("--input", type=Path, required=True, help="合同根目录；其一级子目录名应为合同号")
    parser.add_argument("--output", type=Path, required=True, help="输出目录（不得放入任一合同文件夹）")
    parser.add_argument("--config", type=Path, help="可选JSON配置")
    parser.add_argument("--contracts", nargs="*", help="只处理指定合同号")
    parser.add_argument("--no-resume", action="store_true", help="不复用合同级断点结果")
    parser.add_argument("--force", action="store_true", help="强制重新OCR和抽取")
    parser.add_argument("--ocr-dpi", type=int, help="普通OCR DPI，默认300")
    parser.add_argument("--signature-dpi", type=int, help="签章页增强OCR DPI，默认600")
    parser.add_argument("--verbose", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    if args.input.resolve() == args.output.resolve():
        raise SystemExit("输出目录不能与合同根目录相同")
    summary = run(args.input, args.output, args.config, set(args.contracts or []) or None,
                  not args.no_resume, args.force, args.ocr_dpi, args.signature_dpi)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
