# -*- coding: utf-8 -*-
"""
DR × 脂质代谢周报的邮件版式（新版）。

设计原则（和 AI 科研周报保持一致）：
1. 只用 <table> 排版，关键样式全部内联 —— QQ 邮箱 / Outlook 才不散架
2. 宽度锁 640px，手机上靠 media query 微调
3. 每个板块一条彩色标题带，一眼可分

这个模块是自包含的：不依赖 weekly_brief_v4 里的私有函数，
只有在能导入时才复用它的亮点生成逻辑（失败也不影响渲染）。
"""

from __future__ import annotations

import html
import re
from datetime import date, timedelta

FONT = "-apple-system,'PingFang SC','Microsoft YaHei','Helvetica Neue',Arial,sans-serif"
BREAK = "word-break:break-word;overflow-wrap:anywhere;"

BLUE = "#1d4ed8"
VIOLET = "#7c3aed"
EMERALD = "#047857"
AMBER = "#b45309"


def _e(x) -> str:
    return html.escape(str(x or ""), quote=True)


def _clean(x) -> str:
    return re.sub(r"\s+", " ", str(x or "")).strip()


def _clip(text, limit: int) -> str:
    text = _clean(text)
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


def _chip(text, color="#475569", bg="#f1f5f9") -> str:
    if not _clean(text):
        return ""
    return (
        f'<span style="display:inline-block;background-color:{bg};color:{color};'
        f'font-size:11px;line-height:1.6;padding:2px 8px;border-radius:999px;margin:0 6px 6px 0;'
        f'font-family:{FONT};white-space:nowrap;">{_e(text)}</span>'
    )


def _metric_text(record) -> str:
    m = record.get("journal_metrics") or {}
    jif, q = m.get("jif"), m.get("jcr_quartile")
    source = m.get("metric_source") or "JCR-based 公开目录"
    if jif is not None and q and q != "未检索到":
        return f"JIF {jif:.3g}；{q}；类别排名 {m.get('jcr_category_rank') or '未提供'}；来源：{source}"
    if jif is not None:
        return f"JIF {jif:.3g}；⚠️ 未可靠匹配 JCR Q；来源：{source}"
    return "⚠️ 未可靠匹配到 JIF + JCR Q（不填猜测值）"


def _aff(values) -> str:
    vals = values if isinstance(values, list) else [values]
    return "；".join(_clean(x) for x in vals if _clean(x)) or "公开结构化元数据未确认"


def _direction_summary(records):
    try:
        import weekly_brief_v2 as v2
        return v2.direction_summary(records)
    except Exception:  # noqa: BLE001
        from collections import Counter
        counter = Counter()
        for r in records:
            for tag in r.get("direction_tags") or ["未分类"]:
                counter[str(tag)] += 1
        total = sum(counter.values()) or 1
        return [
            {"direction": k, "papers": v, "share": round(v * 100 / total, 1)}
            for k, v in counter.most_common()
        ]


def _highlights(records):
    try:
        from weekly_brief_v4 import _specific_highlights
        return _specific_highlights(records)
    except Exception:  # noqa: BLE001
        if not records:
            return ["本周没有检索到此前未出现、且达到相关性阈值的文献；没有用旧文献补数。"]
        return [
            f"本周共纳入 {len(records)} 篇：{_clip(records[0].get('title', ''), 60)} 等。",
            "建议优先按证据层级阅读：机制研究 → 临床验证 → 技术方法。",
        ]


def _field(label, value, color="#334155"):
    if not _clean(value):
        return ""
    return (
        "<tr>"
        f'<td width="64" valign="top" style="font-family:{FONT};font-size:11px;font-weight:700;'
        f'color:{color};line-height:1.75;padding:3px 0;white-space:nowrap;">{_e(label)}</td>'
        f'<td style="font-family:{FONT};font-size:13px;color:#3f4a5a;line-height:1.75;'
        f'padding:3px 0;{BREAK}">{_e(value)}</td>'
        "</tr>"
    )


