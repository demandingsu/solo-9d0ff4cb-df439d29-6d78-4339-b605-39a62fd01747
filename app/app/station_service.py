"""站点明细还原：在公告链判定之上解析 CSV 附件并输出受影响站点。

附件规则（对应业务约定，仅作用于查询时刻有效的公告链）：

* 详情页可携带一个指向本站的 CSV 附件（线路、站点编号）；
* 更正公告携带新附件时，新表替换旧表；
* 更正公告未带附件且线路不变时，沿用链上最近一份附件表；
* 更正公告未带附件但线路发生变更时，该链标记为待核实，不用旧表补齐；
* 附件缺失、抓取失败、CSV 解析失败或表内线路与当前线路不符，
  只把相关链标为待核实，绝不使用旧表补齐；
* 撤销/过期/未生效等无效链不输出站点明细。

目录或详情采集不全（抓取/解析失败或触发限量截断）时，
``fully_verified=False``，整站标为“未核全”；附件问题不影响该标志。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from .csv_attachment import AttachmentError, parse_station_csv
from .engine import ChainRecord, build_chains
from .models import (
    Announcement,
    AttachmentRef,
    InactiveChain,
    PendingChain,
    StationDetailsResponse,
    StationRow,
    StationSuspension,
    UnmatchedReference,
)


@dataclass
class _Resolution:
    suspensions: list[StationSuspension] = field(default_factory=list)
    pending: list[PendingChain] = field(default_factory=list)
    inactive: list[InactiveChain] = field(default_factory=list)
    unmatched: list[UnmatchedReference] = field(default_factory=list)
    fetched_urls: set[str] = field(default_factory=set)
    attachment_errors: list[str] = field(default_factory=list)

    @property
    def attachments_fetched(self) -> int:
        return len(self.fetched_urls)


def _chain_attachments(chain_ann: list[Announcement]) -> list[AttachmentRef]:
    """链上携带同站附件的公告，按发布时间排序。"""
    out = [
        AttachmentRef(
            notice_id=a.notice_id,
            notice_url=a.url,
            attachment_url=a.attachment_url,
        )
        for a in sorted(chain_ann, key=lambda x: x.published_at)
        if a.attachment_url
    ]
    return out


async def resolve_station_details(
    announcements: list[Announcement],
    query_time: datetime,
    *,
    crawler,
    index_url: str,
    crawl_errors: list[str],
    list_truncated: bool,
    detail_truncated: bool,
    skipped_offsite: list[str],
    list_pages: list[str],
    detail_pages: int,
) -> StationDetailsResponse:
    """采集结果 -> 站点明细响应。"""
    chains, unmatched, _by_id = build_chains(announcements, query_time)
    r = _Resolution()
    r.unmatched = unmatched

    for chain in chains:
        if chain.inactive_reason is not None:
            r.inactive.append(
                InactiveChain(
                    root_id=chain.root.notice_id,
                    route=chain.route,
                    reason=chain.inactive_reason,
                    chain=list(chain.chain_ids),
                    sources=list(chain.sources),
                    latest_published_at=chain.latest_published_at,
                )
            )
            continue
        await _resolve_active_chain(chain, crawler, index_url, r)

    r.suspensions.sort(key=lambda s: (s.route, s.station_no, s.suspend_start))
    r.pending.sort(key=lambda p: p.root_id)
    r.inactive.sort(key=lambda x: (x.root_id, tuple(x.chain)))

    fully_verified = (
        not crawl_errors
        and not list_truncated
        and not detail_truncated
    )

    return StationDetailsResponse(
        index_url=index_url,
        query_time=query_time,
        list_pages_crawled=list(list_pages),
        detail_pages_crawled=detail_pages,
        announcements_parsed=len(announcements),
        attachments_fetched=r.attachments_fetched,
        attachment_errors=r.attachment_errors,
        fully_verified=fully_verified,
        verified_chains_count=len({tuple(s.source_chain) for s in r.suspensions}),
        pending_chains_count=len(r.pending),
        station_suspensions=r.suspensions,
        pending_chains=r.pending,
        inactive_chains=r.inactive,
        unmatched_references=r.unmatched,
        skipped_offsite_links=list(skipped_offsite),
    )


def _make_pending(
    chain: ChainRecord,
    reason: str,
    detail: str,
    chain_attachments: list[AttachmentRef],
) -> PendingChain:
    return PendingChain(
        root_id=chain.root.notice_id,
        route=chain.route,
        reason=reason,
        detail=detail,
        source_chain=list(chain.chain_ids),
        notice_sources=list(chain.sources),
        chain_attachments=chain_attachments,
        suspend_start=chain.suspend_start,
        suspend_end=chain.suspend_end,
        latest_published_at=chain.latest_published_at,
    )


async def _resolve_active_chain(
    chain: ChainRecord, crawler, index_url: str, r: _Resolution
) -> None:
    """对一条有效链按发布时间逐公告应用附件规则。"""
    ordered = sorted(chain.chain_ann, key=lambda x: x.published_at)
    chain_attachments = _chain_attachments(chain.chain_ann)

    # 当前生效线路（链上最后一条可见公告的线路）
    current_route = chain.route
    # 最近一份成功采用的表，以及它对应的来源公告
    current_table: list[StationRow] | None = None
    current_ref: AttachmentRef | None = None
    # 最近一次出现附件的公告所声明的线路，用于判断“未带附件但线路变更”
    last_attachment_route: str | None = None

    for a in ordered:
        if a.attachment_url is None:
            # 未带附件：若自最近一份表之后线路发生变更，则不能沿用旧表
            if (
                current_table is not None
                and a.route is not None
                and last_attachment_route is not None
                and a.route != last_attachment_route
            ):
                r.pending.append(
                    _make_pending(
                        chain,
                        reason="route_changed_without_attachment",
                        detail=(
                            f"公告 {a.notice_id} 将线路由 {last_attachment_route} "
                            f"变更为 {a.route}，但未携带新附件，无法沿用旧表"
                        ),
                        chain_attachments=chain_attachments,
                    )
                )
                return
            # 线路不变（或无法判断）：继续沿用最近的表
            continue

        # 携带附件：新表必须独立抓取/解析成功，任何失败都只标待核实，不回退旧表
        fetch = await crawler.fetch_attachment(a.attachment_url, index_url)
        if not fetch.ok:
            r.attachment_errors.append(
                f"附件抓取失败 {a.attachment_url}（公告 {a.notice_id}）: {fetch.error}"
            )
            r.pending.append(
                _make_pending(
                    chain,
                    reason="attachment_fetch_failed",
                    detail=f"公告 {a.notice_id} 的附件抓取失败: {fetch.error}",
                    chain_attachments=chain_attachments,
                )
            )
            return

        try:
            rows = parse_station_csv(fetch.content)
        except AttachmentError as exc:
            r.attachment_errors.append(
                f"附件解析失败 {a.attachment_url}（公告 {a.notice_id}）: {exc}"
            )
            r.pending.append(
                _make_pending(
                    chain,
                    reason="attachment_invalid",
                    detail=f"公告 {a.notice_id} 的附件无法解析: {exc}",
                    chain_attachments=chain_attachments,
                )
            )
            return

        # 表内线路必须与“该公告时刻链上的当前线路”一致，逐行核对
        expected_route = a.route or current_route
        bad = [row.route for row in rows if row.route != expected_route]
        if bad:
            detail = (
                f"公告 {a.notice_id} 附件内线路 {sorted(set(bad))} "
                f"与公告线路 {expected_route} 不符"
            )
            r.attachment_errors.append(
                f"附件线路不符 {a.attachment_url}（公告 {a.notice_id}）: {detail}"
            )
            r.pending.append(
                _make_pending(
                    chain,
                    reason="attachment_invalid",
                    detail=detail,
                    chain_attachments=chain_attachments,
                )
            )
            return

        # 新表替换旧表
        ref = AttachmentRef(
            notice_id=a.notice_id,
            notice_url=a.url,
            attachment_url=a.attachment_url,
        )
        current_table = rows
        current_ref = ref
        last_attachment_route = a.route or last_attachment_route
        r.fetched_urls.add(a.attachment_url)

    if current_table is None or current_ref is None:
        r.pending.append(
            _make_pending(
                chain,
                reason="attachment_missing",
                detail="整条公告链未携带任何可用的 CSV 站点清单附件",
                chain_attachments=chain_attachments,
            )
        )
        return

    for row in current_table:
        r.suspensions.append(
            StationSuspension(
                route=current_route or row.route,
                station_no=row.station_no,
                station_name=row.station_name,
                suspend_start=chain.suspend_start,
                suspend_end=chain.suspend_end,
                source_chain=list(chain.chain_ids),
                notice_sources=list(chain.sources),
                attachment_source=current_ref,
                chain_attachments=chain_attachments,
            )
        )
