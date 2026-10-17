# -*- coding: utf-8 -*-
"""
textlib —— 中文网文文本的归一化、分段、分句、局部统计。

零第三方依赖（纯标准库），因为目标环境没有外网、没有 jieba。

设计取舍
--------
不引入分词器：中文 AI 检测的核心特征（句长节奏、标点习惯、套话密度、
模板句式）几乎都**不需要词边界**，用字符 n-gram + 固定词表命中就能测。
引分词器反而会引入版本差异，让"可复核"这个核心卖点失效。
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Iterator, List, Optional, Sequence, Tuple

# --------------------------------------------------------------------------
# 字符类
# --------------------------------------------------------------------------

# 汉字（含扩展 A 基本区；网文里罕见扩展区字一律按"非汉字"处理，避免干扰）
HAN_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
# 所有非空白字符
NONSPACE_RE = re.compile(r"\S")

# 句末终止符
SENT_END = "。！？!?…"
# 句中停顿符
SENT_PAUSE = "，、；：,;:"
# 引号（成对）
QUOTE_PAIRS = [("“", "”"), ("「", "」"), ("『", "』"), ("‘", "’")]
# 破折号
DASH_CHARS = "—–"
# 省略号
ELLIPSIS_CHARS = "…"

_PUNCT_ALL = set("。，、；：？！“”‘’「」『』（）《》〈〉—–…·～~!?,.;:()[]{}<>\"'`@#$%^&*_+=|\\/-")

# 需要被"清洗掉"的章节标记（不影响偏移，只用于题材判断等）
CHAPTER_MARK_RE = re.compile(
    r"^\s*(?:第\s*[0-9零一二三四五六七八九十百千万两]+\s*[章节回卷篇]|Chapter\s*\d+|"
    r"正文\s*$|楔子|序章|尾声|后记|番外)"
)


def is_han(ch: str) -> bool:
    return "\u4e00" <= ch <= "\u9fff" or "\u3400" <= ch <= "\u4dbf"


def is_punct(ch: str) -> bool:
    if ch in _PUNCT_ALL:
        return True
    cat = unicodedata.category(ch)
    return cat.startswith("P") or cat.startswith("S")


def is_han_text(s: str) -> bool:
    return bool(HAN_RE.search(s))


# --------------------------------------------------------------------------
# 归一化
# --------------------------------------------------------------------------

def normalize(text: str) -> str:
    """归一化但**不改变长度**（保证字符偏移可用于原文定位）。

    - \\r\\n / \\r -> \\n（长度会变，这是唯一例外，在任何切分之前做一次）
    - 全角空格 -> 普通空格
    - 删除零宽字符
    - 统一省略号写法（…… 保持，但 ... 归一到 ……）会改长度，故只做等长替换
    """
    t = text.replace("\r\n", "\n").replace("\r", "\n")
    t = t.replace("\u3000", " ")          # 全角空格
    t = t.replace("\ufeff", "").replace("\u200b", "").replace("\u200e", "")
    t = t.replace("\u00a0", " ")
    return t


# v1.1: 支持 dsh-short-video-studio 之外的通用场景，保持简单
def strip_none(text: str) -> str:
    return text


# --------------------------------------------------------------------------
# 分段 / 分句
# --------------------------------------------------------------------------

@dataclass
class Span:
    """带绝对字符偏移的片段。start/end 为 [start, end) 半开区间。"""
    text: str
    start: int
    end: int

    def __len__(self) -> int:
        return self.end - self.start

    @property
    def han_count(self) -> int:
        return sum(1 for c in self.text if is_han(c))


@dataclass
class Paragraph(Span):
    index: int = 0
    sentences: List["Span"] = field(default_factory=list)
    char_len: int = 0          # 去空白后的字符数
    visual_len: int = 0        # 含空白的显示宽度（缩进算 2）


def split_paragraphs(text: str, min_len: int = 0) -> List[Paragraph]:
    """按换行切段，返回带绝对偏移的段落列表。

    min_len: 去空白后字符数小于该值的段被**合并**到上一段（处理短行标题、
    分隔线、单行对话被硬换行的情况）。0 表示不合并。
    """
    paras: List[Paragraph] = []
    pos = 0
    for raw in text.split("\n"):
        line_start = pos
        pos += len(raw) + 1  # +1 为被 split 掉的 \n
        stripped = raw.strip()
        if not stripped:
            continue
        # 段落内部保留原文（含缩进），偏移以 raw 在整文中的位置为准
        lead = len(raw) - len(raw.lstrip())
        core = raw.strip()
        p = Paragraph(
            text=core,
            start=line_start + lead,
            end=line_start + lead + len(core),
            index=len(paras),
            char_len=sum(1 for c in core if not c.isspace()),
        )
        p.visual_len = 2 * (lead // 2) + len(core)  # 粗略：两空格算一个缩进
        if min_len and paras and p.char_len < min_len:
            prev = paras[-1]
            merged = text[prev.start:p.end]
            prev.text = merged
            prev.end = p.end
            prev.char_len += p.char_len
            prev.visual_len += p.char_len
            continue
        paras.append(p)
    for i, p in enumerate(paras):
        p.index = i
        p.sentences = split_sentences(p.text, p.start)
    return paras


def split_sentences(para_text: str, base_offset: int = 0) -> List[Span]:
    """把段落切成句子。

    规则：
      - 句末符（。！？!?…）后成句；连续句末符（如「？！」「……」）并入同一句
      - 引号内的句末符**不切**（对白内部不拆），除非引号内文本超过 60 字
      - 逗号/分号不切句（中文网文里逗号连缀是常态，按逗号切会毁掉句长特征）
      - 末尾无终止符的残句也算一句
    """
    out: List[Span] = []
    n = len(para_text)
    i = 0
    seg_start = 0
    quote_depth = 0
    cur_quote_open: Optional[str] = None

    while i < n:
        ch = para_text[i]
        # 引号状态机
        for op, cl in QUOTE_PAIRS:
            if ch == op:
                quote_depth += 1
                cur_quote_open = cl
                break
            if ch == cl and quote_depth > 0:
                quote_depth -= 1
                cur_quote_open = None
                break

        if ch in SENT_END or ch == ELLIPSIS_CHARS:
            in_quote = quote_depth > 0
            quoted_len = i - seg_start
            if in_quote and quoted_len < 60:
                i += 1
                continue
            # 吞掉连续的句末符
            j = i + 1
            while j < n and (para_text[j] in SENT_END or para_text[j] == ELLIPSIS_CHARS):
                j += 1
            # 吞掉紧跟的收尾引号
            while j < n and para_text[j] in "”」』’\"'":
                j += 1
            seg = para_text[seg_start:j]
            if seg.strip():
                out.append(Span(seg, base_offset + seg_start, base_offset + j))
            seg_start = j
            i = j
            quote_depth = 0
            cur_quote_open = None
            continue
        i += 1

    if seg_start < n:
        seg = para_text[seg_start:]
        if seg.strip():
            out.append(Span(seg, base_offset + seg_start, base_offset + n))
    return out


def iter_dialogue_spans(paragraphs: Sequence[Paragraph]) -> Iterator[Span]:
    """产出所有对白片段（成对引号括起来的部分）。

    支持两种引号体系：
      1. 中文成对引号：“…”「…」『…』‘…’
      2. **ASCII 直引号 "** —— 中文文本里出现直引号，通常意味着作者用了
         英文输入法、或从纯文本源复制粘贴过。这在实际网文里相当常见
         （实测有一份 1520 字的稿子用了 44 个直引号，整篇对白对旧版完全不可见）。

    同一段里若存在中文引号，优先按中文引号解析；否则按直引号**顺序两两配对**。
    """
    for p in paragraphs:
        t = p.text
        n = len(t)
        has_cn = any(op in t for op, _ in QUOTE_PAIRS)
        if has_cn:
            i = 0
            while i < n:
                for op, cl in QUOTE_PAIRS:
                    if t[i] == op:
                        j = t.find(cl, i + 1)
                        if j == -1:
                            j = n - 1
                        yield Span(t[i:j + 1], p.start + i, p.start + j + 1)
                        i = j
                        break
                i += 1
        else:
            idx = [k for k, ch in enumerate(t) if ch == '"']
            for k in range(0, len(idx) - 1, 2):
                s, e = idx[k], idx[k + 1]
                yield Span(t[s:e + 1], p.start + s, p.start + e + 1)


# --------------------------------------------------------------------------
# 局部统计工具
# --------------------------------------------------------------------------

def han_only(s: str) -> str:
    return "".join(c for c in s if is_han(c))


def count_han(s: str) -> int:
    return sum(1 for c in s if is_han(c))


def count_nonspace(s: str) -> int:
    return sum(1 for c in s if not c.isspace())


def per_k(s: str, hits: int, base: str = None) -> float:
    """每千字密度。base 给定时按其汉字数计算（用于"在正文里"的口径）。"""
    src = base if base is not None else s
    h = max(count_han(src), 1)
    return hits * 1000.0 / h


def char_ngrams(s: str, n: int) -> List[str]:
    han = han_only(s)
    if len(han) < n:
        return []
    return [han[i:i + n] for i in range(len(han) - n + 1)]


def mean(xs: Sequence[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def variance(xs: Sequence[float]) -> float:
    if len(xs) < 2:
        return 0.0
    m = mean(xs)
    return sum((x - m) ** 2 for x in xs) / (len(xs) - 1)


def stdev(xs: Sequence[float]) -> float:
    return variance(xs) ** 0.5


def cv(xs: Sequence[float]) -> float:
    """变异系数 = 标准差 / 均值。AI 文本通常显著偏低（节奏均匀）。"""
    m = mean(xs)
    if m <= 0:
        return 0.0
    return stdev(xs) / m


def quantile(xs: Sequence[float], q: float) -> float:
    if not xs:
        return 0.0
    s = sorted(xs)
    if len(s) == 1:
        return s[0]
    pos = q * (len(s) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(s) - 1)
    frac = pos - lo
    return s[lo] * (1 - frac) + s[hi] * frac


def shannon_entropy(counts: Sequence[int]) -> float:
    import math
    total = sum(counts)
    if total <= 0:
        return 0.0
    h = 0.0
    for c in counts:
        if c > 0:
            p = c / total
            h -= p * math.log2(p)
    return h


def finditer_spans(pattern: re.Pattern, text: str, base_offset: int = 0,
                   limit: int = 400) -> List[Tuple[int, int, str]]:
    """返回 [(start, end, matched_text)]，带绝对偏移，带数量上限防爆。"""
    out = []
    for m in pattern.finditer(text):
        out.append((base_offset + m.start(), base_offset + m.end(), m.group(0)))
        if len(out) >= limit:
            break
    return out


def infer_genre(text: str) -> Tuple[str, float, List[str]]:
    """极简题材识别。只用于**放宽**基线，绝不用来加罪。

    返回 (题材键, 置信度 0..1, 命中证据词)
    """
    head = text[:4000]
    cues = {
        "system": ["系统提示", "系统音", "叮！", "属性面板", "签到", "抽奖", "面板", "熟练度", "词条"],
        "xianxia": ["灵力", "真气", "元婴", "结丹", "灵根", "法宝", "道友", "仙尊", "道韵", "剑意"],
        "wuxia": ["内力", "江湖", "门派", "掌门", "轻功", "刀法", "剑法", "武林"],
        "suspense": ["尸体", "现场", "凶手", "刑警", "法医", "线索", "报案", "嫌疑"],
        "apocalypse": ["末世", "丧尸", "幸存者", "基地", "物资", "废土", "感染"],
        "infinite": ["副本", "轮回", "主神", "规则怪谈", "通关", "任务目标"],
        "urban": ["公司", "老板", "手机", "地铁", "加班", "外卖", "房租", "小区"],
        "ancient_romance": ["王爷", "娘娘", "陛下", "本宫", "臣妾", "嫡女", "庶女", "凤印"],
    }
    best, best_hits = "unknown", []
    for k, words in cues.items():
        hits = [w for w in words if w in head]
        if len(hits) > len(best_hits):
            best, best_hits = k, hits
    if not best_hits:
        return "unknown", 0.0, []
    conf = min(1.0, len(best_hits) / 4.0)
    return best, conf, best_hits


GENRE_CN = {
    "system": "系统流/签到流",
    "xianxia": "仙侠/修真",
    "wuxia": "武侠",
    "suspense": "悬疑/刑侦",
    "apocalypse": "末世/废土",
    "infinite": "无限流/规则怪谈",
    "urban": "都市/日常",
    "ancient_romance": "古言/宫廷",
    "unknown": "未识别",
}