def _bullets(items, color="#334155") -> str:
    rows = "".join(
        f'<tr><td valign="top" style="font-family:{FONT};font-size:13px;color:{color};'
        f'line-height:1.75;padding:3px 8px 3px 0;white-space:nowrap;">·</td>'
        f'<td style="font-family:{FONT};font-size:13px;color:#3f4a5a;line-height:1.75;'
        f'padding:3px 0;{BREAK}">{_e(x)}</td></tr>'
        for x in (items or [])
    )
    return f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">{rows}</table>' if rows else ""


def _section_band(icon, name, theme, count_text="") -> str:
    return (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
        f'style="background-color:{theme};border-radius:10px;">'
        "<tr>"
        f'<td style="padding:11px 14px;font-family:{FONT};font-size:15px;font-weight:800;color:#ffffff;">'
        f"{icon} {_e(name)}</td>"
        f'<td align="right" style="padding:11px 14px;font-family:{FONT};font-size:11px;color:#ffffff;">'
        f"{_e(count_text)}</td>"
        "</tr></table>"
    )


def _paper_card(rank, record, theme=EMERALD) -> str:
    a = record.get("annotation") or {}
    url = _clean(record.get("url"))
    metrics = record.get("journal_metrics") or {}
    chips = "".join([
        _chip(record.get("journal"), "#1d4ed8", "#e8efff"),
        _chip(metrics.get("jcr_quartile"), "#047857", "#e7f7ef"),
        _chip(f"评分 {record.get('relevance_score', 0)}", "#7c3aed", "#f2ecff"),
        _chip(a.get("article_type"), "#5b6472", "#f1f5f9"),
        _chip(a.get("evidence_level"), "#b45309", "#fff7ed"),
        _chip(record.get("publication_date"), "#9aa3b2", "#f8fafc"),
    ])
    meta = "".join([
        _field("第一作者", record.get("first_author") or "N/A"),
        _field("单位", _aff(record.get("first_author_affiliations"))),
        _field("通讯作者", record.get("corresponding_author") or "公开元数据未确认"),
        _field("JCR", _metric_text(record)),
    ])
    innovation = _bullets(a.get("innovation_points"))
    focus = _bullets(a.get("research_focus"), color=theme)
    caution = _bullets(a.get("limitations_or_cautions"), color="#b45309")
    abstract_zh = _clean(a.get("abstract_zh"))
    abstract_en = _clean(record.get("abstract"))
    keywords = "、".join(x for x in (a.get("keywords_zh") or a.get("keywords_en") or []) if x)

    parts = [
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
        'style="margin:0 0 14px 0;">'
        '<tr><td style="background-color:#ffffff;border:1px solid #e8edf5;'
        f"border-left:4px solid {theme};border-radius:12px;padding:15px 16px 13px 16px;\">",
        f'<div style="font-family:{FONT};font-size:12px;color:#94a3b8;font-weight:700;letter-spacing:1px;">'
        f"PAPER {rank:02d}</div>",
        f'<a class="item-title" href="{_e(url)}" style="font-family:{FONT};font-size:15px;font-weight:700;color:#131a26;'
        f'line-height:1.6;text-decoration:none;display:block;margin-top:4px;{BREAK}">'
        f"{_e(_clip(record.get('title'), 220))}</a>",
    ]
    if _clean(a.get("title_zh")):
        parts.append(
            f'<div style="font-family:{FONT};font-size:13px;color:#475569;line-height:1.7;'
            f'margin-top:5px;{BREAK}">{_e(_clip(a.get("title_zh"), 160))}</div>'
        )
    parts.append(f'<div style="margin-top:9px;">{chips}</div>')
    parts.append(
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
        f'style="margin-top:2px;">{meta}</table>'
    )
    for label, block, color in (
        ("创新点", innovation, "#0f172a"),
        ("对你的研究的启发", focus, theme),
        ("注意事项", caution, "#b45309"),
    ):
        if block:
            parts.append(
                f'<div style="font-family:{FONT};font-size:12px;font-weight:800;color:{color};'
                f'margin:10px 0 2px 0;">{_e(label)}</div>{block}'
            )
    if abstract_zh:
        parts.append(
            f'<div style="font-family:{FONT};font-size:12px;font-weight:800;color:#0f172a;margin:10px 0 3px 0;">'
            "中文摘要</div>"
            f'<div style="font-family:{FONT};font-size:13px;color:#3f4a5a;line-height:1.8;'
            f'background-color:#f8fafc;border-radius:8px;padding:10px 12px;{BREAK}">{_e(_clip(abstract_zh, 700))}</div>'
        )
    if abstract_en:
        parts.append(
            f'<div style="font-family:{FONT};font-size:11px;color:#94a3b8;line-height:1.7;'
            f'margin-top:8px;{BREAK}">Abstract: {_e(_clip(abstract_en, 320))}</div>'
        )
    if keywords:
        parts.append(
            f'<div style="font-family:{FONT};font-size:11px;color:#64748b;margin-top:8px;{BREAK}">'
            f"关键词：{_e(_clip(keywords, 120))}</div>"
        )
    parts.append(
        f'<div style="font-family:{FONT};font-size:11px;color:#94a3b8;margin-top:8px;{BREAK}">'
        f"PMID {_e(record.get('pmid') or '—')} · DOI {_e(record.get('doi') or '—')}</div>"
    )
    parts.append(
        f'<a href="{_e(url)}" style="font-family:{FONT};font-size:12px;color:{theme};'
        f'text-decoration:none;display:inline-block;margin-top:6px;">打开原文 →</a>'
    )
    if url:
        parts.append(
            f'<div style="font-family:{FONT};font-size:10.5px;color:#94a3b8;line-height:1.6;'
            f'margin-top:2px;{BREAK}">{_e(_clip(url, 110))}</div>'
        )
    parts.append("</td></tr></table>")
    return "".join(parts)


