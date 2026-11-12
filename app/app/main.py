"""FastAPI 入口：POST /query 采集并判定临时停运。"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException

from .crawler import Crawler
from .engine import reconstruct
from .models import QueryRequest, QueryResponse, StationDetailsResponse
from .station_service import resolve_station_details

logger = logging.getLogger("bus_notice_api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(10.0, connect=5.0),
        headers={"User-Agent": "bus-suspension-api/1.0"},
    ) as client:
        app.state.http_client = client
        yield


app = FastAPI(
    title="公交临时停运公告采集 API",
    version="1.0.0",
    description=(
        "从运营方分页发布的线路公告中，还原更正/撤销公告链，"
        "返回查询时刻仍有效的临时停运区间，并通过 CSV 附件输出"
        "每条有效公告链的受影响站点明细。"
    ),
    lifespan=lifespan,
)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/query", response_model=QueryResponse)
async def query(req: QueryRequest) -> QueryResponse:
    crawler = Crawler(app.state.http_client)
    crawl = await crawler.crawl(req.index_url)

    result = reconstruct(crawl.announcements, req.query_time)

    if crawl.errors:
        logger.warning("抓取过程出现 %d 个错误: %s", len(crawl.errors), crawl.errors[:3])

    if not crawl.list_pages:
        raise HTTPException(
            status_code=502,
            detail={
                "message": "公告首页无法抓取或无任何分页内容",
                "errors": crawl.errors,
            },
        )

    return QueryResponse(
        index_url=req.index_url,
        query_time=req.query_time,
        list_pages_crawled=crawl.list_pages,
        detail_pages_crawled=crawl.detail_pages,
        announcements_parsed=len(crawl.announcements),
        active_suspensions=result.active,
        inactive_chains=result.inactive,
        unmatched_references=result.unmatched,
        skipped_offsite_links=crawl.skipped_offsite,
    )


@app.post("/station-details", response_model=StationDetailsResponse)
async def station_details(req: QueryRequest) -> StationDetailsResponse:
    """采集公告目录并输出每条有效停运公告链的受影响站点明细。"""
    crawler = Crawler(app.state.http_client)
    crawl = await crawler.crawl(req.index_url)

    if crawl.errors:
        logger.warning(
            "站点明细查询出现 %d 个采集错误: %s", len(crawl.errors), crawl.errors[:3]
        )

    if not crawl.list_pages:
        raise HTTPException(
            status_code=502,
            detail={
                "message": "公告首页无法抓取或无任何分页内容",
                "errors": crawl.errors,
            },
        )

    return await resolve_station_details(
        crawl.announcements,
        req.query_time,
        crawler=crawler,
        index_url=req.index_url,
        crawl_errors=crawl.errors,
        list_truncated=crawl.list_truncated,
        detail_truncated=crawl.detail_truncated,
        skipped_offsite=crawl.skipped_offsite + crawler.offsite_attachments,
        list_pages=crawl.list_pages,
        detail_pages=crawl.detail_pages,
    )
