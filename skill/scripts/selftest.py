# -*- coding: utf-8 -*-
"""
selftest —— 修改规则或词表后**必须**跑的自检。

两道检查：
  1. 内联核心样本：随脚本分发，任何环境（含全局安装副本）都能跑。
     覆盖三类最关键的失败模式：误报人写、漏报 AI、对话流误报。
  2. 外部语料集：若存在 corpus/ 目录则一并检查（开发环境用）。

通过线（硬要求）：
  人写样本  必须 < 40        —— 误报比漏报致命，作者会立刻不信工具
  AI 样本   必须 ≥ 56
  碎片化 AI 必须落在 36–70   —— 检出但不许声称极高
"""
import glob
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
import analyze  # noqa: E402

# 语料目录可能处在不同位置，取决于这份技能是开发态还是从仓库分发的：
#   开发态：  <root>/skill/scripts/          → <root>/corpus
#   分发布局：<repo>/skills/<name>/scripts/  → <repo>/corpus
#   技能内部：<name>/scripts/                → <name>/corpus
# 找不到就跳过第二道（属正常），但找到就必须全绿。
_CORPUS_CANDIDATES = [
    os.path.normpath(os.path.join(_HERE, "..", "..", "corpus")),
    os.path.normpath(os.path.join(_HERE, "..", "..", "..", "corpus")),
    os.path.normpath(os.path.join(_HERE, "..", "corpus")),
]
CORPUS = next((p for p in _CORPUS_CANDIDATES if os.path.isdir(p)), _CORPUS_CANDIDATES[0])


# --------------------------------------------------------------------------
# 内联核心样本
# --------------------------------------------------------------------------
# 说明：这些样本随脚本分发，是回归保护的最低保障。
# 一旦有人（包括未来改进工具的人）改坏了规则，这三条会立刻报警。

