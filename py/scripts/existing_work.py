# -*- coding: utf-8 -*-
"""
existing_work —— 已有作品识别。

设计决定（v1.1）：**不做指纹库**。
理由：一个静态指纹库再多也覆盖不了"流传很广的已有作品"这个开放集合，
而且维护成本全落在词表上、命中率却取决于我收录了多少。
改用**调用方（agent / 大语言模型）判断 + 由用户或 agent 核验**：
模型的知识本身就是那个开放集合，而且它对改写、节选、混排的鲁棒性远好于逐字指纹。

本模块只做两件事：
  1. 定义判断结果的**数据契约**（schema 校验，防止 agent 传错字段导致报告静默漏报）
  2. 兜底校验：判断结果必须与「逐字复现证据」自洽（见 validate() 的警告逻辑）

**红线：判断必须由 LLM 做，绝不在这里写规则去猜。**
任何"用关键词匹配猜是不是名篇"的做法都会退化成指纹库，且误报率高。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

# 判定等级
MATCHED = "matched"        # 确认是已有作品
PARTIAL = "partial"        # 疑似／部分为已有作品（节选、混排、改写）
NONE = "none"              # 未识别为已有作品

CONFIDENCE_LEVELS = ("high", "medium", "low")

REQUIRED_KEYS = ("match", "confidence", "evidence")

SCHEMA_HINT = """{
  "match": "matched | partial | none",
  "confidence": "high | medium | low",
  "work": {"title": "作品名", "author": "作者", "era": "年代（可空）",
           "type": "modern_essay|classical_poem|web_novel|..."},
  "coverage": "该作品占送检文本的比例（0-1，估计值；整体照搬填 1.0，无法估计填 null）",
  "evidence": ["判断依据1", "判断依据2"],
  "distinguishing_line": "最能确认出处的一句原文",
  "reproduction_note": "用户应了解的版权/合规提示",
  "verify_note": "核验建议（medium/low 置信度时必须写清楚缺什么）"
}"""


def normalize(raw: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """校验并规范化 agent 传来的判断结果。返回 None 表示未提供判断。"""
    if not raw:
        return None
    if not isinstance(raw, dict):
        raise ValueError("existing_work 判断结果必须是对象（dict）")
    missing = [k for k in REQUIRED_KEYS if k not in raw]
    if missing:
        raise ValueError(
            f"existing_work 判断结果缺少必填字段 {missing}。schema：\n{SCHEMA_HINT}")
    match = str(raw.get("match", "")).strip()
    if match not in (MATCHED, PARTIAL, NONE):
        raise ValueError(f"match 必须是 {MATCHED}/{PARTIAL}/{NONE} 之一，收到：{match!r}")
    conf = str(raw.get("confidence", "")).strip()
    if conf not in CONFIDENCE_LEVELS:
        raise ValueError(f"confidence 必须是 {CONFIDENCE_LEVELS} 之一，收到：{conf!r}")
    ev = raw.get("evidence") or []
    if isinstance(ev, str):
        ev = [ev]
    ev = [str(x) for x in ev if str(x).strip()]

    out = {
        "match": match,
        "confidence": conf,
        "work": raw.get("work") or {},
        "coverage": raw.get("coverage"),
        "evidence": ev,
        "distinguishing_line": str(raw.get("distinguishing_line") or ""),
        "reproduction_note": str(raw.get("reproduction_note") or ""),
        "verify_note": str(raw.get("verify_note") or ""),
    }
    out["warnings"] = validate(out)
    return out


def validate(j: Dict[str, Any]) -> List[str]:
    """兜底校验：判断与自身的自洽性。返回警告清单（不改变判定，只提示）。"""
    warns: List[str] = []
    work = j.get("work") or {}

    if j["match"] in (MATCHED, PARTIAL):
        if not work.get("title"):
            warns.append("判定为命中已有作品，但未填写作品名 —— 报告无法提示用户具体出处。")
        if not j["evidence"]:
            warns.append("判定为命中已有作品，但未给出任何判断依据 —— 用户无法复核。")
        if j["confidence"] == "low":
            warns.append(
                "置信度为 low 却判定为命中：请在报告中明确标注「疑似」，"
                "不要用断言语气向用户宣称这是已有作品。")
        if j["confidence"] != "high" and not j.get("verify_note"):
            warns.append("置信度不是 high，必须给出核验建议（verify_note），否则用户无法判断该不该信。")
    if j["match"] == NONE and j["confidence"] == "high":
        warns.append(
            "判定为「未识别为已有作品」且置信度 high —— 这是过度自信。"
            "模型知识有截止日期、也有盲区；请把置信度降到 medium，"
            "并在报告中说明「未识别 ≠ 原创」。")

    cov = j.get("coverage")
    if isinstance(cov, (int, float)):
        if cov > 1.0 or cov < 0.0:
            warns.append(f"coverage 应在 0–1 之间，收到 {cov}。")
        if j["match"] == NONE and cov > 0.3:
            warns.append("判定为未命中，但 coverage > 0.3 —— 两者矛盾，请复核。")
    return warns


def banner_model(j: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """把判断结果转成报告渲染用的视图模型（含配色与文案级别）。"""
    if not j:
        return None
    work = j.get("work") or {}
    level = j["match"]
    view = {
        "level": level,
        "confidence": j["confidence"],
        "title": work.get("title") or "",
        "author": work.get("author") or "",
        "era": work.get("era") or "",
        "type": work.get("type") or "",
        "coverage": j.get("coverage"),
        "evidence": j.get("evidence") or [],
        "distinguishing_line": j.get("distinguishing_line") or "",
        "reproduction_note": j.get("reproduction_note") or "",
        "verify_note": j.get("verify_note") or "",
        "warnings": j.get("warnings") or [],
    }
    if level == MATCHED:
        view["tone"] = "danger" if j["confidence"] == "high" else "warn"
        view["headline"] = ("本文为已有作品" if j["confidence"] == "high"
                            else "本文疑似已有作品")
    elif level == PARTIAL:
        view["tone"] = "warn"
        view["headline"] = "本文包含已有作品内容（疑似节选／混排／改写）"
    else:
        view["tone"] = "ok"
        view["headline"] = "已做过已有作品识别：未识别为已有作品"
    return view
