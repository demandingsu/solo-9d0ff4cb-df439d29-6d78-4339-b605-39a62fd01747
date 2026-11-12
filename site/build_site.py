"""生成离线示例公告站（纯静态 HTML + CSV 附件）。

公告链场景覆盖（查询时刻 2026-11-11T10:00+08:00）：
  001 12 路原始停运，查询时有效（带 CSV 站点清单）
  002 18 路已过停运结束时间（过期）
  003 25 路原始停运（带附件），006 更正（带新附件，替换旧表）、
      010 再更正（再带新附件，二次替换；更正引用更正）
  004 33 路停运，007 撤销（整条链失效）
  005 12 路另一条独立停运（同线路独立判断，带独立附件），
      012 更正发布于查询时刻之后（不可见）
  008 5 路停运区间尚未开始
  009 40 路停运（带附件），011 更正未带附件且线路不变（沿用 009 的表），
      011 同时引用了不存在的 N-2026-999（无法匹配的引用）
  013 50 路整条公告发布于查询时刻之后（时间门控）
  014 60 路原始停运（带附件，核实通过）
  015 61 路原始停运（带附件）→ 016 更正带新附件（新表替换旧表，核实通过）
  017 62 路原始停运（带附件）→ 018 更正未带附件且线路不变（沿用旧表，核实通过）
  019 63 路原始停运，整条链无附件（待核实：attachment_missing）
  020 64 路原始停运，附件链接 404（待核实：attachment_fetch_failed）
  021 65 路原始停运，附件表内线路写成 66 路（待核实：attachment_invalid）
  022 66 路原始停运，附件 CSV 缺少“站点编号”列（待核实：attachment_invalid）
  023 67 路原始停运（带附件）→ 024 更正未带附件且线路变更为 68 路
      （待核实：route_changed_without_attachment，不用旧表补齐）
  另有一个跨站详情链接，验证同站限制。
"""
from __future__ import annotations

import html
import shutil
from dataclasses import dataclass, field
from pathlib import Path

OUT_DIR = Path(__file__).parent / "html"
ATTACH_DIR = "attachments"
PAGE_SIZE = 5
TZ = "+08:00"


@dataclass
class Notice:
    notice_id: str
    kind: str  # suspension | correction | cancellation
    title: str
    route: str | None
    published_at: str
    suspend_start: str | None = None
    suspend_end: str | None = None
    references: list[str] = field(default_factory=list)
    offsite: bool = False  # 列表项指向外站
    attachment: str | None = None  # attachments/ 下的 CSV 文件名；None 表示无附件


