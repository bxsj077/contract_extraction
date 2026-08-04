from __future__ import annotations

import os
import re
import shutil
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
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
                "目录格式": "<根目录>/<前向合同编号>/前向/*.pdf、后向/*.pdf；根目录也可直接指向单个合同编号目录"}

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
    async def upload_project(project_code: str = Form(...), forward_pdfs: list[UploadFile] = File(...),
                             backward_pdfs: list[UploadFile] = File(...), overwrite: bool = Query(False),
                             process_now: bool = Query(True)):
        code = project_code.strip()
        if not code or len(code) > 100 or re.search(r"[\\/:*?\"<>|]", code) or code in {".", ".."}:
            raise HTTPException(400, "项目编码为空、过长或包含非法路径字符")
        folder = service.contract_root / code
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
            project = scan_projects(service.contract_root, {code})[0]
            payload["审查结果"] = service.process_project(project, force=overwrite).to_dict()
            payload["处理状态"] = "已处理"
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
