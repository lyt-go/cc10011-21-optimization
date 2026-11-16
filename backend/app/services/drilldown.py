"""多维下钻业务规则：状态流转、字段校验与筛选口径都收在这里。"""
from __future__ import annotations

from typing import Any

from app.services.filtersnapshot import MODULE as SNAPSHOT_MODULE
from app.services.filtersnapshot import _split_list, build_dimension_registry
from app.store import store

MODULE = "drilldown"
REQUIRED_FIELDS = ["路径编号", "起始维度", "下钻层级序列"]
STATUS_ORDER = ["草稿", "路径评审中", "已启用", "已归档"]
ACTION_RULES = {"提交路径评审": "路径评审中", "启用路径": "已启用", "归档路径": "已归档"}
NEGATIVE_ACTIONS = []


class DrilldownService:
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("路径编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return entry, []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"下钻路径 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于多维下钻可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return entry, f"下钻路径已{action}"

    def snapshot_gaps(self, entry_id: int, snapshot_id: int | None = None) -> tuple[dict[str, Any] | None, str]:
        """消费筛选条件快照，沿着调用链查找下钻路径的数据缺口。

        调用链：多维下钻 → 筛选条件快照 → 筛选预设 → 维度映射。
        对路径的每一层维度判定：已被快照条件覆盖 / 可映射但快照未覆盖（缺筛选上下文）/
        维度已失效（映射断裂），后两者都计入数据缺口。
        """
        path = store.find(MODULE, entry_id)
        if path is None:
            return None, f"下钻路径 {entry_id} 不存在或已归档"
        snapshots = [row for row in store.rows(SNAPSHOT_MODULE) if row.get("status") == "已生成"]
        if snapshot_id is not None:
            snapshots = [row for row in snapshots if int(row.get("id", 0)) == snapshot_id]
        if not snapshots:
            return None, "没有已生成的筛选条件快照，请先在筛选预设页套用预设"
        snapshot = snapshots[-1]

        effective_dims = {str(cond.get("维度", "")) for cond in snapshot.get("生效条件", [])}
        invalid_dims = {str(item.get("维度名", "")): str(item.get("原因", "")) for item in snapshot.get("失效维度", [])}
        mapping, standard, _ = build_dimension_registry()

        levels = [str(path.get("起始维度", "")).strip()] + _split_list(path.get("下钻层级序列"))
        # 起始维度通常就是序列第一层，保持顺序去重，避免同一层被检查两次
        levels = list(dict.fromkeys(level for level in levels if level))
        checks: list[dict[str, Any]] = []
        gaps: list[dict[str, Any]] = []
        for index, dim in enumerate(levels):
            if dim in effective_dims:
                checks.append({"层级": index + 1, "维度": dim, "状态": "已覆盖", "说明": "快照生效条件包含该维度"})
                continue
            if dim in invalid_dims:
                gap = {"层级": index + 1, "维度": dim, "状态": "失效", "说明": f"维度在快照中已失效：{invalid_dims[dim]}"}
            elif dim in standard or dim in mapping:
                target = dim if dim in standard else mapping[dim]
                if target in effective_dims:
                    checks.append({"层级": index + 1, "维度": dim, "状态": "已覆盖", "说明": f"经改名映射到「{target}」后命中快照条件"})
                    continue
                gap = {"层级": index + 1, "维度": dim, "状态": "未覆盖", "说明": "维度有效但快照没有对应筛选条件，下钻缺少筛选上下文"}
            else:
                gap = {"层级": index + 1, "维度": dim, "状态": "失效", "说明": "维度无法映射到标准维度，调用链在维度映射环节断裂"}
            checks.append(gap)
            gaps.append(gap)

        return {
            "路径编号": path.get("路径编号"),
            "快照编号": snapshot.get("快照编号"),
            "预设编号": snapshot.get("预设编号"),
            "调用链": ["多维下钻", "筛选条件快照", "筛选预设", "维度映射"],
            "层级检查": checks,
            "数据缺口": gaps,
        }, f"下钻路径共 {len(levels)} 层，发现 {len(gaps)} 处数据缺口"
