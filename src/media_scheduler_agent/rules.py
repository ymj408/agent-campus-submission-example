"""可解释的规则检查引擎。

模型不能直接声明“审核通过”；审核结论由本模块根据资料与规则计算，
草稿保存工具会再次调用它，防止模型绕过。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .timeutil import CST

SUPPORTED_RULES = {
    "required_fields",
    "allowed_values",
    "forbidden_terms",
    "sensitive_terms",
    "max_length",
}


@dataclass(frozen=True)
class Violation:
    rule_id: str
    rule_name: str
    severity: str
    field: str
    message: str
    evidence: str = ""
    action: str = "warn"

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "rule_name": self.rule_name,
            "severity": self.severity,
            "field": self.field,
            "message": self.message,
            "evidence": self.evidence,
            "action": self.action,
        }


@dataclass
class AuditResult:
    decision: str
    violations: list[Violation] = field(default_factory=list)
    missing_fields: list[str] = field(default_factory=list)
    rule_version: str = "unknown"
    checked_at: str = ""

    @property
    def has_blocking(self) -> bool:
        return any(item.severity == "blocking" for item in self.violations)

    @property
    def needs_review(self) -> bool:
        return self.decision == "needs_review" or any(
            item.severity == "warning" for item in self.violations
        )

    @property
    def risk_reasons(self) -> list[str]:
        return [item.message for item in self.violations]

    @property
    def review_items(self) -> list[str]:
        return [
            f"[{item.rule_id}] {item.message}" for item in self.violations if item.severity == "warning"
        ]

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision,
            "rule_version": self.rule_version,
            "checked_at": self.checked_at,
            "missing_fields": list(self.missing_fields),
            "risk_reasons": self.risk_reasons,
            "violations": [item.to_dict() for item in self.violations],
            "requires_human_review": self.needs_review,
        }


class RuleEngine:
    def __init__(self, rules: list[dict[str, Any]], version: str):
        self.rules = rules
        self.version = version
        for rule in rules:
            rule_type = rule.get("type")
            if rule_type not in SUPPORTED_RULES:
                raise ValueError(f"不支持的规则类型：{rule_type!r}")
            if not rule.get("id") or not rule.get("name"):
                raise ValueError("每条规则必须有 id 与 name")

    def audit(
        self,
        material: dict[str, Any],
        requested_publish_at: str,
    ) -> AuditResult:
        fields = dict(material)
        fields["publish_at"] = requested_publish_at
        violations: list[Violation] = []
        missing_fields: list[str] = []

        for rule in self.rules:
            rule_type = rule["type"]
            if rule_type == "required_fields":
                for item in rule.get("fields", []):
                    name = item["name"]
                    value = fields.get(name)
                    if value is None or (isinstance(value, str) and not value.strip()):
                        missing_fields.append(name)
                        violations.append(
                            Violation(
                                rule_id=rule["id"],
                                rule_name=rule["name"],
                                severity=rule.get("severity", "blocking"),
                                field=name,
                                message=f"缺少必填字段：{item.get('label', name)}（{name}）",
                                action="block",
                            )
                        )
            elif rule_type == "allowed_values":
                name = rule.get("field", "")
                value = fields.get(name)
                if value is None or value == "":
                    continue
                allowed = [str(item) for item in rule.get("allowed", [])]
                if str(value) not in allowed:
                    violations.append(
                        Violation(
                            rule_id=rule["id"],
                            rule_name=rule["name"],
                            severity=rule.get("severity", "blocking"),
                            field=name,
                            message=str(rule.get("message") or f"{name} 的取值不合法"),
                            evidence=f"{name}={value!r}",
                            action="block" if rule.get("severity") == "blocking" else "review",
                        )
                    )
            elif rule_type in {"forbidden_terms", "sensitive_terms"}:
                terms = [str(item) for item in rule.get("terms", [])]
                for name in rule.get("fields", []):
                    text = str(fields.get(name) or "")
                    hits = [term for term in terms if term and term in text]
                    if not hits:
                        continue
                    severity = rule.get("severity", "blocking" if rule_type == "forbidden_terms" else "warning")
                    label = "命中禁用表述" if rule_type == "forbidden_terms" else "命中敏感主题"
                    violations.append(
                        Violation(
                            rule_id=rule["id"],
                            rule_name=rule["name"],
                            severity=severity,
                            field=name,
                            message=f"{label}：{'、'.join(hits)}（{rule['name']}）",
                            evidence=f"{name} 含 {', '.join(hits)}",
                            action="block" if severity == "blocking" else "review",
                        )
                    )
            elif rule_type == "max_length":
                name = rule.get("field", "")
                value = fields.get(name)
                if value is None:
                    continue
                maximum = int(rule.get("max", 0))
                if maximum > 0 and len(str(value)) > maximum:
                    violations.append(
                        Violation(
                            rule_id=rule["id"],
                            rule_name=rule["name"],
                            severity=rule.get("severity", "warning"),
                            field=name,
                            message=str(
                                rule.get("message")
                                or f"{name} 长度超过 {maximum} 个字符（当前 {len(str(value))}）"
                            ),
                            evidence=f"length={len(str(value))}",
                            action="block" if rule.get("severity") == "blocking" else "review",
                        )
                    )

        decision = "pass"
        if any(item.severity == "blocking" for item in violations):
            decision = "blocked"
        elif any(item.severity == "warning" for item in violations):
            decision = "needs_review"

        # 去重，保留规则顺序。
        seen: set[tuple[str, str, str]] = set()
        unique: list[Violation] = []
        for item in violations:
            key = (item.rule_id, item.field, item.message)
            if key in seen:
                continue
            seen.add(key)
            unique.append(item)

        return AuditResult(
            decision=decision,
            violations=unique,
            missing_fields=list(dict.fromkeys(missing_fields)),
            rule_version=self.version,
            checked_at=datetime.now(CST).isoformat(timespec="seconds"),
        )
