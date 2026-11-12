"""端到端：FastAPI + 爬虫 + 引擎，对本地示例公告站做完整查询。

前置：示例站点已在 127.0.0.1:8080 提供服务（见 README / demo 脚本）。
"""
from __future__ import annotations

import os

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app

SITE = os.environ.get("TEST_SITE_URL", "http://127.0.0.1:8080/index.html")
Q = "2026-11-11T10:00:00+08:00"

pytestmark = pytest.mark.asyncio


async def test_full_query_workflow():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # lifespan 由 LifespanManager 之外的方式不保证执行，手动触发
        async with app.router.lifespan_context(app):
            r = await ac.post("/query", json={"index_url": SITE, "query_time": Q})
    assert r.status_code == 200, r.text
    data = r.json()

    # 五个列表页全部爬到，24 条同站详情（跨站 1 条被跳过）
    assert len(data["list_pages_crawled"]) == 5
    assert data["detail_pages_crawled"] == 24
    assert data["announcements_parsed"] == 24
    assert len(data["skipped_offsite_links"]) == 1

    active = data["active_suspensions"]
    chains = {tuple(a["source_chain"]) for a in active}
    # 有效链：001 原始；005 原始（012 次日更正不可见）；
    # 003→006→010 两次更正；009→011 更正（带悬空引用）；
    # 014~024 的站点明细场景链查询时刻全部有效
    assert ("N-2026-001",) in chains
    assert ("N-2026-005",) in chains
    assert ("N-2026-003", "N-2026-006", "N-2026-010") in chains
    assert ("N-2026-009", "N-2026-011") in chains
    assert ("N-2026-017", "N-2026-018") in chains
    assert ("N-2026-023", "N-2026-024") in chains

    by_route = {a["route"]: a for a in active}
    # 两次更正后的最终区间
    assert by_route["25 路"]["suspend_end"].startswith("2026-11-20T22:00:00")
    # 同线路两条独立公告分别判断：12 路有两条有效停运
    routes_12 = [a for a in active if a["route"] == "12 路"]
    assert len(routes_12) == 2

    reasons = {(c["root_id"], c["reason"]) for c in data["inactive_chains"]}
    assert ("N-2026-002", "expired") in reasons
    assert ("N-2026-004", "cancelled") in reasons
    assert ("N-2026-008", "not_yet_effective") in reasons
    assert ("N-2026-013", "not_yet_effective") in reasons

    unmatched = data["unmatched_references"]
    assert len(unmatched) == 1
    assert unmatched[0]["notice_id"] == "N-2026-011"
    assert unmatched[0]["missing_reference"] == "N-2026-999"

    # 来源链接完整
    chain25 = next(a for a in active if a["route"] == "25 路")
    assert all(u.startswith("http://127.0.0.1:8080/notices/") for u in chain25["sources"])


