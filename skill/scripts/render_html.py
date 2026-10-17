# -*- coding: utf-8 -*-
"""
render_html —— 把分析结果渲染成**单文件、离线、可双击打开**的可视化报告。

设计约束：
  - 不引用任何外部资源（无 CDN、无字体、无 JS 库），双击即开、可发给编辑或申诉用
  - 所有图形用内联 SVG 生成，打印友好
  - 正文标注视图用字符偏移切片，保证高亮位置与原文严格一致
"""
from __future__ import annotations

import html
import json
import math
from typing import Any, Dict, List, Tuple

CATEGORY_ORDER = ["rhythm", "lexical", "rhetoric", "punct", "dialogue", "emotion", "structure", "logic"]

CATEGORY_DESC = {
    "rhythm": "句长与段长的起伏程度。真人忽长忽短，AI 被均值拉住。",
    "lexical": "套话、抽象情绪词、通用身体反应、书面连接词的密度。",
    "rhetoric": "「不是A而是B」「像……一样」这类句式模板的复用频率。",
    "punct": "标点种类分布与规范性。半角标点是强人味证据。",
    "dialogue": "对白占比、说话人标注多样性、对白是否像真人说话。",
    "emotion": "抽象命名情绪 vs 具体生理反应；五感是否只剩视觉。",
    "structure": "段落闭合习惯：是否段段收尾、句句升华。",
    "logic": "**由模型判断的逻辑漏洞**（自相矛盾/答非所问/语义跳跃）。"
             "这是唯一不靠统计的分类——逻辑崩了，文风再像人也没用。权重高于所有文体类规则。",
    "openness": "反向项（人味证据）：口语、脏话、方言、语气词、不规范写法。",
}

SEVERITY_CN = {1: "提示", 2: "轻微", 3: "中等", 4: "显著", 5: "强烈"}

CONF_CN = {"high": "高", "medium": "中", "low": "低"}

BAND_CLASS = {
    "极低": "verylow", "偏低": "low", "中等": "mid",
    "偏高": "high", "极高": "veryhigh", "无法判定": "unknown",
}


def esc(s: Any) -> str:
    return html.escape(str(s if s is not None else ""), quote=True)


# --------------------------------------------------------------------------
# SVG 部件
# --------------------------------------------------------------------------

def svg_gauge(risk: float, lo: float, hi: float, color: str, band: str) -> str:
    """风险仪表盘：半圆 + 区间带 + 指针。"""
    cx, cy, r = 180.0, 168.0, 128.0
    w, h = 360, 210

    def pol(frac: float, rr: float = r) -> Tuple[float, float]:
        ang = math.pi * (1.0 - frac)
        return cx + rr * math.cos(ang), cy - rr * math.sin(ang)

    def arc(f0: float, f1: float, rr: float = r) -> str:
        # 区间完全越界/空区间时退化处理，避免生成无效路径
        f0, f1 = max(0.0, min(1.0, f0)), max(0.0, min(1.0, f1))
        if f1 - f0 < 1e-6:
            f1 = min(1.0, f0 + 1e-4)
        x0, y0 = pol(f0, rr)
        x1, y1 = pol(f1, rr)
        # large-arc-flag **恒为 0**。
        # 这是一个半圆仪表盘：f 是半圆的比例，弧的角度跨度 = (f1-f0)×180°，
        # 最大就是 180°，永远达不到"大弧"（>180°）的条件。
        # 历史上这里写成了 `1 if (f1-f0) > 0.5`（误把 90° 当成大弧分界），
        # 导致分数 >50 时弧线被画成绕下半圈的大弯，仪表盘视觉崩坏——
        # 而 <50 分时碰巧正确，所以这个 bug 只在"橙色及以上"才显形。
        large = 0
        return f"M {x0:.2f} {y0:.2f} A {rr} {rr} 0 {large} 1 {x1:.2f} {y1:.2f}"

    parts = [f'<svg viewBox="0 0 {w} {h}" class="gauge" role="img" aria-label="AI 痕迹风险 {risk:.0f} 分">']
    # 底层轨道
    parts.append(f'<path d="{arc(0.0, 1.0)}" stroke="#eceef4" stroke-width="20" fill="none" stroke-linecap="round"/>')
    # 区间带画在轨道外侧，避免与实测值弧混淆
    if hi > lo:
        parts.append(f'<path d="{arc(lo / 100.0, hi / 100.0, r + 15)}" stroke="#9aa1b1" '
                     f'stroke-width="7" fill="none" stroke-linecap="butt" opacity="0.55"/>')
    # 实测值弧（最上层、最醒目）
    if risk > 0.5:
        parts.append(f'<path d="{arc(0.0, risk / 100.0)}" stroke="{color}" stroke-width="20" '
                     f'fill="none" stroke-linecap="round"/>')
    # 刻度
    for t in (0, 25, 50, 75, 100):
        x0, y0 = pol(t / 100.0, r - 12)
        x1, y1 = pol(t / 100.0, r + 1)
        parts.append(f'<line x1="{x0:.1f}" y1="{y0:.1f}" x2="{x1:.1f}" y2="{y1:.1f}" '
                     f'stroke="#9aa1b1" stroke-width="1.5"/>')
        lx, ly = pol(t / 100.0, r + 18)
        parts.append(f'<text x="{lx:.1f}" y="{ly:.1f}" class="tick" text-anchor="middle">{t}</text>')
    # 区间端点标记
    if hi > lo:
        for v in (lo, hi):
            mx, my = pol(v / 100.0, r + 15)
            parts.append(f'<circle cx="{mx:.1f}" cy="{my:.1f}" r="3.5" fill="#6b7280"/>')
    # 指针
    px, py = pol(min(risk, 100.0) / 100.0, r - 26)
    parts.append(f'<line x1="{cx}" y1="{cy}" x2="{px:.2f}" y2="{py:.2f}" '
                 f'stroke="#1f2430" stroke-width="3" stroke-linecap="round"/>')
    parts.append(f'<circle cx="{cx}" cy="{cy}" r="7" fill="#1f2430"/>')
    # 数值
    parts.append(f'<text x="{cx}" y="{cy - 42:.0f}" class="gauge-num" text-anchor="middle" '
                 f'fill="{color}">{risk:.0f}</text>')
    parts.append(f'<text x="{cx}" y="{cy - 18:.0f}" class="gauge-unit" text-anchor="middle">AI 痕迹风险分</text>')
    parts.append(f'<text x="{cx}" y="{cy + 34:.0f}" class="gauge-band" text-anchor="middle" '
                 f'fill="{color}">{esc(band)}</text>')
    parts.append('</svg>')
    return "".join(parts)


