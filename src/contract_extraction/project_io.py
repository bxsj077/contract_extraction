from __future__ import annotations

from pathlib import Path

from .system_models import ProjectFiles


FORWARD_DIR = "前向"
BACKWARD_DIR = "后向"
REVENUE_PLAN_DIR = "收入收款计划"
LEGACY_FORWARD = "前向合同.pdf"
LEGACY_BACKWARD = "后向合同.pdf"
IGNORED_DIRS = {"_合同提取结果", "_履约风险审查结果", "output", "outputs", "ocr_cache", "review_output", "data"}


def _pdfs(folder: Path) -> list[str]:
    if not folder.exists() or not folder.is_dir():
        return []
    return [str(p) for p in sorted(folder.rglob("*")) if p.is_file() and p.suffix.lower() == ".pdf"]


def _plan_files(folder: Path) -> list[str]:
    if not folder.exists() or not folder.is_dir():
        return []
    return [str(p) for p in sorted(folder.rglob("*"))
            if p.is_file() and p.suffix.lower() in {".xls", ".xlsx", ".xml"}]


def _looks_like_project(folder: Path) -> bool:
    return (folder / FORWARD_DIR).is_dir() or (folder / BACKWARD_DIR).is_dir() or \
           (folder / LEGACY_FORWARD).is_file() or (folder / LEGACY_BACKWARD).is_file()


def _build_project(folder: Path) -> ProjectFiles:
    forward = _pdfs(folder / FORWARD_DIR)
    backward = _pdfs(folder / BACKWARD_DIR)
    revenue_plans = _plan_files(folder / REVENUE_PLAN_DIR)
    legacy_forward = folder / LEGACY_FORWARD
    legacy_backward = folder / LEGACY_BACKWARD
    if not forward and legacy_forward.is_file():
        forward = [str(legacy_forward)]
    if not backward and legacy_backward.is_file():
        backward = [str(legacy_backward)]

    direction_paths = {str(Path(p).resolve()) for p in forward + backward}
    extras = [str(p) for p in sorted(folder.glob("*.pdf")) if str(p.resolve()) not in direction_paths]
    issues: list[str] = []
    if not forward:
        issues.append("缺少前向合同")
    if not backward:
        issues.append("缺少后向合同")
    if len(forward) > 1:
        issues.append(f"前向目录包含{len(forward)}个PDF，将作为一份前向合同及附件合并解析")
    if len(backward) > 1:
        issues.append(f"识别到{len(backward)}份后向合同，将分别解析后汇总审查")
    if extras:
        issues.append(f"项目根目录存在{len(extras)}个未归入前向/后向的PDF")
    status = "可对比" if forward and backward else ("可解析单份合同" if forward or backward else "项目处理失败")
    return ProjectFiles(folder.name, str(folder), forward, backward, extras, status, issues, revenue_plans)


def scan_projects(root: Path, wanted: set[str] | None = None) -> list[ProjectFiles]:
    if not root.exists():
        raise FileNotFoundError(f"合同目录不存在：{root}")
    # 允许直接选择单项目目录，例如 .../JSNJA2513970CGN00。
    if root.is_dir() and _looks_like_project(root):
        if wanted and root.name not in wanted:
            return []
        return [_build_project(root)]

    projects: list[ProjectFiles] = []
    for folder in sorted((p for p in root.iterdir() if p.is_dir()), key=lambda p: p.name):
        if folder.name.startswith((".", "_")) or folder.name in IGNORED_DIRS:
            continue
        if wanted and folder.name not in wanted:
            continue
        if _looks_like_project(folder):
            projects.append(_build_project(folder))
    return projects