async def test_station_details_end_to_end():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        async with app.router.lifespan_context(app):
            r = await ac.post("/station-details", json={"index_url": SITE, "query_time": Q})
    assert r.status_code == 200, r.text
    d = r.json()

    # 目录与详情采集完整 -> 整站核全；附件问题不影响该标志
    assert d["fully_verified"] is True
    assert d["attachment_errors"]  # 020/021/022 的附件问题被逐条记录
    assert d["unmatched_references"][0]["missing_reference"] == "N-2026-999"
    assert len(d["skipped_offsite_links"]) == 1

    # 核实通过的链（站点明细）：
    # 001、005、003→006→010、009→011（沿用）、014、015→016（替换）、
    # 017→018（沿用）、023 链因 024 线路变更待核实，不计入
    assert d["verified_chains_count"] == 7
    pending_roots = {p["root_id"]: p for p in d["pending_chains"]}
    assert set(pending_roots) == {
        "N-2026-019",  # 无附件
        "N-2026-020",  # 附件 404
        "N-2026-021",  # 表内线路不符
        "N-2026-022",  # CSV 缺列
        "N-2026-023",  # 更正线路变更且无新附件
    }
    assert pending_roots["N-2026-019"]["reason"] == "attachment_missing"
    assert pending_roots["N-2026-020"]["reason"] == "attachment_fetch_failed"
    assert pending_roots["N-2026-021"]["reason"] == "attachment_invalid"
    assert pending_roots["N-2026-022"]["reason"] == "attachment_invalid"
    assert (
        pending_roots["N-2026-023"]["reason"]
        == "route_changed_without_attachment"
    )
    # 待核实链仍带区间与来源，便于调度室追溯
    p23 = pending_roots["N-2026-023"]
    assert p23["route"] == "68 路"
    assert p23["source_chain"] == ["N-2026-023", "N-2026-024"]
    assert p23["suspend_end"].startswith("2026-11-15T20:00:00")
    assert len(p23["chain_attachments"]) == 1

    suspensions = d["station_suspensions"]

    # 25 路：两次更正各带新附件，最终只输出 010 表的 3 个站点
    s25 = [s for s in suspensions if s["route"] == "25 路"]
    assert sorted(s["station_no"] for s in s25) == ["25-01", "25-03", "25-04"]
    assert all(s["attachment_source"]["notice_id"] == "N-2026-010" for s in s25)
    # 历史附件可追溯（003、006 被替换的旧表仍在 chain_attachments）
    chain_att = [a["notice_id"] for a in s25[0]["chain_attachments"]]
    assert chain_att == ["N-2026-003", "N-2026-006", "N-2026-010"]
    # 区间为最后一次更正后的有效区间
    assert all(s["suspend_end"].startswith("2026-11-20T22:00:00") for s in s25)

    # 40 路：011 无附件且线路不变，沿用 009 的表
    s40 = [s for s in suspensions if s["route"] == "40 路"]
    assert sorted(s["station_no"] for s in s40) == ["40-01", "40-02"]
    assert all(s["attachment_source"]["notice_id"] == "N-2026-009" for s in s40)
    # 区间被 011 更正延长到 11-16
    assert all(s["suspend_end"].startswith("2026-11-16T20:00:00") for s in s40)

    # 61 路：016 新表替换 015 旧表
    s61 = [s for s in suspensions if s["route"] == "61 路"]
    assert sorted(s["station_no"] for s in s61) == ["61-01", "61-03"]
    assert all(s["attachment_source"]["notice_id"] == "N-2026-016" for s in s61)

    # 62 路：018 无附件线路不变，沿用 017 的表
    s62 = [s for s in suspensions if s["route"] == "62 路"]
    assert sorted(s["station_no"] for s in s62) == ["62-01", "62-02"]
    assert all(s["attachment_source"]["notice_id"] == "N-2026-017" for s in s62)

    # 站点名称与两类来源链接
    s001 = next(s for s in suspensions if s["source_chain"] == ["N-2026-001"])
    assert s001["station_name"] == "火车站"
    assert s001["attachment_source"]["attachment_url"].endswith(
        "/attachments/N-2026-001.csv"
    )
    assert s001["notice_sources"] == [
        "http://127.0.0.1:8080/notices/N-2026-001.html"
    ]

    # 12 路两条独立链的站点都在
    s12 = [s for s in suspensions if s["route"] == "12 路"]
    assert {tuple(s["source_chain"]) for s in s12} == {
        ("N-2026-001",),
        ("N-2026-005",),
    }

    # 成功抓取并解析的附件数（020 未抓到；021/022 不计入）
    assert d["attachments_fetched"] == 11

    # 无效链仍回传，但不含站点
    inactive_roots = {c["root_id"] for c in d["inactive_chains"]}
    assert {"N-2026-002", "N-2026-004", "N-2026-008", "N-2026-013"} <= inactive_roots


async def test_naive_query_time_rejected():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        async with app.router.lifespan_context(app):
            r = await ac.post(
                "/station-details",
                json={"index_url": SITE, "query_time": "2026-11-11T10:00:00"},
            )
    assert r.status_code == 422


async def test_bad_index_url_returns_502():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        async with app.router.lifespan_context(app):
            r = await ac.post(
                "/station-details",
                json={
                    "index_url": "http://127.0.0.1:9/never.html",
                    "query_time": Q,
                },
            )
    assert r.status_code == 502