def svg_category_chart(res: Dict[str, Any]) -> str:
    """分类贡献图：**径向条形图**（Nightingale rose），不是雷达图。

    **为什么从雷达换成径向条形**
    --------------------------
    雷达图有一条硬性前提：多个维度都得有值。而这份工具的实际数据是
    「八九成的证据集中在 1–2 个维度，其余全是 0」。雷达会把零分顶点画在圆心，
    多边形退化成一条线——即使加了视觉下限，也只是个尖角，读不出面积。

    径向条形图保留了读者熟悉的那种环形外观，但**每根轴是一根独立的柱子**：
    零分就是一根短柱，不会把整个图形拽塌；有分的柱子长度直接等于贡献分。
    所以"哪个维度在驱动结论、占多少"，一眼就能读出来。

    **画什么数**：某维度贡献分 = 总风险 × (该维度原始得分 / 全部原始得分)。
    八根柱子相加恰好等于总分，与上方仪表盘同一把尺子。

    （注：早版本这里画的是 `category_scores[k]`——每个分类各自归一化到 0–100 的
    显示值。但它从未参与总分计算，导致"仪表盘 86、雷达只有一角"的错位。
    那个尺度现在只保留给「人味证据」使用，因为人味本来就是独立的一把尺。）
    """
    cat_contrib = res.get("category_contrib", {}) or {}
    S = float(res.get("S_total", 0.0) or 0.0)
    risk = float(res.get("risk", 0.0) or 0.0)
    axes = CATEGORY_ORDER
    n = len(axes)

    shares = {a: (cat_contrib.get(a, 0.0) / S if S > 0 else 0.0) for a in axes}
    pts_risk = {a: risk * shares[a] for a in axes}

    # 画布比图形本身宽，给左右两侧的三行标签留出空间
    cx, cy = 218.0, 208.0
    r0, rmax = 44.0, 124.0
    span = 2 * math.pi / n
    gap = math.radians(6)
    vmax = max(max(pts_risk.values()), 1.0)

    def wedge(a0: float, a1: float, r1: float) -> str:
        large = 1 if (a1 - a0) > math.pi else 0
        x0, y0 = cx + r1 * math.cos(a0), cy + r1 * math.sin(a0)
        x1, y1 = cx + r1 * math.cos(a1), cy + r1 * math.sin(a1)
        x2, y2 = cx + r0 * math.cos(a1), cy + r0 * math.sin(a1)
        x3, y3 = cx + r0 * math.cos(a0), cy + r0 * math.sin(a0)
        return (f"M {x0:.2f} {y0:.2f} A {r1:.2f} {r1:.2f} 0 {large} 1 {x1:.2f} {y1:.2f} "
                f"L {x2:.2f} {y2:.2f} A {r0:.2f} {r0:.2f} 0 {large} 0 {x3:.2f} {y3:.2f} Z")

    parts = [f'<svg viewBox="0 0 436 444" class="radar" role="img" '
             f'aria-label="各维度风险贡献的径向条形图（八根之和等于总风险分）">']
    parts.append(
        '<defs>'
        '<linearGradient id="barGrad" x1="0" y1="1" x2="0" y2="0">'
        '<stop offset="0%" stop-color="#f97316"/><stop offset="100%" stop-color="#dc2626"/>'
        '</linearGradient>'
        '<filter id="barGlow" x="-30%" y="-30%" width="160%" height="160%">'
        '<feDropShadow dx="0" dy="1" stdDeviation="2" flood-color="#ea580c" flood-opacity="0.22"/>'
        '</filter>'
        '</defs>')

    # 外圈刻度环（25/50/75/100 分），给柱子一个可读的标尺
    for ring in (0.25, 0.5, 0.75, 1.0):
        rr = r0 + (rmax - r0) * ring
        pts_ring = " ".join(
            f"{cx + rr*math.cos(-math.pi/2 + 2*math.pi*i/n):.1f},"
            f"{cy + rr*math.sin(-math.pi/2 + 2*math.pi*i/n):.1f}" for i in range(n))
        parts.append(f'<polygon points="{pts_ring}" fill="none" stroke="#eceef4" stroke-width="1"/>')

    for i, a in enumerate(axes):
        c = -math.pi / 2 + span * i
        a0, a1 = c - span / 2 + gap / 2, c + span / 2 - gap / 2
        value = pts_risk[a]
        # 轨道：让"这里有一个维度"始终可见
        parts.append(f'<path d="{wedge(a0, a1, rmax)}" fill="#f6f7fa"/>')
        # 柱子
        if value > 0.05:
            r1 = r0 + (rmax - r0) * min(value / 100.0, 1.0)
            # 相对量级 → 透明度，让主力维度更重
            opac = 0.55 + 0.45 * (value / vmax)
            parts.append(f'<path d="{wedge(a0, a1, r1)}" fill="url(#barGrad)" '
                         f'opacity="{opac:.2f}" filter="url(#barGlow)"/>')

        # 标签：分类名 / 占比 / 贡献，三行都带小标题，避免"8% 和 7.1 分是什么"看不懂。
        # 排版按扇区方位走：上半圈三行往上叠（否则压到图形上），下半圈往下叠，
        # 左右两侧以扇区中点垂直居中。
        lx, ly = cx + (rmax + 16) * math.cos(c), cy + (rmax + 16) * math.sin(c)
        anchor = "middle"
        if math.cos(c) > 0.25:
            anchor = "start"
        elif math.cos(c) < -0.25:
            anchor = "end"
        zero = value < 0.05
        cls = "radar-score radar-score-zero" if zero else "radar-score"
        y0 = ly - 30 if math.sin(c) < -0.4 else ly - 15

        parts.append(f'<text x="{lx:.1f}" y="{y0:.1f}" class="radar-label" '
                     f'text-anchor="{anchor}">{esc(CATEGORY_CN[a])}</text>')
        shown = f"{shares[a]*100:.0f}%" if (not zero and shares[a] >= 0.005) else "—"
        parts.append(
            f'<text x="{lx:.1f}" y="{y0 + 15:.1f}" text-anchor="{anchor}">'
            f'<tspan class="axis-lbl">占比</tspan>'
            f'<tspan dx="3" class="{cls}">{shown}</tspan></text>')
        if not zero:
            parts.append(
                f'<text x="{lx:.1f}" y="{y0 + 27:.1f}" text-anchor="{anchor}">'
                f'<tspan class="axis-lbl">贡献</tspan>'
                f'<tspan dx="3" class="radar-pts-v">{value:.1f}</tspan></text>')

    # 圆心：总分
    parts.append(f'<text x="{cx}" y="{cy - 2:.1f}" class="rose-total" '
                 f'text-anchor="middle">{risk:.0f}</text>')
    parts.append(f'<text x="{cx}" y="{cy + 15:.1f}" class="rose-total-label" '
                 f'text-anchor="middle">总风险</text>')
    parts.append('</svg>')

    ov = (res.get("category_scores") or {}).get("openness", 0.0)
    parts.append(f'<div class="radar-openness">人味证据强度 <b>{ov:.0f}</b> / 100'
                 f'<span class="hint">（越高越像人写，作为减项抵扣风险分）</span></div>')
    parts.append(
        '<div class="radar-legend">'
        '<b>每根柱子 = 该维度贡献的风险分，八根之和 = 圆心总分</b>，与上方仪表盘同一把尺子。<br>'
        '柱子上的百分比 = 该维度在全部证据里的占比；没有柱子的维度就是 0 分。'
        '</div>')
    return "".join(parts)


CATEGORY_CN = {}


def _init_cn():
    from rules import CATEGORIES
    for k, v in CATEGORIES.items():
        CATEGORY_CN[k] = v["cn"]


_init_cn()


# --------------------------------------------------------------------------
# 页面顶部的通用声明（用户明确要求的提示）
# --------------------------------------------------------------------------

DISCLAIMER_TITLE = "本页面内容由 AI 生成，请自行判断是否采信"
DISCLAIMER_ITEMS = [
    "页面内容由 AI 生成，只做 **AI 含量**的判断，并提供修改意见。",
    "**不保证结果一定准确**。本工具给出的是概率性风险区间，不是鉴定结论；"
    "已知会低估经过人工改写的 AI 文本、也可能高估高度程式化的类型文。",
    "**不评判作品好坏**。风险分低只说明「不像机器写的」，不代表写得好；"
    "风险分高也不代表作品差——它只描述文体特征。",
    "**是否相信页面内容，请用户自行判断。**",
]


def _disclaimer() -> str:
    items = "".join(f"<li>{_md(x)}</li>" for x in DISCLAIMER_ITEMS)
    return f'''
<div class="banner banner-disclaimer">
  <div class="banner-icon">i</div>
  <div class="banner-body">
    <div class="banner-kicker">声明</div>
    <div class="banner-title">{esc(DISCLAIMER_TITLE)}</div>
    <ul class="banner-list">{items}</ul>
  </div>
</div>'''


# --------------------------------------------------------------------------
# 逻辑漏洞（由模型判断）
# --------------------------------------------------------------------------

