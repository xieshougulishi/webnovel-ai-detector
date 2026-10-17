# -*- coding: utf-8 -*-
"""
features —— 客观量化特征层（第一层证据）。

只报数，不下结论。所有数字都会出现在报告「体检表」里，
作者可以用自己的文本复算，这是"可复核"承诺的基础。

所有 per_k 口径统一为：命中次数 × 1000 / 正文汉字数。
"""
from __future__ import annotations

import json
import math
import os
import re
from collections import Counter
from typing import Any, Dict, List, Optional, Sequence

from textlib import (DASH_CHARS, Paragraph, Span, char_ngrams, count_han,
                     count_nonspace, cv, han_only, infer_genre, is_han,
                     iter_dialogue_spans, mean, quantile, shannon_entropy,
                     split_paragraphs, stdev, variance)

_HERE = os.path.dirname(os.path.abspath(__file__))
_DEFAULT_LEXICON = os.path.join(os.path.dirname(_HERE), "data", "lexicon_ai.json")


def load_lexicon(path: str = None) -> Dict[str, Any]:
    p = path or _DEFAULT_LEXICON
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)


# --------------------------------------------------------------------------
# 标点统计
# --------------------------------------------------------------------------

_PUNCT_KEYS = {
    "comma": "，",
    "period": "。",
    "question": "？",
    "exclaim": "！",
    "pause": "、",
    "semicolon": "；",
    "colon": "：",
    "ellipsis": "…",
    "dash": "—",
    "quote": "“",
    "paren": "（",
}

# 归一化熵的分母：真实中文写作中实际会用的标点类别数（不是全部标点符号数）
_ENTROPY_DENOM = 8.0


def punct_profile(text: str) -> Dict[str, Any]:
    han = max(count_han(text), 1)
    counts = {}
    for key, ch in _PUNCT_KEYS.items():
        counts[key] = text.count(ch)
    # 半角变体并入同一语义计数
    counts["question"] += text.count("?")
    counts["exclaim"] += text.count("!")
    counts["comma"] += text.count(",")
    counts["period"] += text.count(".")
    counts["dash"] += sum(text.count(c) for c in DASH_CHARS if c != "—")
    counts["paren"] += text.count("(")
    counts["pause"] += text.count("、")
    counts["colon"] += text.count(":")

    dist = [v for v in counts.values() if v > 0]
    entropy = shannon_entropy(dist) if dist else 0.0

    # 半角标点混入中文：只统计**紧邻汉字**的半角标点（纯英文段落不算）。
    #
    # ⚠️ 关于 ASCII 直引号 `"` 的判断（v1.1 修正）：
    # 早期把它也算作"混入"，当作人味证据。这是个**方向性错误**——
    # 实测一份确认由 AI 生成的稿子有 44 个 ASCII 直引号，原因几乎肯定是
    # **作者从 AI 聊天窗口复制粘贴**，而聊天界面输出 ASCII 引号。
    # 也就是说，直引号说明的是"这段文字经过了什么渠道"，不是"谁写的"：
    #   人写 + 聊天窗口 = 直引号
    #   AI 写 + 聊天窗口 = 直引号
    #   AI 写 + 直接导出 = 弯引号
    # 所以直引号**没有作者归属判别力**，不计入半角证据。
    # 但它仍要参与对白解析（见 textlib.iter_dialogue_spans）——解析与证据是两件事。
    hw_adjacent = len(re.findall(
        r"[\u4e00-\u9fff][,.;:!?]|[,.;:!?][\u4e00-\u9fff]", text))
    straight_quotes = text.count('"')
    full_total = max(sum(counts.values()), 1)
    halfwidth_ratio = hw_adjacent / full_total

    comma_period_share = ((counts["comma"] + counts["period"]) / full_total
                          if full_total else 0.0)

    # 省略号滥用：AI 爱用「……」冒充留白
    ellipsis_runs = len(re.findall(r"…{2,}", text))
    dash_runs = len(re.findall(r"[—–]{1,2}", text))

    # 引号配对规范性：AI 生成的引号几乎总是成对且规范
    open_q = text.count("“") + text.count("「") + text.count("『")
    close_q = text.count("”") + text.count("」") + text.count("』")
    pair_ok = 1.0 if open_q == close_q and open_q > 0 else 0.0

    return {
        "counts": counts,
        "per_k": {k: round(v * 1000.0 / han, 3) for k, v in counts.items()},
        "total_marks": full_total,
        "entropy_bits": round(entropy, 4),
        "entropy_norm": round(entropy / _ENTROPY_DENOM, 4),
        "distinct_punct_kinds": len(dist),
        "halfwidth_adjacent": hw_adjacent,
        "halfwidth_ratio": round(halfwidth_ratio, 4),
        "straight_quotes": straight_quotes,
        "comma_period_share": round(comma_period_share, 4),
        "ellipsis_runs": ellipsis_runs,
        "ellipsis_run_per_k": round(ellipsis_runs * 1000.0 / han, 3),
        "dash_runs": dash_runs,
        "quote_open": open_q,
        "quote_close": close_q,
        "quote_pair_rate": pair_ok,
        "halfwidth_mixed": bool(re.search(r"[\u4e00-\u9fff][,.;:!?]", text)),
    }


