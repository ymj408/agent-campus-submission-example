"""种子数据与运行期方案/检查点/产物存储。"""
from __future__ import annotations

import copy
import json
import os
import re
import shutil
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

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
    def __init__(self, data_dir: str | Path):
        self.data_dir = Path(data_dir).resolve()
        if not self.data_dir.is_dir():
            raise FileNotFoundError(f"数据目录不存在：{self.data_dir}")
        self.materials_doc = _read_json(self.data_dir / "materials.json")
        self.tools_doc = _read_json(self.data_dir / "tool_costs.json")
        self.plans_doc = _read_json(self.data_dir / "plan_templates.json")

        materials = self.materials_doc.get("materials")
        tools = self.tools_doc.get("tools")
        plans = self.plans_doc.get("plans")
        if not isinstance(materials, dict) or not materials:
            raise ValueError("materials.json 必须包含非空 materials")
        if not isinstance(tools, dict) or not tools:
            raise ValueError("tool_costs.json 必须包含非空 tools")
        if not isinstance(plans, list) or not plans:
            raise ValueError("plan_templates.json 必须包含非空 plans")

        for material_id, material in materials.items():
            if not isinstance(material, dict):
                raise ValueError(f"素材 {material_id} 必须是对象")
            if str(material.get("id", material_id)) != material_id:
                raise ValueError(f"素材 id 与键不一致：{material_id}")

        seen_plans: set[str] = set()
        for plan in plans:
            plan_id = str(plan.get("id") or "")
            if not plan_id or plan_id in seen_plans:
                raise ValueError(f"方案 ID 缺失或重复：{plan_id!r}")
            seen_plans.add(plan_id)
            if str(plan.get("material_id")) not in materials:
                raise ValueError(f"方案 {plan_id} 引用了不存在的素材")
            step_ids: set[str] = set()
            for step in plan.get("steps", []):
                step_id = str(step.get("id") or "")
                if not step_id or step_id in step_ids:
                    raise ValueError(f"方案 {plan_id} 的步骤 ID 缺失或重复")
                step_ids.add(step_id)
                if str(step.get("tool")) not in tools:
                    raise ValueError(f"方案 {plan_id} 的步骤 {step_id} 引用了不存在的工具")
            for step in plan.get("steps", []):
                for dep in step.get("depends_on", []):
                    if dep not in step_ids:
                        raise ValueError(f"方案 {plan_id} 的步骤依赖不存在：{dep}")
                    if dep == step["id"]:
                        raise ValueError(f"方案 {plan_id} 的步骤不能依赖自己")

        self._materials = materials
        self._tools = tools
        self._plans = {str(plan["id"]): copy.deepcopy(plan) for plan in plans}

    @property
    def materials(self) -> dict[str, dict[str, Any]]:
        return self._materials

    @property
    def tools(self) -> dict[str, dict[str, Any]]:
        return self._tools

    @property
    def plans(self) -> list[dict[str, Any]]:
        return list(self._plans.values())

    @property
    def rule_version(self) -> str:
        return str(self.plans_doc.get("version", "plans-demo"))

    def material(self, material_id: str) -> dict[str, Any] | None:
        item = self._materials.get(material_id)
        return copy.deepcopy(item) if item else None

    def tool(self, tool_name: str) -> dict[str, Any]:
        return copy.deepcopy(self._tools[tool_name])

    def plan(self, plan_id: str) -> dict[str, Any] | None:
        item = self._plans.get(plan_id)
        return copy.deepcopy(item) if item else None

    def plan_total(self, plan_id: str) -> float:
        plan = self.plan(plan_id)
        if plan is None:
            raise KeyError(plan_id)
        material = self._materials[str(plan["material_id"])]
        total = float(material.get("cost", 0))
        for step in plan.get("steps", []):
            total += float(self._tools[str(step["tool"])]["unit_cost"])
        return round(total, 2)

    def plan_minutes(self, plan_id: str) -> int:
        plan = self.plan(plan_id)
        if plan is None:
            raise KeyError(plan_id)
        return sum(int(self._tools[str(step["tool"])]["minutes"]) for step in plan.get("steps", []))


