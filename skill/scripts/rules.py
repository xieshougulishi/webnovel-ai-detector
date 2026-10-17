# -*- coding: utf-8 -*-
"""
rules —— 规则证据层（第二层）与计分（第三层）。

每条规则都必须产出：
  - 可定位的字符区间（start/end，指向原文）
  - 观测值 vs 人类基线
  - 为什么这条像 AI（机制解释，不是"特征异常"这种废话）
  - 怎么改（可执行动作）

计分公式全部公开，与 DESIGN.md §4 一致。
"""
from __future__ import annotations

import math
import re
from collections import Counter
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from textlib import (Paragraph, Span, count_han, cv, mean, quantile,
                     split_paragraphs, variance)

# --------------------------------------------------------------------------
# 规则定义
# --------------------------------------------------------------------------

CATEGORIES = {
    "rhythm":    {"cn": "节奏与句法", "risk": True,  "norm": 34.0},
    "lexical":   {"cn": "用词与套话", "risk": True,  "norm": 40.0},
    "rhetoric":  {"cn": "修辞与模板", "risk": True,  "norm": 26.0},
    "punct":     {"cn": "标点习惯",   "risk": True,  "norm": 18.0},
    "dialogue":  {"cn": "对白质感",   "risk": True,  "norm": 20.0},
    "emotion":   {"cn": "情绪与描写", "risk": True,  "norm": 30.0},
    "structure": {"cn": "结构与闭合", "risk": True,  "norm": 24.0},
    # 逻辑一致性：**唯一由模型（LLM）判断、而非统计推断的分类**。
    # 归一常数设得很小，因为逻辑漏洞是"要么有要么没有"的硬证据，
    # 不需要像文体特征那样依赖命中的多寡。
    "logic":     {"cn": "逻辑一致性", "risk": True,  "norm": 10.0},
    "openness":  {"cn": "留白与野性", "risk": False, "norm": 16.0},
}

# ==========================================================================
# 规则档位（解决"权重平均化"）
# ==========================================================================
# 问题：早期所有规则的有效权重（严重度 × 可信度）只差 4.9 倍，
# 而且**排错了**——实测会误报的启发式（RHY_SENT_CV，权重 2.40）
# 反而高于已证实的强信号（RHE_SIMILE，权重 1.98）。
# 后果：一条"套话密度 19 倍"的压倒性证据，被一堆弱启发式摊平。
#
# 分档依据是 **calibration.md 里的实测记录**，不是主观感觉：
#   A 档（实测能区分人写与 AI，几乎可作确定性证据）→ ×2.0
#   B 档（有效性中等，需与其它信号并发）→ ×1.0
#   C 档（受长度/题材干扰大、实测出现过误报或零区分度）→ ×0.4
# 未列出的默认 B 档。
TIER_MULT = {"A": 2.0, "B": 1.0, "C": 0.4}

TIER_MAP = {
    # ---- A：实测区分度最好 ----
    "LEX_CLICHE": "A",          # 人写 0 / 千字 vs AI 22 / 千字
    "RHE_PHRASE_REPEAT": "A",   # 人写 0–4 vs AI 28（逐字重复自己）
    "STR_SUMMARY": "A",         # 人写 1.5 以下 vs AI 4–14
    "RHE_ANTITHESIS": "A",      # 同一段反复用同一种对比结构
    "RHE_SIMILE": "A",          # 人写 0–3 vs AI 10.8
    "STR_PARA_TAIL": "A",       # 段段收尾升华，对应"完整闭合"偏好

    # ---- C：实测不可靠 / 受干扰大 ----
    "RHY_SENT_CV": "C",         # 人写武侠文学向实测 0.415，低于阈值 0.45 → 误报
    "RHY_SENT_UNIFORM": "C",    # 启发式，与上者高度相关
    "RHY_CLAUSE_CV": "C",       # 未单独验证，且与上两者同源
    "RHY_PARA_LEN": "C",        # 纯排版，题材差异大
    "RHY_SENT_PER_PARA": "C",   # 需为"一句一段"打补丁，本身就不稳
    "STR_ONE_LINE": "C",        # 纯排版，后期可调
    "PUNCT_ENTROPY": "C",       # 实测真人 0.21–0.30 / AI 0.17–0.18，**零区分度**
    "LEX_CHAR_TTR": "C",        # 随文本长度单调变化，不适合作跨样本比较
    "DLG_RATIO_LOW": "C",       # 题材依赖强（无限流/悬疑本就少对白）
}

# 题材自适应：只放宽基线，绝不收紧（见 DESIGN.md §5）
## 系数约定（**方向很重要，容易写反**）：
#   系数统一按 `基线/阈值 × 系数` 计算。
#   · "high" 方向规则（越大越可疑，如套话密度）：系数 >1 表示基线抬高 → 更难触发 → **放宽**
#   · "low"  方向规则（越小越可疑，如句长变异系数）：系数 <1 表示阈值降低 → 更难触发 → **放宽**
# 所以同一个"放宽"意图，两类规则要用相反方向的数字。
# 早期版本把 RHE_SIMILE（高方向词表规则）写成 0.70，本意是放宽，实际是收紧——方向写反了。
# 另外早期版本里词表类规则根本没吃到这个表（PatternRule 没调用 genre_factor），
# 两处一起修，题材自适应才真正生效。
GENRE_ADJUST = {
    # 系统流：句式天然重复、段末爱收尾；古风/玄幻用词偏文
    "system":    {"RHY_SENT_CV": 0.70, "STR_PARA_TAIL": 1.80,
                  "RHY_SENT_UNIFORM": 0.70, "LEX_CLICHE": 1.20},
    # 仙侠/修真：成语与比喻密度天然偏高
    "xianxia":   {"PUNCT_ENTROPY": 0.80, "RHE_SIMILE": 1.40, "LEX_CLICHE": 1.50},
    "wuxia":     {"PUNCT_ENTROPY": 0.85, "RHE_SIMILE": 1.30, "LEX_CLICHE": 1.40},
    "suspense":  {"LEX_EMOTION": 1.30},
    "apocalypse":{"RHY_SENT_CV": 0.80, "STR_PARA_TAIL": 0.80},
    "infinite":  {"RHY_SENT_CV": 0.80, "STR_PARA_TAIL": 0.80},
    "ancient_romance": {"LEX_CLICHE": 1.50, "PUNCT_ENTROPY": 0.85},
    "urban":     {},
    "unknown":   {},
}


class Rule:
    def __init__(self, rid: str, category: str, title: str, severity: int,
                 confidence: float, explain: str, fix: str,
                 baseline_desc: str = ""):
        self.id = rid
        self.category = category
        self.title = title
        self.severity = severity          # 1..5
        self.confidence = confidence      # 0..1
        self.explain = explain
        self.fix = fix
        self.baseline_desc = baseline_desc
    def evaluate(self, ctx: "RuleContext") -> Optional[Dict[str, Any]]:
        raise NotImplementedError

    # ---- 计分 ----
    def contribution(self, hits: int, excess_ratio: float) -> float:
        """单条规则得分。

        分两段：
          1. **正常区间**（超额 ≤4 倍）：线性计分，封顶 3 倍。
             封顶的理由是防止单一极端值主导结论。
          2. **极端区间**（超额 >4 倍）：额外给**对数加成**，封顶 +1.5。
             理由：原来的封顶会让「套话密度是基线的 4 倍」和「19 倍」拿到同一个分数，
             而后者其实是压倒性的证据——一个作者不可能自然地写出 19 倍的套话密度。

        再乘一个**档位系数 TIER_MULT**（见 TIER_MAP）：
        这一项解决"权重平均化"——实测可区分的信号（A 档）应该比
        未经验证的启发式（C 档）重得多，否则一条强信号会被一堆弱信号摊平。

        所有这些都**只影响计分**，不改规则的严重度、可信度与基线。
        """
        lin = min(max(excess_ratio - 1.0, 0.0), 3.0)
        extra = 0.0
        if excess_ratio > 4.0:
            extra = min(0.8 * math.log2(excess_ratio / 4.0), 1.5)
        tier_mult = TIER_MULT.get(TIER_MAP.get(self.id, "B"), 1.0)
        raw = self.severity * self.confidence * (lin + extra) * tier_mult
        sat = 1.0 - math.exp(-hits / 1.5)
        return raw * sat


class ThresholdRule(Rule):
    """量化阈值型规则：观测值越过人类基线阈值即计分。"""

    def __init__(self, rid, category, title, severity, confidence, metric: str,
                 direction: str, threshold: float, baseline: float,
                 explain, fix, unit: str = "", baseline_desc: str = "",
                 span_limit: int = 0, evidence_note: str = ""):
        super().__init__(rid, category, title, severity, confidence, explain, fix, baseline_desc)
        self.metric = metric
        self.direction = direction      # "high" 越大越可疑 / "low" 越小越可疑
        self.threshold = threshold
        self.baseline = baseline
        self.unit = unit
        self.span_limit = span_limit
        self.evidence_note = evidence_note

    def evaluate(self, ctx: "RuleContext") -> Optional[Dict[str, Any]]:
        v = ctx.metric(self.metric)
        if v is None:
            return None
        adj = ctx.genre_factor(self.id)
        thr = self.threshold * adj
        base = self.baseline * adj
        if self.direction == "high":
            if v <= thr:
                return None
            ratio = (v / thr) if thr > 0 else 1.0
        else:
            if v >= thr:
                return None
            ratio = (thr / v) if v > 0 else 3.0
        hits = max(int(round(ratio * 2)), 1)
        return {
            "observed": round(v, 4),
            "baseline": round(base, 4),
            "threshold": round(thr, 4),
            "unit": self.unit,
            "excess_ratio": round(ratio, 4),
            "hits": hits,
            "sample": None,
            "evidence_note": self.evidence_note,
        }


