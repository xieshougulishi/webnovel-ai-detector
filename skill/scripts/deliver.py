# -*- coding: utf-8 -*-
"""
deliver —— 检测 + 自动交付。用户不该手敲 Python 命令，这一步必须是默认动作。

用法：
    python deliver.py --text "正文……"                  # 直接给正文
    python deliver.py --file "我的稿子.txt"             # 给文件（.txt/.md/.docx）
    python deliver.py --text "……" --title "第12章"      # 指定报告标题

做的事：
    1. 跑分析，产出 html + md + json
    2. 把 HTML **复制到固定交付路径**（链接永远不变，用户收藏一次就够）
    3. 把 Markdown 报告全文打到 stdout（飞书/TUI 回复正文直接可读）
    4. 最后一行输出 DELIVER 行，含可直接点击的 file:// 链接

固定交付路径设计说明：
    对话框里的链接必须是**稳定**的。所以每次检测都覆盖同一个文件，
    而不是生成一堆带时间戳的报告让用户找。历史报告仍保留在 <out>/history/。
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import shutil
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import analyze  # noqa: E402
import existing_work  # noqa: E402
import coherence as coherence_module  # noqa: E402

# 默认交付路径。
#
# 开发态：__file__ 在 <工作区>/skill/scripts/，上溯两级正好是工作区根。
# 但技能装到别处时（例如 ~/.dsh/skills/<name>/scripts/），上溯两级会变成
# **安装目录本身**——报告会写进 ~/.dsh/skills/检测报告，污染全局目录，且用户找不到。
# 所以这里做一次判断：上溯两级若是个 skills 安装目录，就退回调用者的当前目录。
_UP_TWO = os.path.normpath(os.path.join(_HERE, "..", ".."))
_IS_INSTALL_DIR = os.path.basename(_UP_TWO).lower() in ("skills", "skill_installs")
WORKSPACE = os.getcwd() if _IS_INSTALL_DIR else _UP_TWO
DELIVER_DIR = os.path.join(WORKSPACE, "检测报告")
LATEST_HTML = os.path.join(DELIVER_DIR, "最新检测报告.html")
LATEST_MD = os.path.join(DELIVER_DIR, "最新检测报告.md")


def file_url(path: str) -> str:
    p = os.path.abspath(path).replace("\\", "/")
    return "file:///" + p


def run(text: str, title: str = "", out_dir: str = None, quiet_md: bool = False,
        workspace: str = None, existing: dict = None, coherence: dict = None) -> dict:
    ws = workspace or WORKSPACE
    deliver_dir = os.path.join(ws, "检测报告") if workspace else DELIVER_DIR
    latest_html = os.path.join(deliver_dir, "最新检测报告.html")
    latest_md = os.path.join(deliver_dir, "最新检测报告.md")

    analysis = analyze.analyze_text(text, coherence=None)
    if analysis.get("empty"):
        return {"ok": False, "error": analysis.get("error", "无法分析")}

    ex = None
    if existing:
        try:
            ex = existing_work.normalize(existing)
        except ValueError as e:
            return {"ok": False, "error": f"已有作品判断结果无效：{e}",
                    "schema": existing_work.SCHEMA_HINT}

    coh = None
    if coherence:
        try:
            coh = coherence_module.normalize(coherence)
        except ValueError as e:
            return {"ok": False, "error": f"逻辑漏洞判断结果无效：{e}",
                    "schema": coherence_module.SCHEMA_HINT}
    if coh:
        # 逻辑漏洞要参与计分，必须重跑一次分析（带 extra findings）
        analysis = analyze.analyze_text(text, coherence=coh)

    name = title or "检测样本"
    safe = "".join(c for c in name if c not in '\\/:*?"<>|').strip() or "检测样本"
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    out = out_dir or os.path.join(ws, "ai-check-report")

    report = analyze.build_report(analysis["text"], analysis, source_name=name,
                                  existing=ex, coherence=coh)
    os.makedirs(out, exist_ok=True)

    json_p = os.path.join(out, f"{safe}.report.json")
    md_p = os.path.join(out, f"{safe}.report.md")
    html_p = os.path.join(out, f"{safe}.report.html")

    with open(json_p, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    import render_md
    import render_html
    md = render_md.render(report)
    with open(md_p, "w", encoding="utf-8") as f:
        f.write(md)
    with open(html_p, "w", encoding="utf-8") as f:
        f.write(render_html.render(report))

    # 固定交付路径（覆盖），并留一份带时间戳的历史
    os.makedirs(deliver_dir, exist_ok=True)
    shutil.copyfile(html_p, latest_html)
    shutil.copyfile(md_p, latest_md)
    hist = os.path.join(deliver_dir, "history")
    os.makedirs(hist, exist_ok=True)
    shutil.copyfile(html_p, os.path.join(hist, f"{stamp}-{safe}.html"))

    res = analysis["result"]
    feat = analysis["features"]
    return {
        "ok": True,
        "title": name,
        "band": res["band"],
        "risk": res["risk"],
        "risk_interval": res["risk_interval"],
        "confidence": res["confidence_level"],
        "han_total": feat["length"]["han_total"],
        "openness_score": res["openness_score"],
        "openness_discount": res["openness_discount"],
        "top_evidence": [
            {"title": f["title"], "category": f["category_cn"],
             "contrib": f["contribution"], "observed": f.get("observed"),
             "baseline": f.get("baseline"), "unit": f.get("unit", ""),
             "quote": (f.get("sample") or {}).get("text", "")}
            for f in res["findings"] if f["category"] != "openness"
        ][:5],
        "prescriptions": [
            {"title": p["title"], "contrib": p["contrib"], "fix": p["fix"]}
            for p in report["prescriptions"][:3]
        ],
        "human_evidence": report["human_evidence"],
        "caveats": res["caveats"],
        "existing_work": report.get("existing_work"),
        "latest_html": latest_html,
        "latest_md": latest_md,
        "url": file_url(latest_html),
        "files": {"json": json_p, "md": md_p, "html": html_p},
        "markdown": None if quiet_md else md,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="网文 AI 痕迹检测 · 自动交付")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--text", help="直接给正文")
    g.add_argument("--file", help="给文件路径（.txt/.md/.docx）")
    ap.add_argument("--title", default="", help="报告标题（默认取文件名）")
    ap.add_argument("--existing", default=None,
                    help="已有作品识别结果：JSON 文件路径或 JSON 字符串（由 agent/LLM 判断）。"
                         "不传则报告中该项标注为「未做」")
    ap.add_argument("--coherence", default=None,
                    help="逻辑漏洞判断结果（JSON 文件路径或字符串）。**由 agent/LLM 判断**，"
                         "格式见 coherence.SCHEMA_HINT。逻辑漏洞按高权重计入总分")
    ap.add_argument("--out", default=None, help="中间产物目录")
    ap.add_argument("--workspace", default=None, help="会话工作区（默认自动推导）")
    ap.add_argument("--no-md", action="store_true", help="不把 Markdown 报告打到 stdout")
    args = ap.parse_args(argv)

    existing = None
    if args.existing:
        raw = args.existing
        if os.path.isfile(raw):
            with open(raw, "r", encoding="utf-8") as f:
                raw = f.read()
        try:
            existing = json.loads(raw)
        except json.JSONDecodeError as e:
            print(json.dumps({"ok": False, "error": f"--existing 不是合法 JSON：{e}",
                              "schema": existing_work.SCHEMA_HINT}, ensure_ascii=False))
            return 2

    coh_raw = None
    if args.coherence:
        raw = args.coherence
        if os.path.isfile(raw):
            with open(raw, "r", encoding="utf-8") as f:
                raw = f.read()
        try:
            coh_raw = json.loads(raw)
        except json.JSONDecodeError as e:
            print(json.dumps({"ok": False, "error": f"--coherence 不是合法 JSON：{e}",
                              "schema": coherence_module.SCHEMA_HINT}, ensure_ascii=False))
            return 2

    if args.file:
        if not os.path.isfile(args.file):
            print(json.dumps({"ok": False, "error": f"文件不存在：{args.file}"},
                             ensure_ascii=False))
            return 2
        text = analyze.read_text(args.file)
        title = args.title or os.path.splitext(os.path.basename(args.file))[0]
    else:
        text = args.text or ""
        title = args.title or "检测样本"

    r = run(text, title=title, out_dir=args.out, quiet_md=args.no_md,
            workspace=args.workspace, existing=existing, coherence=coh_raw)

    if not r.get("ok"):
        print(json.dumps(r, ensure_ascii=False, indent=2))
        return 1

    md = r.pop("markdown", None)
    if md:
        sys.stdout.write(md)
        sys.stdout.write("\n\n")

    # 机读摘要 + 交付链接（放在最后，便于 agent 解析）
    print("DELIVER " + json.dumps({
        "band": r["band"], "risk": r["risk"], "interval": r["risk_interval"],
        "confidence": r["confidence"], "han": r["han_total"],
        "existing_work": r.get("existing_work"),
        "url": r["url"], "latest_html": r["latest_html"], "latest_md": r["latest_md"],
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
