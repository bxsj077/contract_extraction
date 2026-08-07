from __future__ import annotations

import json
import hashlib
import sqlite3
from dataclasses import asdict, is_dataclass
from contextlib import contextmanager
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
CREATE TABLE IF NOT EXISTS tasks (
  task_id TEXT PRIMARY KEY, project_code TEXT NOT NULL, status TEXT NOT NULL,
  stage TEXT NOT NULL, progress INTEGER NOT NULL DEFAULT 0, payload_json TEXT NOT NULL,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS field_corrections (
  id INTEGER PRIMARY KEY AUTOINCREMENT, project_code TEXT NOT NULL,
  contract_key TEXT NOT NULL, field_path TEXT NOT NULL, corrected_value_json TEXT NOT NULL,
  note TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  UNIQUE(project_code, contract_key, field_path)
);
CREATE TABLE IF NOT EXISTS dismissed_findings (
  id INTEGER PRIMARY KEY AUTOINCREMENT, project_code TEXT NOT NULL,
  category TEXT NOT NULL, finding_key TEXT NOT NULL, payload_json TEXT NOT NULL,
  note TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL,
  UNIQUE(project_code, category, finding_key)
);
CREATE TABLE IF NOT EXISTS finding_overrides (
  id INTEGER PRIMARY KEY AUTOINCREMENT, project_code TEXT NOT NULL,
  category TEXT NOT NULL, finding_key TEXT NOT NULL, status TEXT NOT NULL,
  risk_level TEXT NOT NULL, description TEXT NOT NULL, note TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  UNIQUE(project_code, category, finding_key)
);
"""


def finding_key(category: str, finding: Any) -> str:
    """Return a stable semantic key for a review finding across recalculations."""
    payload = asdict(finding) if is_dataclass(finding) else dict(finding)
    forward = payload.get("forward") or {}
    identity = {
        "category": category,
        "rule_id": payload.get("rule_id", ""),
        "title": payload.get("title", ""),
        "standard_name": forward.get("standard_name", ""),
        "brand": forward.get("brand", ""),
        "model": forward.get("model", ""),
    }
    encoded = json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class ReviewStore:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript(SCHEMA)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

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

    def save_task(self, task: dict[str, Any]) -> None:
        now = datetime.now().isoformat(timespec="seconds")
        created = str(task.get("created_at") or now)
        with self.connect() as db:
            db.execute("""INSERT INTO tasks VALUES(?,?,?,?,?,?,?,?)
                ON CONFLICT(task_id) DO UPDATE SET status=excluded.status,stage=excluded.stage,
                progress=excluded.progress,payload_json=excluded.payload_json,updated_at=excluded.updated_at""",
                (task["task_id"], task.get("project_code", ""), task.get("status", ""), task.get("stage", ""),
                 int(task.get("progress", 0)), json.dumps(task, ensure_ascii=False), created, now))

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute("SELECT payload_json FROM tasks WHERE task_id=?", (task_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def list_tasks(self, limit: int = 20) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute("SELECT payload_json FROM tasks ORDER BY updated_at DESC LIMIT ?", (limit,)).fetchall()
        return [json.loads(row[0]) for row in rows]

    def save_correction(self, project_code: str, contract_key: str, field_path: str,
                        corrected_value: Any, note: str = "") -> dict[str, Any]:
        now = datetime.now().isoformat(timespec="seconds")
        encoded = json.dumps(corrected_value, ensure_ascii=False)
        with self.connect() as db:
            db.execute("""INSERT INTO field_corrections(project_code,contract_key,field_path,corrected_value_json,note,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?) ON CONFLICT(project_code,contract_key,field_path) DO UPDATE SET
                corrected_value_json=excluded.corrected_value_json,note=excluded.note,updated_at=excluded.updated_at""",
                (project_code, contract_key, field_path, encoded, note, now, now))
            row = db.execute("SELECT * FROM field_corrections WHERE project_code=? AND contract_key=? AND field_path=?",
                             (project_code, contract_key, field_path)).fetchone()
        return self._correction_row(row)

    @staticmethod
    def _correction_row(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        result["corrected_value"] = json.loads(result.pop("corrected_value_json"))
        return result

    def list_corrections(self, project_code: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT * FROM field_corrections"
        args: tuple[Any, ...] = ()
        if project_code:
            query += " WHERE project_code=?"
            args = (project_code,)
        query += " ORDER BY project_code,contract_key,field_path"
        with self.connect() as db:
            rows = db.execute(query, args).fetchall()
        return [self._correction_row(row) for row in rows]

    def delete_correction(self, correction_id: int) -> bool:
        with self.connect() as db:
            cursor = db.execute("DELETE FROM field_corrections WHERE id=?", (correction_id,))
            return cursor.rowcount == 1

    def dismiss_finding(self, project_code: str, category: str, finding: Any, note: str = "") -> dict[str, Any]:
        now = datetime.now().isoformat(timespec="seconds")
        payload = asdict(finding) if is_dataclass(finding) else dict(finding)
        key = finding_key(category, payload)
        with self.connect() as db:
            db.execute("""INSERT INTO dismissed_findings(project_code,category,finding_key,payload_json,note,created_at)
                VALUES(?,?,?,?,?,?) ON CONFLICT(project_code,category,finding_key) DO UPDATE SET
                payload_json=excluded.payload_json,note=excluded.note""",
                (project_code, category, key, json.dumps(payload, ensure_ascii=False), note, now))
        return {"project_code": project_code, "category": category, "finding_key": key,
                "finding": payload, "note": note, "created_at": now}

    def dismissed_finding_keys(self, project_code: str, category: str | None = None) -> set[str]:
        query = "SELECT finding_key FROM dismissed_findings WHERE project_code=?"
        args: tuple[Any, ...] = (project_code,)
        if category:
            query += " AND category=?"
            args = (project_code, category)
        with self.connect() as db:
            return {row[0] for row in db.execute(query, args).fetchall()}

    def save_finding_override(self, project_code: str, category: str, finding: Any,
                              status: str, risk_level: str, description: str,
                              note: str = "") -> dict[str, Any]:
        now = datetime.now().isoformat(timespec="seconds")
        key = finding_key(category, finding)
        with self.connect() as db:
            db.execute("""INSERT INTO finding_overrides(
                project_code,category,finding_key,status,risk_level,description,note,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(project_code,category,finding_key) DO UPDATE SET
                status=excluded.status,risk_level=excluded.risk_level,description=excluded.description,
                note=excluded.note,updated_at=excluded.updated_at""",
                (project_code, category, key, status, risk_level, description, note, now, now))
            row = db.execute("SELECT * FROM finding_overrides WHERE project_code=? AND category=? AND finding_key=?",
                             (project_code, category, key)).fetchone()
        return dict(row)

    def finding_overrides(self, project_code: str, category: str) -> dict[str, dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute("SELECT * FROM finding_overrides WHERE project_code=? AND category=?",
                              (project_code, category)).fetchall()
        return {row["finding_key"]: dict(row) for row in rows}

    def delete_project(self, project_code: str) -> dict[str, int]:
        """Delete every database record owned by one project in one transaction."""
        tables = ("finding_overrides", "dismissed_findings", "field_corrections", "review_issues", "contracts", "tasks", "projects")
        deleted: dict[str, int] = {}
        with self.connect() as db:
            for table in tables:
                cursor = db.execute(f"DELETE FROM {table} WHERE project_code=?", (project_code,))
                deleted[table] = cursor.rowcount
        return deleted

    def dashboard(self) -> dict[str, int]:
        projects = self.list_projects()
        payloads = [p["payload"] for p in projects]
        elevated = {"中风险", "高风险"}
        equipment = [d for p in payloads for d in p.get("equipment_differences", [])]
        schedule = [d for p in payloads for d in p.get("schedule_differences", [])]
        scopes = [d for p in payloads for d in p.get("scope_differences", [])]
        all_diffs = [d for p in payloads for key in ("equipment_differences", "schedule_differences", "scope_differences", "plan_differences") for d in p.get(key, [])]
        return {"项目总数": len(projects), "已完成对比项目数": sum(p["status"] == "已完成" for p in projects),
                "解析失败项目数": sum(p["status"] == "处理失败" for p in projects),
                "高风险项目数": sum(p["risk_level"] == "高风险" for p in projects),
                "设备缺项数量（中高风险）": sum(d.get("risk_level") in elevated for d in equipment),
                "工期风险数量（中高风险）": sum(d.get("risk_level") in elevated for d in schedule),
                "实施内容缺项数量（中高风险）": sum(d.get("risk_level") in elevated for d in scopes),
                "待人工复核数量（中高风险）": sum(d.get("risk_level") in elevated and d.get("needs_review") for d in all_diffs)}