class PatternRule(Rule):
    """词表/正则命中型规则：按每千字密度与人类基线比。"""

    def __init__(self, rid, category, title, severity, confidence, extractor: Callable,
                 baseline_per_k: float, explain, fix, baseline_desc: str = "",
                 max_samples: int = 8, unit: str = "次/千字"):
        super().__init__(rid, category, title, severity, confidence, explain, fix, baseline_desc)
        self.extractor = extractor
        self.baseline_per_k = baseline_per_k
        self.max_samples = max_samples
        self.unit = unit

    def evaluate(self, ctx: "RuleContext") -> Optional[Dict[str, Any]]:
        hits, spans = self.extractor(ctx)
        if hits <= 0:
            return None
        han = max(count_han(ctx.text), 1)
        # v1.3 修正：**密度会被短文本放大**。
        # 531 字的稿子里出现 3 个「仿佛/一股」，密度就是 5.6/千字，
        # 看起来"远超基线"，但其实只是三个自然用词。
        # 所以除了密度，还要求**绝对命中数**够多：短文本至少要 4 次，
        # 长文本按每千字 2 次递增。这条拦截掉了短文本上的密度虚高误报。
        min_hits = max(4, int(round(han / 1000.0 * 2)))
        if hits < min_hits:
            ctx.observe(self.id, {"observed": round(hits * 1000.0 / han, 3),
                                  "baseline": round(self.baseline_per_k, 3),
                                  "hits": hits, "unit": self.unit,
                                  "note": f"命中 {hits} 次，低于最低要求 {min_hits} 次（防短样本密度虚高）"})
            return None
        dens = hits * 1000.0 / han
        # v1.2 修正：题材系数此前只作用于 ThresholdRule，**词表类规则完全没吃到**，
        # 等于"题材自适应"对套话/情绪/身体反应/连接词/比喻/排比这些最依赖题材的规则一直没生效。
        base = self.baseline_per_k * ctx.genre_factor(self.id)
        if dens <= base:
            # 密度未超基线：记为"观察项"，不计分但在报告里展示
            ctx.observe(self.id, {"observed": round(dens, 3), "baseline": round(base, 3),
                                  "hits": hits, "unit": self.unit})
            return None
        ratio = dens / base if base > 0 else 1.0
        return {
            "observed": round(dens, 3),
            "baseline": round(base, 3),
            "threshold": round(base, 3),
            "unit": self.unit,
            "excess_ratio": round(ratio, 4),
            "hits": hits,
            "sample": spans[0] if spans else None,
            "samples": spans[: self.max_samples],
            "by_word": None,
        }


# --------------------------------------------------------------------------
# 规则上下文
# --------------------------------------------------------------------------

class RuleContext:
    def __init__(self, text: str, feat: Dict[str, Any], lex: Dict[str, Any],
                 genre: str = "unknown"):
        self.text = text
        self.feat = feat
        self.lex = lex
        self.genre = genre
        self.paras: List[Paragraph] = split_paragraphs(text, min_len=0)
        self.observations: Dict[str, Any] = {}
        self._cache: Dict[str, Any] = {}

    # ---- 指标取值（虚线路径，如 "sentence.cv_han"）----
    def metric(self, path: str) -> Optional[float]:
        """取指标值。

        支持两种写法：
          1. 特征字典的虚线路径 —— "sentence.cv_han"
          2. **模块内计算函数** —— "_dialogue_ratio"（以下划线开头）
             这类指标由本模块的函数在调用时现算（可能要跨段统计），
             不能放进特征字典。

        v1.1 修正：早期版本只实现了第 1 种，导致所有以 "_" 命名的指标
        永远返回 None，**7 条规则因此从未生效过**（对白占比、五感分布、
        小句变异系数、段末总结率等）。这是"规则看起来在表里、实际是死代码"的典型。
        现在下划线路径会被解析成本模块的可调用对象。
        """
        if path.startswith("_"):
            fn = globals().get(path)
            if callable(fn):
                try:
                    return float(fn(self))
                except Exception:
                    return None
            return None
        cur: Any = self.feat
        for part in path.split("."):
            if isinstance(cur, dict) and part in cur:
                cur = cur[part]
            else:
                return None
        return float(cur) if isinstance(cur, (int, float)) else None

    def genre_factor(self, rule_id: str) -> float:
        adj = GENRE_ADJUST.get(self.genre, {})
        return float(adj.get(rule_id, 1.0))

    def observe(self, key: str, payload: Dict[str, Any]) -> None:
        self.observations[key] = payload


# --------------------------------------------------------------------------
# 各类规则的抽取器
# --------------------------------------------------------------------------

def _word_extractor(lex_key: str):
    def fn(ctx: RuleContext):
        words = ctx.lex.get(lex_key, {}).get("words", [])
        spans = []
        total = 0
        for w in words:
            start = 0
            while True:
                i = ctx.text.find(w, start)
                if i < 0:
                    break
                spans.append({"start": i, "end": i + len(w), "text": w, "word": w})
                total += 1
                start = i + 1
                if len(spans) > 600:
                    break
            if len(spans) > 600:
                break
        spans.sort(key=lambda s: s["start"])
        return total, spans
    return fn


def _regex_extractor(lex_key: str):
    def fn(ctx: RuleContext):
        spans = []
        for pat in ctx.lex.get(lex_key, {}).get("regex", []):
            try:
                rx = re.compile(pat)
            except re.error:
                continue
            for m in rx.finditer(ctx.text):
                spans.append({"start": m.start(), "end": m.end(),
                              "text": m.group(0), "pattern": pat})
                if len(spans) > 600:
                    break
        spans.sort(key=lambda s: s["start"])
        return len(spans), spans
    return fn


def _extract_antithesis(ctx: RuleContext):
    return _regex_extractor("antithesis_pattern")(ctx)


def _extract_simile(ctx: RuleContext):
    return _regex_extractor("simile_template")(ctx)


def _extract_parallelism(ctx: RuleContext):
    """排比：三个以上字数相近（±3 字）的短句连排。比纯正则准。"""
    spans = []
    for p in ctx.paras:
        sents = p.sentences
        i = 0
        while i + 2 < len(sents):
            trio = sents[i:i + 3]
            lens = [count_han(s.text) for s in trio]
            if all(2 <= x <= 12 for x in lens) and (max(lens) - min(lens)) <= 3:
                spans.append({"start": trio[0].start, "end": trio[2].end,
                              "text": ctx.text[trio[0].start:trio[2].end],
                              "pattern": "三连短句等长"})
                i += 3
            else:
                i += 1
    return len(spans), spans


def _extract_artifact(ctx: RuleContext):
    hits = ctx.feat.get("artifact", {}).get("hits", [])
    return len(hits), hits


def _to_span(x: Dict[str, Any]) -> Dict[str, Any]:
    return {"start": x["start"], "end": x["end"], "text": x.get("text", "")}


# --------------------------------------------------------------------------
# 算「句中停顿密度」：AI 爱写长串逗号连缀，真人更爱短句或直接断开
# --------------------------------------------------------------------------

def _comma_density(ctx: RuleContext) -> float:
    t = ctx.text
    han = max(count_han(t), 1)
    return (t.count("，") + t.count("、")) * 1000.0 / han


def _sentence_cv(ctx: RuleContext) -> float:
    return ctx.metric("sentence.cv_han") or 0.0


def _clause_len_cv(ctx: RuleContext) -> float:
    """小句（逗号分隔）长度的变异系数：比句长更敏感。"""
    key = "_clause_cv"
    if key in ctx._cache:
        return ctx._cache[key]
    lengths = []
    for p in ctx.paras:
        buf = 0
        for ch in p.text:
            if ch in "，、；：":
                if buf > 0:
                    lengths.append(buf)
                buf = 0
            elif ch in "。！？…":
                if buf > 0:
                    lengths.append(buf)
                buf = 0
            elif "\u4e00" <= ch <= "\u9fff":
                buf += 1
        if buf > 0:
            lengths.append(buf)
    v = cv(lengths) if len(lengths) >= 8 else 1.0
    ctx._cache[key] = v
    return v


def _dialogue_ratio(ctx: RuleContext) -> float:
    return ctx.metric("length.dialogue_char_ratio") or 0.0


def _tag_variety(ctx: RuleContext) -> float:
    return ctx.metric("dialogue.tag_variety") or 0.0


def _sensory_imbalance(ctx: RuleContext) -> float:
    """嗅觉+味觉+触觉 占比低 = 描写偏视觉，AI 典型。返回三感占比之和。"""
    share = ctx.feat.get("sensory", {}).get("share", {})
    return float(share.get("smell", 0) + share.get("taste", 0) + share.get("touch", 0))


def _human_voice_per_k(ctx: RuleContext) -> float:
    return ctx.metric("human_voice.per_k") or 0.0