INLINE = [
    {
        "id": "inline_human_colloquial",
        "kind": "human",
        "why": "人写·市井口语（含语气词、半角标点、口语省略）。检查误报。",
        "text": """老王蹲在马路牙子上抽烟，抽到一半让城管撵了，只好挪到公交站牌后头继续抽。

他今天本来是去要账的。

欠他钱的是他表弟，三年前借的两万，说是周转两个月就还。两个月后老王打电话过去，表弟说再宽限宽限，老王心一软就答应了。这一宽限，就宽限到了现在。

“所以说啊，”老王把烟屁股摁灭在站牌杆上，“人心隔肚皮。”

旁边等车的大妈瞅了他一眼，往边上挪了半步。

老王也不在意，掏出手机，翻出表弟的号，盯着看了半天，没打。上回打过去，表弟媳妇接的，哭天抢地说家里揭不开锅，孩子上幼儿园的钱都凑不齐。老王挂了电话，自己给自己倒了杯酒，喝完了把杯子往桌上一墩。

“凑不齐，凑不齐你倒是把车停门口啊。”

那辆日产就停在他表弟家楼下，崭新的，车屁股上还贴着“baby in car”。

公交车来了，大妈上去了，老王没上。他今天没事干，专门出来走这一趟，也没想好到底要不要上楼。

他掏出烟盒，空的。

“得，白来。”

老王把烟盒捏扁，扔进垃圾桶，转身往回走。走了两步又停下，回头看了一眼表弟家的窗户。窗帘拉着，阳台晾着两件小孩的衣服，风一吹，晃悠。

“算了。”

他掏出一块钱，去小卖部买了包最便宜的烟，撕开，点上一根，边走边抽。走到路口等红灯的时候，旁边一个穿校服的小子骑电动车蹭了他一下，头也不回地窜出去了。

老王在后面骂了一句。骂完自己乐了。

“跟个孩子较什么劲。”

红灯变绿，老王跟着人流过了马路。烟抽完了，他把烟头弹进路边的水沟里，手插进裤兜，慢慢往家走。

家里还有半碗剩饭，中午热一热，就着咸菜。下午睡一觉，晚上出去摆摊。

日子还得过。
""",
    },
    {
        "id": "inline_human_dialogue_flow",
        "kind": "human",
        "why": ("人写·双人对白流。**这条是防回归的关键用例**："
                "段落必然交替以两个角色名开头，早期版本会把这种正常叙事结构"
                "误判为「段落开头机械重复」，把风险分推高 16 分。"),
        "text": """“你是说，你有两个异能？”

“一个F级，一个S级？”

林昭看着一脸认真的陆行舟，一副你在逗我玩的表情。

他用手摸了摸陆行舟的额头，调侃道。

“也没有发烧啊？怎么还说起胡话了？”

“胖子，我没开玩笑。”

陆行舟将林昭的手拉开，一股能量汇聚，他的额头浮现出淡蓝色纹路，像一片枫叶。

林昭看着这不合常理的一幕，连忙凑近，又摸摸陆行舟的额头。

“我记得你的异能不是火球术吗？你这真不是发光贴？”

“我有那么无聊？拿发光贴来逗你玩？”

陆行舟无奈地指了指额头。

“那可还真不一定？这种事你还干得少？”

林昭撇撇嘴，表示怀疑。

陆行舟闻言，倒没有半点不好意思。

“我那是智商测试，免得你将肌肉练得代替大脑思考。”

“不过话说回来，这一次我倒是没有骗你。”

陆行舟看向窗外，天上两个太阳将地表烤得有些发烫，花圃里的月落花病怏怏的，似乎命不久矣。

林昭瞟了一眼陆行舟的额头，那片枫叶状的纹路，变得明亮起来。

“你看看这个。”

陆行舟从旁边的桌上拿起玻璃瓶，直接在林昭面前开始演示。

一块崭新的、棱角分明、充满能量的晶核出现在林昭的面前。

“这是......”

感受到熟悉的能量波动，林昭顿时瞪大了双眼，他猛地拿起晶核，仔细检查着。

片刻后，林昭眼中闪着光芒，一脸激动地看向陆行舟。

“林哥，我们发财了。这就是你的那个S级异能？你......”

话还没有说完，林昭顿时脸色一变，扶住了突然往后倒的陆行舟。

“林哥，你这是怎么了？”

“难道是因为异能？”

陆行舟坐在一旁的椅子上，喘着粗气，好一会儿才缓过来。

看着一脸担忧的林昭，陆行舟苦笑道：

“胖子，发财你就别想了。”
""",
    },
    {
        "id": "inline_ai_dense_cliche",
        "kind": "ai",
        "why": "AI·高浓度文艺腔言情。检查漏报与「极高」档是否可达。",
        "text": """沈清歌站在山巅之上，望着远处翻涌的云海，心中涌起一股难以言喻的复杂情绪。

山风拂过她的衣袂，带来一丝清冽的凉意。她的目光落在那片无边无际的白色之上，仿佛在看一场无声的潮汐，又仿佛在审视自己这些年来走过的路。

三年前，她还是那个被家族遗弃的少女。没有人相信她能够活下来，更没有人相信她能够站在这里。然而，她做到了。

命运总是如此奇妙。

她缓缓闭上眼睛，任由山风将她的长发吹起。那些曾经让她辗转难眠的夜晚，那些曾经让她痛彻心扉的背叛，那些曾经让她几近崩溃的绝望——在这一刻，都化作了淡淡的释然。

不是所有的伤口都会痊愈，但所有的伤口都会结痂。

她睁开眼睛，眼底深处是一片平静。那不是麻木，也不是妥协，而是一种历经千帆后的坦然。

“清歌。”

一道温润的声音从身后传来。

她转过身。是顾长渊。

他站在几步之外，玄色的衣袍被风吹得猎猎作响，脸上带着一抹淡淡的笑意。那双深邃的眸子望过来的时候，仿佛盛着整个星河的温柔。

“你来了。”沈清歌轻声说道。

“我说过，会来。”顾长渊缓步走近，在离她半步的地方停下，“三年前如此，三年后亦是如此。”

沈清歌怔怔地看着他，心中五味杂陈。

她想起三年前那个血色的黄昏，想起他挡在她身前的那个身影，想起他在她耳边说的那句“活下去”。那时候她以为那只是临死前的安慰，以为他们之间只剩下最后的告别。

然而，他活着回来了。

“顾长渊，”她轻声开口，声音里带着一丝不易察觉的颤抖，“你有没有想过，如果我们当年……”

“没有如果。”顾长渊打断了她，目光坚定而温柔，“清歌，我们走过的每一步，都算数。”

那一刻，沈清歌的眼眶湿润了。

她忽然明白，所谓宿命，不是无法挣脱的枷锁，而是两个人在漫长的岁月里，一次又一次做出的同一个选择。

山巅的风依旧在吹，云海依旧在翻涌。

然而这一刻，整个世界都仿佛安静了下来。

沈清歌看着他，终于露出了一个真正的、发自内心的笑容。那笑容里有释然，有感激，也有一种终于尘埃落定的安宁。

“好。”她说，“那我们，重新开始。”

顾长渊伸出手。她把手放进他的掌心。

温热的触感传来，像是握住了余生的所有可能。

远处的天边，一缕金光刺破了厚重的云层。

天亮了。

而这，只是他们故事的开始。
""",
    },
]

