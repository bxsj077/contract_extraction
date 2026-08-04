from __future__ import annotations

from pathlib import Path

from .system_models import ProjectFiles


FORWARD_NAME = "前向合同.pdf"
BACKWARD_NAME = "后向合同.pdf"
IGNORED_DIRS = {"_合同提取结果", "_履约风险审查结果", "output", "outputs", "ocr_cache"}


def scan_projects(root: Path, wanted: set[str] | None = None) -> list[ProjectFiles]:
    projects: list[ProjectFiles] = []
    if not root.exists():
        raise FileNotFoundError(f"合同根目录不存在：{root}")
    for folder in sorted((p for p in root.iterdir() if p.is_dir()), key=lambda p: p.name):
        if folder.name.startswith((".", "_")) or folder.name in IGNORED_DIRS:
            continue
        if wanted and folder.name not in wanted:
            continue
        forward = folder / FORWARD_NAME
        backward = folder / BACKWARD_NAME
        all_pdfs = sorted(p for p in folder.iterdir() if p.is_file() and p.suffix.lower() == ".pdf")
        extras = [str(p) for p in all_pdfs if p.name not in {FORWARD_NAME, BACKWARD_NAME}]
        issues: list[str] = []
        if not forward.exists():
            issues.append("缺少前向合同")
        if not backward.exists():
            issues.append("缺少后向合同")
        if extras:
            issues.append(f"存在{len(extras)}个默认不处理的其他PDF")
        status = "可对比" if forward.exists() and backward.exists() else ("可解析单份合同" if forward.exists() or backward.exists() else "项目处理失败")
        projects.append(ProjectFiles(folder.name, str(folder), str(forward) if forward.exists() else None,
                                     str(backward) if backward.exists() else None, extras, status, issues))
    return projects