def _particle_per_k(ctx: RuleContext) -> float:
    d = ctx.feat.get("human_voice", {}).get("detail", {}).get("sentence_particles", {})
    return float(d.get("per_k", 0.0))


def _profanity_per_k(ctx: RuleContext) -> float:
    d = ctx.feat.get("human_voice", {}).get("detail", {}).get("profanity", {})
    return float(d.get("per_k", 0.0))


def _colloquial_per_k(ctx: RuleContext) -> float:
    d = ctx.feat.get("human_voice", {}).get("detail", {}).get("colloquial", {})
    return float(d.get("per_k", 0.0))


def _de_nonstandard_rate(ctx: RuleContext) -> float:
    """状语「的/地」不规范率 = 不规范 / (规范+不规范)。越高越像人。"""
    prof = ctx.feat.get("de_di_de", {})
    de_adv = prof.get("de_adv_nonstandard", 0)
    di_std = prof.get("di_adv_standard", 0)
    denom = de_adv + di_std
    if denom == 0:
        return 0.0
    return de_adv / denom


def _para_tail_conclusion(ctx: RuleContext) -> float:
    """段末「总结/升华」比率：段末小句里含总结词或金句模式。"""
    key = "_tail_conc"
    if key in ctx._cache:
        return ctx._cache[key]
    markers = ["这一刻", "或许", "也许", "这就是", "这便是", "注定", "从此", "终究",
               "无论如何", "某种意义上", "不知不觉", "而这", "答案", "真相", "命运",
               "但无论如何", "至少", "终究是", "都会", "才明白", "才知道"]
    tail_hit = 0
    for p in ctx.paras:
        if not p.text:
            continue
        tail = p.text[-24:]
        if any(m in tail for m in markers):
            tail_hit += 1
    v = tail_hit / max(len(ctx.paras), 1)
    ctx._cache[key] = v
    return v


def _para_len_cv(ctx: RuleContext) -> float:
    return ctx.metric("paragraph.cv_chars") or 0.0


def _sent_per_para_cv(ctx: RuleContext) -> float:
    key = "_spp_cv"
    if key in ctx._cache:
        return ctx._cache[key]
    counts = [len(p.sentences) for p in ctx.paras if p.sentences]
    v = cv(counts) if len(counts) >= 6 else 1.0
    ctx._cache[key] = v
    return v


def _one_line_ratio(ctx: RuleContext) -> float:
    """单行短段（≤25 显示字符）占比。网文对话段多是正常现象，故基线宽容。"""
    return ctx.metric("paragraph.one_line_ratio") or 0.0


def _dialogue_para_ratio(ctx: RuleContext) -> float:
    """以引号开头的段落占比（对白段密度）。

    人类写对话密集的场景时会插入**动作节拍**（他喝了口水／她别过头），
    所以纯对白段落占比很少超过 30%；实测人写样本 12.5%–26.3%。
    AI 生成对话容易变成**纯乒乓对话**——问一句答一句，中间没有任何动作或环境描写，
    整章对白段落能占到 70% 以上（实测一份早期 AI 稿件 70.6%，48/68 段）。
    """
    key = "_dlg_para_ratio"
    if key in ctx._cache:
        return ctx._cache[key]
    ps = [p for p in ctx.paras if p.char_len > 0]
    if not ps:
        ctx._cache[key] = 0.0
        return 0.0
    d = sum(1 for p in ps if p.text.lstrip().startswith(("“", "「", "『", '"')))
    v = d / len(ps)
    ctx._cache[key] = v
    return v


def _phrase_repeat_per_k(ctx: RuleContext) -> float:
    """5 字片段出现 ≥2 次的**超额重复密度**（每千字）。

    测的是 AI 反复用同一句原话描述同一个东西：
    实测一份明显的 AI 打斗场景里，「眼神坚定」出现 3 次、「整个虚空都在颤抖着」2 次、
    「震耳欲聋」2 次——人类写手会换词，模型不会。

    长度效应说明：把**不同**人写文本拼成 1883 字，密度仅 3.72/千字；
    把不同 AI 文本拼成 2233 字，密度 17.02/千字。人写随长度上升很慢，AI 上升很快。
    阈值取 15 是为了只抓**极端**重复，宁可漏也不要误伤长文本的正常重复。
    """
    key = "_rep5"
    if key in ctx._cache:
        return ctx._cache[key]
    from textlib import han_only
    h = han_only(ctx.text)
    n = 5
    if len(h) < n * 4:
        ctx._cache[key] = 0.0
        return 0.0
    c = Counter(h[i:i + n] for i in range(len(h) - n + 1))
    rep = sum(v - 1 for v in c.values() if v >= 2)
    v = rep * 1000.0 / max(len(h), 1)
    ctx._cache[key] = v
    return v


# --------------------------------------------------------------------------
# 规则表
# --------------------------------------------------------------------------