EXPECT_INLINE = {
    "human": (0, 40, "人写→必须 <40（误报是致命的）"),
    "ai": (56, 100, "AI→必须 ≥56"),
    "ambiguous": (36, 70, "碎片化AI→检出但不得声称极高"),
}

EXPECT_CORPUS = {
    "human_": (0, 40, "人写→应落 极低/偏低"),
    "ai_": (56, 100, "AI→应落 偏高/极高"),
    "edge_": (0, 62, "边界→可落中档，但不得极高"),
    # 碎片化 AI：设计意图是"检出但别宣称顶格"。
    # v1.3 提高 A 档权重后它被更明确地检出，上限放宽到 85（仍不允许顶到 100）。
    "ambiguous_": (36, 85, "碎片化AI→应检出，但不应顶格"),
}


def _check(name, risk, kind, fails, expect_map, extra=""):
    lo, hi, desc = expect_map[kind]
    ok = lo <= risk <= hi
    flag = "✓" if ok else "✗"
    print(f"  {flag} {name:<34}{risk:>7.1f}  {lo:>3}–{hi:<3}  {extra}")
    if not ok:
        fails.append(f"[{desc}] {name}: 风险={risk:.1f}，期望 {lo}–{hi}")
    return ok


def check_rule_integrity(sample_texts) -> list:
    """规则表完整性检查：防"规则在表里但永远不触发"的死代码。

    背景（v1.1 实际踩到的坑）：`ThresholdRule` 用 metric 字符串取指标，
    早期版本 `metric()` 只支持特征字典的虚线路径，不支持以 "_" 开头的
    **模块内计算函数**，于是一批规则永远拿到 None、**从未生效过**。
    更隐蔽的是还有两条规则把函数名写错（`_clause_cv` vs `_clause_len_cv`），
    表面上完全看不出来。

    这类 bug 不会报错、不会崩，只是悄悄少检出一部分特征。
    所以必须有一条结构性检查：**每条阈值规则的 metric 必须能在某个样本上取到值**。
    """
    import rules as R

    problems = []
    ctxs = []
    for t in sample_texts:
        a = analyze.analyze_text(t)
        if a.get("empty"):
            continue
        f = a["features"]
        ctxs.append(R.RuleContext(a["text"], f, analyze.features.load_lexicon(),
                                  genre=f.get("genre", {}).get("key", "unknown")))
    if not ctxs:
        return ["完整性检查：没有可用的样本上下文"]

    rules = R.build_rules()

    # 1) ID 唯一
    ids = [r.id for r in rules]
    dup = {i for i in ids if ids.count(i) > 1}
    if dup:
        problems.append(f"规则 ID 重复：{sorted(dup)}")

    # 2) 分类合法
    for r in rules:
        if r.category not in R.CATEGORIES:
            problems.append(f"{r.id}: 分类 {r.category!r} 不在 CATEGORIES 里")

    # 3) 每个 ThresholdRule 的 metric 至少在一个样本上取得到值
    dead = []
    for r in rules:
        if not isinstance(r, R.ThresholdRule):
            continue
        vals = [c.metric(r.metric) for c in ctxs]
        if all(v is None for v in vals):
            dead.append(f"{r.id} → metric={r.metric!r}")
    if dead:
        problems.append("以下阈值规则取不到指标，永远不会触发（很可能是名字写错或解析不支持）：\n"
                        + "\n".join("      " + d for d in dead))

    # 4) 每个 PatternRule 的抽取器至少能跑通
    for r in rules:
        if isinstance(r, R.PatternRule):
            try:
                hits, _ = r.extractor(ctxs[0])
                if not isinstance(hits, int):
                    problems.append(f"{r.id}: 抽取器没返回 (int, list)")
            except Exception as e:
                problems.append(f"{r.id}: 抽取器抛异常 {type(e).__name__}: {e}")

    # 5) 分类方向检查：反向规则不能挂在风险分类下
    # 背景：PUNCT_HALFWIDTH（半角标点＝人味证据）曾误挂在 punct（风险分类）下，
    # 于是人味证据反而给作者加风险分（实测加了 16.5 分），方向完全反了。
    # 这种错不会报错、指标也能取到值，只有专门检查"分类方向"才能发现。
    for r in rules:
        meta = R.CATEGORIES[r.category]
        if meta["risk"] and (r.id.startswith("OPN_") or "反向项" in (r.explain or "")
                             or "人味证据" in r.title):
            problems.append(f"{r.id}: 是人味证据/反向规则，却挂在风险分类 {r.category!r} 下"
                            f"（会让它给作者加风险分）")
        if (not meta["risk"]) and not (r.id.startswith("OPN_") or r.category == "openness"):
            problems.append(f"{r.id}: 挂在 openness 分类下，但看起来不是反向规则")

    return problems


