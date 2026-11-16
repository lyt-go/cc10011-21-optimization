"""筛选条件快照接口：检索快照、定位失效维度、恢复中断的生成流水线。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.filtersnapshot import STATUS_ORDER, FiltersnapshotService

router = APIRouter(prefix="/api/filtersnapshot", tags=["筛选条件快照"])

service = FiltersnapshotService()


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按快照编号、预设编号或预设名称检索"),
    status: str | None = Query(default=None, description="生成中、已生成、已归档"),
    invalid_dimension: str | None = Query(default=None, description="按失效维度名定位快照"),
    has_invalid: bool = Query(default=False, description="只看含失效维度或失效显示列的快照"),
    consumer: str | None = Query(default=None, description="按消费方过滤，如多维下钻"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """检索筛选条件快照；失效维度、失效显示列、使用次数都随快照返回，可追溯。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(
        keyword=keyword,
        status=status,
        invalid_dimension=invalid_dimension,
        has_invalid=has_invalid,
        consumer=consumer,
        page=page,
        size=size,
    )
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/invalid-dimensions")
def invalid_dimensions(keyword: str | None = Query(default=None, description="按维度名检索")) -> dict[str, Any]:
    """失效维度定位报告：跨快照聚合失效维度，按出现次数排序。"""
    items = service.invalid_dimension_report(keyword=keyword)
    return {"total": len(items), "items": items}


@router.get("/statuses")
def list_statuses() -> dict[str, Any]:
    """快照状态序列，给前端过滤下拉用。"""
    return {"items": STATUS_ORDER}


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出筛选条件快照清单：返回当前全量快照。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "filtersnapshot", "total": total, "items": items}


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条快照明细：生效条件、失效维度、冲突裁决、套用查询全量返回。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"筛选条件快照 {entry_id} 不存在或已归档")
    return entry


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对快照执行恢复生成、归档快照；恢复只从最后一个完整阶段继续，不回半成品。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