def build_rules() -> List[Rule]:
    R: List[Rule] = []

    # ================= A. 节奏与句法 =================
    R.append(ThresholdRule(
        "RHY_SENT_CV", "rhythm", "句子长度过于均匀（节奏没有起伏）", 4, 0.60,
        metric="sentence.cv_han", direction="low", threshold=0.45, baseline=0.45,
        unit="变异系数", baseline_desc="真人网文句长变异系数常见区间 0.45–1.1",
        explain=(
            "这是 AI 文本最稳定的指纹。人类写作时，情绪一上来会突然甩出一句两字短句，"
            "交代环境时又会写一句四十字长句——句长忽长忽短，变异系数（标准差/均值）通常大于 0.5。"
            "语言模型逐步挑高概率词，句长被\"平均值\"拉住，落在一个很窄的带子里，"
            "变异系数常常只有 0.25–0.45。"
            "通俗讲：**读起来每句话都差不多长，像用尺子量过的。**"),
        fix=(
            "① 找连续 3 句长度接近的地方，把中间那句砍到 4 字以内或拉长到 30 字以上；"
            "② 在情绪爆发点插入独立成段的极短句（如「他愣住了。」单独一段）；"
            "③ 交代场景时允许写一个长复句，不要每句都收在同一个呼吸长度上。"
            "目标是把变异系数拉回 0.55 以上。"),
        evidence_note="这不是「文笔好坏」问题，是节奏分布问题。"))

    R.append(ThresholdRule(
        "RHY_SENT_UNIFORM", "rhythm", "绝大多数句子落在同一个长度带内", 3, 0.55,
        metric="sentence.iqr", direction="low", threshold=6.0, baseline=6.0,
        unit="字（四分位距）", baseline_desc="真人常见 IQR ≥ 6 字（启发式，见规则说明）",
        explain=(
            "四分位距（IQR）衡量中间 50% 句子的长度跨度。人类文本里中间一半句子的长度"
            "通常横跨 8 字以上；AI 文本常常只有 3–6 字，也就是**大部分句子长得几乎一样**。"
            "这条与句长变异系数互为佐证，两条同时命中时判定可靠性显著提高。"
            "**注意**：这是一条启发式规则，依据是节奏分布理论，而非大规模真人语料实测，"
            "权重已相应压低；单独命中不足以构成嫌疑。"),
        fix="同 RHY_SENT_CV：主动制造长短句落差。把两个连续的 15 字句改成「3 字 + 35 字」。"))

    R.append(ThresholdRule(
        "RHY_CLAUSE_CV", "rhythm", "小句（逗号分隔的片段）长度同样均匀", 3, 0.60,
        metric="_clause_len_cv", direction="low", threshold=0.50, baseline=0.50,
        unit="变异系数", baseline_desc="真人常见 ≥ 0.5",
        explain=(
            "比句长更细一层：把逗号也当作断点后，每个小句的长度分布。"
            "AI 常写出\"四字，四字，五字，四字\"这种均匀推进的节奏，人写则会"
            "\"他，一个在工地搬了七年砖的人，忽然有了钱\"——小句长度极不均。"
            "这条命中说明均匀性不只停在句子层面，已经渗进了词组层面。"),
        fix="在逗号链里插入一个明显更长或更短的小句，打乱等长推进。"))

    # 【已移除】RHY_PARA_HEAD（段落开头反复用同样的两字起手）
    # 移除原因：这条规则前后修过四次，每次都还有新的误报源——
    #   ① 双人对白段落必然交替以角色名开头（正常叙事结构）；
    #   ② 中文小说里段落以短词起手是常态（两人/什么/没有/再往/不过）；
    #   ③ 群像文本一个名字都排除不掉（早期的"保险丝"设计本身是错的）。
    # 而它的检出贡献一直很小（连高浓度 AI 样本上也只占 0.1–0.4 分），
    # 因为真正有区分度的词汇信号（套话密度）早早把它盖过去了。
    # 结论：**误报成本 > 检出收益**，整条移除。
    # calibration.md 里写过一句话，按它办："如果一个特征要改四次才能不误报，
    # 它大概不该待在那里。" detect_names() 保留，供日后真正需要时复用。

    R.append(ThresholdRule(
        "RHY_PARA_LEN", "rhythm", "段落长度过于整齐", 2, 0.55,
        metric="paragraph.cv_chars", direction="low", threshold=0.35, baseline=0.35,
        unit="变异系数", baseline_desc="真人常见 ≥ 0.35（启发式）",
        explain=(
            "真人写段落长度极不规整：可能连续三个一行段（对白），忽然来一个三百字的大段"
            "（回忆或打斗）。AI 倾向于每段都写成\"一个完整的小单元\"，长度自然趋同。"
            "这条在对话密集的文本里会自然偏低，因此权重不高。"),
        fix="故意做几处长段和一行段。打斗/回忆可以写成一段长的，紧张处一句一段。"))

    R.append(ThresholdRule(
        "RHY_SENT_PER_PARA", "rhythm", "每段句子数几乎一样（机械切段）", 2, 0.52,
        metric="_sent_per_para_cv", direction="low", threshold=0.40, baseline=0.40,
        unit="变异系数", baseline_desc="真人常见 ≥ 0.4（启发式）",
        explain=(
            "每段包含几句的变异系数。AI 常写成\"每段三句\"的整齐结构，"
            "真人则是一段一句和一段七句混杂。"),
        fix="把某些段落拆成一句一段，或把几段合并成一段长的。"))

    # ================= B. 用词与套话 =================
    R.append(PatternRule(
        "LEX_CLICHE", "lexical", "文艺腔套话密度过高", 5, 0.88,
        extractor=_word_extractor("artistic_cliche"), baseline_per_k=1.2,
        explain=(
            "这是中文 AI 文本**最强的词汇指纹**。模型训练语料里「文学性」文本以抒情散文和"
            "书评影评为重，「仿佛」「一丝」「不易察觉」「缓缓」「微微」「若有所思」这类词的"
            "实际频率远高于长篇网文。模型为了显得\"有文笔\"会主动往这里靠。"
            "真人网文作者写打脸、升级、日常时几乎不用这些词，"
            "密度通常低于每千字 1–2 次；AI 生成常在 5–15 次。"),
        fix=(
            "逐个替换，不要整体润色：\n"
            "· 「仿佛」→ 直接写像什么，或删掉（「仿佛在说」→「像是在说」/ 直接删）\n"
            "· 「一丝/一抹/一缕+情绪」→ 换成具体动作（「一丝不安」→「手在裤缝上蹭了两下」）\n"
            "· 「缓缓/轻轻/微微」→ 换成具体方式（「缓缓抬头」→「脖子一点点抬起来」）\n"
            "· 「不易察觉」→ 删（这个词本身就是 AI 味的重灾区）\n"
            "目标：把这条的每千字密度压到 1.5 以下。"),
        baseline_desc="真人网文常见 ≤ 1.2 次/千字"))

    R.append(PatternRule(
        "LEX_EMOTION", "lexical", "抽象情绪词密集（直接命名情绪）", 4, 0.70,
        extractor=_word_extractor("abstract_emotion"), baseline_per_k=6.0,
        explain=(
            "AI 写情绪的方式是\"命名\"：愤怒、绝望、欣慰、心酸……真人写情绪的方式是"
            "\"表现\"：手抖、嗓子发干、突然想笑、骂了句脏话。"
            "命名式写法读起来\"对\"但不动人，也是检测器最容易抓到的批量特征之一。"),
        fix="每三处抽象情绪词，至少把两处换成身体动作或行为：\n"
            "· 「他感到愤怒」→「他把烟按进了茶杯里」\n"
            "· 「心中一酸」→「他忽然不想说话了」",
        baseline_desc="真人常见 ≤ 6 次/千字"))

    R.append(PatternRule(
        "LEX_BODY", "lexical", "身体反应都是通用库存（瞳孔一缩、心中一紧）", 4, 0.78,
        extractor=_word_extractor("generic_body"), baseline_per_k=5.0,
        explain=(
            "这批词是 AI 写\"人物反应\"时的默认清单。单个词人也会用，"
            "但**成批出现、且反复只用这几个**就非常像机器：真人会写"
            "\"后槽牙咬得发酸\"\"指甲掐进掌心\"\"裤腿上一块湿的\"这种个人化细节。"),
        fix="保留一两个，其余换成具体到生理层面的小动作：牙关、指甲、后背、嗓子、手心、腿软。"
            "越具体越不像 AI，因为具体细节是模型的弱项。",
        baseline_desc="真人常见 ≤ 5 次/千字"))

    R.append(PatternRule(
        "LEX_CONNECTOR", "lexical", "书面连接词密度偏高（文档腔）", 3, 0.72,
        extractor=_word_extractor("written_connector"), baseline_per_k=9.0,
        explain=(
            "「然而」「因此」「于是」「此外」「与此同时」是论文和说明文的连接词。"
            "真人网文更常用口语连接（可、结果、愣是）或干脆不用连接词，"
            "让动作硬接。连接词密度高，读起来像在读报告。"),
        fix="删掉一半连接词，让句子直接相接。中文里动作天然有先后顺序，"
            "不需要「于是」「随后」来提示。",
        baseline_desc="真人常见 ≤ 9 次/千字"))

    R.append(ThresholdRule(
        "LEX_CHAR_TTR", "lexical", "用字丰富度过低", 3, 0.60,
        metric="lexical.char_ttr", direction="low", threshold=0.085, baseline=0.085,
        unit="字种/字数", baseline_desc="2000 字以上文本真人常见 ≥ 0.10",
        explain=(
            "不重复汉字数 ÷ 总汉字数。同样长度下，AI 的用字更集中——"
            "因为它在高概率字上循环。真人会突然用生僻字、地名、器物名、方言字。"
            "注意：这条受文本长度影响大，样本越短比例越高，因此只在 ≥1200 字时启用。"),
        fix="加入具体的专有名词、器物、地名、方言词；避免反复用同一批动词。",
        evidence_note="仅对 ≥1200 字样本启用。"))

    # ================= C. 修辞与模板 =================
    R.append(PatternRule(
        "RHE_ANTITHESIS", "rhetoric", "「不是A而是B」式对偶模板反复出现", 4, 0.80,
        extractor=_extract_antithesis, baseline_per_k=1.0,
        explain=(
            "「不是害怕，而是兴奋」「与其说是愤怒，不如说是失望」——"
            "这是语言模型展示\"思辨感\"的默认句式。单次出现很正常，"
            "但**在同一章里反复使用同一种对比结构**，是模型句式贫乏的直接证据。"),
        fix="整章保留最多一处这种对比句，其余改成正面陈述或直接写行为。"
            "「不是A而是B」的信息量其实很低，多半可以删掉 A 只留 B。",
        baseline_desc="真人常见 ≤ 1 次/千字"))

    R.append(PatternRule(
        "RHE_SIMILE", "rhetoric", "比喻句式模板化（像……一样 / 仿佛……）", 3, 0.66,
        extractor=_extract_simile, baseline_per_k=2.5,
        explain=(
            "真人打比喻是零散的、用词古怪的；AI 打比喻是模板化的，"
            "而且喻体常常抽象得很安全（「像一片落叶」「如同潮水」）。"
            "密度超标时，通常伴随喻体重复。"),
        fix="删掉一半比喻，剩下的换成具体、甚至有点怪的喻体（越私人化越好）。",
        baseline_desc="真人常见 ≤ 2.5 次/千字"))

    R.append(PatternRule(
        "RHE_PARALLEL", "rhetoric", "排比句连排（等长短句三连）", 3, 0.70,
        extractor=_extract_parallelism, baseline_per_k=1.2,
        explain=(
            "三个长度相近的短句连排是模型的\"气势\"模板。"
            "真人写排比通常只在高潮处用一次，AI 会在平铺叙事里反复用。"),
        fix="把排比拆成不等长句子，或只保留一处真正需要气势的地方。",
        baseline_desc="真人常见 ≤ 1.2 次/千字"))

    R.append(ThresholdRule(
        "RHE_PHRASE_REPEAT", "rhetoric", "长片段逐字重复（同一句原话反复用）", 4, 0.62,
        metric="_phrase_repeat_per_k", direction="high", threshold=15.0, baseline=15.0,
        unit="次/千字", baseline_desc="真人常见 ≤15（实测 0–4.2，长文本会自然升高）",
        explain=(
            "模型会**逐字重复自己刚才写过的描述**，因为它在同一个语义空间里采样。"
            "实测一份明显的 AI 打斗场景：「眼神坚定」出现 3 次、"
            "「整个虚空都在颤抖着」2 次、「震耳欲聋」2 次、"
            "「挥剑迎击」2 次。人类写手重复时会换词——"
            "「眼神坚定」第二次可能写成「她没眨眼」，第三次可能只写「握紧了剑」。"
            "**逐字重复同一句，是最不像人的地方。**"),
        fix=(
            "把重复出现的描述逐处换说法：\n"
            "· 「眼神坚定」→「她没眨眼」/「握剑的指节发白」/ 直接删\n"
            "· 「震耳欲聋」→ 写具体的声音（「耳朵里嗡了一下」）\n"
            "· 「整个虚空都在颤抖」→ 换主体（「脚下的石台裂了缝」）\n"
            "同一个意思在一段里最多出现一次。"),
        evidence_note="阈值按 500–8000 字校准；长文本上该指标会自然升高。"))

    # ================= D. 标点习惯 =================
    R.append(ThresholdRule(
        "PUNCT_ENTROPY", "punct", "标点分布高度收敛（归一化熵极低）", 2, 0.45,
        metric="punct.entropy_norm", direction="low", threshold=0.14, baseline=0.14,
        unit="归一化熵", baseline_desc="真人常见 ≥0.14（**弱规则**，单独命中不构成嫌疑）",
        explain=(
            "把标点分成十一类后算分布的归一化熵，衡量「标点用得有多杂」。"
            "**这条规则经过实测下调**：原以为它是强信号，"
            "但实测发现真人样本熵值 0.21–0.30、AI 样本 0.17–0.18，"
            "两者严重重叠，阈值 0.6 会让所有文本都命中——已降到 0.14 并标为弱规则。"
            "真正有区分度的是下面那条「逗号+句号占比」。"),
        fix="有意识地使用破折号（打断）、省略号（语塞）、问号（自问）、顿号（并列）、"
            "括号（补充）。不要为了凑而滥用——用法要贴合语义。",
        evidence_note="弱规则。标点分布在小样本上不稳定，仅对 ≥1200 字且标点种类 ≥4 的文本评分。"))

    R.append(ThresholdRule(
        "PUNCT_ELLIPSIS", "punct", "省略号滥用（用……冒充留白）", 3, 0.62,
        metric="punct.ellipsis_run_per_k", direction="high", threshold=4.0, baseline=4.0,
        unit="次/千字", baseline_desc="真人常见 ≤ 4 次/千字",
        explain=(
            "AI 爱用「……」表示\"意味深长\"。真人用省略号通常真的有话没说完，"
            "频率低且有具体语境。密度超标说明在用省略号替代内容。"),
        fix="把大部分省略号改成句号，或者真的把没说完的话写出来。"))

    R.append(ThresholdRule(
        # 注意分类：这是一条**反向规则（人味证据）**，必须挂在 openness 下。
        # v1.1 修正：此前它被误挂在 punct（风险分类）下，
        # 于是"半角标点"这条人味证据反而**给作者加风险分**（实测加了 16.5 分），
        # 与规则自身的文案完全相反。这类"分类挂错"的 bug 不会报错，只会让分数方向反了。
        "PUNCT_HALFWIDTH", "openness", "中英标点混用/半角标点（人味证据）", 3, 0.62,
        metric="punct.halfwidth_ratio", direction="high", threshold=0.06, baseline=0.06,
        unit="半角占标点比", baseline_desc="真人网文常见 0.06–0.30；AI 输出几乎为 0",
        explain=(
            "中文输入法打出的标点一律是全角。文本里混进半角逗号、句号、惊叹号、"
            "甚至 ASCII 直引号 `\"`，"
            "只可能来自**手动敲击、复制粘贴、不同来源拼接**这些人类行为。"
            "语言模型输出几乎 100% 使用规范全角标点。"
            "注意：这条只在**半角标点与中文混排**时成立；纯英文段落不算。"),
        fix="（这是反向项——人味证据，不需要修改。）",
        evidence_note="反向项：命中说明更像人写的，会从风险分里抵扣。"))

    R.append(ThresholdRule(
        "PUNCT_CONCENTRATION", "punct", "标点分布高度集中（几乎只有逗号句号）", 4, 0.70,
        metric="punct.comma_period_share", direction="high", threshold=0.88, baseline=0.88,
        unit="逗号+句号占比", baseline_desc="真人常见 ≤0.88（样本 ≥1200 字时才评）",
        explain=(
            "把标点分成八类后，统计「逗号 + 句号」占全部标点的比例。"
            "人类写作时会自然加入问号（自问）、顿号（并列）、破折号（打断）、"
            "分号、括号、省略号，这个比例通常低于 88%；"
            "AI 的标点使用高度收敛，常达 90%–96%。"
            "**这是标点类里唯一有实测区分度的指标**："
            "真人样本 77%–89%，AI 样本 89%–94%，阈值取 88%。"),
        fix="按语义补充其他标点：并列用顿号、打断用破折号、自问用问号、补充说明用括号。"
            "不要为了凑而滥用——用法要贴合语义。",
        evidence_note="仅对 ≥1200 字的样本评分。"))

    # ================= E. 对白质感 =================
    R.append(ThresholdRule(
        "DLG_TAG_VARIETY", "dialogue", "说话人标注过于单调", 3, 0.58,
        metric="dialogue.tag_variety", direction="low", threshold=0.40, baseline=0.40,
        unit="标签种类占比", baseline_desc="真人常见 ≥ 0.4（标注数 ≥ 8 时才评）",
        explain=(
            "「他说道」「她淡淡道」「沉声道」——AI 的对白标注集中在少数几个词上。"
            "真人写作更杂：喊、嘟囔、噎了一下、话没说完，甚至只用动作代替标注。"),
        fix="换标注词；更好的做法是**删掉标注**，用动作或直接换行表示说话人变化。",
        evidence_note="仅在对白占比 > 8% 且标注数 ≥ 8 时启用，否则样本不足。"))

    R.append(ThresholdRule(
        "DLG_RATIO_LOW", "dialogue", "对白占比过低（全篇叙述）", 2, 0.55,
        metric="_dialogue_ratio", direction="low", threshold=0.12, baseline=0.12,
        unit="对白字符占比", baseline_desc="网文常见 0.2–0.5",
        explain=(
            "网文的推进力大量来自对白。AI 生成章节容易一路叙述，"
            "把该用对话交代的信息写成说明。这条权重低，"
            "因为确有以叙述为主的题材（如无限流、悬疑铺垫）。"),
        fix="把交代性叙述改成人物对话：让角色在说话中透露信息，而不是旁白说明。",
        evidence_note="低权重参考项，单独命中不构成嫌疑。"))

    R.append(ThresholdRule(
        "DLG_PINGPONG", "dialogue", "纯对白乒乓（整段问答，无动作节拍）", 3, 0.58,
        metric="_dialogue_para_ratio", direction="high", threshold=0.50, baseline=0.50,
        unit="对白段占全部段落", baseline_desc="人写样本实测 12%–26%，阈值取 50%",
        explain=(
            "人类写对话密集的场景时会**插入动作节拍**——"
            "「他喝了口水」「她别过头去」「他把烟摁灭了」，"
            "所以纯对白段落（整段只有一句引号）很少超过三成。"
            "AI 生成对话容易变成**乒乓对话**：问一句、答一句，"
            "中间没有任何动作、环境或心理描写，整章对白段落能占七成以上。"
            "**纯对白占比过高，本质是「没有身体在场」。**"),
        fix=(
            "在问答之间插入动作或环境：\n"
            "· 「是的，请您跟我来。」→「丧尸转过身，往前走了两步，才想起招呼他：跟我来。」\n"
            "· 每三次问答，至少插入一次动作、环境或心理\n"
            "· 让角色在说话时**同时在做事**（点烟、擦桌子、避开视线）"),
        evidence_note="阈值按人写样本上限（约 26%）留了近一倍余量。"))

    R.append(ThresholdRule(
        "DLG_FLUENCY", "dialogue", "对白过长且结构完整（不像真人说话）", 3, 0.58,
        metric="_dialogue_mean_len", direction="high", threshold=48.0, baseline=48.0,
        unit="字/段对白", baseline_desc="真人常见 ≤ 48 字",
        explain=(
            "真人说话是碎的：抢话、重复、说半截、答非所问。AI 写的对白"
            "一段四五十字还逻辑完整、主谓宾齐全，读起来像在朗读台词。"),
        fix="把长对白拆成两三句，中间插入对方的反应；让角色说错话、重复、跑题。"))

    # ================= F. 情绪与描写 =================
    R.append(ThresholdRule(
        "EMO_SENSORY", "emotion", "五感严重偏视觉，嗅味触觉缺失", 3, 0.62,
        metric="_sensory_imbalance", direction="low", threshold=0.10, baseline=0.10,
        unit="嗅+味+触占比", baseline_desc="真人常见 ≥ 0.10（样本 ≥2000 字时才评）",
        explain=(
            "把描写拆成视、听、嗅、味、触五感后统计占比。AI 极度偏好视觉描写，"
            "对嗅觉、味觉、触觉的使用显著偏少——因为训练语料里这三类描写本就稀疏，"
            "模型不会主动调用。真人写手（尤其写市井、打斗、食物、身体痛苦时）"
            "会自然带入气味和触感。"),
        fix="每章至少加 2 处非视觉描写：气味（油烟、血腥、消毒水）、"
            "触感（黏、烫、粗、凉）、味道（铁锈味、发苦）。这些细节几乎无法被模型模仿。",
        evidence_note="描写总量过少时该指标不稳定，仅对 ≥2000 字的样本评分。"))

    # ================= G. 结构与闭合 =================
    R.append(ThresholdRule(
        "STR_PARA_TAIL", "structure", "段落爱收尾总结（段段都下结论）", 4, 0.70,
        metric="_para_tail_conclusion", direction="high", threshold=0.22, baseline=0.22,
        unit="占比", baseline_desc="真人常见 ≤ 0.22",
        explain=(
            "人类写网的段落经常\"话没说完就断\"，下一段接着走；"
            "AI 倾向让每段都闭合、都留一句意味深长的话。"
            "结果就是**每段结尾都在升华**，读起来很累，也是检测器的强特征。"),
        fix="删掉段末那句总结。多数情况下它不提供新信息，只是作者（模型）在自我确认。"
            "让段落停在动作或对白上，不要停在感慨上。"))

    R.append(PatternRule(
        "STR_SUMMARY", "structure", "总结/升华词高频（这一刻、或许、注定）", 4, 0.72,
        extractor=_word_extractor("summary_habit"), baseline_per_k=1.5,
        explain=(
            "「这一刻」「或许」「注定」「从此以后」这类词是叙事的\"句号\"。"
            "模型偏好把事件收在一个有意义的结论上。真人网文更多是"
            "「他骂了一句，转身走了」——不给意义。"),
        fix="删掉一半这类词。尤其「这一刻」「或许」，在网文里九成是多余的。",
        baseline_desc="真人常见 ≤ 1.5 次/千字"))

    R.append(ThresholdRule(
        "STR_ONE_LINE", "structure", "几乎没有单行短段（缺对话/强调的排版节奏）", 2, 0.50,
        metric="_one_line_ratio", direction="low", threshold=0.25, baseline=0.25,
        unit="占比", baseline_desc="网文常见 ≥ 0.25（启发式）",
        explain=(
            "网文的阅读节奏靠排版：对话一句一段、强调一句一段。"
            "AI 常写出\"三句一段\"的均匀大段，缺少一行一段的呼吸感。"
            "这条是弱证据（排版可后期调整），单独命中不构成嫌疑。"),
        fix="把对白和关键动作拆成独立段落。",
        evidence_note="弱参考项。"))

    # ================= H. 留白与野性（反向：人味证据）=================
    R.append(PatternRule(
        "OPN_COLLOQUIAL", "openness", "口语/俗语用词（人味证据）", 4, 0.70,
        extractor=_word_extractor("human_voice_colloquial"), baseline_per_k=8.0,
        explain="口语词、俚语、粗话是人写作的自然产物，模型默认不会用。密度越高越像人。",
        fix="（这是反向项，不需要修改。）",
        baseline_desc="越高越像人"))

    R.append(PatternRule(
        "OPN_PROFANITY", "openness", "粗话/脏话（强人味证据）", 5, 0.82,
        extractor=_word_extractor("human_voice_profanity"), baseline_per_k=2.0,
        explain=(
            "脏话是**最强的单条人味信号**。RLHF 后的模型被强烈抑制输出脏话，"
            "商用检测器也很少把脏话作为 AI 特征。网文角色骂人是常态，"
            "整章一次脏话都没有，反而值得注意。"),
        fix="（反向项，不需要修改。）如实保留角色该骂的话。",
        baseline_desc="越高越像人"))

    R.append(PatternRule(
        "OPN_PARTICLE", "openness", "语气助词丰富（吧/呢/啊/嘛/嘞）", 3, 0.66,
        extractor=_word_extractor("human_voice_particles"), baseline_per_k=12.0,
        explain="语气助词的分布极度个人化，AI 使用显著偏少且偏集中。",
        fix="（反向项。）",
        baseline_desc="越高越像人"))

    R.append(PatternRule(
        "OPN_NOISE", "openness", "语病/不规范写法（的人味噪声）", 3, 0.60,
        extractor=lambda ctx: _extract_noise(ctx), baseline_per_k=3.0,
        explain=(
            "真人写作有噪声：「的/地」混用、「得/的」混用、重复字、"
            "半角全角混排、多余的逗号。AI 输出几乎总是语法规范的——"
            "**\"太干净\"本身就是可疑信号**。本工具把噪声作为人味证据。"
            "注意：这条不能反过来当作\"有噪声就一定是人\"，AI 也能被刻意加噪声。"),
        fix="（反向项。）不必为了过检测故意写错字。",
        baseline_desc="越高越像人"))

    return R