class RuntimeStore:
    def __init__(self, root: str | Path, seed: SeedData):
        self.root = Path(root).resolve()
        self.seed = seed
        self.state_path = self.root / "state.json"
        self.plans_dir = self.root / "plans"
        self.artifacts_dir = self.root / "artifacts"
        self.runs_dir = self.root / "runs"
        self._lock = _lock_for(self.root)

    def initialize(self, *, reset: bool = False) -> None:
        with self._lock:
            if reset:
                self._reset_if_safe()
            self.root.mkdir(parents=True, exist_ok=True)
            for directory in (self.plans_dir, self.artifacts_dir, self.runs_dir):
                directory.mkdir(parents=True, exist_ok=True)
            if not self.state_path.exists():
                _atomic_write_json(
                    self.state_path,
                    {
                        "schema_version": 1,
                        "created_at": utc_now(),
                        "updated_at": utc_now(),
                        "sequence": 0,
                        "plans_index": {},
                        "request_index": {},
                    },
                )

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
            raise RuntimeError(f"拒绝重置未识别的非空目录：{self.root}")
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

    def next_sequence(self, state: dict[str, Any]) -> int:
        state["sequence"] = int(state.get("sequence", 0)) + 1
        return state["sequence"]

    def plan_path(self, plan_id: str) -> Path:
        return self.plans_dir / f"{plan_id}.json"

    def write_plan(self, plan: dict[str, Any]) -> Path:
        plan_id = str(plan.get("plan_id") or "").strip()
        if not plan_id or not re.fullmatch(r"PLAN-\d{4,}", plan_id):
            raise ValueError("方案 ID 不合法")
        path = self.plan_path(plan_id)
        _atomic_write_json(path, plan)
        return path

    def read_plan(self, plan_id: str) -> dict[str, Any] | None:
        path = self.plan_path(plan_id)
        return _read_json(path) if path.exists() else None

    def iter_plans(self) -> list[dict[str, Any]]:
        return [_read_json(path) for path in sorted(self.plans_dir.glob("PLAN-*.json"))]

    def find_saved_plan(self, fingerprint: str) -> dict[str, Any] | None:
        state = self.load_state()
        plan_id = state.get("request_index", {}).get(fingerprint)
        if plan_id:
            plan = self.read_plan(str(plan_id))
            if plan is not None:
                return plan
        for plan in self.iter_plans():
            if plan.get("fingerprint") == fingerprint:
                return plan
        return None

    def checkpoint_path(self, fingerprint: str) -> Path:
        return self.runs_dir / f"RUN-{fingerprint[:16]}.json"

    def load_checkpoint(self, fingerprint: str) -> dict[str, Any] | None:
        path = self.checkpoint_path(fingerprint)
        return _read_json(path) if path.exists() else None

    def save_checkpoint(self, fingerprint: str, payload: dict[str, Any]) -> None:
        payload = dict(payload)
        payload["fingerprint"] = fingerprint
        payload["updated_at"] = utc_now()
        _atomic_write_json(self.checkpoint_path(fingerprint), payload)

    def write_artifact(self, fingerprint: str, filename: str, content: str) -> str:
        safe_name = re.sub(r"[^A-Za-z0-9_.-]", "_", filename)
        directory = self.artifacts_dir / fingerprint[:12]
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / safe_name
        temp = path.with_name(f".{path.name}.tmp-{uuid.uuid4().hex}")
        temp.write_text(content, encoding="utf-8")
        os.replace(temp, path)
        return path.relative_to(self.root).as_posix()

    def summary(self) -> dict[str, Any]:
        state = self.load_state()
        plans = self.iter_plans()
        return {
            "plan_count": len(plans),
            "plan_ids": [plan.get("plan_id") for plan in plans],
            "saved_plan_count": sum(1 for plan in plans if plan.get("saved") is True),
            "store": str(self.root),
            "sequence": state.get("sequence", 0),
        }
