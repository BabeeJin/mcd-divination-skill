"""玄易点麦 · 卦象决策引擎（纯标准库，可 CLI / 可单测）。

用途：
    把"今天吃什么麦当劳"的决策疲劳，转化为"处境 -> 配卦 -> 饮食宜忌 -> 心愿彩头 -> 吉祥话"
    的结构化推演。本模块只负责"配卦"与"彩头映射"的纯逻辑，不直接调用任何 MCP；
    真实菜单 / 价格 / 门店由 SKILL.md 工作流里的 mcd-mcp 工具负责。

依赖：仅 json / argparse / os / sys 标准库，无任何第三方包。
用法：
    python xuanyi_engine.py --help
    python xuanyi_engine.py --hunger low --wish job
    python xuanyi_engine.py --date 07-20 --hunger normal
    python xuanyi_engine.py --hunger high --wish exam --json
"""

import argparse
import json
import os
import sys

# 数据文件默认与主脚本同目录，保证从任意 cwd 调用都能读到。
DEFAULT_DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hexagrams.json")

# 合法取值（用于 CLI 校验与文档）。
VALID = {
    "hunger": ["low", "normal", "high"],
    "season": ["normal", "autumn"],
    "wish": ["none", "job", "exam", "promote", "love", "health"],
    "scene": ["office", "home", "out"],
}

# state.season 与卦象 season 标签的映射（数据里写的是中文"秋燥"）。
SEASON_LABEL = {"normal": "平季", "autumn": "秋燥"}


def load_data(path=None):
    """读取卦象映射数据。path 缺省时取同目录下的 hexagrams.json。"""
    path = path or DEFAULT_DATA
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _norm_mmdd(s):
    """'07/20' 或 '7/20' -> '07-20'；无法解析返回 None。"""
    if not s:
        return None
    s = s.strip().replace("/", "-")
    parts = s.split("-")
    if len(parts) != 2:
        return None
    try:
        m, d = int(parts[0]), int(parts[1])
    except ValueError:
        return None
    if not (1 <= m <= 12 and 1 <= d <= 31):
        return None
    return "{0:02d}-{1:02d}".format(m, d)


def month_day_to_term(md):
    """MM-DD -> 节气分组（春生 / 夏暑 / 秋燥 / 冬寒）；未匹配返回 None。"""
    norm = _norm_mmdd(md)
    if not norm:
        return None
    m, d = norm.split("-")
    val = int(m) * 100 + int(d)
    # 冬寒跨年：[1107,1231] 或 [0101,0204]
    if 1107 <= val <= 1231 or 104 <= val <= 204:
        return "冬寒"
    if 204 < val <= 505:
        return "春生"
    if 505 < val <= 807:
        return "夏暑"
    if 807 < val <= 1107:
        return "秋燥"
    return None


def _score(hexagram, state):
    """给单个卦象打分：饥饿度权重最高，其次场景，时令为软加成。"""
    score = 0
    match = hexagram.get("match", {}) or {}
    if match.get("hunger") and state.get("hunger") == match.get("hunger"):
        score += 3
    if match.get("scene") and state.get("scene") == match.get("scene"):
        score += 2
    # 时令加成：仅当处境为秋燥、且卦象 season 含"秋燥"时 +1
    if state.get("season") == "autumn" and "秋燥" in (hexagram.get("season") or []):
        score += 1
    return score


def _select_hexagram(data, state):
    """按打分选最合适卦象；同分时取数据中出现更早者（确定性）。"""
    best = None
    best_score = -1
    for h in data.get("hexagrams", []):
        s = _score(h, state)
        if s > best_score:
            best = h
            best_score = s
    return best