def _extract_para_head(ctx: RuleContext):
    """段首两字重复。只计**超出正常重复的部分**（每段首次出现不计），
    且排除自动识别的角色名/专名（见 detect_names）。

    重要实测说明：本规则最初想抓的是"用同一批起手词反复开段"（他/她/空气/沉默/片刻），
    但实测发现中文小说里段落以短词起手是常态——「两人」「什么」「没有」「再往」「楼梯」
    这些普通语素在任何真人小说里都会反复出现在段首。所以本规则的权重已降到很低
    （严重度 2、可信度 0.45），且阈值放宽到 2.0/千字：
    **它只在"同一批起手词被机械化重复"时才贡献分数，且贡献很小。**
    真正的节奏类信号应该看句长变异系数与句长四分位距。
    """
    names = detect_names(ctx)
    counter: Counter = Counter()
    for p in ctx.paras:
        t = p.text
        if len(t) < 6:
            continue
        head = t[:2]
        if not all("\u4e00" <= c <= "\u9fff" for c in head):
            continue
        if head in names:
            continue
        counter[head] += 1
    hot = {k: v for k, v in counter.items() if v >= 3}
    if not hot:
        return 0, []
    seen: Counter = Counter()
    spans = []
    for p in ctx.paras:
        if len(p.text) < 6:
            continue
        head = p.text[:2]
        if head not in hot or head in names:
            continue
        seen[head] += 1
        if seen[head] >= 2:          # 第 2 次起才算"重复"
            spans.append({"start": p.start, "end": p.start + 2, "text": head,
                          "word": head})
    # hits 用"超额重复次数"（Σ(v-1)），而不是命中段落数
    excess = sum(v - 1 for v in hot.values())
    return excess, spans