NOTICES: list[Notice] = [
    Notice("N-2026-001", "suspension", "12 路临时停运公告", "12 路",
           "2026-11-08T09:00:00+08:00", "2026-11-10T06:00:00+08:00",
           "2026-11-12T22:00:00+08:00", attachment="N-2026-001.csv"),
    Notice("N-2026-002", "suspension", "18 路临时停运公告", "18 路",
           "2026-11-03T14:00:00+08:00", "2026-11-05T06:00:00+08:00",
           "2026-11-08T20:00:00+08:00"),
    Notice("N-2026-003", "suspension", "25 路临时停运公告", "25 路",
           "2026-11-07T16:30:00+08:00", "2026-11-09T06:00:00+08:00",
           "2026-11-12T20:00:00+08:00", attachment="N-2026-003.csv"),
    Notice("N-2026-004", "suspension", "33 路临时停运公告", "33 路",
           "2026-11-08T11:00:00+08:00", "2026-11-10T05:30:00+08:00",
           "2026-11-20T22:00:00+08:00"),
    Notice("N-2026-005", "suspension", "12 路临时停运公告（施工绕行）", "12 路",
           "2026-11-09T17:20:00+08:00", "2026-11-11T05:00:00+08:00",
           "2026-11-13T21:00:00+08:00", attachment="N-2026-005.csv"),
    Notice("N-2026-006", "correction", "25 路停运时间更正公告", "25 路",
           "2026-11-09T15:00:00+08:00", "2026-11-10T00:00:00+08:00",
           "2026-11-15T22:00:00+08:00", references=["N-2026-003"],
           attachment="N-2026-006.csv"),
    Notice("N-2026-007", "cancellation", "33 路停运撤销公告", "33 路",
           "2026-11-09T10:00:00+08:00", references=["N-2026-004"]),
    Notice("N-2026-008", "suspension", "5 路临时停运公告", "5 路",
           "2026-11-09T08:00:00+08:00", "2026-11-12T06:00:00+08:00",
           "2026-11-14T20:00:00+08:00"),
    Notice("N-2026-009", "suspension", "40 路临时停运公告", "40 路",
           "2026-11-10T09:30:00+08:00", "2026-11-11T06:00:00+08:00",
           "2026-11-13T20:00:00+08:00", attachment="N-2026-009.csv"),
    Notice("N-2026-010", "correction", "25 路停运时间第二次更正公告", "25 路",
           "2026-11-10T08:00:00+08:00", "2026-11-10T00:00:00+08:00",
           "2026-11-20T22:00:00+08:00", references=["N-2026-006"],
           attachment="N-2026-010.csv"),
    Notice("N-2026-011", "correction", "40 路停运更正公告（含一处错误引用）", "40 路",
           "2026-11-10T18:00:00+08:00", "2026-11-11T06:00:00+08:00",
           "2026-11-16T20:00:00+08:00",
           references=["N-2026-009", "N-2026-999"]),
    Notice("N-2026-012", "correction", "12 路施工绕行安排更正（次日发布）", "12 路",
           "2026-11-12T09:00:00+08:00", "2026-11-11T05:00:00+08:00",
           "2026-11-14T21:00:00+08:00", references=["N-2026-005"]),
    Notice("N-2026-013", "suspension", "50 路临时停运公告（提前发布）", "50 路",
           "2026-11-14T09:00:00+08:00", "2026-11-15T06:00:00+08:00",
           "2026-11-18T20:00:00+08:00"),
    # ---- 站点明细附件场景 ----
    Notice("N-2026-014", "suspension", "60 路临时停运公告", "60 路",
           "2026-11-09T09:00:00+08:00", "2026-11-11T06:00:00+08:00",
           "2026-11-13T20:00:00+08:00", attachment="N-2026-014.csv"),
    Notice("N-2026-015", "suspension", "61 路临时停运公告", "61 路",
           "2026-11-08T09:00:00+08:00", "2026-11-10T06:00:00+08:00",
           "2026-11-16T20:00:00+08:00", attachment="N-2026-015.csv"),
    Notice("N-2026-016", "correction", "61 路停运更正公告（替换站点表）", "61 路",
           "2026-11-10T10:00:00+08:00", "2026-11-10T06:00:00+08:00",
           "2026-11-18T20:00:00+08:00", references=["N-2026-015"],
           attachment="N-2026-016.csv"),
    Notice("N-2026-017", "suspension", "62 路临时停运公告", "62 路",
           "2026-11-08T09:00:00+08:00", "2026-11-11T06:00:00+08:00",
           "2026-11-15T20:00:00+08:00", attachment="N-2026-017.csv"),
    Notice("N-2026-018", "correction", "62 路停运更正公告（无附件，线路不变）", "62 路",
           "2026-11-10T10:00:00+08:00", "2026-11-11T06:00:00+08:00",
           "2026-11-17T20:00:00+08:00", references=["N-2026-017"]),
    Notice("N-2026-019", "suspension", "63 路临时停运公告（无附件）", "63 路",
           "2026-11-09T09:00:00+08:00", "2026-11-11T06:00:00+08:00",
           "2026-11-14T20:00:00+08:00"),
    Notice("N-2026-020", "suspension", "64 路临时停运公告（附件 404）", "64 路",
           "2026-11-09T09:00:00+08:00", "2026-11-11T06:00:00+08:00",
           "2026-11-14T20:00:00+08:00", attachment="N-2026-020.csv"),
    Notice("N-2026-021", "suspension", "65 路临时停运公告（附件线路不符）", "65 路",
           "2026-11-09T09:00:00+08:00", "2026-11-11T06:00:00+08:00",
           "2026-11-14T20:00:00+08:00", attachment="N-2026-021.csv"),
    Notice("N-2026-022", "suspension", "66 路临时停运公告（附件格式错误）", "66 路",
           "2026-11-09T09:00:00+08:00", "2026-11-11T06:00:00+08:00",
           "2026-11-14T20:00:00+08:00", attachment="N-2026-022.csv"),
    Notice("N-2026-023", "suspension", "67 路临时停运公告", "67 路",
           "2026-11-08T09:00:00+08:00", "2026-11-11T06:00:00+08:00",
           "2026-11-15T20:00:00+08:00", attachment="N-2026-023.csv"),
    Notice("N-2026-024", "correction", "68 路停运更正公告（无附件且线路变更）", "68 路",
           "2026-11-10T10:00:00+08:00", "2026-11-11T06:00:00+08:00",
           "2026-11-15T20:00:00+08:00", references=["N-2026-023"]),
    Notice("N-EXT-001", "suspension", "外站转载公告（跨站链接示例）", "99 路",
           "2026-11-08T09:00:00+08:00", "2026-11-11T06:00:00+08:00",
           offsite=True),
]


