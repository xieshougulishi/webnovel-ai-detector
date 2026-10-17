# -*- coding: utf-8 -*-
"""
analyze —— 网文 AI 含量分析主程序。

用法：
    python analyze.py <文本文件> [--out 报告目录] [--json] [--md] [--stdout]
    python analyze.py <文本文件> --format json

输出：
    <out>/<name>.report.json   完整结构化结果（供其他工具消费）
    <out>/<name>.report.md     纯文本报告（TUI/飞书/无浏览器环境）
    <out>/<name>.report.html   可视化报告（单文件离线，主交付物）
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import datetime
from typing import Any, Dict

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import features  # noqa: E402
import existing_work  # noqa: E402
import coherence as coherence_module  # noqa: E402
import rules as rules_mod  # noqa: E402
from textlib import normalize  # noqa: E402

__version__ = "1.3.0"


# --------------------------------------------------------------------------
# 输入读取
# --------------------------------------------------------------------------

def read_text(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    if ext == ".docx":
        return _read_docx(path)
    if ext in (".md", ".txt", ".text", ""):
        for enc in ("utf-8-sig", "utf-8", "gb18030", "gbk", "big5"):
            try:
                with open(path, "r", encoding=enc) as f:
                    return f.read()
            except (UnicodeDecodeError, LookupError):
                continue
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    raise SystemExit(f"不支持的文件类型：{ext}（支持 .txt / .md / .docx）")


def _read_docx(path: str) -> str:
    """读取 .docx。优先用 python-docx（若可用），否则手工解 XML（零依赖兜底）。"""
    try:
        from docx import Document  # type: ignore
        doc = Document(path)
        return "\n".join(p.text for p in doc.paragraphs)
    except Exception:
        pass
    import zipfile
    import xml.etree.ElementTree as ET
    try:
        with zipfile.ZipFile(path) as z:
            xml = z.read("word/document.xml")
    except Exception as e:
        raise SystemExit(f"无法读取 docx：{e}")
    ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    root = ET.fromstring(xml)
    paras = []
    for p in root.iter(f"{ns}p"):
        buf = []
        for t in p.iter(f"{ns}t"):
            buf.append(t.text or "")
        paras.append("".join(buf))
    return "\n".join(paras)


# --------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------

def analyze_text(text: str, lex_path: str = None,
                 coherence: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    text = normalize(text)
    lex = features.load_lexicon(lex_path)
    feat = features.extract(text, lex)
    if feat.get("empty"):
        return {"empty": True, "error": "文本为空或没有可分析的段落"}
    han = feat["length"]["han_total"]
    # 中文字符太少：本工具的词表、基线与分句规则都是为中文网文设计的，
    # 对纯英文/纯符号文本下结论是错误行为，直接拒绝而不是硬算一个分数。
    if han < 30:
        return {
            "empty": True,
            "error": (f"正文汉字仅 {han} 个，不构成可分析的中文文本。"
                      "本工具的基线、词表与分句规则均为中文网文设计，"
                      "对纯英文或纯符号文本不做判定。"),
        }
    extra = coherence_module.to_findings(coherence) if coherence else None
    result = rules_mod.evaluate(text, feat, lex, extra_findings=extra)
    return {"empty": False, "features": feat, "result": result, "text": text}


def build_report(text: str, analysis: Dict[str, Any], source_name: str = "",
                 existing: Optional[Dict[str, Any]] = None,
                 coherence: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """组装报告所需的一切（供 HTML / MD 渲染共用）。

    existing:  已有作品识别结果（由 agent/LLM 判断后传入，见 existing_work.py）。
    coherence: 逻辑漏洞判断结果（由 agent/LLM 判断，见 coherence.py）。
               两者为 None 表示本次未做该项判断 —— 报告会如实说明"未做"。
    """
    if analysis.get("empty"):
        return {"error": analysis.get("error", "无内容")}
    feat = analysis["features"]
    res = analysis["result"]

    # 证据 → 逐处标注（按位置排序，供正文标注视图使用）
    marks = []
    for f in res["findings"]:
        if f["category"] == "openness":
            continue
        for s in (f.get("samples") or ([f["sample"]] if f.get("sample") else [])):
            if not s:
                continue
            marks.append({
                "start": s["start"], "end": s["end"], "text": s.get("text", ""),
                "rule": f["id"], "title": f["title"], "category": f["category"],
                "category_cn": f["category_cn"], "severity": f["severity"],
                "explain": f["explain"], "fix": f["fix"],
                "observed": f.get("observed"), "baseline": f.get("baseline"),
                "contrib": f["contribution"],
            })
    # 去重（同一区间被多条规则命中时保留贡献最高的一条）
    dedup: Dict[tuple, Dict[str, Any]] = {}
    for m in marks:
        k = (m["start"], m["end"])
        if k not in dedup or m["contrib"] > dedup[k]["contrib"]:
            dedup[k] = m
    marks = sorted(dedup.values(), key=lambda m: (-m["contrib"], m["start"]))

    # 人味证据（用于「误判对冲」区）
    human_evidence = []
    hv = feat.get("human_voice", {}).get("detail", {})
    label_map = {
        "colloquial": "口语/俗语用词", "profanity": "粗话/脏话",
        "dialect": "方言用词", "hesitation": "犹豫、不确定的表述",
        "self_correction": "自我更正/改口", "sentence_particles": "语气助词",
    }
    for k, v in hv.items():
        if v.get("count", 0) > 0:
            human_evidence.append({
                "kind": label_map.get(k, k), "count": v["count"],
                "per_k": v["per_k"],
                "words": sorted(v["by_word"].items(), key=lambda x: -x[1])[:12],
            })
    de_prof = feat.get("de_di_de", {})
    if de_prof.get("de_adv_nonstandard", 0) > 0:
        human_evidence.append({
            "kind": "状语「地」写成「的」（写作噪声）",
            "count": de_prof["de_adv_nonstandard"], "per_k": None,
            "words": [("不规范写法次数", de_prof["de_adv_nonstandard"])],
        })

    # 处方：按性价比排序（贡献 / 改动成本）
    prescriptions = []
    for f in res["findings"]:
        if f["category"] == "openness":
            continue
        cost = {"lexical": 1, "rhetoric": 2, "punct": 1, "rhythm": 4,
                "structure": 2, "dialogue": 3, "emotion": 3}.get(f["category"], 3)
        prescriptions.append({
            "rule": f["id"], "title": f["title"], "category_cn": f["category_cn"],
            "contrib": f["contribution"], "cost": cost,
            "value": round(f["contribution"] / cost, 3),
            "fix": f["fix"], "observed": f.get("observed"),
            "baseline": f.get("baseline"), "unit": f.get("unit", ""),
            "sample": f.get("sample"),
        })
    prescriptions.sort(key=lambda p: -p["value"])

    return {
        "meta": {
            "source": source_name,
            "generated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "engine_version": __version__,
            "lexicon_version": features.load_lexicon().get("version", "?"),
        },
        "existing_work": existing_work.banner_model(existing) if existing else None,
        "existing_work_raw": existing,
        "coherence": coherence,
        "text": text,
        "features": feat,
        "result": res,
        "marks": marks,
        "human_evidence": human_evidence,
        "prescriptions": prescriptions,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="网文 AI 含量检测（可解释版）")
    ap.add_argument("input", help="文本文件路径（.txt/.md/.docx）")
    ap.add_argument("--out", default=None, help="报告输出目录（默认：输入文件同级的 ai-check-report/）")
    ap.add_argument("--format", default="html,md,json",
                    help="输出格式，逗号分隔：html,md,json（默认全出）")
    ap.add_argument("--lexicon", default=None, help="自定义词表 JSON 路径")
    ap.add_argument("--existing", default=None,
                    help="已有作品识别结果：JSON 文件路径，或直接给 JSON 字符串。"
                         "由 agent/LLM 判断后传入；不传则报告中该项标注为「未做」")
    ap.add_argument("--coherence", default=None,
                    help="逻辑漏洞判断结果（JSON 文件路径或字符串）。**由 agent/LLM 判断**，"
                         "格式见 coherence.SCHEMA_HINT。逻辑漏洞按高权重计入总分")
    ap.add_argument("--stdout", action="store_true", help="把 Markdown 报告打到标准输出")
    args = ap.parse_args(argv)

    if not os.path.isfile(args.input):
        print(f"文件不存在：{args.input}", file=sys.stderr)
        return 2

    coh = None
    if args.coherence:
        raw_c = args.coherence
        if os.path.isfile(raw_c):
            with open(raw_c, "r", encoding="utf-8") as f:
                raw_c = f.read()
        try:
            coh = coherence_module.normalize(json.loads(raw_c))
        except (ValueError, json.JSONDecodeError) as e:
            print(json.dumps({"ok": False, "error": f"逻辑漏洞判断结果无法解析：{e}",
                              "schema": coherence_module.SCHEMA_HINT}, ensure_ascii=False))
            return 2

    existing = None
    if args.existing:
        raw_txt = args.existing
        if os.path.isfile(raw_txt):
            with open(raw_txt, "r", encoding="utf-8") as f:
                raw_txt = f.read()
        try:
            existing = existing_work.normalize(json.loads(raw_txt))
        except (ValueError, json.JSONDecodeError) as e:
            print(json.dumps({"ok": False, "error": f"已有作品判断结果无法解析：{e}",
                              "schema": existing_work.SCHEMA_HINT}, ensure_ascii=False))
            return 2

    text = read_text(args.input)
    analysis = analyze_text(text, args.lexicon, coherence=coh)
    if analysis.get("empty"):
        print(json.dumps({"error": analysis.get("error")}, ensure_ascii=False))
        return 1

    name = os.path.splitext(os.path.basename(args.input))[0]
    out_dir = args.out or os.path.join(os.path.dirname(os.path.abspath(args.input)), "ai-check-report")
    os.makedirs(out_dir, exist_ok=True)

    report = build_report(text, analysis, source_name=os.path.basename(args.input),
                          existing=existing)
    formats = {f.strip() for f in args.format.split(",") if f.strip()}
    written = []

    if "json" in formats:
        p = os.path.join(out_dir, f"{name}.report.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        written.append(p)

    if "md" in formats:
        import render_md
        p = os.path.join(out_dir, f"{name}.report.md")
        md = render_md.render(report)
        with open(p, "w", encoding="utf-8") as f:
            f.write(md)
        written.append(p)
        if args.stdout:
            print(md)

    if "html" in formats:
        import render_html
        p = os.path.join(out_dir, f"{name}.report.html")
        with open(p, "w", encoding="utf-8") as f:
            f.write(render_html.render(report))
        written.append(p)

    summary = {
        "ok": True,
        "band": report["result"]["band"],
        "risk": report["result"]["risk"],
        "risk_interval": report["result"]["risk_interval"],
        "confidence": report["result"]["confidence_level"],
        "han_total": report["features"]["length"]["han_total"],
        "existing_work": report.get("existing_work"),
        "top_evidence": [
            {"rule": f["id"], "title": f["title"], "contrib": f["contribution"],
             "sample": (f.get("sample") or {}).get("text", "")}
            for f in report["result"]["findings"][:5]
        ],
        "files": written,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