_NAME_STOP = set("""
他们 她们 我们 你们 咱们 这个 那个 这里 那里 这时 那时 此刻 此时 突然 忽然 然后 接着
于是 但是 不过 可是 然而 因为 所以 如果 虽然 只是 就是 还是 已经 正在 可以 没有 什么
怎么 这么 那么 一样 一起 一样 似乎 仿佛 好像 显然 当然 终于 果然 竟然 居然 毕竟 到底
空气 时间 目光 眼神 声音 心里 心中 身上 手中 眼前 身后 面前 旁边 远处 窗外 门口
她的 他的 它的 我的 你的 谁的 别的 真的 似的 似的 似的 似的
""".split())


def detect_names(ctx: "RuleContext", max_names: int = 6):
    """从文本自身自动识别**高频角色名/专有名词**。

    为什么需要：页首两字重复在**双人对白的对话流**里必然发生——
    段落交替以角色名开头。这是人类对话场景的正常写法，绝不能被当成 AI 痕迹。
    检测器必须把自己统计出来的高频专名排除在外，否则会对对话密集的文本产生系统性假阳性。

    判据（两级，第二级是关键）：
      1. **姓氏假说**：某字出现在段落开头的比例（前置边界含换行/句末标点/引号）
         + 后接"说/道"的比例 ≥ 0.25 → 该字是角色名首字的候选。
         这能滤掉"楼梯""教室""一层"这类非人名词。
      2. **频次门槛 + 相对最高频的比例**：角色名会被反复提到，因此要求
         段首出现次数 ≥ 阈值（随段落数自适应：max(3, min(8, 段落数×2%))），
         且不低于最高频名字的 40%。
         这一级用于滤掉"夹克""两人""那人""再往"这类过得了第一级、
         但次数上不去的高频非人名片段。

    历史教训：
      - 早期版本用"识别出 >3 个专名就全部放弃"做保险丝，结果**群像文本一个名字都不排除**
        （实测某三章 5408 字文本，两个主角名各出现 66/68 次，规则仍以 28.1/千字命中、贡献 7.2 分）。
        现改为**按频次排序取前 N 个**，不再整体放弃。
      - 固定门槛（≥8 次）对小样本过高，会让 500 字级别的双人对白文本识别不出人名。
        现改为随段落数自适应。
    """
    key = "_names"
    if key in ctx._cache:
        return ctx._cache[key]

    first_chars = [p.text[0] for p in ctx.paras if p.text and "\u4e00" <= p.text[0] <= "\u9fff"]
    if len(first_chars) < 5:
        ctx._cache[key] = set()
        return set()

    # 第一级：姓氏假说
    boundaries = ("。", "？", "！", "…", "\n", "”", "」", "：")
    ratio: Dict[str, float] = {}
    for ch in set(first_chars):
        before = sum(ctx.text.count(b + ch) for b in boundaries)
        after = ctx.text.count(ch + "说") + ctx.text.count(ch + "道")
        n = ctx.text.count(ch)
        if n <= 0:
            continue
        ratio[ch] = (before + after) / n
    cand_chars = {ch for ch, v in ratio.items() if v >= 0.25}

    # 收集段首候选项及其出现次数
    c: Counter = Counter()
    for p in ctx.paras:
        for L in (2, 3):
            seg = p.text[:L]
            if len(seg) < L or not all("\u4e00" <= x <= "\u9fff" for x in seg):
                continue
            if seg in _NAME_STOP:
                continue
            if seg[0] in cand_chars:
                c[seg] += 1
    if not c:
        ctx._cache[key] = set()
        return set()

    # 第二级：自适应频次门槛 + 相对最高频的比例
    min_count = max(3, min(8, int(round(len(ctx.paras) * 0.02))))
    top = c.most_common(1)[0][1]
    names = {w for w, v in c.items() if v >= min_count and v >= 0.4 * top}
    # 按段首出现次数排序，取前 max_names 个作为确定的名字
    ranked = sorted(names, key=lambda w: (-c[w], -len(w)))
    # 第三级：补上前缀并去掉被更长名字包含的短名
    # 例如识别出「陆行舟」，必须同时排除「陆行」——因为 RHY_PARA_HEAD 只取段首**两字**，
    # 只排除全名会让规则继续打在三字名的前两字上（这是实测踩到的坑）。
    # 反之识别出「沈清」，若同时有「沈清歌」，则去掉短的「沈清」。
    expanded = set()
    for w in ranked[:max_names]:
        for L in range(2, len(w) + 1):
            expanded.add(w[:L])
    # 若一个短名是某个更长名的前缀，且短名自身在段首没有独立出现，则去掉它
    final = set()
    for w in expanded:
        longer_prefix_of_it = any(w != o and o.startswith(w) for o in expanded)
        if longer_prefix_of_it and c.get(w, 0) == 0:
            continue
        final.add(w)
    if not final:
        final = set(expanded)

    ctx._cache[key] = final
    return final