def _logic_panel(coh: Dict[str, Any]) -> str:
    """逻辑漏洞面板。放在雷达图之前——它比任何文体指标都硬。"""
    if not coh:
        return ""
    holes = coh.get("holes") or []
    if not holes:
        return ""
    rows = []
    for i, h in enumerate(holes, 1):
        sev = h.get("severity", 3)
        rows.append(f'''
<div class="hole sev{sev}">
  <div class="hole-head">
    <span class="hole-no">{i}</span>
    <span class="hole-type">{esc(h.get("type_cn", "逻辑问题"))}</span>
    <span class="hole-sev">严重度 {sev}</span>
  </div>
  <div class="hole-why">{_md(h.get("why", ""))}</div>
  <div class="quote">{_esc_text(h.get("quote", ""))}</div>
</div>''')
    summary = coh.get("summary") or ""
    note = coh.get("note") or ""
    warns = coh.get("warnings") or []
    warn_html = ("".join(f"<li>{esc(w)}</li>" for w in warns))
    return f'''
<section class="panel panel-logic">
  <h2>逻辑漏洞 <span class="hint">共 {len(holes)} 处 · 由模型判断，不是统计推断</span></h2>
  <p class="hint">
    这一项<b>权重高于所有文体类规则</b>：用词可以模仿，<b>逻辑崩了就是崩了</b>。
    一个作者可能不小心写出 AI 腔，但不会在一千字里自我矛盾四五次。
  </p>
  {f'<div class="logic-summary">{_md(summary)}</div>' if summary else ''}
  {"".join(rows)}
  {f'<div class="ex-note ex-note-muted"><b>判断说明</b>：{_md(note)}</div>' if note else ''}
  {f'<div class="ex-note ex-note-muted"><b>本次判断的自检提示</b>（供复核）：<ul>{warn_html}</ul></div>' if warns else ''}
</section>'''


# --------------------------------------------------------------------------
# 已有作品识别（由 agent / LLM 判断后传入）
# --------------------------------------------------------------------------

def _existing_banner(ex: Dict[str, Any], feat: Dict[str, Any]) -> str:
    """页面顶部的醒目提醒。**只在检测到已有作品时才出现。**

    未识别到（level == "none"）或本次未做判断（ex 为 None）时**完全不出现在页面上**——
    这两件事对作者没有行动价值，占着第一屏只会稀释真正重要的提示。
    未做判断这件事改由回复正文说明（见 SKILL.md 第 1c 步），不占页面位置。
    """
    if not ex or ex.get("level") == "none":
        return ""

    level, tone = ex.get("level"), ex.get("tone", "warn")
    title = ex.get("title") or "（未填写作品名）"
    author = ex.get("author") or "未知作者"
    conf_cn = CONF_CN.get(ex.get("confidence"), "?")

    disp = "已有作品" if level == "matched" else "已有作品内容（节选／混排／改写）"
    cov = ex.get("coverage")
    cov_txt = ""
    if isinstance(cov, (int, float)):
        cov_txt = f'<div class="banner-kv"><span>占本文比例</span><b>约 {cov:.0%}</b></div>'
    return f'''
<div class="banner banner-{esc(tone)}">
  <div class="banner-icon">!</div>
  <div class="banner-body">
    <div class="banner-kicker">请先确认这一条，它比下面的 AI 风险分重要</div>
    <div class="banner-title">⚠ 本文为{esc(disp)}</div>
    <div class="banner-work">
      <b>{esc(title)}</b>
      <span class="banner-author">{esc(author)}</span>
      {f'<span class="banner-era">{esc(ex.get("era"))}</span>' if ex.get("era") else ''}
    </div>
    <div class="banner-kv-row">
      <div class="banner-kv"><span>识别置信度</span><b>{conf_cn}</b></div>
      {cov_txt}
    </div>
    <div class="banner-text">
      下面的「AI 痕迹风险分」对已有作品<b>没有意义</b>——名篇大多写于 AI 出现之前，
      低分只说明它是人写的，不能说明它<b>是你的</b>。
      若这不是你的作品，请不要以自己名义发布或投稿。
    </div>
  </div>
</div>'''


def _existing_section(ex: Dict[str, Any]) -> str:
    """详情区块：判断依据、最可确认出处的一句、版权提示、核验建议、自检警告。"""
    ev = ex.get("evidence") or []
    ev_html = "".join(f"<li>{_md(x)}</li>" for x in ev) or "<li>（未提供判断依据）</li>"
    line = ex.get("distinguishing_line") or ""
    conf_cn = CONF_CN.get(ex.get("confidence"), "?")
    cov = ex.get("coverage")
    cov_txt = f"约 {cov * 100:.0f}%" if isinstance(cov, (int, float)) else "—"
    parts = [f'''
<div class="ex-grid">
  <div><div class="lbl">识别置信度</div><div class="ex-big">{esc(conf_cn)}</div></div>
  <div><div class="lbl">占本文比例</div><div class="ex-big">{esc(cov_txt)}</div></div>
  <div><div class="lbl">体裁</div><div class="ex-big">{esc(ex.get("type") or "—")}</div></div>
</div>
<div class="lbl">判断依据</div>
<ul class="ex-ev">{ev_html}</ul>''']
    if line:
        parts.append('<div class="lbl">最能确认出处的一句原文</div>'
                     f'<div class="quote">{_esc_text(line)}</div>')
    if ex.get("reproduction_note"):
        parts.append('<div class="ex-note ex-note-danger"><b>需要注意</b>：'
                     f'{_md(ex["reproduction_note"])}</div>')
    if ex.get("verify_note"):
        parts.append('<div class="ex-note ex-note-warn"><b>核验建议</b>：'
                     f'{_md(ex["verify_note"])}</div>')
    if ex.get("warnings"):
        ws = "".join(f"<li>{esc(w)}</li>" for w in ex["warnings"])
        parts.append('<div class="ex-note ex-note-muted"><b>本次判断的自检提示</b>'
                     f'（供复核，不影响上面的结论）：<ul>{ws}</ul></div>')
    return "".join(parts)


# --------------------------------------------------------------------------
# 正文标注视图
# --------------------------------------------------------------------------

def marked_text(text: str, marks: List[Dict[str, Any]]) -> str:
    """按字符偏移切片渲染正文，命中处加下划线高亮。

    要求：marks 已按 start 排序、互不重叠（analyze.build_report 已去重）。
    """
    out: List[str] = []
    pos = 0
    for idx, m in enumerate(marks):
        s, e = m["start"], m["end"]
        if s < pos:
            continue
        if s > len(text):
            break
        e = min(e, len(text))
        out.append(_esc_text(text[pos:s]))
        cls = f"mk sev{m['severity']}"
        out.append(
            f'<mark id="mk-{idx}" class="{cls}" data-ev="ev{idx}" '
            f'title="{esc(m["category_cn"])}｜{esc(m["title"])}">'
            f'{_esc_text(text[s:e])}</mark>')
        pos = e
    out.append(_esc_text(text[pos:]))
    return "".join(out)


def _esc_text(t: str) -> str:
    return html.escape(t, quote=False).replace("\n", "<br>")


# --------------------------------------------------------------------------
# 各区块
# --------------------------------------------------------------------------