def decide(state, data=None):
    """依据处境 dict 返回结构化决策结果。

    state 字段：
        hunger: low / normal / high
        season: normal / autumn（缺省退化用）
        wish:   none / job / exam / promote / love
        scene:  office / home / out
        term:   显式节气分组（春生/夏暑/秋燥/冬寒），优先级最高
        date:   MM-DD，用于推导节气(天时轴)并识别特殊日期(生日/纪念日)
    返回 dict：
        hexagram, tagline, term, term_note, yi, ji, recommendation_hint,
        cai_tou, ji_xiang, egg, special, taboo
        （ji_xiang = 吉祥话；cai_tou = 心愿彩头/特殊日期彩头；egg = 彩蛋吉祥话）
    """
    if data is None:
        data = load_data()

    # 规整 / 兜底，避免缺字段导致 KeyError。
    s = {
        "hunger": state.get("hunger", "normal"),
        "season": state.get("season", "normal"),
        "wish": state.get("wish", "none"),
        "scene": state.get("scene", "office"),
    }

    # —— 节气（天时轴）解析：term > date 推导 > season 退化 ——
    term = state.get("term")
    date_str = state.get("date")
    if not term and date_str:
        term = month_day_to_term(date_str)
    if not term:
        term = "秋燥" if s["season"] == "autumn" else None
    term_info = (data.get("solar_terms", {}) or {}).get(term, {}) if term else {}

    hexagram = _select_hexagram(data, s)
    if hexagram is None:
        raise ValueError("hexagrams.json 中没有任何可用卦象数据")

    yi = list(hexagram.get("diet_yi", []))
    ji = list(hexagram.get("diet_ji", []))
    # 时令调制：把 solar_terms 的 yi/ji 去重并入基础宜忌。
    for x in term_info.get("yi", []):
        if x not in yi:
            yi.append(x)
    for x in term_info.get("ji", []):
        if x not in ji:
            ji.append(x)

    # —— 特殊日期（生日/纪念日）识别：叠加心愿轴 ——
    special = None
    if date_str:
        norm = _norm_mmdd(date_str)
        for sd in (data.get("special_dates", []) or []):
            if sd.get("mmdd") == norm:
                special = sd
                break

    # —— 心愿彩头 + 吉祥话（特殊日期层与心愿层独立共存）——
    cai_tou = None
    ji_xiang = hexagram.get("auspicious", "")
    wish = s["wish"]
    wish_map = hexagram.get("wishes", {}) or {}

    # 特殊日期层（生日/纪念日/节日）：作为"今日彩头"载体。
    sp_cai = None
    if special:
        sp_wish = special.get("wish", "special")
        sp_cai = {
            "wish": sp_wish,
            "occasion": special.get("occasion", ""),
            "product": "",
            "meaning": special.get("meaning", ""),
        }
        if sp_wish in wish_map and wish_map[sp_wish].get("product"):
            sp_cai["product"] = wish_map[sp_wish]["product"]

    # 心愿层（job/exam/promote/love/health…）：用户显式心愿，优先决定吉祥话。
    w_cai = None
    if wish and wish != "none" and wish in wish_map:
        w = wish_map[wish]
        w_cai = {"wish": wish, "product": w.get("product", ""), "meaning": w.get("meaning", "")}
        ji_xiang = w["auspicious"]

    # 合并：两层共存；特殊日期作"今日彩头"occasion，心愿层补充 meaning/product 与吉祥话。
    if sp_cai and w_cai:
        cai_tou = {
            "wish": w_cai["wish"],
            "occasion": sp_cai["occasion"],
            "product": w_cai["product"] or sp_cai["product"],
            "meaning": sp_cai["meaning"] + "；" + w_cai["meaning"],
        }
    elif sp_cai:
        cai_tou = sp_cai
        sp_wish = special.get("wish", "special")
        if sp_wish in wish_map and wish_map[sp_wish].get("auspicious"):
            ji_xiang = wish_map[sp_wish]["auspicious"]
    elif w_cai:
        cai_tou = w_cai

    egg = special.get("auspicious", "") if special else ""

    season_text = SEASON_LABEL.get(s["season"], s["season"])
    term_text = term or season_text
    recommendation_hint = (
        "推荐方向：{yi_top}；避免{ji_top}。结合处境（{scene}·{hunger}·{term}）"
        "从真实在售菜单里筛 1–2 套方案，再算价。"
    ).format(
        yi_top="、".join(yi[:3]) if yi else "温润清淡",
        ji_top="、".join(ji[:2]) if ji else "油腻冰饮",
        scene=s["scene"],
        hunger=s["hunger"],
        term=term_text,
    )

    taboo = "忌{s_ji}；以七分饱、温润节制为度，不诱导超量或浪费。".format(
        s_ji="、".join(ji[:3]) if ji else "油腻冰饮"
    )

    return {
        "hexagram": hexagram.get("name", ""),
        "tagline": hexagram.get("tagline", ""),
        "term": term,
        "term_note": term_info.get("note", ""),
        "yi": yi,
        "ji": ji,
        "recommendation_hint": recommendation_hint,
        "cai_tou": cai_tou,
        "ji_xiang": ji_xiang,
        "egg": egg,
        "special": special.get("occasion") if special else None,
        "taboo": taboo,
    }