# --------------------------------------------------------------------------
# 的地得规范度（AI 几乎永远用对；真人网文混用率很高）
# --------------------------------------------------------------------------

_DE_RE = re.compile(r"[\u4e00-\u9fff]的[\u4e00-\u9fff]")
_DI_RE = re.compile(r"[\u4e00-\u9fff]地[\u4e00-\u9fff]")
_DE_ADV = re.compile(r"[\u4e00-\u9fff]{1,3}的(?:说|道|看|走|笑|想|站|坐|点|摇|问|答|望|听)")
_DI_STD = re.compile(r"[\u4e00-\u9fff]{1,4}地(?:说|道|看|走|笑|想|站|坐|点|摇|问|答|望|听)")


def de_di_de_profile(text: str) -> Dict[str, Any]:
    """测的是「的地得规范度」。真人网文大量把状语「地」写成「的」，
    这是最难伪装的人味信号之一，也是本工具对抗误报的重要依据。"""
    de_total = len(_DE_RE.findall(text))
    di_total = len(_DI_RE.findall(text))
    de_adv = len(_DE_ADV.findall(text))     # 「XX的说」这种不规范写法
    di_std = len(_DI_STD.findall(text))     # 「XX地说」这种规范写法
    han = max(count_han(text), 1)

    # 规范副词率：地/（地+不规范状语）
    denom = di_std + de_adv
    std_rate = (di_std / denom) if denom > 0 else None
    return {
        "de_count": de_total,
        "di_count": di_total,
        "de_adv_nonstandard": de_adv,
        "di_adv_standard": di_std,
        "adverbial_standard_rate": (round(std_rate, 4) if std_rate is not None else None),
        "de_per_k": round(de_total * 1000.0 / han, 3),
        "di_per_k": round(di_total * 1000.0 / han, 3),
    }


# --------------------------------------------------------------------------
# 主特征抽取
# --------------------------------------------------------------------------

_ARTIFACT_PATTERNS = [
    (r"作为一个?(?:AI|人工智能|语言模型|大模型)", "AI 自述身份"),
    (r"(?:以下是|下面是)(?:我|为?你)(?:写|生成|创作)", "AI 助手口吻"),
    (r"(?:希望|但愿)(?:这|本)(?:篇|段|些)(?:文字|内容)(?:能|可以)(?:帮|对)", "AI 助手结语"),
    (r"(?:如果|若)(?:你|您)(?:需要|想要)(?:我)?(?:继续|调整|修改|润色)", "AI 助手追问"),
    (r"^\s*(?:首先|其次|最后)[，、]", "说明文式分点"),
    (r"(?:综上|总而言之|总的来说)[，、]", "总结句"),
    (r"```", "代码块残留"),
    (r"(?:如下|以下)(?:所示|几点|几个方面)", "说明书腔"),
    (r"——\s*(?:完|全文完|end)", "生成结束标记"),
]


def ai_artifact_profile(text: str) -> Dict[str, Any]:
    """直接的"生成器残留"痕迹——命中任何一条都是强证据。"""
    found = []
    for pat, label in _ARTIFACT_PATTERNS:
        for m in re.finditer(pat, text, re.M):
            found.append({"label": label, "text": m.group(0)[:40],
                          "start": m.start(), "end": m.end()})
    return {"hits": found, "count": len(found)}


