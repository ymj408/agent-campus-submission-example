"""合成素材与品牌规则加载。"""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any


class KnowledgeBase:
    def __init__(self, data_dir: str | Path):
        self.data_dir = Path(data_dir).resolve()
        materials_path = self.data_dir / "materials.json"
        rules_path = self.data_dir / "brand_rules.json"
        if not materials_path.exists() or not rules_path.exists():
            raise FileNotFoundError("缺少 materials.json 或 brand_rules.json")
        with materials_path.open("r", encoding="utf-8") as handle:
            materials_doc = json.load(handle)
        with rules_path.open("r", encoding="utf-8") as handle:
            rules = json.load(handle)
        materials = materials_doc.get("materials")
        if not isinstance(materials, dict) or not materials:
            raise ValueError("materials.json 必须包含非空 materials")
        for material_id, material in materials.items():
            if str(material.get("id", material_id)) != material_id:
                raise ValueError(f"素材 id 与键不一致：{material_id}")
            if not isinstance(material.get("facts", []), list):
                raise ValueError(f"素材 {material_id} 的 facts 必须是数组")
        if not isinstance(rules.get("forbidden_phrases"), list):
            raise ValueError("brand_rules.json 缺少 forbidden_phrases")
        self.materials_doc = materials_doc
        self.rules = rules
        self._materials = {key: copy.deepcopy(value) for key, value in materials.items()}

    @property
    def materials(self) -> dict[str, dict[str, Any]]:
        return self._materials

    @property
    def usable_materials(self) -> dict[str, dict[str, Any]]:
        return {
            material_id: material
            for material_id, material in self._materials.items()
            if material.get("usable", False)
        }

    def get(self, material_id: str) -> dict[str, Any] | None:
        material = self._materials.get(material_id)
        return copy.deepcopy(material) if material else None

    def platform_range(self, platform: str) -> dict[str, int] | None:
        item = (self.rules.get("platform_duration_seconds") or {}).get(platform)
        return dict(item) if item else None
