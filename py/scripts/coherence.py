# -*- coding: utf-8 -*-
"""
coherence —— 逻辑漏洞识别（由模型判断，不由规则推断）。

**为什么必须由模型做，不能用规则**
----------------------------------
实测过一条"答非所问"的字符重叠指标：取问答对，算问答双方的实字重合度。
在早期 AI 文本上判出 5 处「答非所问」，**逐一核对后只有 1 处是真的**——
其余 4 处是「答对了，但换了词」，比如：
    问：那座森林里住着什么人？   答：我不清楚，老板从来没告诉过我。
答句当然不含问句的字符，但它是一个**正确的答案**。字符重叠度在这里毫无意义。

逻辑漏洞的本质是**语义关系**（矛盾、跳跃、答非所问、因果倒置），
任何基于字面统计的代理指标都会重蹈上面的覆辙。
所以本模块只做两件事：
  1. 定义模型判断结果的**数据契约**（防止字段写错导致静默漏报）
  2. 把判断结果转成计分用的 finding（**高权重**，因为逻辑漏洞是 AI 最硬的破绽）

**权重设计**：逻辑漏洞比任何文体特征都硬——文体可以模仿，逻辑崩了就是崩了。
所以每条漏洞按 severity 直接给分（不经超额倍数换算），分类归一常数也设得很小，
让它能快速顶到高分区。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

# 漏洞类型（模型判断时选一个，便于在报告里归类）
HOLE_TYPES = {
    "contradiction": "自相矛盾",
    "non_answer": "答非所问",
    "jump": "语义跳跃",
    "causality": "因果倒置",
    "reference": "指代崩坏",
    "world": "设定自相冲突",
    "nonsense": "内容荒谬",
}

REQUIRED = ("holes",)

SCHEMA_HINT = """{
  "severity_max": 5,
  "holes": [
    {
      "quote": "原文片段（10-40 字，用于在报告里定位）",
      "type": "contradiction | non_answer | jump | causality | reference | world | nonsense",
      "severity": 1,
      "why": "为什么这是逻辑漏洞（一句话，要具体到两个互相冲突的点）"
    }
  ],
  "summary": "整体判断（一句话）",
  "note": "（可选）说明判断的不确定性"
}"""


def normalize(raw: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError("coherence 判断结果必须是对象（dict）")
    if "holes" not in raw:
        raise ValueError(f"coherence 缺少必填字段 'holes'。schema：\n{SCHEMA_HINT}")
    holes_in = raw.get("holes") or []
    if not isinstance(holes_in, list):
        raise ValueError("holes 必须是数组")

    holes: List[Dict[str, Any]] = []
    for i, h in enumerate(holes_in, 1):
        if not isinstance(h, dict):
            raise ValueError(f"holes[{i}] 必须是对象")
        q = str(h.get("quote") or "").strip()
        why = str(h.get("why") or "").strip()
        if not q:
            raise ValueError(f"holes[{i}] 缺少 quote（原文片段，用于定位）")
        if not why:
            raise ValueError(f"holes[{i}] 缺少 why（为什么这是逻辑漏洞）")
        t = str(h.get("type") or "").strip()
        if t and t not in HOLE_TYPES:
            raise ValueError(f"holes[{i}].type 必须是 {list(HOLE_TYPES)} 之一，收到 {t!r}")
        sev = h.get("severity", 3)
        try:
            sev = int(sev)
        except (TypeError, ValueError):
            raise ValueError(f"holes[{i}].severity 必须是 1-5 的整数")
        sev = max(1, min(5, sev))
        holes.append({"quote": q, "type": t or "nonsense",
                      "type_cn": HOLE_TYPES.get(t, "逻辑问题"),
                      "severity": sev, "why": why})

    return {
        "holes": holes,
        "count": len(holes),
        "summary": str(raw.get("summary") or "").strip(),
        "note": str(raw.get("note") or "").strip(),
        "warnings": validate(holes, raw),
    }


def validate(holes: List[Dict[str, Any]], raw: Dict[str, Any]) -> List[str]:
    """自检：判断与自身是否自洽。只提示，不改判定。"""
    warns: List[str] = []
    if not holes:
        warns.append("holes 为空：如果文本确实没有逻辑漏洞，请把 summary 写明"
                     "「通读未见明显逻辑矛盾」；若只是没细看，请直说没查。")
    # 平均严重度过高 → 可能把"文笔差"当成了"逻辑错"
    if holes:
        avg = sum(h["severity"] for h in holes) / len(holes)
        if avg >= 4.5 and len(holes) >= 4:
            warns.append(
                f"平均严重度高达 {avg:.1f}（{len(holes)} 处）：请复核是否把"
                "「写得不好／设定粗糙」误判为「逻辑矛盾」。"
                "逻辑漏洞必须是**两处信息互相冲突**，不是「我不喜欢这个写法」。")
    # 同一 quote 重复
    qs = [h["quote"] for h in holes]
    if len(qs) != len(set(qs)):
        warns.append("存在重复的 quote —— 请合并同一处漏洞。")
    return warns


def to_findings(coh: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """把逻辑漏洞转成计分用的 finding。

    贡献直接按 severity 给分，**不走"超额倍数"换算**——
    因为逻辑漏洞没有"人类基线"可算，它要么矛盾要么不矛盾。

    权重：每条 = severity × 1.2 分（severity 5 → 6 分）。
    这个权重明显高于文体类规则（后者通常在 1–4 分区间），理由：
    **文体可以模仿，逻辑崩了就是崩了。** 一个作者模仿了 AI 的用词习惯不奇怪，
      但不会在一千字里自我矛盾四五次。所以逻辑漏洞是最不该被稀释的证据。
    实测：一份 4 处漏洞的文本，逻辑贡献 19.2 分，能把总分从 17 顶到 65。
    """
    if not coh or not coh.get("holes"):
        return []
    out = []
    for h in coh["holes"]:
        type_cn = h.get("type_cn") or HOLE_TYPES.get(h.get("type", ""), "逻辑问题")
        out.append({
            "id": "LOGIC_HOLE",
            "category": "logic",
            "category_cn": "逻辑一致性",
            "title": f"{type_cn}：{h['why'][:40]}",
            "severity": h["severity"],
            "confidence": 0.9,
            "observed": h["quote"][:60],
            "baseline": "逻辑必须自洽",
            "threshold": "—",
            "unit": "",
            "excess_ratio": 1.0,
            "hits": 1,
            "contribution": round(h["severity"] * 1.2, 3),
            "sample": None,
            "samples": [{"start": -1, "end": -1, "text": h["quote"]}],
            "explain": f"**{h['type_cn']}**。{h['why']}",
            "fix": ("把互相冲突的两处信息统一：要么改前一处、要么改后一处。\n"
                    "AI 生成文本的逻辑漏洞通常来自「上下文遗忘」——"
                    "它在生成后文时忘了前文说过什么。**人类写作的这类错误更随机、更局部；"
                    "AI 则倾向于整段垮掉。**"),
            "baseline_desc": "逻辑一致性没有统计基线，由模型判断",
            "evidence_note": "本项由模型（LLM）判断，不是统计推断；请以原文复核。"
                             "权重高于文体类规则，因为逻辑矛盾无法靠模仿文风规避。",
            "genre_adjusted": False,
            "source": "llm_coherence",
        })
    return out