def _quant_table(feat: Dict[str, Any]) -> str:
    s, p, lx = feat["sentence"], feat["paragraph"], feat["lexical"]
    l, pu, d = feat["length"], feat["punct"], feat["dialogue"]
    rows = [
        ("正文字数（汉字）", f"{l['han_total']}", "", ""),
        ("段落数 / 句子数", f"{l['paragraphs']} / {l['sentences']}", "", ""),
        ("对白字符占比", f"{l['dialogue_char_ratio']:.1%}", "网文常见 20%–50%",
         _judge(l['dialogue_char_ratio'], 0.20, 0.50)),
        ("平均句长（字）", f"{s['mean_han']:.1f}", "网文常见 8–22",
         _judge(s['mean_han'], 8, 22)),
        ("句长变异系数", f"{s['cv_han']:.3f}", "真人常见 ≥0.45",
         _judge(s['cv_han'], 0.45, None)),
        ("句长四分位距 IQR", f"{s['iqr']:.1f}", "真人常见 ≥6",
         _judge(s['iqr'], 6, None)),
        ("短句占比（≤5 字）", f"{s['short_ratio_le5']:.1%}", "真人常见 ≥8%",
         _judge(s['short_ratio_le5'], 0.08, None)),
        ("长句占比（≥40 字）", f"{s['long_ratio_ge40']:.1%}", "—", "neutral"),
        ("段长变异系数", f"{p['cv_chars']:.3f}", "真人常见 ≥0.35",
         _judge(p['cv_chars'], 0.35, None)),
        ("单行短段占比", f"{p['one_line_ratio']:.1%}", "网文常见 ≥25%",
         _judge(p['one_line_ratio'], 0.25, None)),
        ("标点种类数", f"{pu['distinct_punct_kinds']}", "真人常见 ≥4",
         _judge(pu['distinct_punct_kinds'], 4, None)),
        ("逗号+句号占比", f"{pu['comma_period_share']:.1%}", "真人常见 ≤90%",
         _judge(pu['comma_period_share'], None, 0.90)),
        ("半角标点混入", f"{pu['halfwidth_ratio']:.1%}", "存在即为人味证据",
         _judge(pu['halfwidth_ratio'], 0.02, None)),
        ("文艺腔套话密度", f"{lx['artistic_cliche']['per_k']:.2f}", "真人常见 ≤1.2",
         _judge(lx['artistic_cliche']['per_k'], None, 1.2)),
        ("抽象情绪词密度", f"{lx['abstract_emotion']['per_k']:.2f}", "真人常见 ≤6.0",
         _judge(lx['abstract_emotion']['per_k'], None, 6.0)),
        ("通用身体反应密度", f"{lx['generic_body']['per_k']:.2f}", "真人常见 ≤5.0",
         _judge(lx['generic_body']['per_k'], None, 5.0)),
        ("书面连接词密度", f"{lx['written_connector']['per_k']:.2f}", "真人常见 ≤9.0",
         _judge(lx['written_connector']['per_k'], None, 9.0)),
        ("总结/升华词密度", f"{lx['summary_habit']['per_k']:.2f}", "真人常见 ≤1.5",
         _judge(lx['summary_habit']['per_k'], None, 1.5)),
        ("对白标注种类占比", f"{d['tag_variety']:.2f}", "真人常见 ≥0.40",
         _judge(d['tag_variety'], 0.40, None)),
        ("人味词总密度", f"{feat['human_voice']['per_k']:.2f}", "越高越像人",
         "neutral"),
    ]
    tr = []
    for name, val, base, judge in rows:
        cls = {"bad": "cell-bad", "good": "cell-good", "warn": "cell-warn",
               "neutral": ""}.get(judge, "")
        tr.append(f'<tr><td>{esc(name)}</td><td class="num {cls}">{esc(val)}</td>'
                  f'<td class="base">{esc(base)}</td></tr>')
    return ('<table class="quant"><thead><tr><th>指标</th><th>你的文本</th>'
            '<th>人类基线（参考）</th></tr></thead><tbody>' + "".join(tr) + '</tbody></table>')


def _judge(v: float, lo, hi) -> str:
    """返回 good / bad / warn / neutral。"bad" 表示偏离人类基线的方向（可疑）。"""
    if lo is not None and v < lo:
        return "bad"
    if hi is not None and v > hi:
        return "bad"
    return "good"


def _evidence_cards(findings: List[Dict[str, Any]], marks: List[Dict[str, Any]]) -> str:
    if not findings:
        return ('<div class="empty-ok">未命中任何 AI 痕迹规则。'
                '文本的节奏、用词、标点、结构都落在真人写作区间内。</div>')
    cards = []
    # 建立 (rule,start,end) → mark 序号 的映射，用于"跳转到正文"
    for i, f in enumerate(findings):
        if f["category"] == "openness":
            continue
        samples_html = ""
        samples = f.get("samples") or ([f["sample"]] if f.get("sample") else [])
        seen = set()
        for s in samples[:6]:
            if not s:
                continue
            key = (s["start"], s["end"])
            if key in seen:
                continue
            seen.add(key)
            ev_idx = next((j for j, m in enumerate(marks)
                           if m["start"] == s["start"] and m["end"] == s["end"]), None)
            jump = (f' <a class="jump" href="#mk-{ev_idx}">定位</a>'
                    if ev_idx is not None else "")
            samples_html += (f'<div class="quote">{_esc_text(s.get("text", ""))}{jump}</div>')
        obs = f.get("observed")
        base = f.get("baseline")
        obs_txt = _fmt_obs(obs, f.get("unit", ""))
        base_txt = _fmt_obs(base, f.get("unit", ""))
        formula = (f'{f["severity"]}（严重度）× {f["confidence"]}（可信度）× '
                   f'min(超额 {f.get("excess_ratio", 0):.2f} 倍, 3) × 命中饱和 → '
                   f'<b>{f["contribution"]:.2f}</b> 分')
        cards.append(f'''
<details class="card sev{f['severity']}" id="ev{i}" open>
  <summary>
    <span class="sev-dot sev{f['severity']}"></span>
    <span class="card-title">{esc(f['title'])}</span>
    <span class="card-cat">{esc(f['category_cn'])}</span>
    <span class="card-score">{f['contribution']:.2f}</span>
  </summary>
  <div class="card-body">
    <div class="metrics">
      <div><span class="k">你的文本</span><span class="v bad">{esc(obs_txt)}</span></div>
      <div><span class="k">人类基线</span><span class="v">{esc(base_txt)}</span></div>
      <div><span class="k">超额倍数</span><span class="v bad">{f.get('excess_ratio', 0):.2f}×</span></div>
      <div><span class="k">严重度</span><span class="v">{SEVERITY_CN.get(f['severity'], '')}</span></div>
    </div>
    <div class="why"><div class="lbl">为什么这条像 AI</div><p>{_md(f['explain'])}</p></div>
    <div class="how"><div class="lbl">怎么改</div><p>{_md(f['fix'])}</p></div>
    <div class="quotes"><div class="lbl">原文命中位置</div>{samples_html or '<div class="quote muted">（该项为分布型指标，无单点命中）</div>'}</div>
    <div class="calc">计分：{formula}</div>
    {f'<div class="note">注：{esc(f["evidence_note"])}</div>' if f.get("evidence_note") else ''}
    {f'<div class="note">已按题材放宽基线 {f.get("genre_factor", "")}</div>' if f.get("genre_adjusted") else ''}
  </div>
</details>''')
    return "".join(cards)


def _fmt_obs(v, unit: str) -> str:
    if v is None:
        return "—"
    if isinstance(v, float):
        s = f"{v:.3f}".rstrip("0").rstrip(".")
    else:
        s = str(v)
    return f"{s} {unit}".strip()


def _md(t: str) -> str:
    """极简 markdown：**粗体**、换行、· 列表。"""
    t = html.escape(str(t), quote=False)
    t = t.replace("**", "\x00")
    parts = t.split("\x00")
    out = []
    for i, p in enumerate(parts):
        out.append(f"<strong>{p}</strong>" if i % 2 else p)
    t = "".join(out)
    t = t.replace("\n", "<br>")
    return t


def _prescriptions(presc: List[Dict[str, Any]]) -> str:
    if not presc:
        return '<div class="empty-ok">没有需要修改的项。</div>'
    rows = []
    for i, p in enumerate(presc[:10], 1):
        rows.append(f'''
<div class="presc">
  <div class="presc-head"><span class="rank">{i}</span>
    <span class="presc-title">{esc(p['title'])}</span>
    <span class="presc-meta">省 {p['contrib']:.1f} 分 · 改动成本 {'低' if p['cost'] <= 2 else ('中' if p['cost'] == 3 else '高')}</span>
  </div>
  <div class="presc-body">{_md(p['fix'])}</div>
</div>''')
    return "".join(rows)


def _human_evidence(he: List[Dict[str, Any]], openness: float) -> str:
    if not he:
        return ('<div class="warn-box">未发现任何典型的人味证据（口语、粗话、方言、'
                '语气词、写作噪声、半角标点）。<b>这本身不构成 AI 证据</b>——'
                '但意味着没有可用于申诉的对冲材料。</div>')
    items = []
    for h in he:
        words = "、".join(f"{w}×{c}" for w, c in h["words"][:10])
        pk = f'（{h["per_k"]:.2f}/千字）' if h.get("per_k") is not None else ""
        items.append(f'<li><b>{esc(h["kind"])}</b>：共 {h["count"]} 次{pk}'
                     f'<div class="words">{esc(words)}</div></li>')
    return (f'<div class="score-line">人味证据强度：<b>{openness:.0f}</b>/100</div>'
            f'<ul class="human-list">{"".join(items)}</ul>')


