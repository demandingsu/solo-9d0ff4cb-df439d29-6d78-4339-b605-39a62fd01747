"""领域数据模型。

所有时间在系统内部统一为时区感知的 ``datetime``（UTC 或带原始偏移量均可，
比较时 Python 会自动换算）。
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field, field_validator


class NoticeKind(str, Enum):
    """公告类型。"""

    SUSPENSION = "suspension"          # 原始停运公告
    CORRECTION = "correction"          # 更正公告
    CANCELLATION = "cancellation"      # 撤销公告


class Announcement(BaseModel):
    """从详情页解析出的单条公告。"""

    notice_id: str = Field(..., description="公告编号，如 2026-001")
    route: str | None = Field(None, description="线路，如 12 路；更正/撤销页可能缺失")
    kind: NoticeKind
    suspend_start: datetime | None = None
    suspend_end: datetime | None = None
    published_at: datetime
    references: list[str] = Field(default_factory=list, description="更正/撤销引用的公告编号")
    url: str
    title: str | None = None
    attachment_url: str | None = Field(
        None, description="详情页中指向本站的 CSV 站点清单附件绝对 URL；无附件时为 None"
    )

    @field_validator("published_at")
    @classmethod
    def _published_aware(cls, v: datetime) -> datetime:
        if v.tzinfo is None or v.utcoffset() is None:
            raise ValueError("published_at 必须带时区信息")
        return v


class QueryRequest(BaseModel):
    """查询入参。"""

    index_url: str = Field(..., description="公告列表首页 URL（同站分页的起点）")
    query_time: datetime = Field(..., description="带时区的查询时刻，ISO 8601")

    @field_validator("index_url")
    @classmethod
    def _url_scheme(cls, v: str) -> str:
        if not (v.startswith("http://") or v.startswith("https://")):
            raise ValueError("index_url 必须是 http/https URL")
        return v

    @field_validator("query_time")
    @classmethod
    def _query_aware(cls, v: datetime) -> datetime:
        if v.tzinfo is None or v.utcoffset() is None:
            raise ValueError("query_time 必须带时区，例如 2026-11-11T10:00:00+08:00")
        return v


class ActiveSuspension(BaseModel):
    """查询时刻仍然有效的停运区间。"""

    route: str
    suspend_start: datetime
    suspend_end: datetime
    source_chain: list[str] = Field(..., description="形成该结论的公告链编号，根公告在前")
    sources: list[str] = Field(..., description="链上各公告详情页链接")
    published_at: datetime = Field(..., description="链上最新生效公告的发布时间")


class InactiveChain(BaseModel):
    """未产生有效停运的公告链，用于解释判定过程。"""

    root_id: str
    route: str | None
    reason: str = Field(..., description="cancelled / not_yet_effective / expired / no_interval")
    chain: list[str]
    sources: list[str]
    latest_published_at: datetime | None = None


class UnmatchedReference(BaseModel):
    """指向不存在公告的引用。"""

    notice_id: str = Field(..., description="发出引用的公告编号")
    missing_reference: str
    url: str
    kind: NoticeKind


class QueryResponse(BaseModel):
    """查询结果。"""

    index_url: str
    query_time: datetime
    list_pages_crawled: list[str]
    detail_pages_crawled: int
    announcements_parsed: int
    active_suspensions: list[ActiveSuspension]
    inactive_chains: list[InactiveChain]
    unmatched_references: list[UnmatchedReference]
    skipped_offsite_links: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# 站点明细接口（/station-details）
# ---------------------------------------------------------------------------


class AttachmentRef(BaseModel):
    """一份 CSV 站点清单附件的来源信息。"""

    notice_id: str = Field(..., description="携带该附件的公告编号")
    notice_url: str = Field(..., description="附件所在详情页 URL")
    attachment_url: str = Field(..., description="CSV 附件绝对 URL（同站）")


class StationRow(BaseModel):
    """CSV 附件中的一行站点数据。"""

    route: str = Field(..., description="线路（取自 CSV 线路列）")
    station_no: str = Field(..., description="站点编号（取自 CSV 站点编号列）")
    station_name: str | None = Field(None, description="站点名称；CSV 未提供该列时为 None")


class StationSuspension(BaseModel):
    """一条已核实有效公告链输出的单个受影响站点。"""

    route: str = Field(..., description="当前生效线路（公告链最新线路）")
    station_no: str
    station_name: str | None = None
    suspend_start: datetime = Field(..., description="当前生效停运区间起点（半开区间）")
    suspend_end: datetime = Field(..., description="当前生效停运区间终点（半开区间）")
    source_chain: list[str] = Field(..., description="形成该结论的公告链编号，根公告在前")
    notice_sources: list[str] = Field(..., description="链上各公告详情页链接")
    attachment_source: AttachmentRef = Field(..., description="实际采用的 CSV 附件来源")
    chain_attachments: list[AttachmentRef] = Field(
        ..., description="链上各公告携带的全部同站附件（含被替换的旧表），按发布时间排序"
    )


class PendingChain(BaseModel):
    """附件无法核实、需要人工介入的有效公告链。"""

    root_id: str
    route: str | None = Field(None, description="链上最新公告的线路；缺失时为 None")
    reason: str = Field(
        ...,
        description=(
            "attachment_missing / attachment_fetch_failed / "
            "attachment_invalid / route_changed_without_attachment"
        ),
    )
    detail: str = Field(..., description="待核实原因的具体说明")
    source_chain: list[str]
    notice_sources: list[str]
    chain_attachments: list[AttachmentRef] = Field(default_factory=list)
    suspend_start: datetime | None = None
    suspend_end: datetime | None = None
    latest_published_at: datetime | None = None


class StationDetailsResponse(BaseModel):
    """站点明细查询结果。"""

    index_url: str
    query_time: datetime
    list_pages_crawled: list[str]
    detail_pages_crawled: int
    announcements_parsed: int
    attachments_fetched: int = Field(..., description="成功抓取并解析的同站 CSV 附件数")
    attachment_errors: list[str] = Field(
        default_factory=list, description="附件抓取/解析失败的逐条错误信息"
    )
    fully_verified: bool = Field(
        ...,
        description=(
            "目录与详情是否全部采集成功（无抓取/解析失败、未触发限量截断）；"
            "附件问题不影响该标志，只影响相关链的待核实状态"
        ),
    )
    verified_chains_count: int
    pending_chains_count: int
    station_suspensions: list[StationSuspension]
    pending_chains: list[PendingChain]
    inactive_chains: list[InactiveChain] = Field(
        default_factory=list, description="撤销/过期/未生效等无效链，不含站点明细"
    )
    unmatched_references: list[UnmatchedReference] = Field(default_factory=list)
    skipped_offsite_links: list[str] = Field(default_factory=list)