def _word_hits(text: str, words: Sequence[str]) -> Dict[str, Any]:
    """对词表做一次遍历统计。返回次数、每千字密度、逐词计数。"""
    cnt: Counter = Counter()
    total = 0
    for w in words:
        if not w:
            continue
        c = text.count(w)
        if c:
            cnt[w] = c
            total += c
    han = max(count_han(text), 1)
    return {"count": total, "per_k": round(total * 1000.0 / han, 3), "by_word": dict(cnt)}


def _regex_hits(text: str, patterns: Sequence[str], limit: int = 200):
    out = []
    for pat in patterns:
        try:
            rx = re.compile(pat)
        except re.error:
            continue
        for m in rx.finditer(text):
            out.append({"text": m.group(0), "start": m.start(), "end": m.end(), "pattern": pat})
            if len(out) >= limit:
                return out
    return out


_QUOTE_STRIP = "，。、：；！？…—“”‘’「」『』（）() \n\t\r"


def _strip_quote_punct(s: str) -> str:
    return s.strip(_QUOTE_STRIP)


# 「道」「说」这类单字标注词大量出现在普通词里（味道、知道、难道、一道、街道、报道、
# 与其说…不如说），直接数子串会把它们全算成说话人标注。
# 这些前置字出现时一律不算标注。
_TAG_FALSE_PREFIX = set("知味一难道街报频通渠霸厚地正公轨隧政门") 


def dialogue_tag_profile(text: str, paras, tags) -> Dict[str, Any]:
    """统计**紧贴引号**出现的说话人标注词，而不是全篇子串计数。

    人类写对白的标注形式基本只有两种：
        [标注]：“台词”        → 标注在引号前
        “台词”[标注]           → 标注在引号后
    所以只看每段对白的引号前后一个小窗口，能滤掉绝大多数假命中。
    """
    dlg = list(iter_dialogue_spans(paras))
    ordered = sorted(tags, key=len, reverse=True)   # 长词优先，避免「说道」被「说」抢先
    cnt: Counter = Counter()
    tagged = 0
    for d in dlg:
        pre = _strip_quote_punct(text[max(0, d.start - 16):d.start])
        post = _strip_quote_punct(text[d.end:d.end + 16])
        hit = None
        for w in ordered:
            if pre.endswith(w):
                i = len(pre) - len(w) - 1
                if i >= 0 and pre[i] in _TAG_FALSE_PREFIX and len(w) == 1:
                    continue
                hit = w
                break
        if hit is None:
            for w in ordered:
                if post.startswith(w):
                    j = len(w)
                    if j < len(post) and post[j] in "的了着" and len(w) == 1:
                        continue
                    hit = w
                    break
        if hit:
            cnt[hit] += 1
            tagged += 1
    return {"count": tagged, "by_word": dict(cnt)}


