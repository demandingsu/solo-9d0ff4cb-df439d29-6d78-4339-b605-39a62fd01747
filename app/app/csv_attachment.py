"""CSV 站点清单附件解析。

附件约定（随附示例站点遵循，接入真实站点时只需调整列名）：

* UTF-8 编码（允许 BOM），逗号分隔，首行为表头；
* 必含列：``线路``、``站点编号``；可选列：``站点名称``，其余列忽略；
* 每行表示一个受影响站点；站点编号为空的行跳过；
* 调用方负责用公告链当前线路逐行核对，线路不符由调用方标记待核实。

解析失败一律抛 :class:`AttachmentError`，由上层转为“待核实”，
绝不回退使用旧表。
"""
from __future__ import annotations

import csv
import io

from .models import StationRow

ROUTE_HEADER = "线路"
STATION_NO_HEADER = "站点编号"
STATION_NAME_HEADER = "站点名称"


class AttachmentError(ValueError):
    """CSV 附件无法解析或内容不满足约定。"""


def parse_station_csv(content: bytes) -> list[StationRow]:
    """解析 CSV 字节流为站点行列表。

    :raises AttachmentError: 解码失败、表头缺列或没有任何有效站点行。
    """
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise AttachmentError(f"CSV 不是有效的 UTF-8 文本: {exc}") from exc

    reader = csv.DictReader(io.StringIO(text))
    headers = reader.fieldnames
    if not headers:
        raise AttachmentError("CSV 缺少表头行")
    headers = [h.strip() if h else h for h in headers]
    reader.fieldnames = headers

    for required in (ROUTE_HEADER, STATION_NO_HEADER):
        if required not in headers:
            raise AttachmentError(f"CSV 缺少必需列: {required}（实际表头: {headers}）")

    rows: list[StationRow] = []
    for line_no, raw in enumerate(reader, start=2):
        if raw is None:
            continue
        route = (raw.get(ROUTE_HEADER) or "").strip()
        station_no = (raw.get(STATION_NO_HEADER) or "").strip()
        if not route and not station_no:
            continue  # 空行
        if not station_no:
            raise AttachmentError(f"CSV 第 {line_no} 行缺少站点编号")
        if not route:
            raise AttachmentError(f"CSV 第 {line_no} 行缺少线路")
        name_raw = raw.get(STATION_NAME_HEADER)
        station_name = name_raw.strip() if name_raw and name_raw.strip() else None
        rows.append(
            StationRow(route=route, station_no=station_no, station_name=station_name)
        )

    if not rows:
        raise AttachmentError("CSV 没有任何有效站点行")
    return rows
