"""站点明细还原服务：附件替换/沿用/待核实规则（纯单元，无网络）。"""
from __future__ import annotations

from datetime import datetime

import pytest

from app.csv_attachment import parse_station_csv
from app.models import Announcement, NoticeKind
from app.station_service import resolve_station_details

TZ = "+08:00"


def dt(s: str) -> datetime:
    return datetime.fromisoformat(s)


Q = dt(f"2026-11-11T10:00:00{TZ}")


def make(
    notice_id: str,
    kind: NoticeKind,
    published: str,
    *,
    route: str | None = "12 路",
    start: str = f"2026-11-11T06:00:00{TZ}",
    end: str = f"2026-11-15T20:00:00{TZ}",
    refs: list[str] | None = None,
    attachment: str | None = None,
) -> Announcement:
    return Announcement(
        notice_id=notice_id,
        route=route,
        kind=kind,
        suspend_start=dt(start) if start else None,
        suspend_end=dt(end) if end else None,
        published_at=dt(published),
        references=refs or [],
        url=f"http://x/notices/{notice_id}.html",
        attachment_url=attachment,
    )


def csv_bytes(route: str, nos: list[str]) -> bytes:
    lines = ["线路,站点编号,站点名称"] + [
        f"{route},{no},站{no}" for no in nos
    ]
    return ("\n".join(lines) + "\n").encode("utf-8")


class FakeCrawler:
    """按 URL 返回预置字节；未登记的 URL 视为抓取失败。"""

    def __init__(self, contents: dict[str, bytes]):
        self._contents = contents
        self.calls: list[str] = []

    async def fetch_attachment(self, url: str, base_url: str):
        from app.crawler import AttachmentFetch

        self.calls.append(url)
        if url in self._contents:
            return AttachmentFetch(url=url, ok=True, content=self._contents[url])
        return AttachmentFetch(url=url, ok=False, error="HTTPStatusError: 404")


async def _resolve(anns, crawler):
    return await resolve_station_details(
        anns,
        Q,
        crawler=crawler,
        index_url="http://x/index.html",
        crawl_errors=[],
        list_truncated=False,
        detail_truncated=False,
        skipped_offsite=[],
        list_pages=["http://x/index.html"],
        detail_pages=len(anns),
    )


async def test_single_notice_with_attachment_verified():
    anns = [make("A1", NoticeKind.SUSPENSION, f"2026-11-08T09:00:00{TZ}",
                 attachment="http://x/attachments/A1.csv")]
    crawler = FakeCrawler({"http://x/attachments/A1.csv": csv_bytes("12 路", ["01", "02"])})
    res = await _resolve(anns, crawler)

    assert res.fully_verified is True
    assert res.pending_chains == []
    assert res.verified_chains_count == 1
    assert len(res.station_suspensions) == 2
    s = res.station_suspensions[0]
    assert s.route == "12 路"
    assert s.station_no == "01"
    assert s.attachment_source.notice_id == "A1"
    assert s.attachment_source.attachment_url == "http://x/attachments/A1.csv"
    assert s.notice_sources == ["http://x/notices/A1.html"]
    assert s.source_chain == ["A1"]
    assert res.attachments_fetched == 1


async def test_correction_with_new_attachment_replaces_old_table():
    anns = [
        make("A1", NoticeKind.SUSPENSION, f"2026-11-07T09:00:00{TZ}",
             attachment="http://x/a1.csv"),
        make("A2", NoticeKind.CORRECTION, f"2026-11-09T09:00:00{TZ}",
             start=f"2026-11-10T06:00:00{TZ}", refs=["A1"],
             attachment="http://x/a2.csv"),
    ]
    crawler = FakeCrawler({
        "http://x/a1.csv": csv_bytes("12 路", ["OLD"]),
        "http://x/a2.csv": csv_bytes("12 路", ["NEW1", "NEW2"]),
    })
    res = await _resolve(anns, crawler)

    assert res.pending_chains == []
    nos = {s.station_no for s in res.station_suspensions}
    assert nos == {"NEW1", "NEW2"}  # 旧表被替换
    used = res.station_suspensions[0].attachment_source
    assert used.notice_id == "A2"
    # 历史附件仍可追溯
    chain_att = res.station_suspensions[0].chain_attachments
    assert [a.notice_id for a in chain_att] == ["A1", "A2"]


