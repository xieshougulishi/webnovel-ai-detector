# -*- coding: utf-8 -*-
"""
render_md —— 纯文本报告（TUI / 飞书 / 无浏览器环境）。

对应 HTML 报告的核心信息，但压缩为可读文本，方便复制到申诉邮件里。
"""
from __future__ import annotations

from typing import Any, Dict, List

BAND_MARK = {
    "极低": "[极低]", "偏低": "[偏低]", "中等": "[中等]",
    "偏高": "[偏高]", "极高": "[极高]", "无法判定": "[无法判定]",
}

CONF_CN = {"high": "高", "medium": "中", "low": "低"}

DISCLAIMER_ITEMS_MD = [
    "页面内容由 AI 生成，只做 **AI 含量**的判断，并提供修改意见。",
    "**不保证结果一定准确**：给的是概率性风险区间，不是鉴定结论；"
    "已知会低估经过人工改写的 AI 文本、也可能高估高度程式化的类型文。",
    "**不评判作品好坏**：风险分低只说明「不像机器写的」，不代表写得好；"
    "风险分高也不代表作品差——它只描述文体特征。",
    "**是否相信页面内容，请用户自行判断。**",
]


def _clean(s: str) -> str:
    return str(s).replace("**", "")


def render(report: Dict[str, Any]) -> str:
    if report.get("error"):
        return f"# 分析失败\n\n{report['error']}\n"
    meta, feat, res = report["meta"], report["features"], report["result"]
    L = feat["length"]
    S = feat["sentence"]
    P = feat["paragraph"]
    pu = feat["punct"]
    lx = feat["lexical"]

    out: List[str] = []
    w = out.append

    # ---- 通用声明：放在最前面 ----
    w("> **声明**：页面内容由 AI 生成，请自行判断是否采信。")
    w(">")
    for _it in DISCLAIMER_ITEMS_MD:
        w(f"> - {_it}")
    w("")

    ex = report.get("existing_work")

    # ---- 已有作品识别：只在检测到时出现（未识别 / 未做都不占篇幅）----
    if ex and ex.get("level") in ("matched", "partial"):
        w("> # ⚠ 提醒：本文为已有作品内容")
        w(">")
        w(f"> **{ex.get('title') or '（未填写作品名）'}**　{ex.get('author') or '未知作者'}"
          + (f"　{ex['era']}" if ex.get("era") else ""))
        w(">")
        w(f"> 识别置信度：{CONF_CN.get(ex.get('confidence'), '?')}"
          + (f"　｜　占本文比例：约 {ex['coverage']:.0%}"
             if isinstance(ex.get("coverage"), (int, float)) else ""))
        w(">")
        w("> 下面的「AI 痕迹风险分」对已有作品**没有意义**——名篇大多写于 AI 出现之前，"
          "低分只说明它是人写的，不能说明它**是你的**。若这不是你的作品，请不要以自己名义发布或投稿。")
        w("")
        if ex.get("evidence"):
            w("**判断依据**")
            w("")
            for e in ex["evidence"]:
                w(f"- {e}")
            w("")
        if ex.get("distinguishing_line"):
            w(f"最能确认出处的一句：`{ex['distinguishing_line']}`")
            w("")
        if ex.get("reproduction_note"):
            w(f"**需要注意**：{ex['reproduction_note']}")
            w("")
        if ex.get("verify_note"):
            w(f"**核验建议**：{ex['verify_note']}")
            w("")
        if ex.get("warnings"):
            w("**本次判断的自检提示**（供复核，不影响上面的结论）：")
            w("")
            for x in ex["warnings"]:
                w(f"- {x}")
            w("")
        w("---")
        w("")

    w(f"# 网文 AI 痕迹检测报告")
    w("")
    w(f"- 文件：`{meta['source']}`")
    w(f"- 规模：{L['han_total']} 汉字 / {L['paragraphs']} 段 / {L['sentences']} 句")
    w(f"- 生成于：{meta['generated_at']}（引擎 v{meta['engine_version']}，词表 v{meta['lexicon_version']}）")
    w("")
    w("## 一、结论")
    w("")
    w(f"**AI 痕迹风险分：{res['risk']:.0f} / 100　　{BAND_MARK.get(res['band'], res['band'])}**")
    w(f"**风险区间：{res['risk_interval'][0]:.0f} – {res['risk_interval'][1]:.0f}**"
      f"（区间外的情况本工具无法排除）")
    w("")
    w(_clean(res["band_text"]))
    w("")
    w(f"- 七个嫌疑分类合计 {res['S_total']:.2f} 分 → 饱和换算 {res['risk_raw']:.1f} 分")
    if res["openness_discount"] > 0:
        w(f"- 人味证据强度 {res['openness_score']:.0f}/100 → 抵扣 {res['openness_discount']:.1f} 分")
    w("")

    w("## 二、分类风险")
    w("")
    from rules import CATEGORIES
    for k, m in CATEGORIES.items():
        if not m["risk"]:
            continue
        w(f"- {m['cn']}：{res['category_scores'][k]:.0f} / 100")
    w(f"- 留白与野性（人味，反向）：{res['category_scores']['openness']:.0f} / 100")
    w("")

    # ---- 逻辑漏洞（权重最高，单独成节）----
    coh = report.get("coherence")
    if coh and coh.get("holes"):
        w(f"## 二之二、逻辑漏洞（{len(coh['holes'])} 处）")
        w("")
        w("> 这一项**权重高于所有文体类规则**：用词可以模仿，**逻辑崩了就是崩了**。"
          "一个作者可能不小心写出 AI 腔，但不会在一千字里自我矛盾四五次。")
        if coh.get("summary"):
            w("")
            w(f"**整体判断**：{coh['summary']}")
        w("")
        for i, h in enumerate(coh["holes"], 1):
            w(f"**{i}. {h.get('type_cn', '逻辑问题')}**（严重度 {h.get('severity', 3)}）")
            w("")
            w(f"- 为什么：{h.get('why', '')}")
            w(f"- 原文：`{h.get('quote', '')}`")
            w("")
        if coh.get("note"):
            w(f"> 判断说明：{coh['note']}")
            w("")

    w("## 三、体检表（客观指标 vs 人类基线）")
    w("")
    w("| 指标 | 你的文本 | 人类基线 |")
    w("|---|---|---|")
    rows = [
        ("对白字符占比", f"{L['dialogue_char_ratio']:.1%}", "20%–50%"),
        ("平均句长（字）", f"{S['mean_han']:.1f}", "8–22"),
        ("句长变异系数", f"{S['cv_han']:.3f}", "≥0.45"),
        ("句长四分位距 IQR", f"{S['iqr']:.1f}", "≥6"),
        ("短句占比（≤5 字）", f"{S['short_ratio_le5']:.1%}", "≥8%"),
        ("段长变异系数", f"{P['cv_chars']:.3f}", "≥0.35"),
        ("单行短段占比", f"{P['one_line_ratio']:.1%}", "≥25%"),
        ("标点种类数", f"{pu['distinct_punct_kinds']}", "≥4"),
        ("逗号+句号占比", f"{pu['comma_period_share']:.1%}", "≤90%"),
        ("半角标点混入占比", f"{pu['halfwidth_ratio']:.1%}", "≥2% 为人味证据"),
        ("文艺腔套话密度", f"{lx['artistic_cliche']['per_k']:.2f}", "≤1.2 /千字"),
        ("抽象情绪词密度", f"{lx['abstract_emotion']['per_k']:.2f}", "≤6.0 /千字"),
        ("通用身体反应密度", f"{lx['generic_body']['per_k']:.2f}", "≤5.0 /千字"),
        ("书面连接词密度", f"{lx['written_connector']['per_k']:.2f}", "≤9.0 /千字"),
        ("总结/升华词密度", f"{lx['summary_habit']['per_k']:.2f}", "≤1.5 /千字"),
        ("对白标注种类占比", f"{feat['dialogue']['tag_variety']:.2f}", "≥0.40"),
        ("人味词总密度", f"{feat['human_voice']['per_k']:.2f}", "越高越像人"),
    ]
    for r in rows:
        w("| " + " | ".join(r) + " |")
    w("")

    w("## 四、判定证据（按贡献排序）")
    w("")
    findings = [f for f in res["findings"] if f["category"] != "openness"]
    if not findings:
        w("未命中任何 AI 痕迹规则。")
        w("")
    for i, f in enumerate(findings, 1):
        w(f"### {i}. {f['title']}　（{f['category_cn']}，贡献 {f['contribution']:.2f} 分）")
        w("")
        w(f"- 你的文本：**{_fmt(f.get('observed'))} {f.get('unit','')}**　"
          f"人类基线：{_fmt(f.get('baseline'))}　超额：{f.get('excess_ratio', 0):.2f}×")
        samples = f.get("samples") or ([f["sample"]] if f.get("sample") else [])
        if samples:
            w("- 命中原文：")
            for s in samples[:4]:
                if s:
                    w(f"  - `{s.get('text','')}`")
        w("")
        w(f"**为什么像 AI**：{_clean(f['explain'])}")
        w("")
        w(f"**怎么改**：{_clean(f['fix'])}")
        if f.get("evidence_note"):
            w("")
            w(f"> 注：{f['evidence_note']}")
        w("")

    w("## 五、修改处方（按性价比排序）")
    w("")
    for i, p in enumerate(report["prescriptions"][:8], 1):
        cost = "低" if p["cost"] <= 2 else ("中" if p["cost"] == 3 else "高")
        w(f"**{i}. {p['title']}**（可省 {p['contrib']:.1f} 分，改动成本 {cost}）")
        w("")
        w(_clean(p["fix"]))
        w("")

    w("## 六、误判对冲：支持「这是人写的」的证据")
    w("")
    he = report["human_evidence"]
    if not he:
        w("未发现典型人味证据（口语、粗话、方言、语气词、写作噪声、半角标点）。"
          "**这不构成 AI 证据**，但意味着没有可用于申诉的对冲材料。")
    else:
        for h in he:
            words = "、".join(f"{k}×{v}" for k, v in h["words"][:10])
            pk = f"（{h['per_k']:.2f}/千字）" if h.get("per_k") is not None else ""
            w(f"- **{h['kind']}**：{h['count']} 次{pk} — {words}")
    w("")

    w("## 七、申诉可用论据")
    w("")
    for i, x in enumerate(_appeal_points(res, feat), 1):
        w(f"{i}. {x}")
        w("")

    w("## 八、不确定性与已知失效场景")
    w("")
    for c in res.get("caveats", []):
        w(f"- {_clean(c)}")
    w("- 若文本经过人工改写或 AI 辅助润色，本工具倾向于**低估**。")
    w("- 若题材为系统流/签到流等高度程式化类型，人写也会句式重复，可能**高估**。")
    if res.get("skipped_rules"):
        w(f"- 因样本不足未参与评分的规则："
          f"{'；'.join(s['id'] for s in res['skipped_rules'])}")
    w("")

    w("## 九、免责声明")
    w("")
    w("本报告基于可观测的文体统计特征做概率性推断，**不构成法律意义上的鉴定结论**，"
      "不得作为认定创作主体的唯一依据。本工具无法获取任何模型的服务端信息，"
      "也无法证明文本由谁创作。")
    w("")
    return "\n".join(out)