def _appeal(result: Dict[str, Any], feat: Dict[str, Any]) -> str:
    """申诉要件：把客观事实整理成可以直接引用的条目。"""
    pt = []
    l = feat["length"]
    s = feat["sentence"]
    pu = feat["punct"]
    scale = ("属单章规模的完整样本。" if l["han_total"] >= 1800
             else "属片段样本（低于单章规模），单凭此样本不宜对整部作品下结论。")
    pt.append(f"本次送检文本共 {l['han_total']} 个汉字，{l['paragraphs']} 段，"
              f"{l['sentences']} 句，{scale}")
    n_strong = len([f for f in result["findings"] if f["contribution"] >= 2.0])
    pt.append(f"检出证据 {len(result['findings'])} 条，其中权重较高（≥2 分）的 {n_strong} 条；"
              f"结论区间为 {result['risk_interval'][0]:.0f}–{result['risk_interval'][1]:.0f} 分，"
              f"即本工具自己承认存在 {result['risk_interval'][1] - result['risk_interval'][0]:.0f} 分的不确定度。")
    if result.get("sensitivity"):
        worst = result["sensitivity"][0]
        pt.append(f"单条证据的敏感度：剔除贡献最大的「{worst['title']}」后，"
                  f"风险分由 {result['risk']:.0f} 降至 {worst['risk_without']:.0f}"
                  f"（单项影响 {worst['delta']:.0f} 分）——说明结论对单条特征高度敏感。")
    if result["openness_score"] > 0:
        pt.append(f"文本中存在人味特征（口语/语气词/写作噪声等），人味证据强度 "
                  f"{result['openness_score']:.0f}/100，已从风险分中抵扣 "
                  f"{result['openness_discount']:.1f} 分。")
    if pu["halfwidth_ratio"] > 0.01:
        pt.append(f"文本含 {pu['halfwidth_adjacent']} 处半角标点混排"
                  f"（占标点 {pu['halfwidth_ratio']:.1%}）。中文输入法产生不了半角标点，"
                  f"这只能来自人工敲击或跨来源复制，是难以被生成模型复现的痕迹。")
    if feat.get("de_di_de", {}).get("de_adv_nonstandard", 0) > 0:
        pt.append(f"文本中「的/地」不规范写法出现 "
                  f"{feat['de_di_de']['de_adv_nonstandard']} 次，属真人写作噪声。")
    pt.append("**方法论主张**：现有 AI 检测器普遍基于困惑度与突发性，其输出是概率性的，"
              "不构成对创作主体的认定。本工具的结论同样只表述为「风险区间」，"
              "若平台以单一分数作为处置依据，请要求其公开判定阈值、检测文本范围与复核机制。")
    return "<ol>" + "".join(f"<li>{_md(x)}</li>" for x in pt) + "</ol>"


def _caveats(result: Dict[str, Any], feat: Dict[str, Any]) -> str:
    items = list(result.get("caveats", []))
    items.append("**若文本经过人工改写或 AI 辅助润色，本工具倾向于低估**——"
                 "改写会稀释典型痕迹，但不会把它变成人写。")
    items.append("**高度程式化的题材**（系统流、签到流、战神文）人写也会出现句式重复，"
                 "本工具已按题材放宽基线，但无法完全排除误判。")
    if result.get("skipped_rules"):
        sk = "；".join(f"{s['id']}：{s['reason']}" for s in result["skipped_rules"][:8])
        items.append(f"因样本不足而**未参与评分**的规则：{sk}。"
                     "这些项目缺失会让结论偏离中性，不代表它们正常。")
    return "<ul>" + "".join(f"<li>{_md(x)}</li>" for x in items) + "</ul>"


def _methodology(result: Dict[str, Any]) -> str:
    # 注意：这里显示的是**贡献分**（总风险 × 该维度占比），不是 category_scores。
    # category_scores 是每个分类各自归一化到 0–100 的"自身可疑程度"，
    # 它从未参与总分计算——早版本把它当"分类得分"展示，才造成
    # 「仪表盘 86、图上只有一角」的错位。那个尺度现在只留给「人味证据」用。
    _S = float(result.get("S_total", 0.0) or 0.0)
    _risk = float(result.get("risk", 0.0) or 0.0)
    _cc = result.get("category_contrib", {}) or {}
    cats = "".join(
        f'<tr><td>{esc(CATEGORY_CN[k])}</td>'
        f'<td class="num">{_risk * (_cc.get(k, 0.0) / _S if _S > 0 else 0.0):.1f}</td>'
        f'<td class="num">{(_cc.get(k, 0.0) / _S * 100 if _S > 0 else 0.0):.0f}%</td>'
        f'<td>{esc(CATEGORY_DESC[k])}</td></tr>'
        for k in CATEGORY_ORDER)
    return f'''
<h3>计分公式（全部公开）</h3>
<pre class="formula">单条规则得分 = 严重度(1-5) × 可信度(0-1) × 档位系数 × [ 超额分 + 极端加成 ] × (1 - e^(-命中次数/1.5))
               档位系数 = A级 2.0 ｜ B级 1.0 ｜ C级 0.4          ← v1.3 新增
               超额分   = min(超额倍数 - 1, 3)
               极端加成 = min(0.8 × log2(超额倍数 / 4), 1.5)  （超额 >4 倍时）

分类自身分   = 100 × (1 - e^(-Σ规则得分 / 该分类归一常数))   ← 仅用于「人味证据」的抵扣量
证据多样性   = 1 + 0.20 × min(有实质贡献(≥1.5分)的维度数 - 2, 3)
总风险       = 100 × (1 - e^(-证据总分 × 证据多样性 / 22)) - 0.30 × 人味证据强度
区间         = 风险 ± (证据分散惩罚 5-14 + 样本长度惩罚 0-12 + 短样本放宽 0-15)
档位         = &lt;18 极低 ｜ &lt;36 偏低 ｜ &lt;55 中等 ｜ &lt;72 偏高 ｜ ≥72 极高</pre>
<h3>「档位系数」是什么（v1.3）</h3>
<p class="hint">
早期所有规则的有效权重只差 4.9 倍，而且<b>排错了</b>——实测会误报的启发式
（句长均匀度，权重 2.40）反而高于已证实的强信号（比喻模板，权重 1.98）。
结果是一条"套话密度是基线 19 倍"的压倒性证据，被一堆弱启发式<b>摊平</b>。<br>
现在按<b>实测区分度</b>分三档：
</p>
<table class="quant"><thead><tr><th>档位</th><th>系数</th><th>判据</th><th>典型规则</th></tr></thead><tbody>
<tr><td><b>A 级</b></td><td>×2.0</td><td>实测能区分人写与 AI，几乎可作确定性证据</td>
    <td>文艺腔套话、逐字重复、总结词、对偶模板、比喻模板、段末升华</td></tr>
<tr><td><b>B 级</b></td><td>×1.0</td><td>有效性中等，需与其它信号并发</td>
    <td>情绪命名、身体库存、标注单调、对白乒乓、五感偏视觉</td></tr>
<tr><td><b>C 级</b></td><td>×0.4</td><td>受长度/题材干扰大，实测出现过误报或零区分度</td>
    <td>句长均匀度、段长整齐、单行段、标点熵、用字丰富度</td></tr>
</tbody></table>
<p class="hint">
权重跨度因此从 <b>4.9 倍扩大到 24 倍</b>：一条 A 级强信号（8.80）现在是一条
C 级启发式（0.36）的 <b>24 倍</b>，不会再被摊平。
所有分档依据都写在 <code>rules.py</code> 的 <code>TIER_MAP</code> 注释里，可逐条复核。
</p>
<h3>「极端加成」是什么（v1.3）</h3>
<p class="hint">
原来的封顶会让「套话密度是基线的 4 倍」和「19 倍」拿到<b>同一个分数</b>——
而后者是压倒性证据：没有人会自然地写出 19 倍的套话密度。
所以对<b>超额超过 4 倍</b>的信号额外给对数加成（封顶 +1.5，即最多比原分多 50%）。<br>
用对数而非线性，是为了让加成随倍数<b>缓慢</b>上升，避免极端值再次主导结论。
加成<b>只影响本次计分</b>，不改规则的严重度、可信度与基线——<b>不会污染判断力</b>。
</p>
<h3>为什么要有「证据多样性」这一项</h3>
<p class="hint">
实测发现：只看证据总量会把两种很不一样的情况算成同分——
「3 条证据全挤在同一个维度」和「8 条证据横跨 4–5 个维度」，两者证据总分都约 25 分。
但后者可信得多：不同维度的特征是**互相独立**的，同时出现才说明"系统性"；
前者可能只是某一种写作习惯被反复命中。<br>
加上多样性加成后：同为 25 分的两份文本，横跨 2 个维度得 <b>68 分</b>（偏高），
横跨 4 个维度得 <b>80 分</b>（极高）。<b>这一项只加成、不削减</b>，
单一维度命中的文本分数不变。
</p>
<h3>各维度贡献（相加 = 总分）</h3>
<table class="quant"><thead><tr><th>分类</th><th>贡献分</th><th>占证据</th><th>测什么</th></tr></thead>
<tbody>{cats}</tbody></table>
<h3>本工具明确不做什么</h3>
<ul>
<li>不给"AI 生成率"这类确定语气的数字，只给风险区间与档位。</li>
<li>不提供整章一键改写。给出的只是改法说明与示范，因为洗稿是用 AI 掩盖 AI，改完更像机器。</li>
<li>不声称"证明"。所有结论都是概率性推断，附误差与失效场景。</li>
</ul>'''


