from __future__ import annotations

import os
import re
import shutil
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel

from .project_io import scan_projects
from .review_export import export_review
from .review_service import CORRECTABLE_FIELDS, ReviewService


class ResolveRequest(BaseModel):
    resolution: str


class CorrectionRequest(BaseModel):
    contract_key: str
    field_path: str
    corrected_value: object | None = None
    note: str = ""


def create_app(contract_root: Path | None = None, output_root: Path | None = None) -> FastAPI:
    project_root = Path(__file__).resolve().parents[2]
    root = contract_root or Path(os.getenv("CONTRACT_ROOT", str(project_root / "data" / "contracts")))
    output = output_root or Path(os.getenv("CONTRACT_OUTPUT", str(project_root / "data" / "review_output")))
    root.mkdir(parents=True, exist_ok=True)
    output.mkdir(parents=True, exist_ok=True)
    service = ReviewService(root, output)
    app = FastAPI(title="前后向合同智能解析与履约风险审查系统", version="0.2.0")
    app.state.service = service
    app.state.tasks = {}

    def update_task(task_id: str, **values) -> dict:
        task = app.state.tasks.get(task_id) or service.store.get_task(task_id) or {"task_id": task_id}
        task.update(values)
        app.state.tasks[task_id] = task
        service.store.save_task(task)
        return task

    def run_review_task(task_id: str, code: str, overwrite: bool) -> None:
        task = app.state.tasks[task_id]
        try:
            task = update_task(task_id, status="运行中", stage="正在枚举前向及后向合同", progress=20,
                               started_at=datetime.now().isoformat(timespec="seconds"))
            found = scan_projects(service.contract_root, {code})
            if not found:
                raise RuntimeError("上传目录中未找到该项目")
            task = update_task(task_id, stage=f"正在逐份执行OCR解析和履约风险审查（前向{len(found[0].forward_pdfs)}份、后向{len(found[0].backward_pdfs)}份）",
                               progress=45, forward_count=len(found[0].forward_pdfs), backward_count=len(found[0].backward_pdfs))
            result = service.process_project(found[0], force=overwrite)
            if result.status == "处理失败":
                detail = "；".join(x.get("description", "") for x in result.review_issues) or "项目处理失败"
                raise RuntimeError(detail)
            update_task(task_id, status=result.status, stage="解析和审查已完成" if result.status == "已完成" else "解析完成但存在页面错误，请查看合同解析状态",
                        progress=100, result=result.to_dict(), risk_level=result.risk_level,
                        completed_at=datetime.now().isoformat(timespec="seconds"),
                        result_directory=str(service.output_root / "项目明细" / code))
        except Exception as exc:
            update_task(task_id, status="失败", stage="处理失败", progress=100, error=str(exc),
                        completed_at=datetime.now().isoformat(timespec="seconds"))

    @app.get("/", response_class=HTMLResponse)
    def home():
        return (Path(__file__).parent / "web" / "index.html").read_text(encoding="utf-8")

    @app.get("/api/health")
    def health():
        return {"status": "ok", "contract_root": str(service.contract_root), "output_root": str(service.output_root)}

    @app.get("/api/config")
    def config():
        return {"合同上传根目录": str(service.contract_root), "审查结果目录": str(service.output_root),
                "项目数据库": str(service.store.path),
                "后台运行日志": str(service.output_root / "logs" / "contract_review.log"),
                "目录格式": "<根目录>/<前向合同编号>/前向/*.pdf、后向/*.pdf；根目录也可直接指向单个合同编号目录"}

    @app.get("/api/correction-fields")
    def correction_fields():
        labels = {
            "contract_number": "合同编号", "contract_name": "合同名称", "party_a": "甲方", "party_b": "乙方",
            "sign_date": "合同签订日期", "effective_date": "合同生效日期", "contract_type": "合同性质",
            "procurement_involved": "是否涉及货物采购", "procurement_note": "货物采购说明",
            "time_plan.duration_value": "工期数值", "time_plan.duration_unit": "工期单位",
            "time_plan.duration_conclusion": "工期提取结论", "time_plan.duration_raw": "工期约定原文",
            "time_plan.calculation_status": "工期计算状态", "time_plan.start_condition_type": "起算条件类型",
            "time_plan.start_condition_text": "起算条件原文", "time_plan.start_date": "实际起算日期",
            "time_plan.finish_date": "预计完成日期", "time_plan.completion_node": "完成节点",
            "time_plan.fixed_deadline": "固定截止日期",
        }
        return [{"field_path": field, "label": labels.get(field, field)} for field in sorted(CORRECTABLE_FIELDS)]

    @app.get("/api/tasks")
    def task_list():
        return service.store.list_tasks(20)

    @app.get("/api/tasks/{task_id}")
    def task_status(task_id: str):
        task = app.state.tasks.get(task_id) or service.store.get_task(task_id)
        if not task:
            raise HTTPException(404, "任务不存在")
        return task

    async def save_pdf(upload: UploadFile, target: Path) -> None:
        if not upload.filename or Path(upload.filename).suffix.lower() != ".pdf":
            raise HTTPException(400, f"{target.name}必须上传PDF文件")
        temporary = target.with_suffix(".uploading")
        try:
            with temporary.open("wb") as stream:
                while chunk := await upload.read(1024 * 1024):
                    stream.write(chunk)
            with temporary.open("rb") as stream:
                if stream.read(5) != b"%PDF-":
                    raise HTTPException(400, f"{upload.filename}不是有效PDF文件")
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
            await upload.close()

    @app.post("/api/projects/upload")
    async def upload_project(background_tasks: BackgroundTasks, project_code: str = Form(...),
                             forward_pdfs: list[UploadFile] = File(...),
                             backward_pdfs: list[UploadFile] = File(...), overwrite: bool = Query(False),
                             process_now: bool = Query(True)):
        code = project_code.strip()
        if not code or len(code) > 100 or re.search(r"[\\/:*?\"<>|]", code) or code in {".", ".."}:
            raise HTTPException(400, "项目编码为空、过长或包含非法路径字符")
        single_project_mode = (service.contract_root / "前向").is_dir() or (service.contract_root / "后向").is_dir()
        if single_project_mode and code != service.contract_root.name:
            raise HTTPException(400, f"当前服务绑定单项目目录，只能上传项目 {service.contract_root.name}")
        folder = service.contract_root if single_project_mode else service.contract_root / code
        if folder.exists() and not overwrite and any(folder.iterdir()):
            raise HTTPException(409, "项目已存在；如需替换，请勾选覆盖已有项目")
        forward_dir = folder / "前向"
        backward_dir = folder / "后向"
        if overwrite:
            shutil.rmtree(forward_dir, ignore_errors=True)
            shutil.rmtree(backward_dir, ignore_errors=True)
        forward_dir.mkdir(parents=True, exist_ok=True)
        backward_dir.mkdir(parents=True, exist_ok=True)
        try:
            forward_paths = []
            for index, upload in enumerate(forward_pdfs, 1):
                name = re.sub(r"[\\/:*?\"<>|]", "_", Path(upload.filename or f"前向_{index}.pdf").name)
                target = forward_dir / name
                if target.exists():
                    target = forward_dir / f"{index:03d}_{name}"
                await save_pdf(upload, target); forward_paths.append(str(target))
            backward_paths = []
            for index, upload in enumerate(backward_pdfs, 1):
                name = re.sub(r"[\\/:*?\"<>|]", "_", Path(upload.filename or f"后向_{index}.pdf").name)
                target = backward_dir / name
                if target.exists():
                    target = backward_dir / f"{index:03d}_{name}"
                await save_pdf(upload, target); backward_paths.append(str(target))
        except Exception:
            if not any(folder.iterdir()):
                shutil.rmtree(folder, ignore_errors=True)
            raise
        payload = {"项目编码": code, "项目目录": str(folder), "前向文件": forward_paths,
                   "后向合同": backward_paths, "后向合同数量": len(backward_paths), "处理状态": "已上传"}
        if process_now:
            task_id = uuid.uuid4().hex
            update_task(task_id, project_code=code, status="排队中",
                        stage="合同文件已保存，等待开始", progress=10,
                        created_at=datetime.now().isoformat(timespec="seconds"))
            background_tasks.add_task(run_review_task, task_id, code, overwrite)
            payload.update({"处理状态": "后台处理中", "task_id": task_id,
                            "任务查询地址": f"/api/tasks/{task_id}"})
        return payload

    @app.get("/api/dashboard")
    def dashboard():
        return service.store.dashboard()

    @app.get("/api/projects")
    def projects():
        return service.store.list_projects()

    @app.get("/api/projects/{project_code}")
    def project(project_code: str):
        payload = service.store.get_project(project_code)
        if not payload:
            raise HTTPException(404, "项目不存在或尚未处理")
        return payload

    @app.delete("/api/projects/{project_code}")
    def delete_project(project_code: str):
        code = project_code.strip()
        if not code or len(code) > 100 or re.search(r"[\\/:*?\"<>|]", code) or code in {".", ".."}:
            raise HTTPException(400, "项目编码无效")
        running = [task for task in app.state.tasks.values()
                   if task.get("project_code") == code and task.get("status") in {"排队中", "运行中"}]
        if running:
            raise HTTPException(409, "该项目正在解析，完成后才能删除")

        root_resolved = service.contract_root.resolve()
        single_project_mode = (service.contract_root / "前向").is_dir() or (service.contract_root / "后向").is_dir()
        project_folder = service.contract_root if single_project_mode and service.contract_root.name == code else service.contract_root / code
        project_resolved = project_folder.resolve()
        if project_resolved != root_resolved and root_resolved not in project_resolved.parents:
            raise HTTPException(400, "项目目录超出合同上传根目录，拒绝删除")

        existed = service.store.get_project(code) is not None or project_folder.exists()
        if not existed:
            raise HTTPException(404, "项目不存在")

        removed_paths: list[str] = []
        if single_project_mode and project_resolved == root_resolved:
            for name in ("前向", "后向"):
                target = service.contract_root / name
                if target.exists():
                    shutil.rmtree(target)
                    removed_paths.append(str(target))
        elif project_folder.exists():
            shutil.rmtree(project_folder)
            removed_paths.append(str(project_folder))

        detail = service.output_root / "项目明细" / code
        if detail.exists():
            shutil.rmtree(detail)
            removed_paths.append(str(detail))
        cache_root = service.output_root / "ocr_cache"
        for cache in cache_root.glob(f"{code}_*") if cache_root.exists() else []:
            if cache.is_dir():
                shutil.rmtree(cache)
                removed_paths.append(str(cache))

        deleted_records = service.store.delete_project(code)
        for task_id, task in list(app.state.tasks.items()):
            if task.get("project_code") == code:
                del app.state.tasks[task_id]
        return {"status": "项目已彻底删除", "project_code": code,
                "removed_paths": removed_paths, "deleted_records": deleted_records}

    @app.get("/api/projects/{project_code}/corrections")
    def corrections(project_code: str):
        return service.store.list_corrections(project_code)

    @app.post("/api/projects/{project_code}/corrections")
    def save_correction(project_code: str, request: CorrectionRequest):
        if request.field_path not in CORRECTABLE_FIELDS:
            raise HTTPException(400, "该字段不允许人工纠正")
        if request.contract_key != "前向" and not re.fullmatch(r"后向:\d{3}", request.contract_key):
            raise HTTPException(400, "合同标识应为“前向”或“后向:001”格式")
        if request.field_path == "time_plan.duration_value" and request.corrected_value not in (None, ""):
            try:
                if int(request.corrected_value) < 0:
                    raise ValueError
            except (TypeError, ValueError):
                raise HTTPException(400, "工期数值必须为非负整数或留空")
        correction = service.store.save_correction(project_code, request.contract_key, request.field_path,
                                                     request.corrected_value, request.note.strip())
        found = scan_projects(service.contract_root, {project_code})
        if not found:
            raise HTTPException(404, "纠正记录已保存，但项目合同目录不存在，暂未重新计算")
        result = service.process_project(found[0], force=False)
        return {"status": "已保存并重新计算", "correction": correction, "project_status": result.status,
                "risk_level": result.risk_level}

    @app.delete("/api/projects/{project_code}/corrections/{correction_id}")
    def remove_correction(project_code: str, correction_id: int):
        existing = next((x for x in service.store.list_corrections(project_code) if x["id"] == correction_id), None)
        if not existing or not service.store.delete_correction(correction_id):
            raise HTTPException(404, "人工纠正记录不存在")
        found = scan_projects(service.contract_root, {project_code})
        result = service.process_project(found[0], force=False) if found else None
        return {"status": "已删除并重新计算" if result else "已删除", "project_status": result.status if result else ""}

    @app.post("/api/scan")
    def scan(force: bool = Query(False)):
        if not service.contract_root.exists():
            raise HTTPException(400, "CONTRACT_ROOT不存在")
        return service.run(force=force)

    @app.post("/api/projects/{project_code}/process")
    def process(project_code: str, force: bool = Query(False)):
        found = scan_projects(service.contract_root, {project_code})
        if not found:
            raise HTTPException(404, "项目目录不存在")
        return service.process_project(found[0], force).to_dict()

    @app.get("/api/reviews")
    def reviews(project_code: str | None = None):
        return service.store.list_issues(project_code)

    @app.post("/api/reviews/{issue_id}/resolve")
    def resolve(issue_id: int, request: ResolveRequest):
        if not service.store.resolve_issue(issue_id, request.resolution):
            raise HTTPException(404, "复核事项不存在")
        return {"status": "已复核"}

    @app.get("/api/export.xlsx")
    def export():
        path = export_review(service.store, service.output_root / f"前后向合同履约风险审查_{datetime.now():%Y%m%d_%H%M%S}.xlsx")
        return FileResponse(path, filename=path.name)

    return app


app = create_app()
