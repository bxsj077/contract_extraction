from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any


SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
  project_code TEXT PRIMARY KEY, folder TEXT NOT NULL, status TEXT NOT NULL,
  risk_level TEXT NOT NULL DEFAULT '待确认', payload_json TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS contracts (
  project_code TEXT NOT NULL, direction TEXT NOT NULL, file_hash TEXT NOT NULL,
  parse_version TEXT NOT NULL, payload_json TEXT NOT NULL, updated_at TEXT NOT NULL,
  PRIMARY KEY(project_code, direction)
);
CREATE TABLE IF NOT EXISTS review_issues (
  id INTEGER PRIMARY KEY AUTOINCREMENT, project_code TEXT NOT NULL, category TEXT NOT NULL,
  description TEXT NOT NULL, status TEXT NOT NULL DEFAULT '待复核', resolution TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL, resolved_at TEXT
);
CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT, root_path TEXT NOT NULL, status TEXT NOT NULL,
  summary_json TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS mappings (
  mapping_type TEXT NOT NULL, source_value TEXT NOT NULL, standard_value TEXT NOT NULL,
  updated_at TEXT NOT NULL, PRIMARY KEY(mapping_type, source_value)
);
"""


class ReviewStore:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript(SCHEMA)

    def connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        return db

    def upsert_project(self, project_code: str, folder: str, status: str, risk: str, payload: dict[str, Any]) -> None:
        now = datetime.now().isoformat(timespec="seconds")
        with self.connect() as db:
            db.execute("""INSERT INTO projects VALUES(?,?,?,?,?,?)
                ON CONFLICT(project_code) DO UPDATE SET folder=excluded.folder,status=excluded.status,
                risk_level=excluded.risk_level,payload_json=excluded.payload_json,updated_at=excluded.updated_at""",
                (project_code, folder, status, risk, json.dumps(payload, ensure_ascii=False), now))

    def save_contract(self, project_code: str, direction: str, file_hash: str, version: str, payload: dict[str, Any]) -> None:
        now = datetime.now().isoformat(timespec="seconds")
        with self.connect() as db:
            db.execute("""INSERT INTO contracts VALUES(?,?,?,?,?,?)
                ON CONFLICT(project_code,direction) DO UPDATE SET file_hash=excluded.file_hash,
                parse_version=excluded.parse_version,payload_json=excluded.payload_json,updated_at=excluded.updated_at""",
                (project_code, direction, file_hash, version, json.dumps(payload, ensure_ascii=False), now))

    def load_contract(self, project_code: str, direction: str, file_hash: str, version: str) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute("SELECT payload_json FROM contracts WHERE project_code=? AND direction=? AND file_hash=? AND parse_version=?",
                             (project_code, direction, file_hash, version)).fetchone()
        return json.loads(row[0]) if row else None

    def replace_issues(self, project_code: str, issues: list[dict[str, Any]]) -> None:
        now = datetime.now().isoformat(timespec="seconds")
        with self.connect() as db:
            db.execute("DELETE FROM review_issues WHERE project_code=? AND status='待复核'", (project_code,))
            db.executemany("INSERT INTO review_issues(project_code,category,description,created_at) VALUES(?,?,?,?)",
                           [(project_code, i.get("category", "其他"), i.get("description", ""), now) for i in issues])

    def list_projects(self) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute("SELECT project_code,folder,status,risk_level,updated_at,payload_json FROM projects ORDER BY project_code").fetchall()
        return [{**dict(row), "payload": json.loads(row["payload_json"])} for row in rows]

    def get_project(self, code: str) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute("SELECT payload_json FROM projects WHERE project_code=?", (code,)).fetchone()
        return json.loads(row[0]) if row else None

    def list_issues(self, project_code: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT * FROM review_issues"
        args: tuple[Any, ...] = ()
        if project_code:
            query += " WHERE project_code=?"
            args = (project_code,)
        query += " ORDER BY status DESC,id"
        with self.connect() as db:
            return [dict(row) for row in db.execute(query, args).fetchall()]

    def resolve_issue(self, issue_id: int, resolution: str) -> bool:
        with self.connect() as db:
            cursor = db.execute("UPDATE review_issues SET status='已复核',resolution=?,resolved_at=? WHERE id=?",
                                (resolution, datetime.now().isoformat(timespec="seconds"), issue_id))
            return cursor.rowcount == 1

    def dashboard(self) -> dict[str, int]:
        projects = self.list_projects()
        issues = self.list_issues()
        payloads = [p["payload"] for p in projects]
        diffs = [d for p in payloads for key in ("equipment_differences", "schedule_differences", "scope_differences") for d in p.get(key, [])]
        return {"项目总数": len(projects), "已完成对比项目数": sum(p["status"] == "已完成" for p in projects),
                "解析失败项目数": sum(p["status"] == "处理失败" for p in projects),
                "高风险项目数": sum(p["risk_level"] == "高风险" for p in projects),
                "设备缺项数量": sum(d.get("status") == "后向未采购" for d in diffs),
                "工期风险数量": sum(d.get("category") == "工期" and d.get("risk_level") in {"高风险", "中风险"} for d in diffs),
                "实施内容缺项数量": sum(d.get("status") == "实施内容缺失" for d in diffs),
                "待人工复核数量": sum(i["status"] == "待复核" for i in issues)}