# --------------------------------------------------------------------------
# 主模板
# --------------------------------------------------------------------------

def render(report: Dict[str, Any]) -> str:
    if report.get("error"):
        return _wrap("分析失败", f'<p class="err">{esc(report["error"])}</p>', report)
    meta = report["meta"]
    feat = report["features"]
    res = report["result"]
    text = report["text"]
    marks = report["marks"]

    gauge = svg_gauge(res["risk"], res["risk_interval"][0], res["risk_interval"][1],
                      res["band_color"], res["band"])
    radar = svg_category_chart(res)

    verdict_cls = "v-" + BAND_CLASS.get(res["band"], "unknown")
    existing = report.get("existing_work")
    banner = _existing_banner(existing, feat)
    logic_block = _logic_panel(report.get("coherence"))
    body = f'''
{_disclaimer()}
{banner}
<header class="hero">
  <div class="hero-left">
    <div class="eyebrow">网文 AI 痕迹检测 · 可解释报告</div>
    <h1>{esc(meta['source'] or '未命名文本')}</h1>
    <div class="sub">
      {feat['length']['han_total']} 汉字 · {feat['length']['paragraphs']} 段 ·
      {feat['length']['sentences']} 句 · 生成于 {esc(meta['generated_at'])} ·
      引擎 v{esc(meta['engine_version'])} / 词表 v{esc(meta['lexicon_version'])}
    </div>
    <div class="verdict {verdict_cls}">
      <div class="v-band">{esc(res['band'])}</div>
      <div class="v-text">{_md(res['band_text'])}</div>
      <div class="v-interval">风险区间 <b>{res['risk_interval'][0]:.0f} – {res['risk_interval'][1]:.0f}</b>
        <span class="hint">（同一份文本在不同检测器上的落点大概率落在这个带里）</span></div>
    </div>
  </div>
  <div class="hero-right">{gauge}</div>
</header>
''' + (f'''
<section class="panel panel-existing">
  <h2>已有作品识别 · 详情</h2>
  {_existing_section(existing)}
</section>''' if existing and existing.get("level") in ("matched", "partial") else "") + logic_block + f'''
<section class="grid2">
  <div class="panel"><h2>风险贡献分布 <span class="hint">各柱之和 = 总分</span></h2>{radar}</div>
  <div class="panel"><h2>体检表（客观指标 vs 人类基线）</h2>
    <div class="table-wrap">{_quant_table(feat)}</div>
  </div>
</section>

<section class="panel">
  <h2>判定证据（按贡献排序）<span class="hint">共 {len([f for f in res['findings'] if f['category'] != 'openness'])} 条</span></h2>
  <p class="hint">每条都给出：你的实测值、人类基线、为什么这条像 AI、以及具体改法。点击「定位」跳到正文对应位置。</p>
  {_evidence_cards(res['findings'], marks)}
</section>

<section class="panel">
  <h2>正文标注视图<span class="hint">下划线 = 被判定为 AI 痕迹的位置，颜色深浅代表严重度</span></h2>
  <div class="legend">
    <span><i class="sev1"></i>提示</span><span><i class="sev2"></i>轻微</span>
    <span><i class="sev3"></i>中等</span><span><i class="sev4"></i>显著</span>
    <span><i class="sev5"></i>强烈</span>
  </div>
  <div class="doc">{marked_text(text, marks)}</div>
</section>

<section class="panel">
  <h2>修改处方（按性价比排序）</h2>
  <p class="hint">排序依据 = 该条能省下的风险分 ÷ 改动成本。先改前三条，通常就能降一个档位。</p>
  {_prescriptions(report['prescriptions'])}
</section>

<section class="panel">
  <h2>误判对冲：支持「这是人写的」的证据</h2>
  <p class="hint">AI 检测最大的问题是只找"像 AI"的证据，不找反证。这一节专门列反证。</p>
  {_human_evidence(report['human_evidence'], res['openness_score'])}
</section>

<section class="panel">
  <h2>如果被平台判定，可以用这些论据申诉</h2>
  {_appeal(res, feat)}
</section>

<section class="panel">
  <h2>结论的不确定性与已知失效场景</h2>
  {_caveats(res, feat)}
</section>

<section class="panel methodology">
  <h2>方法学透明说明</h2>
  {_methodology(res)}
</section>

<footer class="disclaimer">
  <b>免责声明</b>
  <ul style="margin:6px 0 0;padding-left:20px;">
    <li>页面内容由 AI 生成，只做 <b>AI 含量</b>的判断，并提供修改意见。</li>
    <li><b>不保证结果一定准确。</b>本报告基于可观测的文体统计特征做概率性推断，
      输出的是风险区间而非鉴定结论；已知会低估经过人工改写的 AI 文本，
      也可能高估系统流等高度程式化的类型文。</li>
    <li><b>不评判作品好坏。</b>风险分低只说明「文体上不像机器写的」，不代表写得好；
      风险分高也不代表作品差，它只描述文体特征。</li>
    <li><b>是否相信页面内容，请用户自行判断。</b>本工具无法获取任何模型的服务端信息，
      也无法证明文本由谁创作；不构成法律意义上的鉴定结论，不得作为认定创作主体的唯一依据。</li>
  </ul>
</footer>
'''
    return _wrap(f"AI 痕迹检测报告 · {meta['source']}", body, report)


def _wrap(title: str, body: str, report: Dict[str, Any]) -> str:
    data = ""
    if report.get("result"):
        data = json.dumps({
            "risk": report["result"]["risk"],
            "band": report["result"]["band"],
            "findings": [{"id": f["id"], "contrib": f["contribution"]} for f in report["result"]["findings"]],
        }, ensure_ascii=False)
    return f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)}</title>