def _to_human(result, location=""):
    """把结构化结果渲染成人类可读文本（全程不出现"谐音"二字）。"""
    lines = []
    lines.append("【玄易点麦 · 卦象决策】")
    lines.append("卦象：{hexagram}（{tagline}）".format(**result))
    lines.append("")
    if result.get("term"):
        lines.append("时令调制（{term}）：{note}".format(
            term=result["term"], note=result.get("term_note", "")))
    lines.append("饮食宜：" + ("、".join(result["yi"]) if result["yi"] else "—"))
    lines.append("饮食忌：" + ("、".join(result["ji"]) if result["ji"] else "—"))
    lines.append("")
    lines.append(result["recommendation_hint"])
    if result.get("cai_tou"):
        c = result["cai_tou"]
        if c.get("occasion"):
            label = "今日彩头（{0}）".format(c["occasion"])
        else:
            label = "心愿彩头"
        extra = "（{0}）".format(c["product"]) if c.get("product") else ""
        lines.append("{label}：{meaning}{extra}".format(
            label=label, meaning=c.get("meaning", ""), extra=extra))
    lines.append("吉祥话：" + (result.get("ji_xiang") or "—"))
    if result.get("egg"):
        lines.append("🎂 彩蛋吉祥话：" + result["egg"])
    lines.append("禁忌提示：" + result["taboo"])
    if location:
        lines.append("定位参考：" + location)
    lines.append("")
    lines.append("（趣味决策参考，不构成营养 / 医疗建议；真实菜单与价格以麦当劳官方渠道实时结果为准）")
    return "\n".join(lines)


def _build_arg_parser():
    p = argparse.ArgumentParser(
        prog="xuanyi_engine.py",
        description="玄易点麦卦象决策引擎：依处境配卦并给出心愿彩头与吉祥话。",
    )
    p.add_argument("--hunger", choices=VALID["hunger"], default="normal",
                   help="饥饿度：low（不饿）/ normal（一般）/ high（很饿）")
    p.add_argument("--season", choices=VALID["season"], default="normal",
                   help="时令体质：normal（平季）/ autumn（秋燥）")
    p.add_argument("--wish", choices=VALID["wish"], default="none",
                   help="心愿轴：none / job（求职）/ exam（考试）/ promote（升职）/ love（脱单）/ health（备孕·健康调理）")
    p.add_argument("--scene", choices=VALID["scene"], default="office",
                   help="场景：office（公司）/ home（居家）/ out（外出）")
    p.add_argument("--term", default=None,
                   help="显式节气分组：春生 / 夏暑 / 秋燥 / 冬寒（缺省由 --date 或 --season 推导）")
    p.add_argument("--date", default=None,
                   help="当前日期 MM-DD（如 07-20），推导节气并识别特殊日期（生日/纪念日）")
    p.add_argument("--location", default="",
                   help="定位参考（仅展示用，不影响配卦，如：公司附近 / 当前位置）")
    p.add_argument("--json", action="store_true",
                   help="以 JSON 形式输出结构化结果（默认人类可读文本）")
    p.add_argument("--data", default=None,
                   help="卦象数据文件路径（默认同目录 hexagrams.json）")
    return p


def main(argv=None):
    args = _build_arg_parser().parse_args(argv)
    state = {
        "hunger": args.hunger,
        "season": args.season,
        "wish": args.wish,
        "scene": args.scene,
        "term": args.term,
        "date": args.date,
    }
    data = load_data(args.data) if args.data else load_data()
    result = decide(state, data)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(_to_human(result, location=args.location))
    return 0


if __name__ == "__main__":
    sys.exit(main())