_NOISE_PATTERNS = [
    # 说明：这条只统计「副词性修饰语 + 的 + 动词」这种明显不规范用法
    # （如「认真的望着」）。**不能**用宽泛的「XX的+任意字」——那会把
    # 「脸上的笑容」这类完全正确的「形容词+的+名词」结构判成噪声。
    # 仍然会有误报（「的确」「目的」等），但密度极低、影响有限，
    # 且本规则阈值 3.0/千字 只有高噪声文本才会触发。
    (r"[\u4e00-\u9fff]{2}的(?:说|道|看|走|站|坐|点|摇|问|答|望|听|想|笑|哭|骂|喊|叫|抓|拉|推|拿|放|躺|蹲|跑|跳|张嘴|摇头|点头)",
     "状语「地」写成「的」（疑似）"),
    (r"(?:[\u4e00-\u9fff]{2})的的", "重复「的」"),
    # 重复的虚词/高频字（如「是是」「了了」「在在」）几乎必然是输入错误。
    # 只限虚词与高频功能字，绝不包含「慢慢／渐渐／轻轻」这类正常叠词。
    (r"(是|的|了|在|就|也|不|有|和|与|而|但|却|都|还|又|太|更|最|我|你|他|她|它|这|那)\1",
     "虚词重复（输入错误）"),
    (r"([\u4e00-\u9fff])\1\1", "三连重复字"),
    (r"，{2,}|。{2,}(?!。)", "重复标点"),
    (r"[\u4e00-\u9fff],[^\d]", "半角逗号混入中文"),
    (r"[！？]{2,}", "连续感叹/疑问（网文常见，人味）"),
]
# 刻意**不**收录的一类：
#   「中英数混排无空格」（如"F级""Lv.7""7562号"）曾被计入写作噪声，实际会误伤
#   系统流/异能流网文——这类文本的世界观本身就长这样，是全篇内容特征，
#   既不是 AI 痕迹，也不是人写噪声（AI 同样会产出"F级"）。
#   判据：噪声信号必须能区分人写与 AI；字母+汉字这类模式两边都出现，无区分度 → 剔除。


def _extract_noise(ctx: RuleContext):
    spans = []
    for pat, label in _NOISE_PATTERNS:
        try:
            rx = re.compile(pat)
        except re.error:
            continue
        for m in rx.finditer(ctx.text):
            spans.append({"start": m.start(), "end": m.end(), "text": m.group(0),
                          "word": label})
    spans.sort(key=lambda s: s["start"])
    return len(spans), spans


def _dialogue_mean_len(ctx: RuleContext) -> float:
    from textlib import iter_dialogue_spans
    dlgs = list(iter_dialogue_spans(ctx.paras))
    if len(dlgs) < 3:
        return 0.0
    return mean([len(d.text) for d in dlgs])


# 让词表抽取器支持 human_voice 下的子表。
# 必须先抓住"原始实现"的引用，否则包装层会递归调用自己。
_WORD_EXTRACTOR_ORIGINAL = _word_extractor


def _word_extractor(lex_key: str):  # type: ignore[no-redef]
    if lex_key.startswith("human_voice_"):
        sub = lex_key[len("human_voice_"):]
        submap = {"colloquial": "colloquial", "profanity": "profanity",
                  "particles": "sentence_particles", "dialect": "dialect"}
        real = submap.get(sub, sub)

        def fn(ctx: RuleContext):
            words = ctx.lex.get("human_voice", {}).get(real, [])
            spans = []
            total = 0
            for w in words:
                start = 0
                while True:
                    i = ctx.text.find(w, start)
                    if i < 0:
                        break
                    spans.append({"start": i, "end": i + len(w), "text": w, "word": w})
                    total += 1
                    start = i + 1
                    if len(spans) > 600:
                        break
                if len(spans) > 600:
                    break
            spans.sort(key=lambda s: s["start"])
            return total, spans
        return fn
    return _WORD_EXTRACTOR_ORIGINAL(lex_key)


# --------------------------------------------------------------------------
# 评估 + 计分
# --------------------------------------------------------------------------