async def test_correction_without_attachment_same_route_keeps_table():
    anns = [
        make("A1", NoticeKind.SUSPENSION, f"2026-11-07T09:00:00{TZ}",
             attachment="http://x/a1.csv"),
        make("A2", NoticeKind.CORRECTION, f"2026-11-09T09:00:00{TZ}",
             refs=["A1"], attachment=None),
    ]
    crawler = FakeCrawler({"http://x/a1.csv": csv_bytes("12 路", ["K1"])})
    res = await _resolve(anns, crawler)

    assert res.pending_chains == []
    assert {s.station_no for s in res.station_suspensions} == {"K1"}
    assert res.station_suspensions[0].attachment_source.notice_id == "A1"
    # 更正只改了区间，站点沿用
    assert res.station_suspensions[0].suspend_start == dt(
        f"2026-11-11T06:00:00{TZ}"
    )


async def test_route_change_without_attachment_pending_no_fallback():
    anns = [
        make("A1", NoticeKind.SUSPENSION, f"2026-11-07T09:00:00{TZ}", route="12 路",
             attachment="http://x/a1.csv"),
        make("A2", NoticeKind.CORRECTION, f"2026-11-09T09:00:00{TZ}", route="13 路",
             refs=["A1"], attachment=None),
    ]
    crawler = FakeCrawler({"http://x/a1.csv": csv_bytes("12 路", ["OLD"])})
    res = await _resolve(anns, crawler)

    assert res.station_suspensions == []
    assert len(res.pending_chains) == 1
    p = res.pending_chains[0]
    assert p.reason == "route_changed_without_attachment"
    assert p.source_chain == ["A1", "A2"]
    assert p.route == "13 路"


async def test_missing_attachment_marks_chain_pending():
    anns = [make("A1", NoticeKind.SUSPENSION, f"2026-11-08T09:00:00{TZ}")]
    res = await _resolve(anns, FakeCrawler({}))
    assert res.station_suspensions == []
    assert res.pending_chains[0].reason == "attachment_missing"


async def test_fetch_failure_marks_chain_pending_without_old_table():
    anns = [
        make("A1", NoticeKind.SUSPENSION, f"2026-11-07T09:00:00{TZ}",
             attachment="http://x/a1.csv"),
        make("A2", NoticeKind.CORRECTION, f"2026-11-09T09:00:00{TZ}",
             refs=["A1"], attachment="http://x/missing.csv"),
    ]
    crawler = FakeCrawler({"http://x/a1.csv": csv_bytes("12 路", ["OLD"])})
    res = await _resolve(anns, crawler)

    assert res.station_suspensions == []  # 不用旧表补齐
    assert res.pending_chains[0].reason == "attachment_fetch_failed"
    assert any("missing.csv" in e for e in res.attachment_errors)


async def test_route_mismatch_in_csv_marks_chain_pending():
    anns = [make("A1", NoticeKind.SUSPENSION, f"2026-11-08T09:00:00{TZ}",
                 route="12 路", attachment="http://x/a1.csv")]
    crawler = FakeCrawler({"http://x/a1.csv": csv_bytes("99 路", ["X1"])})
    res = await _resolve(anns, crawler)

    assert res.station_suspensions == []
    assert res.pending_chains[0].reason == "attachment_invalid"
    assert "99 路" in res.pending_chains[0].detail


async def test_malformed_csv_marks_chain_pending():
    bad = "线路,编号\n12 路,1\n".encode("utf-8")  # 缺站点编号列
    anns = [make("A1", NoticeKind.SUSPENSION, f"2026-11-08T09:00:00{TZ}",
                 attachment="http://x/a1.csv")]
    res = await _resolve(anns, FakeCrawler({"http://x/a1.csv": bad}))
    assert res.pending_chains[0].reason == "attachment_invalid"
    assert any("a1.csv" in e for e in res.attachment_errors)