def _csv(route: str, rows: list[tuple[str, str]]) -> str:
    """生成约定格式 CSV：线路,站点编号,站点名称。"""
    lines = ["线路,站点编号,站点名称"]
    lines += [f"{route},{no},{name}" for no, name in rows]
    return "\n".join(lines) + "\n"


# 附件文件名 -> CSV 内容。N-2026-020.csv 故意不生成（404 场景）。
ATTACHMENTS: dict[str, str] = {
    "N-2026-001.csv": _csv("12 路", [
        ("12-01", "火车站"), ("12-02", "文化宫"), ("12-03", "南门")]),
    "N-2026-005.csv": _csv("12 路", [
        ("12-11", "工业园"), ("12-12", "新区管委会")]),
    "N-2026-003.csv": _csv("25 路", [
        ("25-01", "东站"), ("25-02", "水厂")]),
    "N-2026-006.csv": _csv("25 路", [
        ("25-01", "东站"), ("25-02", "水厂"), ("25-03", "体育馆")]),
    "N-2026-010.csv": _csv("25 路", [
        ("25-01", "东站"), ("25-03", "体育馆"), ("25-04", "高铁北站")]),
    "N-2026-009.csv": _csv("40 路", [
        ("40-01", "西山"), ("40-02", "老城口")]),
    "N-2026-014.csv": _csv("60 路", [
        ("60-01", "河口"), ("60-02", "盐场")]),
    "N-2026-015.csv": _csv("61 路", [
        ("61-01", "北环"), ("61-02", "旧街")]),
    "N-2026-016.csv": _csv("61 路", [
        ("61-01", "北环"), ("61-03", "会展中心")]),
    "N-2026-017.csv": _csv("62 路", [
        ("62-01", "南湖"), ("62-02", "科技园")]),
    # 表内线路与公告线路（65 路）不符
    "N-2026-021.csv": _csv("66 路", [
        ("66-01", "西街口"), ("66-02", "滨河路")]),
    # 缺少必需列“站点编号”
    "N-2026-022.csv": "线路,编号,站点名称\n66 路,66-09,西关\n",
    "N-2026-023.csv": _csv("67 路", [
        ("67-01", "北岭"), ("67-02", "松林")]),
}

KIND_CN = {"suspension": "停运", "correction": "更正", "cancellation": "撤销"}


def _esc(s: str) -> str:
    return html.escape(s, quote=True)


def detail_path(n: Notice) -> str:
    return f"notices/{n.notice_id}.html"