def _fmt(v) -> str:
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:.3f}".rstrip("0").rstrip(".")
    return str(v)


def _appeal_points(res: Dict[str, Any], feat: Dict[str, Any]) -> List[str]:
    L, pu = feat["length"], feat["punct"]
    scale = ("属单章规模的完整样本。" if L["han_total"] >= 1800
             else "属片段样本（低于单章规模），单凭此样本不宜对整部作品下结论。")
    pt = [
        f"本次送检文本共 {L['han_total']} 个汉字，{L['paragraphs']} 段 {L['sentences']} 句，{scale}",
        f"检出证据 {len(res['findings'])} 条，结论区间 "
        f"{res['risk_interval'][0]:.0f}–{res['risk_interval'][1]:.0f} 分，"
        f"即本工具自认存在 {res['risk_interval'][1] - res['risk_interval'][0]:.0f} 分不确定度。",
    ]
    if res.get("sensitivity"):
        wp = res["sensitivity"][0]
        pt.append(f"单条证据敏感度：剔除贡献最大的「{wp['title']}」后风险分由 "
                  f"{res['risk']:.0f} 降至 {wp['risk_without']:.0f}（单项影响 {wp['delta']:.0f} 分）。")
    if res["openness_score"] > 0:
        pt.append(f"文本含人味特征，人味证据强度 {res['openness_score']:.0f}/100，"
                  f"已抵扣 {res['openness_discount']:.1f} 分。")
    if pu["halfwidth_ratio"] > 0.01:
        pt.append(f"含 {pu['halfwidth_adjacent']} 处半角标点混排（占 {pu['halfwidth_ratio']:.1%}）。"
                  f"中文输入法产生不了半角标点，只能来自人工敲击或跨来源复制。")
    if feat.get("de_di_de", {}).get("de_adv_nonstandard", 0) > 0:
        pt.append(f"「的/地」不规范写法 {feat['de_di_de']['de_adv_nonstandard']} 次，属真人写作噪声。")
    pt.append("方法论主张：现有 AI 检测器普遍基于困惑度与突发性，输出是概率性的，"
              "不构成对创作主体的认定。若平台以单一分数作为处置依据，"
              "可要求其公开判定阈值、检测文本范围与复核机制。")
    return pt
