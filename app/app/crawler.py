"""同站分页 + 详情 + 附件抓取。

约束：
* 只沿同站链接爬行（协议/主机/端口一致），跨站分页、详情或附件链接跳过并记录；
* 分页跟随 ``rel=next``，带最大页数与死循环保护；
* 详情 URL 去重，抓取失败不中断整体查询，作为错误收集返回；
* 触发最大页数/详情数限量时置 ``truncated=True``，供上层把整站标为“未核全”；
* CSV 附件单独按需抓取（去重缓存），失败不中断，错误信息交由上层挂到相关链。
"""
from __future__ import annotations

from dataclasses import dataclass, field

import httpx

from .models import Announcement
from .parser import is_same_site, parse_detail, parse_detail_links, parse_next_page


@dataclass
class AttachmentFetch:
    """一次附件抓取的结果。"""

    url: str
    ok: bool
    content: bytes | None = None
    error: str | None = None


@dataclass
class CrawlResult:
    announcements: list[Announcement] = field(default_factory=list)
    list_pages: list[str] = field(default_factory=list)
    detail_pages: int = 0
    skipped_offsite: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    list_truncated: bool = False
    detail_truncated: bool = False


class Crawler:
    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        max_list_pages: int = 50,
        max_detail_pages: int = 500,
    ) -> None:
        self._client = client
        self._max_list_pages = max_list_pages
        self._max_detail_pages = max_detail_pages
        self._attachment_cache: dict[str, AttachmentFetch] = {}
        self.offsite_attachments: list[str] = []

    async def _get(self, url: str) -> str | None:
        try:
            resp = await self._client.get(url, follow_redirects=True)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            return f"__ERROR__:{type(exc).__name__}:{exc}"
        return resp.text

    async def crawl(self, index_url: str) -> CrawlResult:
        result = CrawlResult()
        seen_pages: set[str] = set()
        detail_urls: list[str] = []
        seen_details: set[str] = set()

        # 1) 抓列表分页（只走同站 rel=next）
        page_url: str | None = index_url
        while page_url and page_url not in seen_pages:
            if len(seen_pages) >= self._max_list_pages:
                result.list_truncated = True
                break
            seen_pages.add(page_url)
            html = await self._get(page_url)
            if html is None or html.startswith("__ERROR__:"):
                result.errors.append(f"列表页抓取失败 {page_url}: {html}")
                break
            result.list_pages.append(page_url)

            for detail_url, _id, _kind in parse_detail_links(html, page_url):
                if not is_same_site(detail_url, index_url):
                    result.skipped_offsite.append(detail_url)
                    continue
                if detail_url not in seen_details:
                    seen_details.add(detail_url)
                    detail_urls.append(detail_url)

            nxt = parse_next_page(html, page_url)
            if nxt and not is_same_site(nxt, index_url):
                result.skipped_offsite.append(nxt)
                nxt = None
            page_url = nxt

        # 2) 抓详情（同站已保证），限量保护
        queued = detail_urls
        if len(queued) > self._max_detail_pages:
            result.detail_truncated = True
        for detail_url in queued[: self._max_detail_pages]:
            html = await self._get(detail_url)
            if html is None or html.startswith("__ERROR__:"):
                result.errors.append(f"详情页抓取失败 {detail_url}: {html}")
                continue
            result.detail_pages += 1
            try:
                result.announcements.append(parse_detail(html, detail_url))
            except ValueError as exc:
                result.errors.append(f"详情页解析失败 {detail_url}: {exc}")

        return result

    async def fetch_attachment(self, url: str, base_url: str) -> AttachmentFetch:
        """抓取同站 CSV 附件（按 URL 去重缓存）。

        跨站链接不发起请求，直接返回失败结果，由上层标记相关链待核实，
        同时把链接计入跨站跳过列表由调用方维护。
        """
        if url in self._attachment_cache:
            return self._attachment_cache[url]
        if not is_same_site(url, base_url):
            out = AttachmentFetch(
                url=url, ok=False, error="附件为跨站链接，按同站限制跳过"
            )
            self._attachment_cache[url] = out
            if url not in self.offsite_attachments:
                self.offsite_attachments.append(url)
            return out
        try:
            resp = await self._client.get(url, follow_redirects=True)
            resp.raise_for_status()
            out = AttachmentFetch(url=url, ok=True, content=resp.content)
        except httpx.HTTPError as exc:
            out = AttachmentFetch(
                url=url, ok=False, error=f"{type(exc).__name__}: {exc}"
            )
        self._attachment_cache[url] = out
        return out
