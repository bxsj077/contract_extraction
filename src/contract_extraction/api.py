from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel

from .project_io import scan_projects
from .review_export import export_review
from .review_service import ReviewService


class ResolveRequest(BaseModel):
    resolution: str


def create_app(contract_root: Path | None = None, output_root: Path | None = None) -> FastAPI:
    root = contract_root or (Path(os.environ["CONTRACT_ROOT"]) if os.getenv("CONTRACT_ROOT") else None)
    output = output_root or Path(os.getenv("CONTRACT_OUTPUT", "./review_output"))
    service = ReviewService(root or Path("."), output)
    app = FastAPI(title="前后向合同智能解析与履约风险审查系统", version="0.2.0")
    app.state.service = service

    @app.get("/", response_class=HTMLResponse)
    def home():
        return (Path(__file__).parent / "web" / "index.html").read_text(encoding="utf-8")

    @app.get("/api/health")
    def health():
        return {"status": "ok", "contract_root": str(service.contract_root), "output_root": str(service.output_root)}

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