def extract(text: str, lexicon: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """主入口：返回 dict（可直接 json.dumps）。"""
    lex = lexicon or load_lexicon()
    paras = split_paragraphs(text, min_len=0)
    if not paras:
        return {"empty": True}

    han_total = count_han(text)
    nonspace_total = count_nonspace(text)

    # ---- 句子 ----
    sents: List[Span] = []
    for p in paras:
        sents.extend(p.sentences)
    sent_han = [count_han(s.text) for s in sents if count_han(s.text) > 0]
    sent_all_chars = [len(s.text) for s in sents if s.text.strip()]

    # ---- 段落 ----
    para_han = [p.char_len for p in paras if p.char_len > 0]
    para_visual = [len(p.text) for p in paras]

    # ---- 对白 ----
    dlg = list(iter_dialogue_spans(paras))
    dlg_chars = sum(len(d.text) for d in dlg)
    dlg_paras = sum(1 for p in paras if p.text.lstrip().startswith(("“", "「", "『")))
    # 对白行占比：以引号开头的段落
    dlg_line_ratio = dlg_paras / len(paras)

    # 说话人标签：只统计紧贴引号出现的（见 dialogue_tag_profile 的说明）
    tags = lex.get("dialogue_tags", {}).get("tags", [])
    tag_hits = dialogue_tag_profile(text, paras, tags)
    tag_variety = (len([k for k, v in tag_hits["by_word"].items() if v > 0])
                   / max(len(tags), 1))

    # ---- 标点 ----
    punct = punct_profile(text)

    # ---- 的字得 ----
    deprofile = de_di_de_profile(text)

    # ---- 词表类 ----
    def wl(key: str):
        return _word_hits(text, lex.get(key, {}).get("words", []))

    art = wl("artistic_cliche")
    emo = wl("abstract_emotion")
    body = wl("generic_body")
    conn = wl("written_connector")
    summ = wl("summary_habit")

    # ---- 正则模板类 ----
    anti = _regex_hits(text, lex.get("antithesis_pattern", {}).get("regex", []))
    simile = _regex_hits(text, lex.get("simile_template", {}).get("regex", []))
    para_rule = _regex_hits(text, lex.get("parallelism", {}).get("regex", []), limit=120)

    # ---- 四字格密度（近似：连续 4 汉字段中排除人名/常用词的粗估）----
    four_grams = char_ngrams(text, 4)
    four_gram_repeat = 0
    if four_grams:
        c4 = Counter(four_grams)
        four_gram_repeat = sum(v - 1 for v in c4.values() if v > 1)
    # 成语近似：四字格中"非高频语素"比例——用 2-gram 重复率做代理更稳
    bigrams = char_ngrams(text, 2)
    bg_counter = Counter(bigrams)
    bigram_types = len(bg_counter)
    bigram_repeat_rate = (1 - bigram_types / len(bigrams)) if bigrams else 0.0
    # 实词复用：出现 >=3 次的 2-gram 占比
    bg_hot = sum(v for v in bg_counter.values() if v >= 3)

    trigrams = char_ngrams(text, 3)
    tg_counter = Counter(trigrams)
    trigram_types = len(tg_counter)

    char_types = len(set(han_only(text)))
    ttr_char = char_types / han_total if han_total else 0.0

    # ---- 五感 ----
    sensory = {}
    for k, words in lex.get("sensory", {}).items():
        if k.startswith("_"):
            continue
        sensory[k] = _word_hits(text, words)["per_k"]
    sensory_total = sum(sensory.values()) or 1.0
    sensory_share = {k: round(v / sensory_total, 4) for k, v in sensory.items()}

    # ---- 人味信号 ----
    hv = lex.get("human_voice", {})
    human = {}
    for k in ("colloquial", "profanity", "dialect", "hesitation", "self_correction"):
        human[k] = _word_hits(text, hv.get(k, []))
    particles = _word_hits(text, hv.get("sentence_particles", []))
    human["sentence_particles"] = particles
    human_total = sum(human[k]["count"] for k in human)

    # ---- 生成器残留 ----
    artifact = ai_artifact_profile(text)

    # ---- 段落开头重复 ----
    heads = [p.text[:2] for p in paras if len(p.text) >= 4]
    head_counter = Counter(heads)
    head_repeat = sum(v - 1 for v in head_counter.values() if v > 1)

    # ---- 句首重复（2 字）----
    sheads = [s.text[:2] for s in sents if len(s.text) >= 4]
    shead_counter = Counter(sheads)
    shead_repeat = sum(v - 1 for v in shead_counter.values() if v > 1)

    # ---- 段末标点分布（段末用句号的比率：AI 收尾更"齐"）----
    para_end_period = sum(1 for p in paras if p.text.rstrip("”」』")[-1:] in ("。", "！", "？"))

    # ---- 题材 ----
    genre, genre_conf, genre_cues = infer_genre(text)

    # ---- 组装 ----
    feat: Dict[str, Any] = {
        "empty": False,
        "length": {
            "chars_total": len(text),
            "chars_nonspace": nonspace_total,
            "han_total": han_total,
            "paragraphs": len(paras),
            "sentences": len(sents),
            "dialogue_spans": len(dlg),
            "dialogue_chars": dlg_chars,
            "dialogue_char_ratio": round(dlg_chars / max(nonspace_total, 1), 4),
            "dialogue_line_ratio": round(dlg_line_ratio, 4),
        },
        "sentence": {
            "mean_han": round(mean(sent_han), 3),
            "stdev_han": round(stdev(sent_han), 3),
            "cv_han": round(cv(sent_han), 4),
            "variance_han": round(variance(sent_han), 3),
            "q10": round(quantile(sent_han, 0.10), 2),
            "q50": round(quantile(sent_han, 0.50), 2),
            "q90": round(quantile(sent_han, 0.90), 2),
            "iqr": round(quantile(sent_han, 0.75) - quantile(sent_han, 0.25), 2),
            "max_han": max(sent_han) if sent_han else 0,
            "min_han": min(sent_han) if sent_han else 0,
            "short_ratio_le5": round(sum(1 for x in sent_han if x <= 5) / max(len(sent_han), 1), 4),
            "long_ratio_ge40": round(sum(1 for x in sent_han if x >= 40) / max(len(sent_han), 1), 4),
            "len_hist": _hist(sent_han, [0, 6, 11, 16, 21, 26, 31, 41, 51, 10 ** 9]),
        },
        "paragraph": {
            "mean_chars": round(mean(para_han), 3),
            "stdev_chars": round(stdev(para_han), 3),
            "cv_chars": round(cv(para_han), 4),
            "q50": round(quantile(para_han, 0.50), 2),
            "q90": round(quantile(para_han, 0.90), 2),
            "max_chars": max(para_han) if para_han else 0,
            "one_line_ratio": round(sum(1 for x in para_visual if x <= 25) / max(len(para_visual), 1), 4),
            "end_punct_period_ratio": round(para_end_period / max(len(paras), 1), 4),
        },
        "punct": punct,
        "de_di_de": deprofile,
        "lexical": {
            "char_types": char_types,
            "char_ttr": round(ttr_char, 4),
            "bigram_types": bigram_types,
            "bigram_repeat_rate": round(bigram_repeat_rate, 4),
            "bigram_hot_share": round(bg_hot / max(len(bigrams), 1), 4),
            "trigram_types": trigram_types,
            "four_gram_repeat": four_gram_repeat,
            "artistic_cliche": art,
            "abstract_emotion": emo,
            "generic_body": body,
            "written_connector": conn,
            "summary_habit": summ,
        },
        "template": {
            "antithesis": {"count": len(anti), "per_k": round(len(anti) * 1000.0 / max(han_total, 1), 3),
                           "items": anti[:60]},
            "simile": {"count": len(simile), "per_k": round(len(simile) * 1000.0 / max(han_total, 1), 3),
                       "items": simile[:60]},
            "parallelism": {"count": len(para_rule), "per_k": round(len(para_rule) * 1000.0 / max(han_total, 1), 3),
                            "items": para_rule[:40]},
        },
        "dialogue": {
            "tag_total": tag_hits["count"],
            "tag_per_k": round(tag_hits["count"] * 1000.0 / max(han_total, 1), 3),
            "tag_variety": round(tag_variety, 4),
            "tag_by_word": tag_hits["by_word"],
        },
        "sensory": {"per_k": sensory, "share": sensory_share},
        "human_voice": {
            "total": human_total,
            "per_k": round(human_total * 1000.0 / max(han_total, 1), 3),
            "detail": {k: v for k, v in human.items()},
        },
        "repetition": {
            "para_head_repeat": head_repeat,
            "para_head_repeat_per_k": round(head_repeat * 1000.0 / max(han_total, 1), 3),
            "sent_head_repeat": shead_repeat,
            "sent_head_repeat_per_k": round(shead_repeat * 1000.0 / max(han_total, 1), 3),
        },
        "artifact": artifact,
        "genre": {"key": genre, "confidence": round(genre_conf, 3), "cues": genre_cues},
    }
    return feat


def _hist(values: Sequence[int], edges: Sequence[int]) -> List[Dict[str, Any]]:
    out = []
    for i in range(len(edges) - 1):
        lo, hi = edges[i], edges[i + 1]
        c = sum(1 for v in values if lo <= v < hi)
        label = f"{lo}-{hi - 1}" if hi < 10 ** 9 else f"{lo}+"
        out.append({"label": label, "count": c})
    return out