def evaluate(text: str, feat: Dict[str, Any], lex: Dict[str, Any],
             extra_findings: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """跑全部规则并计分。

    extra_findings: 由**模型判断**得来的证据（目前是逻辑漏洞 coherence.to_findings）。
    它们和其它规则的 finding 结构相同，会被一起计入分类分与总分。
    这条通路是刻意的：逻辑漏洞没有统计基线，只能靠模型判断，
    但它的**权重必须和统计特征同台计分**，否则最硬的证据反而影响不到结论。
    """
    genre = "unknown"
    if feat and not feat.get("empty"):
        genre = feat.get("genre", {}).get("key", "unknown")
    ctx = RuleContext(text, feat, lex, genre=genre)
    rules = build_rules()

    findings: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []

    han = max(count_han(text), 1)
    dlg_ratio = feat.get("length", {}).get("dialogue_char_ratio", 0) if feat else 0

    for r in rules:
        han = max(count_han(text), 1)
        punct_kinds = (feat.get("punct", {}) or {}).get("distinct_punct_kinds", 0)
        sent_n = (feat.get("length", {}) or {}).get("sentences", 0)
        tag_n = (feat.get("dialogue", {}) or {}).get("tag_total", 0)

        # 前置条件：样本不足的规则直接跳过，并**如实写进报告的跳过清单**
        if r.id == "LEX_CHAR_TTR" and han < 1200:
            skipped.append({"id": r.id, "reason": "样本不足 1200 字，用字丰富度指标不可靠"})
            continue
        if r.id in ("PUNCT_ENTROPY", "PUNCT_CONCENTRATION") and (han < 1200 or punct_kinds < 4):
            skipped.append({"id": r.id,
                            "reason": f"标点分布需 ≥1200 字且 ≥4 类标点"
                                      f"（当前 {han} 字 / {punct_kinds} 类），样本不足不评分"})
            continue
        if r.id == "DLG_TAG_VARIETY" and (dlg_ratio < 0.08 or tag_n < 8):
            skipped.append({"id": r.id,
                            "reason": f"说话人标注需 ≥8 次（当前 {tag_n} 次），样本不足"})
            continue
        if r.id == "DLG_FLUENCY" and dlg_ratio < 0.10:
            skipped.append({"id": r.id, "reason": "对白占比低于 10%，对白长度样本不足"})
            continue
        if r.id in ("EMO_SENSORY",) and han < 2000:
            skipped.append({"id": r.id, "reason": f"五感占比需 ≥2000 字才稳定（当前 {han} 字）"})
            continue
        if r.id == "OPN_NOISE":
            # 这条规则天然不精确（无法区分「认真的望着」与「脸上的笑容」），
            # 所以只在**模式明确成立**（至少 8 处）时才纳入统计。
            # 零星几处不构成"写作噪声"这个统计现象，只会给报告添噪声。
            _n, _ = _extract_noise(ctx)
            if _n < 8:
                skipped.append({"id": r.id,
                                "reason": f"写作噪声需 ≥8 处才构成统计现象（当前 {_n} 处），"
                                          "零星几处无法与人名/固定搭配区分，不纳入"})
                continue
        if r.id == "RHY_SENT_PER_PARA":
            # 一句一段是网文手机阅读的标准排版（短句、对白各自独立成段），
            # 此时"每段句数"的变异系数趋近 0，但它反映的是**排版习惯**，不是机械切段。
            # 只有段落普遍含多句时，变异系数偏低才说明"每段都写成一样长的小单元"。
            _ps = ctx.paras
            _mean_spp = (sum(len(p.sentences) for p in _ps) / len(_ps)) if _ps else 0.0
            if _mean_spp <= 1.1:
                skipped.append({"id": r.id,
                                "reason": f"平均每段仅 {_mean_spp:.2f} 句（一句一段的排版习惯），"
                                          "该指标在此写法下无意义，不纳入"})
                continue
        if r.id in ("RHY_SENT_CV", "RHY_SENT_UNIFORM", "RHY_CLAUSE_CV") and sent_n < 30:
            skipped.append({"id": r.id, "reason": f"句长分布需 ≥30 句才稳定（当前 {sent_n} 句）"})
            continue

        res = r.evaluate(ctx)
        if res is None:
            continue
        contrib = r.contribution(res["hits"], res["excess_ratio"])
        if contrib <= 0:
            continue
        findings.append({
            "id": r.id,
            "category": r.category,
            "category_cn": CATEGORIES[r.category]["cn"],
            "title": r.title,
            "severity": r.severity,
            "confidence": r.confidence,
            "observed": res.get("observed"),
            "baseline": res.get("baseline"),
            "threshold": res.get("threshold"),
            "unit": res.get("unit", ""),
            "excess_ratio": res.get("excess_ratio"),
            "hits": res.get("hits"),
            "contribution": round(contrib, 3),
            "sample": res.get("sample"),
            "samples": res.get("samples", []),
            "explain": r.explain,
            "fix": r.fix,
            "baseline_desc": r.baseline_desc,
            "evidence_note": res.get("evidence_note") or "",
            "genre_adjusted": ctx.genre_factor(r.id) != 1.0,
        })

    # 模型判断得来的证据（逻辑漏洞）与统计证据同台计分
    if extra_findings:
        findings.extend(extra_findings)

    return _score(findings, ctx, feat, han, skipped)


def _score(findings, ctx: RuleContext, feat, han: int, skipped) -> Dict[str, Any]:
    cat_sum: Dict[str, float] = {k: 0.0 for k in CATEGORIES}
    for f in findings:
        cat_sum[f["category"]] += f["contribution"]

    cat_score: Dict[str, float] = {}
    for k, meta in CATEGORIES.items():
        raw = cat_sum[k]
        norm = meta["norm"]
        cat_score[k] = round(100.0 * (1 - math.exp(-raw / norm)), 2)

    risk_cats = [k for k, m in CATEGORIES.items() if m["risk"]]
    S = sum(cat_sum[k] for k in risk_cats)
    O = cat_score["openness"]

    # ---- 证据多样性加成（v1.2）----
    # 问题：只按"证据总量"计分，会让「3 条证据集中在 1 个维度」和
    # 「8 条证据横跨 5 个维度」得到几乎相同的分数——实测两者 S 都是约 25。
    # 但常识上后者可信得多：不同维度的特征互相独立，同时出现才说明"系统性"。
    # 所以对**有实质贡献（≥1.5 分）的维度数**做加成：
    #   命中 2 个维度 → ×1.00（基线，不惩罚）
    #   命中 3 个     → ×1.15
    #   命中 4 个     → ×1.30
    #   命中 5 个及以上 → ×1.45（封顶）
    # 只加成、不削减：单一维度命中的文本保持原分，避免把本来就轻的证据压得更轻。
    cat_spread = len([k for k in risk_cats if cat_sum[k] >= 1.5])
    diversity = 1.0 + 0.20 * max(0, min(cat_spread - 2, 3))
    S_eff = S * diversity

    K = 22.0
    risk0 = 100.0 * (1 - math.exp(-S_eff / K))
    openness_discount = 0.30 * O
    risk = max(0.0, min(100.0, risk0 - openness_discount))

    # ---- 长度门槛 ----
    caveats: List[str] = []
    width_bonus = 0.0
    confidence_level = "normal"
    if han < 300:
        confidence_level = "insufficient"
        caveats.append(
            f"样本仅 {han} 字，低于 300 字门槛。**本工具不对这个长度的文本给出 AI 含量结论**——"
            "任何检测器在这个长度上都只能猜。平台若用几百字判定作者，方法论本身就不成立，"
            "这一点可直接作为申诉理由。")
    elif han < 1000:
        confidence_level = "low"
        width_bonus = 15.0
        caveats.append(
            f"样本 {han} 字，低于 1000 字。结论区间已强制放宽 ±15 分，请勿单独作为判断依据。"
            "建议至少提供 2000 字（单章规模）再判定。")
    elif han > 8000:
        caveats.append(
            f"样本 {han} 字，属长文本。以下结论适用于**整体**；"
            "个别章节可能与人写段落混杂，请结合「按章/按段分布」查看。")

    if feat and feat.get("genre", {}).get("key") == "unknown":
        caveats.append("未识别出题材，未启用题材基线放宽。若这是系统流/古风等高度程式化题材，"
                       "本工具可能偏严，请参考「误判对冲」一节的说明。")
    else:
        g = feat.get("genre", {})
        caveats.append(
            f"识别题材：{_genre_cn(g.get('key'))}（置信度 {g.get('confidence')}），"
            "已按题材放宽对应基线（题材自适应**只放宽、不收紧**）。")

    # ---- 最重要的一条诚实声明：低分不等于排除 AI ----
    # 实测依据：一份确认由 AI 生成的 1520 字稿件被判为「极低」。
    # 它套话密度仅 1.97/千字、抽象情绪 0、通用身体反应 0、句长 cv 0.821，
    # 压缩率、用字丰富度也都落在人类区间内——**击败了本工具全部特征**。
    # 结论：本工具能抓"风格平庸的 AI 输出"，抓不住"刻意避免套话的高质量 AI 输出"。
    # 所以只要风险低而样本够长，就必须主动提醒这一点，否则用户会把"极低"当成"排除"。
    if risk < 18.0 and han >= 800:
        caveats.append(
            "**「极低」不等于「排除 AI」。** 本工具实测有过漏检：一份确认由 AI 生成、约 1500 字的稿件，"
            "套话密度仅 1.97/千字、抽象情绪词与通用身体反应都近乎为零、句长起伏充分，"
            "最终被判为「极低」——它绕开了本工具全部基于词表与统计的特征。"
            "**文本越干净、越精致、越刻意避免套话，本工具的漏检风险越高。**"
            "若你怀疑这份稿子用过 AI，请把低分当作「没有抓到证据」，而不是「证明是纯人写」。")

    # ---- 证据一致性 → 区间 ----
    # 区间宽度 = 基础不确定度 + 证据分散惩罚 + 样本长度惩罚
    # 依据：检出数集中(证据方向一致)时结论更硬；样本越短越不可信。
    strong = [f for f in findings if f["contribution"] >= 2.0]
    cats_hit = {f["category"] for f in strong}
    cat_spread = len(cats_hit)
    if not strong:
        spread_penalty = 12.0
    elif cat_spread >= 4:
        spread_penalty = 5.0
    elif cat_spread == 3:
        spread_penalty = 8.0
    elif cat_spread == 2:
        spread_penalty = 11.0
    else:
        spread_penalty = 14.0

    if han < 1000:
        length_penalty = 12.0
    elif han < 2000:
        length_penalty = 7.0
    elif han < 4000:
        length_penalty = 4.0
    else:
        length_penalty = 0.0

    half_width = spread_penalty + length_penalty + width_bonus
    if confidence_level == "insufficient":
        half_width = 0.0
    # 区间上下界做保守收敛：不声称"确认是 AI"，也不声称"确认不是 AI"
    lo = max(0.0, risk - half_width)
    hi = min(95.0, risk + half_width)
    lo = min(lo, 92.0)

    # ---- 灵敏度分析 ----
    sensitivity = []
    top = sorted(findings, key=lambda f: -f["contribution"])[:3]
    for f in top:
        S2 = S - f["contribution"]
        r2 = 100.0 * (1 - math.exp(-S2 / K)) - openness_discount
        sensitivity.append({
            "rule": f["id"], "title": f["title"],
            "contrib": f["contribution"],
            "risk_without": round(max(0.0, min(100.0, r2)), 2),
            "delta": round(risk - max(0.0, min(100.0, r2)), 2),
        })

    # ---- 档位 ----
    if confidence_level == "insufficient":
        band = ("无法判定", "#6b7280",
                "样本过短，拒绝给出 AI 含量结论。")
    elif risk < 18:
        band = ("极低", "#16a34a",
                "未检出系统性的 AI 痕迹。**但这不等于证明为人写**——"
                "本工具实测有过漏检：一份确认由 AI 生成的稿件也落在这一档。"
                "「极低」只表示「没有抓到证据」。")
    elif risk < 36:
        band = ("偏低", "#65a30d", "存在个别共性痕迹，属正常写作习惯范围，通常不会触发平台判定。")
    elif risk < 55:
        band = ("中等", "#ca8a04",
                "存在多处并发痕迹。部分检测器可能标为可疑，建议按清单处理高权重项后再自查一次。")
    elif risk < 72:
        band = ("偏高", "#ea580c",
                "痕迹密集且缺少人味对冲，进入多数检测器的高危区间。换词解决不了，需要成段重写。")
    else:
        band = ("极高", "#dc2626",
                "系统性 AI 特征全面出现。建议按报告重写；如确有独立创作过程，请参考「申诉要件」一节。")

    # ---- 观测项（未超基线但值得看）----
    return {
        "risk": round(risk, 2),
        "risk_interval": [round(lo, 2), round(hi, 2)],
        "risk_raw": round(risk0, 2),
        "openness_score": round(O, 2),
        "openness_discount": round(openness_discount, 2),
        "S_total": round(S, 3),
        "band": band[0],
        "band_color": band[1],
        "band_text": band[2],
        "confidence_level": confidence_level,
        "category_scores": cat_score,
        "category_contrib": {k: round(v, 3) for k, v in cat_sum.items()},
        "findings": sorted(findings, key=lambda f: -f["contribution"]),
        "skipped_rules": skipped,
        "caveats": caveats,
        "sensitivity": sensitivity,
        "observations": ctx.observations,
    }


def _genre_cn(key: str) -> str:
    from textlib import GENRE_CN
    return GENRE_CN.get(key, key)