def render_detail(n: Notice) -> str:
    rows: list[str] = []

    def row(field_name: str, label: str, value: str | None, is_time: bool = False) -> None:
        if value is None:
            return
        if is_time:
            cell = f'<time datetime="{_esc(value)}">{_esc(value.replace("T", " "))}</time>'
        else:
            cell = _esc(value)
        rows.append(
            f'      <tr data-field="{field_name}"><th>{label}</th><td>{cell}</td></tr>'
        )

    row("notice_id", "公告编号", n.notice_id)
    row("kind", "类型", KIND_CN[n.kind])
    row("route", "线路", n.route)
    row("suspend_start", "停运开始时间", n.suspend_start, is_time=True)
    row("suspend_end", "停运结束时间", n.suspend_end, is_time=True)
    row("published_at", "发布时间", n.published_at, is_time=True)

    refs_html = ""
    if n.references:
        links = "、".join(
            f'<a class="reference" data-ref-id="{_esc(r)}" href="/notices/{_esc(r)}.html">'
            f"{_esc(r)}</a>"
            for r in n.references
        )
        refs_html = f"\n    <section class=refs><h2>引用公告</h2><p>{links}</p></section>"

    attachment_html = ""
    if n.attachment:
        attachment_html = (
            '\n    <section class="attachments"><h2>站点清单附件</h2><p>'
            f'<a class="attachment" href="../{ATTACH_DIR}/{_esc(n.attachment)}">'
            f"{_esc(n.attachment)}</a></p></section>"
        )

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="utf-8"><title>{_esc(n.title)}</title></head>
<body>
  <article>
    <h1 class="notice-title">{_esc(n.title)}</h1>
    <table class="notice-fields">
{chr(10).join(rows)}
    </table>{refs_html}{attachment_html}
    <p><a href="../index.html">返回公告列表</a></p>
  </article>
</body>
</html>
"""


def render_list(page_notices: list[Notice], page_idx: int, total_pages: int) -> str:
    items: list[str] = []
    for n in page_notices:
        if n.offsite:
            href = "http://external.example.org/notice/N-EXT-001.html"
        else:
            href = detail_path(n)
        items.append(
            f'    <article class="notice" data-id="{_esc(n.notice_id)}" '
            f'data-kind="{n.kind}">\n'
            f'      <a class="detail-link" href="{_esc(href)}">{_esc(n.title)}</a>\n'
            f'      <span class="published">{_esc(n.published_at)}</span>\n'
            f"    </article>"
        )

    pager_parts: list[str] = []
    if page_idx > 0:
        prev_file = "index.html" if page_idx == 1 else f"page{page_idx}.html"
        pager_parts.append(f'<a rel="prev" href="{prev_file}">上一页</a>')
    if page_idx < total_pages - 1:
        next_file = "page2.html" if page_idx == 0 else f"page{page_idx + 2}.html"
        pager_parts.append(f'<a rel="next" href="{next_file}">下一页</a>')
    pager = f'  <nav class="pager">{" ".join(pager_parts)}</nav>' if pager_parts else ""

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="utf-8"><title>线路公告列表（第 {page_idx + 1} 页）</title></head>
<body>
  <h1>线路公告列表</h1>
{chr(10).join(items)}
{pager}
</body>
</html>
"""


def build() -> None:
    if OUT_DIR.exists():
        shutil.rmtree(OUT_DIR)
    (OUT_DIR / "notices").mkdir(parents=True)
    (OUT_DIR / ATTACH_DIR).mkdir(parents=True)

    real_notices = [n for n in NOTICES if not n.offsite]
    for n in real_notices:
        (OUT_DIR / detail_path(n)).write_text(render_detail(n), encoding="utf-8")

    for filename, content in ATTACHMENTS.items():
        (OUT_DIR / ATTACH_DIR / filename).write_text(content, encoding="utf-8")

    pages: list[list[Notice]] = [
        NOTICES[i : i + PAGE_SIZE] for i in range(0, len(NOTICES), PAGE_SIZE)
    ]
    total = len(pages)
    for idx, page_notices in enumerate(pages):
        name = "index.html" if idx == 0 else f"page{idx + 1}.html"
        (OUT_DIR / name).write_text(
            render_list(page_notices, idx, total), encoding="utf-8"
        )

    print(
        f"示例站点已生成: {OUT_DIR}（{len(real_notices)} 条公告，{total} 个列表页，"
        f"{len(ATTACHMENTS)} 个 CSV 附件；N-2026-020.csv 故意缺省）"
    )


if __name__ == "__main__":
    build()
