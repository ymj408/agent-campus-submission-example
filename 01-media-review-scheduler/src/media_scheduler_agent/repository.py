"""种子数据读取与运行期草稿存储。

设计约束：
- 种子数据（data/）只读；运行状态在 --store 指定目录内。
- 草稿与确认请求不写回 schedule，也没有任何“发布”写接口。
- 草稿文件采用临时文件 + os.replace 原子替换；索引写入 state.json。
"""
from __future__ import annotations

import copy
import json
import os
import shutil
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .timeutil import format_datetime

_LOCKS: dict[str, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()


def _lock_for(path: Path) -> threading.RLock:
    key = str(path.resolve()).casefold()
    with _LOCKS_GUARD:
        lock = _LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _LOCKS[key] = lock
        return lock


def _read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.tmp-{uuid.uuid4().hex}")
    try:
        with temp.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        if temp.exists():
            try:
                temp.unlink()
            except OSError:
                pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class SeedData:
    """加载并校验 data/ 下的合成资料。"""

    def __init__(self, data_dir: str | Path):
        self.data_dir = Path(data_dir).resolve()
        if not self.data_dir.is_dir():
            raise FileNotFoundError(f"数据目录不存在：{self.data_dir}")
        self.materials_doc = _read_json(self.data_dir / "materials.json")
        self.rules_doc = _read_json(self.data_dir / "rules.json")
        self.schedule_doc = _read_json(self.data_dir / "schedule.json")
        source_path = self.data_dir / "source_notes.json"
        self.source_doc = _read_json(source_path) if source_path.exists() else {"notes": {}}

        materials = self.materials_doc.get("materials")
        if not isinstance(materials, dict) or not materials:
            raise ValueError("materials.json 必须包含非空 materials 对象")
        for material_id, material in materials.items():
            if not isinstance(material, dict):
                raise ValueError(f"素材 {material_id} 必须是对象")
            if str(material.get("id", material_id)) != material_id:
                raise ValueError(f"素材 {material_id} 的 id 与键不一致")
        if not isinstance(self.rules_doc.get("rules"), list):
            raise ValueError("rules.json 必须包含 rules 数组")
        if not isinstance(self.schedule_doc.get("entries"), list):
            raise ValueError("schedule.json 必须包含 entries 数组")
        if not isinstance(self.source_doc.get("notes", {}), dict):
            raise ValueError("source_notes.json 的 notes 必须是对象")

    @property
    def materials(self) -> dict[str, dict[str, Any]]:
        return self.materials_doc["materials"]

    @property
    def rules(self) -> list[dict[str, Any]]:
        return self.rules_doc["rules"]

    @property
    def rule_version(self) -> str:
        return str(self.rules_doc.get("version", "unknown"))

    @property
    def schedule_entries(self) -> list[dict[str, Any]]:
        return self.schedule_doc["entries"]

    def material(self, material_id: str) -> dict[str, Any] | None:
        item = self.materials.get(material_id)
        return copy.deepcopy(item) if item else None

    def source_note(self, material_id: str) -> dict[str, Any] | None:
        item = self.source_doc.get("notes", {}).get(material_id)
        return copy.deepcopy(item) if item else None


class RuntimeStore:
    """一个可复现、可重复提交的本地运行状态目录。"""

    def __init__(self, root: str | Path, seed: SeedData):
        self.root = Path(root).resolve()
        self.seed = seed
        self.state_path = self.root / "state.json"
        self.drafts_dir = self.root / "drafts"
        self._lock = _lock_for(self.root)

    def initialize(self, *, reset: bool = False) -> None:
        with self._lock:
            if reset:
                self._reset_if_safe()
            self.root.mkdir(parents=True, exist_ok=True)
            self.drafts_dir.mkdir(parents=True, exist_ok=True)
            if not self.state_path.exists():
                state = {
                    "schema_version": 1,
                    "seed_rule_version": self.seed.rule_version,
                    "created_at": utc_now(),
                    "updated_at": utc_now(),
                    "sequence": 0,
                    "material_overrides": {},
                    "schedule": copy.deepcopy(self.seed.schedule_entries),
                    "draft_index": {},
                    "confirmation_index": {},
                }
                _atomic_write_json(self.state_path, state)

    def _reset_if_safe(self) -> None:
        if not self.root.exists():
            return
        if self.root == Path(self.root.anchor):
            raise RuntimeError("拒绝重置磁盘根目录")
        if self.root == self.seed.data_dir or self.seed.data_dir in self.root.parents:
            raise RuntimeError("拒绝重置数据目录")
        try:
            entries = list(self.root.iterdir())
        except OSError as exc:
            raise RuntimeError(f"无法读取运行目录：{self.root}") from exc
        if not entries:
            return
        if not self.state_path.exists():
            raise RuntimeError(
                f"拒绝重置未识别的非空目录：{self.root}；请换一个空目录或手动清理"
            )
        shutil.rmtree(self.root)

    @contextmanager
    def locked(self) -> Iterator["RuntimeStore"]:
        with self._lock:
            yield self

    def load_state(self) -> dict[str, Any]:
        if not self.state_path.exists():
            self.initialize()
        return _read_json(self.state_path)

    def save_state(self, state: dict[str, Any]) -> None:
        state["updated_at"] = utc_now()
        _atomic_write_json(self.state_path, state)

    def get_material(self, material_id: str) -> dict[str, Any] | None:
        material = self.seed.material(material_id)
        if material is None:
            return None
        state = self.load_state()
        overrides = state.get("material_overrides", {}).get(material_id, {})
        material.update(copy.deepcopy(overrides))
        material["id"] = material_id
        return material

    def apply_material_fields(self, material_id: str, fields: dict[str, Any]) -> dict[str, Any]:
        clean = {key: value for key, value in fields.items() if value not in (None, "")}
        with self._lock:
            state = self.load_state()
            overrides = state.setdefault("material_overrides", {}).setdefault(material_id, {})
            overrides.update(clean)
            self.save_state(state)
        merged = self.get_material(material_id)
        if merged is None:
            raise KeyError(material_id)
        return merged

    def next_sequence(self, state: dict[str, Any]) -> int:
        state["sequence"] = int(state.get("sequence", 0)) + 1
        return state["sequence"]

    def write_draft(self, draft: dict[str, Any]) -> Path:
        draft_id = str(draft.get("draft_id") or "").strip()
        if not draft_id or "/" in draft_id or "\\" in draft_id:
            raise ValueError("草稿 ID 不合法")
        path = self.drafts_dir / f"{draft_id}.json"
        _atomic_write_json(path, draft)
        return path

    def read_draft(self, draft_id: str) -> dict[str, Any] | None:
        path = self.drafts_dir / f"{draft_id}.json"
        if not path.exists():
            return None
        return _read_json(path)

    def iter_drafts(self) -> list[dict[str, Any]]:
        state = self.load_state()
        drafts: list[dict[str, Any]] = []
        for draft_id in state.get("draft_index", {}).values():
            draft = self.read_draft(str(draft_id))
            if draft is not None:
                drafts.append(draft)
        return drafts

    def schedule_conflict(self, platform: str, publish_at: str) -> dict[str, Any] | None:
        target = format_datetime(publish_at)
        for entry in self.seed.schedule_entries:
            if str(entry.get("platform")) != platform:
                continue
            try:
                same = format_datetime(entry.get("publish_at", "")) == target
            except ValueError:
                continue
            if same:
                return dict(entry, kind="confirmed_schedule")
        return None

    def draft_conflict(
        self,
        platform: str,
        publish_at: str,
        *,
        ignore_material_id: str | None = None,
        ignore_draft_id: str | None = None,
    ) -> dict[str, Any] | None:
        target = format_datetime(publish_at)
        for draft in self.iter_drafts():
            if draft.get("published") is not False:
                # 防御性检查：本项目只应存在未发布草稿。
                continue
            if ignore_draft_id and draft.get("draft_id") == ignore_draft_id:
                continue
            if ignore_material_id and draft.get("material_id") == ignore_material_id:
                continue
            if str(draft.get("platform")) != platform:
                continue
            try:
                same = format_datetime(draft.get("proposed_publish_at", "")) == target
            except ValueError:
                continue
            if same:
                return {
                    "kind": "draft",
                    "draft_id": draft.get("draft_id"),
                    "material_id": draft.get("material_id"),
                    "title": draft.get("title"),
                    "proposed_publish_at": draft.get("proposed_publish_at"),
                }
        return None

    def summary(self) -> dict[str, Any]:
        state = self.load_state()
        drafts = self.iter_drafts()
        status_counts: dict[str, int] = {}
        for draft in drafts:
            status = str(draft.get("status", "unknown"))
            status_counts[status] = status_counts.get(status, 0) + 1
        return {
            "draft_count": len(drafts),
            "draft_ids": [draft.get("draft_id") for draft in drafts],
            "draft_status_counts": status_counts,
            "confirmation_count": len(state.get("confirmation_index", {})),
            "published_count": 0,
            "store": str(self.root),
        }

    def read_state_snapshot(self) -> dict[str, Any]:
        return copy.deepcopy(self.load_state())
