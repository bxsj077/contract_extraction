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
from .review_service import ReviewService


class ResolveRequest(BaseModel):
    resolution: str


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