async def test_cancelled_chain_excluded_from_station_output():
    anns = [
        make("A1", NoticeKind.SUSPENSION, f"2026-11-07T09:00:00{TZ}",
             attachment="http://x/a1.csv"),
        make("A2", NoticeKind.CANCELLATION, f"2026-11-09T09:00:00{TZ}",
             start=None, end=None, refs=["A1"]),
    ]
    crawler = FakeCrawler({"http://x/a1.csv": csv_bytes("12 路", ["X"])})
    res = await _resolve(anns, crawler)

    assert res.station_suspensions == []
    assert res.pending_chains == []
    assert res.inactive_chains[0].reason == "cancelled"
    assert crawler.calls == []  # 无效链不抓附件


async def test_one_bad_chain_does_not_affect_others():
    good = make("A1", NoticeKind.SUSPENSION, f"2026-11-08T09:00:00{TZ}", route="12 路",
                attachment="http://x/good.csv")
    bad = make("B1", NoticeKind.SUSPENSION, f"2026-11-08T09:00:00{TZ}", route="13 路",
               attachment="http://x/bad.csv")
    crawler = FakeCrawler({
        "http://x/good.csv": csv_bytes("12 路", ["G1"]),
        "http://x/bad.csv": csv_bytes("13 路", ["B1", "B2"]),
    })
    # 让 B 链附件抓取失败
    crawler._contents.pop("http://x/bad.csv")
    res = await _resolve([good, bad], crawler)

    assert res.verified_chains_count == 1
    assert {s.station_no for s in res.station_suspensions} == {"G1"}
    assert res.pending_chains[0].root_id == "B1"
    assert res.pending_chains[0].reason == "attachment_fetch_failed"


async def test_fully_verified_false_when_crawl_errors():
    anns = [make("A1", NoticeKind.SUSPENSION, f"2026-11-08T09:00:00{TZ}",
                 attachment="http://x/a1.csv")]
    res = await resolve_station_details(
        anns,
        Q,
        crawler=FakeCrawler({"http://x/a1.csv": csv_bytes("12 路", ["X"])}),
        index_url="http://x/index.html",
        crawl_errors=["详情页抓取失败 http://x/missing.html: ..."],
        list_truncated=False,
        detail_truncated=False,
        skipped_offsite=[],
        list_pages=["http://x/index.html"],
        detail_pages=1,
    )
    assert res.fully_verified is False
    # 附件正常，链本身仍核实通过
    assert res.verified_chains_count == 1
    assert res.pending_chains == []


async def test_fully_verified_false_when_truncated():
    anns = [make("A1", NoticeKind.SUSPENSION, f"2026-11-08T09:00:00{TZ}",
                 attachment="http://x/a1.csv")]
    res = await resolve_station_details(
        anns,
        Q,
        crawler=FakeCrawler({"http://x/a1.csv": csv_bytes("12 路", ["X"])}),
        index_url="http://x/index.html",
        crawl_errors=[],
        list_truncated=True,
        detail_truncated=False,
        skipped_offsite=[],
        list_pages=["http://x/index.html"],
        detail_pages=1,
    )
    assert res.fully_verified is False


async def test_offsite_attachment_pending():
    from app.crawler import AttachmentFetch

    class OffsiteCrawler(FakeCrawler):
        def __init__(self, contents):
            super().__init__(contents)
            self.offsite_attachments: list[str] = []

        async def fetch_attachment(self, url, base_url):
            from app.crawler import AttachmentFetch

            self.offsite_attachments.append(url)
            return AttachmentFetch(url=url, ok=False, error="附件为跨站链接，按同站限制跳过")

    anns = [make("A1", NoticeKind.SUSPENSION, f"2026-11-08T09:00:00{TZ}",
                 attachment="http://other.example.org/a.csv")]
    crawler = OffsiteCrawler({})
    res = await _resolve(anns, crawler)
    assert res.pending_chains[0].reason == "attachment_fetch_failed"
    assert "跨站" in res.pending_chains[0].detail
    assert crawler.offsite_attachments == ["http://other.example.org/a.csv"]