def main():
    fails = []
    rows = []

    print("=" * 78)
    print("第一道：内联核心样本（随脚本分发，任何环境可跑）")
    print("=" * 78)
    for s in INLINE:
        a = analyze.analyze_text(s["text"])
        if a.get("empty"):
            fails.append(f"{s['id']}: 分析失败 {a.get('error')}")
            continue
        r = a["result"]
        f = a["features"]
        top = ", ".join(f"{x['id']}({x['contribution']:.1f})" for x in r["findings"][:2]) or "（无命中）"
        _check(s["id"], r["risk"], s["kind"], fails, EXPECT_INLINE,
               f"{r['band']}  汉字{f['length']['han_total']}  人味{r['openness_score']:.0f}  {top}")
        rows.append({"file": s["id"], "risk": r["risk"], "band": r["band"],
                     "han": f["length"]["han_total"], "openness": r["openness_score"],
                     "why": s["why"]})
    print()
    for s in INLINE:
        print(f"    · {s['id']}：{s['why']}")

    print()
    print("=" * 78)
    print("第三道：规则表完整性（防死代码）")
    print("=" * 78)
    integrity = check_rule_integrity([s["text"] for s in INLINE])
    if integrity:
        for p in integrity:
            print("  ✗ " + p)
        fails.append("规则表完整性检查未通过")
    else:
        print("  ✓ 所有规则的指标/抽取器均可解析，ID 与分类合法")

    print()
    files = sorted(glob.glob(os.path.join(CORPUS, "*.txt")))
    if not files:
        print("=" * 78)
        print(f"第二道：外部语料集 —— 未找到（{CORPUS}），跳过")
        print("        （开发环境下应存在 corpus/；分发的技能副本不含语料属正常）")
        print("=" * 78)
    else:
        print("=" * 78)
        print(f"第二道：外部语料集（{len(files)} 份）")
        print("=" * 78)
        for fp in files:
            name = os.path.basename(fp)
            a = analyze.analyze_text(analyze.read_text(fp))
            if a.get("empty"):
                continue
            r, f = a["result"], a["features"]
            kind = next((k for k in EXPECT_CORPUS if name.startswith(k)), None)
            if kind is None:
                continue
            top = ", ".join(f"{x['id']}({x['contribution']:.1f})" for x in r["findings"][:2]) or "（无命中）"
            _check(name, r["risk"], kind, fails, EXPECT_CORPUS,
                   f"{r['band']}  汉字{f['length']['han_total']}  人味{r['openness_score']:.0f}  {top}")
            rows.append({"file": name, "risk": r["risk"], "band": r["band"],
                         "han": f["length"]["han_total"], "openness": r["openness_score"]})

    print()
    print("=" * 78)
    if fails:
        print("!! 未通过：")
        for x in fails:
            print("   -", x)
        print()
        print("若本次改动是有意的（例如调整了权重），请同步更新本文档顶部的通过线，")
        print("并在 references/calibration.md 记录新的实测结果与原因。")
    else:
        print("✓ 全部样本通过期望区间")
    print("=" * 78)

    out = os.path.join(_HERE, "_selftest_result.json")
    try:
        with open(out, "w", encoding="utf-8") as fh:
            json.dump(rows, fh, ensure_ascii=False, indent=2)
    except OSError:
        pass
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