<style>
:root {{
  --bg:#f4f5f8; --paper:#ffffff; --ink:#1f2430; --ink2:#4b5568; --ink3:#7c8598;
  --line:#e6e8ef; --line2:#d3d7e2;
  --s1:#94a3b8; --s2:#60a5fa; --s3:#ca8a04; --s4:#ea580c; --s5:#dc2626;
  --good:#16a34a; --bad:#dc2626; --warn:#ca8a04;
}}
* {{ box-sizing:border-box; }}
body {{
  margin:0; background:var(--bg); color:var(--ink);
  font-family:"Microsoft YaHei","PingFang SC","Hiragino Sans GB","Source Han Sans SC",
              "Noto Sans CJK SC",system-ui,-apple-system,"Segoe UI",sans-serif;
  line-height:1.75; font-size:15px;
}}
.wrap {{ max-width:1080px; margin:0 auto; padding:24px 18px 60px; }}
.hero {{
  background:var(--paper); border:1px solid var(--line); border-radius:16px;
  padding:26px 28px; display:flex; gap:26px; align-items:center;
  box-shadow:0 1px 3px rgba(20,26,45,.05); margin-bottom:18px; flex-wrap:wrap;
}}
.hero-left {{ flex:1 1 420px; min-width:320px; }}
.hero-right {{ flex:0 0 360px; max-width:100%; }}
.eyebrow {{ font-size:12px; letter-spacing:.14em; color:var(--ink3); text-transform:uppercase; }}
h1 {{ font-size:24px; margin:6px 0 4px; line-height:1.35; word-break:break-all; }}
.sub {{ font-size:12.5px; color:var(--ink3); }}
.verdict {{ margin-top:16px; padding:14px 16px; border-radius:12px; background:#fafbfd; border:1px solid var(--line); }}
.v-band {{ font-size:20px; font-weight:700; }}
.v-text {{ font-size:14px; color:var(--ink2); margin-top:2px; }}
.v-interval {{ font-size:13px; color:var(--ink2); margin-top:8px; }}
.v-verylow .v-band {{ color:var(--good); }}
.v-low .v-band {{ color:#65a30d; }}
.v-mid .v-band {{ color:var(--warn); }}
.v-high .v-band {{ color:var(--s4); }}
.v-veryhigh .v-band {{ color:var(--bad); }}
.v-unknown .v-band {{ color:var(--ink3); }}
.gauge {{ width:100%; height:auto; }}
.tick {{ font-size:10px; fill:var(--ink3); }}
.gauge-num {{ font-size:46px; font-weight:800; }}
.gauge-unit {{ font-size:11px; fill:var(--ink3); letter-spacing:.08em; }}
.gauge-band {{ font-size:16px; font-weight:700; }}

.panel {{
  background:var(--paper); border:1px solid var(--line); border-radius:16px;
  padding:22px 24px; margin-bottom:18px; box-shadow:0 1px 3px rgba(20,26,45,.05);
}}
.panel h2 {{ font-size:17px; margin:0 0 12px; display:flex; align-items:baseline; gap:10px; flex-wrap:wrap; }}
.panel h3 {{ font-size:15px; margin:20px 0 8px; }}
.grid2 {{ display:grid; grid-template-columns:1fr 1.25fr; gap:18px; align-items:start; }}
@media (max-width:900px) {{ .grid2 {{ grid-template-columns:1fr; }} }}
.hint {{ font-size:12.5px; color:var(--ink3); font-weight:400; }}
.radar {{ width:100%; height:auto; max-width:440px; display:block; margin:0 auto; overflow:visible; }}
/* 层级：分类名最大最重 → 占比次之 → 贡献最轻。数字一律小于名称。 */
.radar-label {{ font-size:11.5px; font-weight:600; fill:#4b5563; }}
.radar-score {{ font-size:10.5px; font-weight:700; fill:#c2410c; }}
.radar-score-zero {{ fill:#c3c7d2; font-weight:400; }}
.axis-lbl {{ font-size:8.5px; fill:#c0c4ce; }}
.radar-pts-v {{ font-size:10px; font-weight:500; fill:#ea580c; }}
.radar-ring {{ font-size:9px; fill:#c3c7d2; }}
.radar-risk-ring {{ font-size:10.5px; font-weight:700; fill:#dc2626; }}
.radar-openness {{ text-align:center; font-size:13px; color:var(--ink2); margin-top:4px; }}
.radar-legend {{ text-align:center; font-size:11.5px; color:var(--ink3); margin-top:5px; line-height:1.5; }}
.radar-legend .r0 {{ display:inline-block; width:7px; height:7px; border:1.4px solid #ea580c;
  border-radius:50%; background:#fff; vertical-align:middle; margin:0 1px; }}

.table-wrap {{ max-height:560px; overflow:auto; }}
table.quant {{ width:100%; border-collapse:collapse; font-size:13.5px; }}
table.quant th {{ text-align:left; font-weight:600; color:var(--ink3); font-size:12px;
  border-bottom:1px solid var(--line2); padding:6px 8px; position:sticky; top:0; background:var(--paper); }}
table.quant td {{ padding:6px 8px; border-bottom:1px solid var(--line); vertical-align:top; }}
table.quant td.num {{ font-variant-numeric:tabular-nums; font-weight:600; white-space:nowrap; }}
table.quant td.base {{ color:var(--ink3); font-size:12.5px; }}
.cell-bad {{ color:var(--bad); }}
.cell-good {{ color:var(--good); }}
.cell-warn {{ color:var(--warn); }}

details.card {{ border:1px solid var(--line); border-left:4px solid var(--s3);
  border-radius:10px; margin-bottom:10px; background:#fcfcfe; }}
details.card.sev4 {{ border-left-color:var(--s4); }}
details.card.sev5 {{ border-left-color:var(--s5); }}
details.card.sev2 {{ border-left-color:var(--s2); }}
details.card.sev1 {{ border-left-color:var(--s1); }}
details.card summary {{ cursor:pointer; padding:11px 14px; display:flex; align-items:center; gap:10px;
  font-weight:600; font-size:14.5px; list-style:none; }}
details.card summary::-webkit-details-marker {{ display:none; }}
.sev-dot {{ width:9px; height:9px; border-radius:50%; flex:0 0 auto; background:var(--s3); }}
.sev-dot.sev5 {{ background:var(--s5); }} .sev-dot.sev4 {{ background:var(--s4); }}
.sev-dot.sev2 {{ background:var(--s2); }} .sev-dot.sev1 {{ background:var(--s1); }}
.card-title {{ flex:1 1 auto; }}
.card-cat {{ font-size:11.5px; color:var(--ink3); font-weight:400;
  border:1px solid var(--line2); border-radius:20px; padding:1px 8px; }}
.card-score {{ font-variant-numeric:tabular-nums; color:var(--s4); font-weight:700; }}
.card-body {{ padding:0 14px 14px; border-top:1px dashed var(--line); }}
.metrics {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(120px,1fr)); gap:8px; margin:12px 0; }}
.metrics > div {{ background:#f6f7fb; border-radius:8px; padding:7px 9px; }}
.metrics .k {{ display:block; font-size:11px; color:var(--ink3); }}
.metrics .v {{ font-weight:700; font-size:14px; font-variant-numeric:tabular-nums; }}
.metrics .v.bad {{ color:var(--bad); }}
.lbl {{ font-size:11.5px; color:var(--ink3); letter-spacing:.05em; margin-top:10px; }}
.why p, .how p {{ margin:3px 0 0; font-size:14px; }}
.how {{ background:#f7fbf7; border-radius:8px; padding:8px 11px; margin-top:8px; }}
.how .lbl {{ margin-top:0; color:var(--good); }}
.quote {{ font-family:Consolas,"Courier New",monospace; font-size:13px; background:#fff8ef;
  border-left:3px solid var(--s4); padding:5px 9px; margin:5px 0; border-radius:0 6px 6px 0;
  word-break:break-all; }}
.quote.muted {{ color:var(--ink3); border-left-color:var(--line2); background:#f8f9fc; }}
.jump {{ font-size:11.5px; color:#2563eb; text-decoration:none; margin-left:6px; }}
.calc {{ font-size:12px; color:var(--ink3); margin-top:10px; font-variant-numeric:tabular-nums; }}
.note {{ font-size:12.5px; color:var(--warn); margin-top:6px; }}

.legend {{ display:flex; gap:14px; font-size:12px; color:var(--ink3); margin-bottom:10px; flex-wrap:wrap; }}
.legend i {{ display:inline-block; width:14px; height:3px; border-radius:2px; margin-right:4px;
  vertical-align:middle; background:var(--s3); }}
.legend i.sev5 {{ background:var(--s5); }} .legend i.sev4 {{ background:var(--s4); }}
.legend i.sev2 {{ background:var(--s2); }} .legend i.sev1 {{ background:var(--s1); }}
.doc {{ background:#fcfcfe; border:1px solid var(--line); border-radius:10px;
  padding:16px 18px; font-size:15px; line-height:2; max-height:640px; overflow:auto;
  white-space:normal; }}
mark.mk {{ background:transparent; border-bottom:3px solid var(--s3);
  padding-bottom:1px; cursor:help; scroll-margin-top:80px; }}
mark.mk.sev5 {{ border-bottom-color:var(--s5); background:rgba(220,38,38,.07); }}
mark.mk.sev4 {{ border-bottom-color:var(--s4); background:rgba(234,88,12,.07); }}
mark.mk.sev2 {{ border-bottom-color:var(--s2); }}
mark.mk.sev1 {{ border-bottom-color:var(--s1); }}
mark.mk:target {{ background:#fffbdc; box-shadow:0 0 0 3px #fffbdc; }}

.presc {{ border:1px solid var(--line); border-radius:10px; padding:11px 14px; margin-bottom:9px; background:#fcfcfe; }}
.presc-head {{ display:flex; align-items:center; gap:9px; flex-wrap:wrap; }}
.rank {{ width:20px; height:20px; border-radius:50%; background:var(--ink); color:#fff;
  font-size:11.5px; display:inline-flex; align-items:center; justify-content:center; flex:0 0 auto; }}
.presc-title {{ font-weight:600; font-size:14px; flex:1 1 auto; }}
.presc-meta {{ font-size:12px; color:var(--ink3); }}
.presc-body {{ font-size:13.5px; margin-top:6px; color:var(--ink2); }}

.score-line {{ font-size:14px; margin-bottom:8px; }}
.human-list {{ margin:0; padding-left:20px; font-size:13.5px; }}
.human-list li {{ margin-bottom:7px; }}
.words {{ font-family:Consolas,monospace; font-size:12px; color:var(--ink3); word-break:break-all; }}
.empty-ok {{ background:#f2fbf4; border:1px solid #cdeed6; color:#166534;
  border-radius:10px; padding:12px 14px; font-size:14px; }}
.warn-box {{ background:#fffbeb; border:1px solid #fde68a; color:#92400e;
  border-radius:10px; padding:12px 14px; font-size:14px; }}
pre.formula {{ background:#f6f7fb; border:1px solid var(--line); border-radius:8px;
  padding:12px 14px; font-size:12.5px; line-height:1.9; overflow:auto;
  font-family:Consolas,"Courier New",monospace; white-space:pre-wrap; }}
.methodology ul {{ font-size:13.5px; color:var(--ink2); padding-left:20px; }}
.disclaimer {{ font-size:12.5px; color:var(--ink3); background:#fbfbfd;
  border:1px dashed var(--line2); border-radius:12px; padding:14px 16px; }}
.err {{ color:var(--bad); }}

/* ---------- 已有作品识别横幅（页面最顶部，必须最醒目） ---------- */
.banner {{ display:flex; gap:14px; align-items:flex-start; border-radius:14px;
  padding:16px 20px; margin-bottom:18px; border:2px solid; }}
.banner-icon {{ flex:0 0 auto; width:34px; height:34px; border-radius:50%;
  display:flex; align-items:center; justify-content:center; font-size:20px; font-weight:800; }}
.banner-body {{ flex:1 1 auto; min-width:0; }}
.banner-kicker {{ font-size:11.5px; letter-spacing:.06em; font-weight:700;
  text-transform:uppercase; opacity:.75; margin-bottom:4px; }}
.banner-title {{ font-size:19px; font-weight:800; line-height:1.4; }}
.banner-text {{ font-size:13.5px; margin-top:8px; line-height:1.75; }}
.banner-work {{ font-size:22px; font-weight:800; margin-top:6px; }}
.banner-author {{ font-size:14px; font-weight:600; opacity:.8; margin-left:10px; }}
.banner-era {{ font-size:13px; opacity:.7; margin-left:8px; }}
.banner-kv-row {{ display:flex; gap:22px; margin-top:8px; flex-wrap:wrap; }}
.banner-kv {{ font-size:12.5px; }} .banner-kv span {{ opacity:.7; margin-right:6px; }}
.banner-kv b {{ font-size:15px; }}

.banner-danger {{ background:#fef2f2; border-color:#fca5a5; color:#7f1d1d; }}
.banner-danger .banner-icon {{ background:#dc2626; color:#fff; }}
.banner-warn {{ background:#fffbeb; border-color:#fcd34d; color:#78350f; }}
.banner-warn .banner-icon {{ background:#d97706; color:#fff; }}
.banner-ok {{ background:#f0fdf4; border-color:#86efac; color:#14532d; }}
.banner-ok .banner-icon {{ background:#16a34a; color:#fff; }}
.banner-info {{ background:#f8fafc; border-color:var(--line2); color:var(--ink2); }}
.banner-info .banner-icon {{ background:#94a3b8; color:#fff; }}

/* 通用声明：中性色但必须有足够存在感，不能被当成装饰略过 */
.banner-disclaimer {{ background:#f1f5f9; border-color:#94a3b8; color:#1e293b; }}
.banner-disclaimer .banner-icon {{ background:#475569; color:#fff;
  font-family:Georgia,serif; font-style:italic; font-size:19px; }}
.banner-disclaimer .banner-title {{ font-size:16px; }}
.banner-list {{ margin:8px 0 0; padding-left:20px; font-size:13px; line-height:1.8; }}
.banner-list li {{ margin-bottom:3px; }}

.panel-existing {{ border-color:#fca5a5; }}
.panel-existing h2 {{ color:#991b1b; }}
.ex-grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(140px,1fr));
  gap:10px; margin:6px 0 14px; }}
.ex-grid > div {{ background:#f6f7fb; border-radius:8px; padding:8px 10px; }}
.ex-big {{ font-size:16px; font-weight:700; }}
.ex-ev {{ font-size:13.5px; margin:4px 0 10px; padding-left:20px; color:var(--ink2); }}
.ex-ev li {{ margin-bottom:5px; }}
.ex-note {{ font-size:13px; border-radius:8px; padding:9px 12px; margin-top:9px; line-height:1.7; }}
.ex-note-danger {{ background:#fef2f2; border-left:3px solid #dc2626; color:#7f1d1d; }}
.ex-note-warn {{ background:#fffbeb; border-left:3px solid #d97706; color:#78350f; }}
.ex-note-muted {{ background:#f8fafc; border-left:3px solid var(--line2); color:var(--ink3); font-size:12.5px; }}
.ex-note-muted ul {{ margin:4px 0 0; padding-left:18px; }}

/* ---------- 逻辑漏洞面板 ---------- */
.panel-logic {{ border-color:#fca5a5; }}
.panel-logic h2 {{ color:#991b1b; }}
.logic-summary {{ font-size:14px; background:#fef2f2; border-left:3px solid #dc2626;
  padding:9px 12px; border-radius:0 8px 8px 0; margin:8px 0 14px; color:#7f1d1d; }}
.hole {{ border:1px solid var(--line); border-left:4px solid #d97706;
  border-radius:10px; padding:10px 13px; margin-bottom:9px; background:#fffdf8; }}
.hole.sev5 {{ border-left-color:#dc2626; background:#fff7f7; }}
.hole.sev4 {{ border-left-color:#ea580c; }}
.hole-head {{ display:flex; align-items:center; gap:9px; flex-wrap:wrap; margin-bottom:5px; }}
.hole-no {{ width:20px; height:20px; border-radius:50%; background:#991b1b; color:#fff;
  font-size:11.5px; display:inline-flex; align-items:center; justify-content:center; flex:0 0 auto; }}
.hole-type {{ font-weight:700; font-size:14px; color:#991b1b; }}
.hole-sev {{ font-size:11.5px; color:var(--ink3); border:1px solid var(--line2);
  border-radius:20px; padding:1px 8px; }}
.hole-why {{ font-size:13.5px; color:var(--ink2); margin-bottom:5px; }}

@media print {{
  body {{ background:#fff; font-size:11.5px; }}
  .panel,.hero {{ box-shadow:none; break-inside:avoid; }}
  details.card {{ break-inside:avoid; }}
  details.card:not([open]) .card-body {{ display:block !important; }}
  .doc {{ max-height:none; }}
  .table-wrap {{ max-height:none; }}
}}
</style>
</head>
<body>
<div class="wrap">
{body}
</div>
<script type="application/json" id="report-data">{data}</script>
<script>
// 点击证据卡里的「定位」时，高亮正文中的对应片段
document.addEventListener('click', function (e) {{
  var a = e.target.closest('a.jump');
  if (!a) return;
  var id = a.getAttribute('href').slice(1);
  var el = document.getElementById(id);
  if (!el) return;
  el.scrollIntoView({{ behavior: 'smooth', block: 'center' }});
  el.style.transition = 'background .2s';
  el.style.background = '#fffbdc';
  setTimeout(function () {{ el.style.background = ''; }}, 1600);
}});
</script>
</body>
</html>'''