def render_html(records, candidates, raw_candidates, days, excluded_count, history_size) -> str:
    today = date.today().isoformat()
    start = (date.today() - timedelta(days=max(days - 1, 0))).isoformat()
    highlights = _highlights(records)
    summary = _direction_summary(records)

    out = [
        "<!doctype html><html lang='zh-CN'><head>",
        '<meta charset="utf-8">',
        "<meta http-equiv='Content-Type' content='text/html; charset=UTF-8'>",
        "<meta name='viewport' content='width=device-width,initial-scale=1'>",
        "<title>DR × 脂质代谢周报</title>",
        "<style>@media (max-width:640px){.wrap{padding:12px 8px !important}"
        ".pad{padding-left:18px !important;padding-right:18px !important}"
        ".shell{width:100% !important;border-radius:12px !important}}</style>",
        "</head>",
        "<body style='margin:0;padding:0;background-color:#eef1f6;'>",
        "<table role='presentation' width='100%' cellpadding='0' cellspacing='0' border='0' "
        "style='background-color:#eef1f6;'><tr><td align='center' class='wrap' style='padding:26px 12px;'>",
        '<table role="presentation" class="shell" width="640" cellpadding="0" cellspacing="0" border="0" '
        "style='width:640px;max-width:640px;background-color:#ffffff;border-radius:16px;border:1px solid #e3e8f0;'>",
        # header
        "<tr><td style='background-color:#0f172a;background-image:linear-gradient(135deg,#0f172a 0%,#1e293b 55%,#312e81 100%);"
        "padding:28px 28px 24px 28px;'>",
        f"<div style='font-family:{FONT};font-size:11px;letter-spacing:2px;color:#93c5fd;font-weight:700;'>DR × LIPID METABOLISM</div>",
        f"<div style='font-family:{FONT};font-size:25px;line-height:1.35;color:#ffffff;font-weight:800;margin-top:8px;'>"
        "糖尿病视网膜病变 × 脂质代谢周报</div>",
        f"<div style='font-family:{FONT};font-size:12px;color:#cbd5e1;margin-top:10px;'>"
        f"{_e(start)} ~ {_e(today)}　·　覆盖 {days} 天</div>",
        "</td></tr>",
        # stats
        "<tr><td style='background-color:#f8fafc;border-bottom:1px solid #e8edf5;padding:11px 28px;'>",
        f"<div style='font-family:{FONT};font-size:12px;color:#475569;'>"
        f"原始抓取 <b>{raw_candidates}</b> 条　→　严格去重 <b>{candidates}</b> 条　→　"
        f"历史剔除 <b>{excluded_count}</b> 条　→　<b style='color:#047857;'>本期纳入 {len(records)} 篇</b>"
        f"　（历史库 {history_size} 篇）</div>",
        "</td></tr>",
        # 0 highlights
        "<tr><td class='pad' style='padding:24px 28px 0 28px;'>",
        _section_band("📌", "本周亮点", BLUE, f"{len(highlights)} 条"),
        "</td></tr>",
        "<tr><td class='pad' style='padding:12px 28px 0 28px;'>",
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
        "style='background-color:#eff6ff;border:1px solid #bfdbfe;border-radius:12px;'>",
        f"<tr><td style='padding:14px 16px;'>{_bullets(highlights, color=BLUE)}</td></tr></table>",
        "</td></tr>",
    ]

    # 1 direction distribution
    if summary:
        out += [
            "<tr><td class='pad' style='padding:24px 28px 0 28px;'>",
            _section_band("📊", "研究方向分布", VIOLET, f"{len(summary)} 个方向"),
            "</td></tr>",
            "<tr><td class='pad' style='padding:12px 28px 0 28px;'>",
            '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
            "style='background-color:#faf5ff;border:1px solid #e9d5ff;border-radius:12px;'>",
            "<tr><td style='padding:14px 16px;'>",
        ]
        for item in summary:
            share = float(item.get("share") or 0)
            out.append(
                f"<div style='font-family:{FONT};font-size:12px;color:#4c1d95;margin:0 0 4px 0;'>"
                f"{_e(item.get('direction'))}　<b>{item.get('papers')}</b> 篇　{share:g}%</div>"
                f"<div style='height:6px;background-color:#ede9fe;border-radius:4px;margin-bottom:10px;'>"
                f"<div style='height:6px;width:{min(share, 100):g}%;background-color:{VIOLET};border-radius:4px;'></div></div>"
            )
        out += ["</td></tr></table>", "</td></tr>"]

    # 2 papers
    out += [
        "<tr><td class='pad' style='padding:24px 28px 0 28px;'>",
        _section_band("📄", "本周重点文献", EMERALD, f"{len(records)} 篇"),
        "</td></tr>",
    ]
    if records:
        out += [
            "<tr><td class='pad' style='padding:12px 28px 0 28px;'>",
            "".join(_paper_card(i, r, EMERALD) for i, r in enumerate(records, 1)),
            "</td></tr>",
        ]
    else:
        out += [
            "<tr><td class='pad' style='padding:12px 28px 0 28px;'>",
            f"<div style='font-family:{FONT};font-size:13px;color:#64748b;line-height:1.8;"
            "background-color:#f8fafc;border:1px solid #e8edf5;border-radius:12px;padding:16px;'>"
            "本周没有达到阈值的未读文献。系统不会用旧文献补数，宁缺毋滥。</div>",
            "</td></tr>",
        ]

    # footer
    out += [
        "<tr><td class='pad' style='padding:22px 28px 24px 28px;'>",
        f"<div style='font-family:{FONT};font-size:11px;color:#94a3b8;line-height:1.8;padding-top:14px;"
        "border-top:1px solid #e8edf5;'>"
        "JCR 说明：优先匹配 2026 JCR release 对应的 2025 指标年；无法可靠匹配时留空，不填写猜测值。"
        "中文摘要为自动翻译，请以英文原文为准。</div>",
        "</td></tr>",
        "</table></td></tr></table></body></html>",
    ]
    return "".join(out)
