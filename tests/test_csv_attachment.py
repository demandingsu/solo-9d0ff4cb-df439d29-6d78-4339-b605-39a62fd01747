"""CSV 站点清单附件解析。"""
from __future__ import annotations

import pytest

from app.csv_attachment import AttachmentError, parse_station_csv


def test_parse_basic_csv_with_optional_name():
    content = "线路,站点编号,站点名称\n12 路,12-01,火车站\n12 路,12-02,文化宫\n".encode()
    rows = parse_station_csv(content)
    assert [(r.route, r.station_no, r.station_name) for r in rows] == [
        ("12 路", "12-01", "火车站"),
        ("12 路", "12-02", "文化宫"),
    ]


def test_parse_csv_without_name_column():
    content = "线路,站点编号\n60 路,60-01\n60 路,60-02\n".encode()
    rows = parse_station_csv(content)
    assert len(rows) == 2
    assert rows[0].station_name is None
    assert rows[1].station_no == "60-02"


def test_extra_columns_ignored_and_whitespace_trimmed():
    content = "线路,站点编号,站点名称,备注\n 12 路 , 12-09 , 南关 ,临时\n".encode()
    rows = parse_station_csv(content)
    assert rows[0].route == "12 路"
    assert rows[0].station_no == "12-09"
    assert rows[0].station_name == "南关"


def test_utf8_bom_accepted():
    content = "﻿线路,站点编号\n12 路,12-01\n".encode("utf-8")
    rows = parse_station_csv(content)
    assert rows[0].station_no == "12-01"


def test_missing_required_column_raises():
    content = "线路,编号\n12 路,12-01\n".encode()
    with pytest.raises(AttachmentError, match="站点编号"):
        parse_station_csv(content)


def test_empty_table_raises():
    content = "线路,站点编号,站点名称\n".encode()
    with pytest.raises(AttachmentError, match="没有任何有效站点行"):
        parse_station_csv(content)


def test_blank_lines_skipped_but_missing_no_raises():
    content = "线路,站点编号,站点名称\n12 路,12-01,南关\n,\n12 路,,编号缺失\n".encode()
    with pytest.raises(AttachmentError, match="第 4 行缺少站点编号"):
        parse_station_csv(content)


def test_invalid_encoding_raises():
    with pytest.raises(AttachmentError):
        parse_station_csv(b"\xff\xfe not utf8")


def test_sample_site_csv_files_parse():
    from pathlib import Path

    attach_dir = Path(__file__).parent.parent / "site" / "html" / "attachments"
    ok = [
        "N-2026-001.csv",
        "N-2026-003.csv",
        "N-2026-006.csv",
        "N-2026-010.csv",
        "N-2026-009.csv",
        "N-2026-014.csv",
        "N-2026-015.csv",
        "N-2026-016.csv",
        "N-2026-017.csv",
        "N-2026-021.csv",
        "N-2026-023.csv",
    ]
    for name in ok:
        rows = parse_station_csv((attach_dir / name).read_bytes())
        assert rows, name

    # 格式错误附件（缺“站点编号”列）
    with pytest.raises(AttachmentError):
        parse_station_csv((attach_dir / "N-2026-022.csv").read_bytes())
