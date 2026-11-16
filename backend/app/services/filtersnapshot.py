"""筛选条件快照业务规则：预设套用后的维度映射、失效定位与分阶段快照生成。

业务背景：筛选预设套用后直接拼接旧字段名，标准维度改名或显示列调整后，
条件静默失效，用户只看到列表变小却不知道哪一维被丢弃。

这里的套用流水线拆成五个完整阶段：读取预设 → 解析条件 → 维度映射 → 冲突裁决 → 生成快照。
每个阶段整体提交（输出落库与标记完成是同一个动作），中断后只能从最后一个完整阶段
恢复，绝不回到半成品阶段；已完成的阶段不会重跑，使用次数也因此不会重复计数。
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any

from app.store import store

MODULE = "filtersnapshot"
PRESET_MODULE = "filterpreset"
MAPPING_MODULE = "dimensionmap"

STAGES = ["读取预设", "解析条件", "维度映射", "冲突裁决", "生成快照"]
STATUS_ORDER = ["生成中", "已生成", "已归档"]
ACTION_RULES = {"归档快照": "已归档"}
RESUME_ACTION = "恢复生成"

# 标准维度/标准列注册表：基础注册表 ∪ 已生效映射规则的目标维度。
# 标准维度改名后，旧名靠「已生效」的映射规则续命；规则未生效或已废弃都算失效。
BASE_DIMENSIONS = ["日期", "省份", "城市", "渠道", "设备类型", "用户分群", "应用版本", "运营商"]
BASE_MEASURES = ["曝光量", "点击量", "转化率", "订单数", "成交金额", "活跃用户数"]

OPERATORS = ["!=", ">=", "<=", "=", ">", "<"]
CONFLICT_POLICY = "请求显式条件优先于预设同名条件；完全相同的条件只计一次，不重复计数"


class StageInterrupted(Exception):
    """演练钩子：模拟流水线在阶段执行中途被中断（如 worker 崩溃）。

    中断发生在阶段提交之前，所以快照里不会留下该阶段的半成品输出。
    """

    def __init__(self, stage: str) -> None:
        self.stage = stage
        super().__init__(f"阶段「{stage}」执行中断")


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _split_list(raw: Any) -> list[str]:
    """把 '省份>城市' / '省份,城市' / JSON 数组 统一拆成字符串列表。"""
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(item).strip() for item in raw if str(item).strip()]
    text = str(raw).strip()
    if not text:
        return []
    if text.startswith("["):
        try:
            parsed = json.loads(text)
            if isinstance(parsed, list):
                return [str(item).strip() for item in parsed if str(item).strip()]
        except json.JSONDecodeError:
            pass
    parts = re.split(r"[>→,，;；、\n]", text)
    return [part.strip() for part in parts if part.strip()]


def parse_conditions(raw: Any, source: str) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """把维度条件组解析成结构化条件列表。

    支持三种写法：JSON 数组（{"维度","操作符","值"} 或 dimension/operator/value）、
    JSON 对象（{"省份": "浙江"}）、分号文本（省份=浙江;渠道!=小程序）。
    解析不出的片段不静默丢弃，记入 failures，后续进入失效维度清单。
    """
    conditions: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []

    def add(dimension: Any, operator: Any, value: Any) -> None:
        dim = str(dimension or "").strip()
        val = str(value or "").strip()
        op = str(operator or "=").strip() or "="
        if not dim or not val:
            failures.append({"片段": f"{dim}{op}{val}", "原因": "条件缺少维度名或取值", "来源": source})
            return
        conditions.append({"维度": dim, "操作符": op, "值": val, "来源": source})

    if raw is None or str(raw).strip() == "":
        return conditions, failures
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict):
                add(item.get("维度", item.get("dimension")), item.get("操作符", item.get("operator", "=")), item.get("值", item.get("value")))
            else:
                failures.append({"片段": str(item), "原因": "条件条目不是对象结构", "来源": source})
        return conditions, failures
    if isinstance(raw, dict):
        for dim, val in raw.items():
            add(dim, "=", val)
        return conditions, failures

    text = str(raw).strip()
    if text.startswith("[") or text.startswith("{"):
        try:
            return parse_conditions(json.loads(text), source)
        except json.JSONDecodeError:
            failures.append({"片段": text, "原因": "JSON 结构无法解析", "来源": source})
            return conditions, failures

    for fragment in re.split(r"[;；\n]", text):
        fragment = fragment.strip()
        if not fragment:
            continue
        matched = False
        for op in OPERATORS:
            if op in fragment:
                dim, _, val = fragment.partition(op)
                add(dim, op, val)
                matched = True
                break
        if not matched:
            failures.append({"片段": fragment, "原因": "缺少可识别的操作符（=、!=、>、<、>=、<=）", "来源": source})
    return conditions, failures


def build_dimension_registry() -> tuple[dict[str, str], set[str], dict[str, str]]:
    """汇总维度映射规则，返回 (原始名→标准名映射, 标准维度注册表, 原始名→规则状态)。

    多条规则命中同一原始维度名时，按映射优先级取高者；标准维度注册表 =
    基础维度 ∪ 已生效规则的目标维度，显示列另加基础度量。
    """
    rules = store.rows(MAPPING_MODULE)

    def priority(rule: dict[str, Any]) -> int:
        try:
            return int(str(rule.get("映射优先级", "0")))
        except ValueError:
            return 0

    mapping: dict[str, str] = {}
    rule_status: dict[str, str] = {}
    for rule in sorted(rules, key=priority, reverse=True):
        origin = str(rule.get("原始维度名", "")).strip()
        target = str(rule.get("标准维度名", "")).strip()
        if not origin:
            continue
        rule_status.setdefault(origin, str(rule.get("status", "")))
        if rule.get("status") == "已生效" and target:
            mapping.setdefault(origin, target)
    standard = set(BASE_DIMENSIONS)
    standard.update(mapping.values())
    return mapping, standard, rule_status


class FiltersnapshotService:
    # ---------------------------------------------------------------- 查询
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        invalid_dimension: str | None = None,
        has_invalid: bool = False,
        consumer: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        """快照检索：关键字、状态、失效维度名、是否含失效、消费方都可过滤。"""
        rows = list(reversed(store.rows(MODULE)))
        if keyword:
            rows = [
                row for row in rows
                if keyword in str(row.get("快照编号", ""))
                or keyword in str(row.get("预设编号", ""))
                or keyword in str(row.get("预设名称", ""))
            ]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        if consumer:
            rows = [row for row in rows if row.get("消费方") == consumer]
        if invalid_dimension:
            rows = [
                row for row in rows
                if any(invalid_dimension in str(item.get("维度名", "")) for item in row.get("失效维度", []))
            ]
        if has_invalid:
            rows = [row for row in rows if row.get("失效维度") or row.get("失效显示列")]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def invalid_dimension_report(self, keyword: str | None = None) -> list[dict[str, Any]]:
        """失效维度定位报告：跨快照聚合，按出现次数排序，方便定位哪个维度丢得最多。"""
        report: dict[str, dict[str, Any]] = {}
        for snap in store.rows(MODULE):
            for item in snap.get("失效维度", []):
                name = str(item.get("维度名", ""))
                slot = report.setdefault(name, {
                    "维度名": name,
                    "出现次数": 0,
                    "涉及预设": [],
                    "涉及快照": [],
                    "最近出现时间": "",
                    "失效原因": str(item.get("原因", "")),
                })
                slot["出现次数"] += 1
                for bucket, key in (("涉及预设", "预设编号"), ("涉及快照", "快照编号")):
                    code = str(snap.get(key, ""))
                    if code and code not in slot[bucket]:
                        slot[bucket].append(code)
                created = str(snap.get("创建时间", ""))
                if created > slot["最近出现时间"]:
                    slot["最近出现时间"] = created
                    slot["失效原因"] = str(item.get("原因", ""))
        rows = sorted(report.values(), key=lambda item: (-int(item["出现次数"]), str(item["维度名"])))
        if keyword:
            rows = [row for row in rows if keyword in str(row["维度名"])]
        return rows

    # ---------------------------------------------------------------- 套用
    def apply_preset(
        self,
        preset_id: int,
        *,
        explicit_raw: Any = None,
        consumer: str = "多维下钻",
        fail_at_stage: str | None = None,
    ) -> tuple[dict[str, Any] | None, str]:
        """套用预设：读取预设模板、维度条件组与时间范围记录，分阶段生成快照。

        fail_at_stage 是演练参数，用来验证「中断后从最后一个完整阶段恢复」。
        """
        preset = store.find(PRESET_MODULE, preset_id)
        if preset is None:
            return None, f"预设模板 {preset_id} 不存在或已归档"
        rows = store.rows(MODULE)
        snapshot: dict[str, Any] = {
            "id": max((int(row.get("id", 0)) for row in rows), default=0) + 1,
            "status": STATUS_ORDER[0],
            "pending": True,
            "abnormal": False,
            "预设编号": str(preset.get("预设编号", "")),
            "预设名称": str(preset.get("预设名称", "")),
            "preset_id": int(preset.get("id", 0)),
            "消费方": consumer or "多维下钻",
            "时间范围": "",
            "显式条件原文": explicit_raw if explicit_raw is not None else "",
            "生效条件": [],
            "失效维度": [],
            "失效显示列": [],
            "冲突裁决记录": [],
            "套用查询": {},
            "使用次数": 0,
            "已完成阶段": [],
            "当前阶段": STAGES[0],
            "阶段输出": {},
            "创建时间": _now(),
            "完成时间": "",
        }
        snapshot["快照编号"] = f"SNAP-{snapshot['id']:04d}"
        rows.append(snapshot)
        message = self._run_pipeline(snapshot, fail_at_stage=fail_at_stage)
        return snapshot, message

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"筛选条件快照 {entry_id} 不存在或已归档"
        if action == RESUME_ACTION:
            if entry.get("status") != STATUS_ORDER[0]:
                return None, f"快照 {entry.get('快照编号')} 已推进到「{entry.get('status')}」，无需恢复"
            message = self._run_pipeline(entry)
            return entry, message
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于筛选条件快照可执行范围"
        entry["status"] = ACTION_RULES[action]
        entry["pending"] = False
        return entry, f"快照 {entry.get('快照编号')} 已归档"

    # ---------------------------------------------------------------- 流水线
    def _run_pipeline(self, snap: dict[str, Any], fail_at_stage: str | None = None) -> str:
        """从最后一个完整阶段之后继续推进；每个阶段整体提交，不留半成品。"""
        try:
            for stage in STAGES:
                if stage in snap["已完成阶段"]:
                    continue  # 中断恢复：已完整完成的阶段不重跑，使用次数不重复计数
                output = self._run_stage(stage, snap, fail_at_stage=fail_at_stage)
                # 阶段提交是原子动作：输出落库 + 标记完成同时发生
                snap["阶段输出"][stage] = output
                snap["已完成阶段"].append(stage)
                snap["当前阶段"] = stage
        except StageInterrupted as interrupted:
            idx = STAGES.index(interrupted.stage)
            snap["当前阶段"] = interrupted.stage
            snap["abnormal"] = True
            return (
                f"流水线在阶段「{interrupted.stage}」中断，已完成 {idx} 个阶段；"
                f"恢复快照后将从「{interrupted.stage}」继续，不会重跑已完成阶段"
            )
        snap["当前阶段"] = "已完成"
        snap["status"] = "已生成"
        snap["pending"] = False
        snap["abnormal"] = bool(snap["失效维度"] or snap["失效显示列"])
        snap["完成时间"] = _now()
        dropped = len(snap["失效维度"]) + len(snap["失效显示列"])
        if dropped:
            return f"筛选条件快照已生成：{len(snap['生效条件'])} 条生效条件，{dropped} 项失效已定位，可解释可追溯"
        return f"筛选条件快照已生成：{len(snap['生效条件'])} 条生效条件全部有效"

    def _run_stage(self, stage: str, snap: dict[str, Any], fail_at_stage: str | None) -> dict[str, Any]:
        if fail_at_stage == stage:
            raise StageInterrupted(stage)
        handler = {
            "读取预设": self._stage_load_preset,
            "解析条件": self._stage_parse_conditions,
            "维度映射": self._stage_map_dimensions,
            "冲突裁决": self._stage_resolve_conflicts,
            "生成快照": self._stage_finalize,
        }[stage]
        return handler(snap)

    def _stage_load_preset(self, snap: dict[str, Any]) -> dict[str, Any]:
        """阶段一：读取预设模板、维度条件组与时间范围记录，并登记使用次数。"""
        preset = store.find(PRESET_MODULE, int(snap.get("preset_id", 0)))
        if preset is None:
            raise StageInterrupted("读取预设")
        try:
            used = int(str(preset.get("使用次数", "0")) or 0)
        except ValueError:
            used = 0
        used += 1
        preset["使用次数"] = str(used)  # 只在本阶段登记一次；恢复不会重跑本阶段
        snap["使用次数"] = used
        snap["时间范围"] = str(preset.get("时间范围", ""))
        return {
            "预设编号": str(preset.get("预设编号", "")),
            "维度条件组原文": str(preset.get("维度条件组", "")),
            "时间范围": snap["时间范围"],
            "排序规则": str(preset.get("排序规则", "")),
            "显示列原文": str(preset.get("显示列", "")),
            "使用次数": used,
        }

    def _stage_parse_conditions(self, snap: dict[str, Any]) -> dict[str, Any]:
        """阶段二：把维度条件组与请求显式条件解析成结构化条件。"""
        loaded = snap["阶段输出"]["读取预设"]
        preset_conditions, preset_failures = parse_conditions(loaded["维度条件组原文"], "预设")
        explicit_conditions, explicit_failures = parse_conditions(snap.get("显式条件原文"), "请求")
        return {
            "预设条件": preset_conditions,
            "请求条件": explicit_conditions,
            "解析失败": preset_failures + explicit_failures,
        }

    def _stage_map_dimensions(self, snap: dict[str, Any]) -> dict[str, Any]:
        """阶段三：经维度映射规则改写条件与显示列，失效项定位到具体原因。"""
        parsed = snap["阶段输出"]["解析条件"]
        mapping, standard, rule_status = build_dimension_registry()
        standard_columns = standard | set(BASE_MEASURES)

        # 解析失败的片段统一成失效维度结构，保证报告聚合与前端展示口径一致
        invalid: list[dict[str, Any]] = [
            {"维度名": str(item.get("片段", "")), "原因": str(item.get("原因", "")), "来源": str(item.get("来源", "预设")), "条件": str(item.get("片段", ""))}
            for item in parsed["解析失败"]
        ]

        def map_one(condition: dict[str, Any]) -> dict[str, Any] | None:
            dim = str(condition["维度"])
            if dim in standard:
                return {**condition, "映射路径": "标准维度"}
            if dim in mapping:
                return {**condition, "维度": mapping[dim], "映射路径": f"改名映射：{dim}→{mapping[dim]}"}
            status = rule_status.get(dim)
            reason = {
                "规则评审中": "映射规则未生效（规则评审中）",
                "已废弃": "映射规则已废弃",
                "草稿": "映射规则未生效（草稿）",
            }.get(status or "", "未找到映射规则且不是标准维度")
            invalid.append({"维度名": dim, "原因": reason, "来源": condition.get("来源", "预设"), "条件": f"{dim}{condition['操作符']}{condition['值']}"})
            return None

        preset_mapped = [item for item in (map_one(c) for c in parsed["预设条件"]) if item]
        explicit_mapped = [item for item in (map_one(c) for c in parsed["请求条件"]) if item]

        loaded = snap["阶段输出"]["读取预设"]
        valid_columns: list[str] = []
        invalid_columns: list[dict[str, Any]] = []
        for column in _split_list(loaded["显示列原文"]):
            if column in standard_columns:
                valid_columns.append(column)
            elif column in mapping and mapping[column] in standard_columns:
                valid_columns.append(mapping[column])
            else:
                invalid_columns.append({"列名": column, "原因": "显示列不在标准维度/度量注册表中，可能已被调整或改名"})

        snap["失效维度"] = invalid
        snap["失效显示列"] = invalid_columns
        return {
            "预设条件映射后": preset_mapped,
            "请求条件映射后": explicit_mapped,
            "失效维度": invalid,
            "有效显示列": valid_columns,
            "失效显示列": invalid_columns,
        }

    def _stage_resolve_conflicts(self, snap: dict[str, Any]) -> dict[str, Any]:
        """阶段四：预设条件与请求显式条件冲突裁决。

        业务代价权衡：请求显式条件优先（代价最低——尊重当前查询意图、无需回写预设、
        裁决记录留在快照里可解释）；完全相同的条件去重合并，不重复计数。
        """
        mapped = snap["阶段输出"]["维度映射"]
        effective: list[dict[str, Any]] = []
        decisions: list[dict[str, Any]] = []
        by_exact: dict[tuple[str, str, str], int] = {}
        by_dim_op: dict[tuple[str, str], int] = {}

        def key_exact(cond: dict[str, Any]) -> tuple[str, str, str]:
            return (str(cond["维度"]), str(cond["操作符"]), str(cond["值"]))

        def key_dim_op(cond: dict[str, Any]) -> tuple[str, str]:
            return (str(cond["维度"]), str(cond["操作符"]))

        for cond in mapped["预设条件映射后"]:
            exact = key_exact(cond)
            if exact in by_exact:
                decisions.append({"维度": cond["维度"], "操作符": cond["操作符"], "预设值": cond["值"], "请求值": "", "裁决": "预设内去重", "说明": "预设内重复条件只保留一条，不重复计数"})
                continue
            by_exact[exact] = len(effective)
            by_dim_op[key_dim_op(cond)] = len(effective)
            effective.append({**cond, "来源": "预设"})

        for cond in mapped["请求条件映射后"]:
            exact = key_exact(cond)
            if exact in by_exact:
                idx = by_exact[exact]
                effective[idx]["来源"] = "预设+请求"
                decisions.append({"维度": cond["维度"], "操作符": cond["操作符"], "预设值": cond["值"], "请求值": cond["值"], "裁决": "去重合并", "说明": "预设与请求条件完全一致，合并为一条，不重复计数"})
                continue
            dim_op = key_dim_op(cond)
            if dim_op in by_dim_op:
                idx = by_dim_op[dim_op]
                overridden = effective[idx]
                effective[idx] = {**cond, "来源": "请求"}
                by_exact[key_exact(cond)] = idx
                decisions.append({"维度": cond["维度"], "操作符": cond["操作符"], "预设值": overridden["值"], "请求值": cond["值"], "裁决": "请求显式条件优先", "说明": "预设显示优先级与当前请求冲突，按业务代价采用请求优先：不回写预设、快照留痕可解释"})
                continue
            by_exact[exact] = len(effective)
            by_dim_op[dim_op] = len(effective)
            effective.append({**cond, "来源": "请求"})

        snap["生效条件"] = effective
        snap["冲突裁决记录"] = decisions
        return {"生效条件": effective, "冲突裁决记录": decisions, "裁决口径": CONFLICT_POLICY}

    def _stage_finalize(self, snap: dict[str, Any]) -> dict[str, Any]:
        """阶段五：汇总套用查询与统计口径，快照定稿。"""
        mapped = snap["阶段输出"]["维度映射"]
        loaded = snap["阶段输出"]["读取预设"]
        where = " AND ".join(f"{c['维度']} {c['操作符']} '{c['值']}'" for c in snap["生效条件"]) or "1=1"
        columns = mapped["有效显示列"] or ["*"]
        statement = f"SELECT {', '.join(columns)} WHERE {where} /* 时间范围：{snap['时间范围'] or '未指定'}；排序：{loaded['排序规则'] or '默认'} */"
        query = {
            "select": columns,
            "where": snap["生效条件"],
            "时间范围": snap["时间范围"],
            "order_by": loaded["排序规则"],
            "statement": statement,
        }
        snap["套用查询"] = query
        return {
            "套用查询": query,
            "统计": {
                "有效条件数": len(snap["生效条件"]),
                "失效维度数": len(snap["失效维度"]),
                "失效显示列数": len(snap["失效显示列"]),
                "冲突裁决数": len(snap["冲突裁决记录"]),
                "使用次数": snap["使用次数"],
            },
        }
