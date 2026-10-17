# -*- coding: utf-8 -*-
"""浏览器端入口：文本进 → 完整 HTML 报告出。

**这个模块不发起任何网络请求，也不调用任何大模型。**
网页版的全部计算都发生在用户自己的浏览器里（Pyodide / WebAssembly），
稿子一个字都不会离开这台设备。

逻辑漏洞（--coherence）与已有作品识别（--existing）在网页版里是**可选**的：
这两项需要模型判断，默认关闭。用户若自己在别处拿到了判断结果，
可以粘贴进「高级选项」里一起计分——那部分的判断成本跟本网页无关。
"""
import json


def make_report(text: str, title: str = "",
                existing_json: str = "", coherence_json: str = "") -> str:
    """返回 JSON 字符串：{ok, html, risk, band, interval, han} 或 {ok:false, error}。"""
    import analyze
    import render_html
    import coherence as coherence_module

    existing = None
    if existing_json and existing_json.strip():
        try:
            existing = json.loads(existing_json)
        except Exception as e:
            return json.dumps({"ok": False, "error": f"已有作品判断不是合法 JSON：{e}"},
                              ensure_ascii=False)

    coh = None
    if coherence_json and coherence_json.strip():
        try:
            coh = coherence_module.normalize(json.loads(coherence_json))
        except Exception as e:
            return json.dumps({"ok": False, "error": f"逻辑漏洞判断无法解析：{e}"},
                              ensure_ascii=False)

    try:
        a = analyze.analyze_text(text, coherence=coh)
    except Exception as e:
        return json.dumps({"ok": False, "error": f"分析出错：{type(e).__name__}: {e}"},
                          ensure_ascii=False)
    if a.get("empty"):
        return json.dumps({"ok": False, "error": a.get("error", "无法分析")},
                          ensure_ascii=False)

    try:
        rep = analyze.build_report(a["text"], a, source_name=title or "网页检测",
                                   existing=existing, coherence=coh)
        html = render_html.render(rep)
    except Exception as e:
        return json.dumps({"ok": False, "error": f"生成报告出错：{type(e).__name__}: {e}"},
                          ensure_ascii=False)

    r = rep["result"]
    return json.dumps({
        "ok": True,
        "html": html,
        "risk": round(r["risk"], 1),
        "band": r["band"],
        "interval": [round(r["risk_interval"][0], 1), round(r["risk_interval"][1], 1)],
        "han": rep["features"]["length"]["han_total"],
    }, ensure_ascii=False)
