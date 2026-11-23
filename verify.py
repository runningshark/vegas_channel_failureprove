# -*- coding: utf-8 -*-
"""
复现脚本：python verify.py
只用 Python 自带模块。读取 data/ 里的币安K线快照，复现维纳斯通道引擎的关键检验：

  1. 消融测试：逐个禁用 主区/隧道/EMA12过滤/MACD/斐位，看哪个条件在拖后腿
  2. 前视偏差：S1 日线主区用"当天最终收盘"(前视)/"当天进行中"/"昨日收盘"三种口径
  3. 长周期：旧4H主区 vs S1日线主区，去龙虾
  4. 参数网格 36 组：样本内(0-4000) 调参 -> 样本外(4000-6000) 验证，检验过拟合

统一约定（所有检验一致）：
  * 从 WARM=700 根 4H 之后才开始交易（指标热身）
  * 信号在 4H bar 收盘产生，下一根 4H 开盘成交，单边 0.05% 费
  * 每个币独立账本起始 100，组合按权重归一化（含龙虾时权重和为 1.05，归一化到 1）
  * 龙虾数据长度短于主流币时，数据结束后的部分按现金持有，不截断整个组合

结果写到 results/output.txt。
"""
import gzip
import json
import math
import os
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
COINS = [("BTCUSDT", 0.45), ("ETHUSDT", 0.25),
         ("SOLUSDT", 0.15), ("XRPUSDT", 0.15)]   # 去龙虾，权重和=1
LOBSTER = ("LOBSTER", 0.05)
ALL = COINS + [LOBSTER]
WARM = 700
FEE = 0.0005
REBAL = 0.02
SPLIT = 4000          # 参数扫描的样本内/外分界
SHORT = 4000          # 短窗口 = 最近2000根4H(4000-6000)。原项目fetch(2000)取的是末尾2000根，
                      # 因此档案里必须用 4000 起点才是同一段行情(2025-10~2026-11)。
TOTAL = 6000

# 默认参数（项目实盘口径）
S1_FAST, S1_SLOW = 20, 60
TUN_FAST, TUN_SLOW = 144, 169
FILT = 12
MACD_F, MACD_S, MACD_SIG = 12, 26, 9
FIB_MAX, FIB_STOP = 0.62, 0.786
SWING = 120

_IND_CACHE = {}


def load(tag, period):
    path = os.path.join(DATA, f"{tag}_{period}.json.gz")
    with gzip.open(path, "rt", encoding="utf-8") as f:
        d = json.load(f)
    return {
        "tag": tag, "period": period, "n": len(d["t"]),
        "t": d["t"], "o": d["o"], "h": d["h"], "l": d["l"], "c": d["c"],
        "day": [datetime.fromtimestamp(ms/1000, timezone.utc).date().isoformat()
                for ms in d["t"]],
    }


def ema(vals, span):
    a = 2.0 / (span + 1)
    out = [vals[0]]
    for v in vals[1:]:
        out.append(out[-1] + a * (v - out[-1]))
    return out


def indicators(K):
    c = K["c"]
    n = len(c)
    ef, es = ema(c, MACD_F), ema(c, MACD_S)
    dif = [a - b for a, b in zip(ef, es)]
    retr = [float("nan")] * n
    for i in range(SWING, n):
        win = c[i-SWING:i+1]
        hi, lo = max(win), min(win)
        retr[i] = (hi - c[i]) / (hi - lo) if hi > lo else float("nan")
    return {
        "e12": ema(c, FILT), "e144": ema(c, TUN_FAST),
        "e169": ema(c, TUN_SLOW), "e576": ema(c, 576), "e676": ema(c, 676),
        "dif": dif, "dea": ema(dif, MACD_SIG), "retr": retr,
    }


def get_ind(K):
    key = K["tag"] + "_" + K["period"]
    if key not in _IND_CACHE:
        _IND_CACHE[key] = indicators(K)
    return _IND_CACHE[key]


def s1_allow(K, daily, mode="live", sf=S1_FAST, ss=S1_SLOW):
    """4H bar 的 S1 日线主区(bool数组)。
    same_final = 用当天日线最终收盘(前视，旧变体B的口径)
    live       = 用当天进行中日线(收盘=当前4H收盘)，无前视，等价看板实时逻辑
    prev       = 用昨日已收盘日线，最保守，无前视
    """
    n = K["n"]
    dc = {d: c for d, c in zip(daily["day"], daily["c"])}
    days_sorted = sorted(dc)
    kdays = K["day"]
    allow = [False] * n
    if mode == "same_final":
        cache = {}
        for day in sorted(set(kdays)):
            seq = [dc[d] for d in days_sorted if d <= day]
            if len(seq) < ss:
                continue
            cache[day] = (ema(seq, sf)[-1], ema(seq, ss)[-1], seq[-1])
        for i in range(n):
            v = cache.get(kdays[i])
            if v:
                allow[i] = bool(v[0] > v[1] and v[2] > v[0])
    elif mode == "prev":
        cache = {}
        for day in sorted(set(kdays)):
            seq = [dc[d] for d in days_sorted if d < day]
            if len(seq) < ss:
                continue
            e_f = ema(seq, sf)[-1]
            e_s = ema(seq, ss)[-1]
            cache[day] = bool(e_f > e_s and seq[-1] > e_f)
        for i in range(n):
            allow[i] = cache.get(kdays[i], False)
    else:  # live
        asof = {}
        for day in sorted(set(kdays)):
            seq = [dc[d] for d in days_sorted if d < day]
            if len(seq) < ss:
                continue
            asof[day] = (ema(seq, sf)[-1], ema(seq, ss)[-1])
        af, a_s = 2.0/(sf+1), 2.0/(ss+1)
        for i in range(n):
            base = asof.get(kdays[i])
            if base is None:
                continue
            e_f, e_s = base
            cur = K["c"][i]
            e_f += af * (cur - e_f)
            e_s += a_s * (cur - e_s)
            allow[i] = bool(e_f > e_s and cur > e_f)
    return allow


def target_series(K, daily, regime="score", s1mode="live", disabled=frozenset(),
                  sf=S1_FAST, ss=S1_SLOW, tf=TUN_FAST, ts=TUN_SLOW, fibmax=FIB_MAX):
    """regime: score=无开关只按共振分仓位(消融口径) / old=旧4H主区开关 /
    s1=S1日线主区开关。disabled 里的条件视为满分(消融)。"""
    ind = get_ind(K)
    n = K["n"]
    if regime == "s1":
        allow = s1_allow(K, daily, s1mode, sf, ss)
    elif regime == "old":
        allow = [ind["e576"][i] > ind["e676"][i] for i in range(n)]
    else:
        allow = [True] * n
    e_tf, e_ts = (ema(K["c"], tf), ema(K["c"], ts)) if (tf, ts) != (TUN_FAST, TUN_SLOW) \
                 else (ind["e144"], ind["e169"])
    target = [0.0] * n
    for i in range(n):
        if not allow[i]:
            continue
        c = K["c"][i]
        retr = ind["retr"][i]
        if retr != retr:
            retr = 1.0
        t = 25 if ("regime" in disabled or ind["e576"][i] > ind["e676"][i]) else 0
        if "tunnel" in disabled or (e_tf[i] > e_ts[i] and c > e_ts[i]):
            t += 20
        elif e_tf[i] > e_ts[i]:
            t += 10
        if "filt" in disabled or (ind["e12"][i] > e_ts[i] and c > ind["e12"][i]):
            t += 15
        elif c > ind["e12"][i]:
            t += 8
        if "macd" in disabled or (ind["dif"][i] > ind["dea"][i] and ind["dif"][i] > 0):
            t += 20
        elif ind["dif"][i] > ind["dea"][i]:
            t += 10
        if "fib" in disabled or retr <= fibmax:
            t += 20
        elif retr <= FIB_STOP:
            t += 8
        target[i] = t / 100.0
    return target


def sim_one(K, target, s0, s1):
    """[s0,s1) 权益数组，起点100，返回长度 s1-s0。"""
    cash, units, curf = 100.0, 0.0, 0.0
    eq = []
    for i in range(s0, s1 - 1):
        f = target[i]
        if abs(f - curf) >= REBAL:
            px = K["o"][i+1]
            val = cash + units * px
            d = f * val - units * px
            if d > 0:
                cash -= d
                units += d * (1 - FEE) / px
            elif d < 0:
                cash += (-d) * (1 - FEE)
                units += d / px
            curf = f
        eq.append(cash + units * K["c"][i+1])
    if units > 0:
        cash += units * K["c"][s1-1] * (1 - 2*FEE)
    if eq:
        eq[-1] = cash
    return eq or [100.0]


_REF = None


def ref_timeline():
    """以 BTC 4H 时间戳为全局时间轴，各币按时间戳对齐（龙虾数据更短）。"""
    global _REF
    if _REF is None:
        _REF = load("BTCUSDT", "4h")
    return _REF


def portfolio(s0, s1, regime="score", s1mode="live", disabled=frozenset(),
              include_lobster=True, **kw):
    """返回 (区间收益, 最大回撤)。s0/s1 是全局时间轴索引，统一从 WARM 起算。
    某币在窗口内没数据则全程现金；数据晚开始则前面补现金，早结束则后面按收盘持有。"""
    s0 = max(s0, WARM)
    ref = ref_timeline()
    t0, t1 = ref["t"][s0], ref["t"][s1-1]
    gidx = {t: i for i, t in enumerate(ref["t"])}
    L = s1 - s0
    eqs = {}
    wsum = 0.0
    for sym, w in (ALL if include_lobster else COINS):
        K4 = load(sym, "4h")
        Kd = load(sym, "1d")
        idx = [i for i, t in enumerate(K4["t"]) if t0 <= t <= t1]
        if not idx:
            eq = [100.0] * L
        else:
            a, b = idx[0], idx[-1] + 1
            tg = target_series(K4, Kd, regime, s1mode, disabled, **kw)
            part = sim_one(K4, tg, a, b)
            lead = max(gidx.get(K4["t"][a], s0) - s0, 0)
            eq = [100.0]*lead + part + [part[-1]]*L
            eq = eq[:L]
        eqs[sym] = (eq, w)
        wsum += w
    port = [sum(eq[i] * (w/wsum) for eq, w in eqs.values()) for i in range(L)]
    peak = port[0]
    mdd = 0.0
    for v in port:
        peak = max(peak, v)
        mdd = min(mdd, v/peak - 1)
    return port[-1]/100.0 - 1, mdd


def buyhold(s0, s1, include_lobster=True):
    s0 = max(s0, WARM)
    ref = ref_timeline()
    t0, t1 = ref["t"][s0], ref["t"][s1-1]
    tot = wsum = 0.0
    for sym, w in (ALL if include_lobster else COINS):
        K = load(sym, "4h")
        idx = [i for i, t in enumerate(K["t"]) if t0 <= t <= t1]
        if len(idx) < 2:
            continue
        tot += (K["c"][idx[-1]]/K["c"][idx[0]] - 1) * w
        wsum += w
    return tot/wsum if wsum else 0.0


def section(title):
    print("\n" + "="*90, flush=True)
    print(title, flush=True)
    print("="*90, flush=True)


def main():
    lines = []
    def out(s=""):
        print(s, flush=True)
        lines.append(s)

    section("检验0  数据快照")
    for sym, _ in ALL:
        k4 = load(sym, "4h"); kd = load(sym, "1d")
        out("%-10s 4H %5d 根 %s ~ %s | 1d %4d 根" % (
            sym, k4["n"], k4["day"][0], k4["day"][-1], kd["n"]))

    # ---- 检验1: 消融（无开关，只按共振分；禁用=视为满分） ----
    section("检验1  消融测试（最近2000根4H 4000-6000 ≈ 2025-10~2026-11，无主区开关，去/含龙虾）")
    out("%-16s %10s %10s" % ("配置", "含龙虾", "去龙虾"))
    out("-"*40)
    cfgs = [("baseline", frozenset()),
            ("no_regime", frozenset({"regime"})),
            ("no_tunnel", frozenset({"tunnel"})),
            ("no_filt", frozenset({"filt"})),
            ("no_macd", frozenset({"macd"})),
            ("no_fib", frozenset({"fib"}))]
    for name, dis in cfgs:
        a, _ = portfolio(SHORT, TOTAL, regime="score", disabled=dis, include_lobster=True)
        b, _ = portfolio(SHORT, TOTAL, regime="score", disabled=dis, include_lobster=False)
        out("%-16s %+9.2f%% %+9.2f%%" % (name, a*100, b*100))

    # ---- 检验2: 前视偏差 ----
    section("检验2  S1 日线口径：前视偏差量化（去龙虾，最近2000根4H）")
    out("%-28s %10s %10s" % ("日线口径", "收益", "回撤"))
    out("-"*52)
    for mode, label in [("same_final", "当天日线最终收盘(前视)"),
                        ("live", "当天进行中(看板口径)"),
                        ("prev", "昨日已收盘(最保守)")]:
        r, dd = portfolio(SHORT, TOTAL, regime="s1", s1mode=mode, include_lobster=False)
        out("%-28s %+9.2f%% %+9.2f%%" % (label, r*100, dd*100))
    r, dd = portfolio(SHORT, TOTAL, regime="old", include_lobster=False)
    out("%-28s %+9.2f%% %+9.2f%%" % ("旧4H主区(对照)", r*100, dd*100))

    # ---- 检验3: 长周期 ----
    section("检验3  长周期（4主流币6000根4H，2024-02~2026-11，去龙虾）")
    out("%-28s %10s %10s %10s" % ("配置", "收益", "回撤", "买入持有"))
    out("-"*62)
    bh7 = buyhold(SHORT, TOTAL, include_lobster=False)
    bh7_all = buyhold(SHORT, TOTAL, include_lobster=True)
    bhlong = buyhold(0, TOTAL, include_lobster=False)
    r7, dd7 = portfolio(SHORT, TOTAL, regime="s1", s1mode="live", include_lobster=False)
    r7a, dd7a = portfolio(SHORT, TOTAL, regime="s1", s1mode="live", include_lobster=True)
    out("%-28s %+9.2f%% %+9.2f%% %+9.2f%%" % ("执行层S1(短窗口,含龙虾)", r7a*100, dd7a*100, bh7_all*100))
    out("%-28s %+9.2f%% %+9.2f%% %+9.2f%%" % ("执行层S1(短窗口,去龙虾)", r7*100, dd7*100, bh7*100))
    for regime, label, mode in [("old", "旧4H主区(长周期)", "same_final"),
                                ("s1", "S1日线主区(长周期)", "live")]:
        r, dd = portfolio(0, TOTAL, regime=regime, s1mode=mode, include_lobster=False)
        out("%-28s %+9.2f%% %+9.2f%% %+9.2f%%" % (label, r*100, dd*100, bhlong*100))

    # ---- 检验4: 参数网格样本内/外 ----
    section("检验4  参数网格36组：样本内(700-4000)调参 → 样本外(4000-6000)验证，去龙虾")
    out("%-32s %10s %10s" % ("参数(S1;隧道;斐位)", "样本内", "样本外"))
    out("-"*56)
    grid = [(a,b,c,d,e) for a,b in [(10,30),(20,60),(30,90),(50,150)]
            for c,d in [(100,117),(144,169),(200,235)] for e in [0.50, 0.62, 0.75]]
    rows = []
    for sf,ss,tf,ts,fib in grid:
        ins,_ = portfolio(0, SPLIT, regime="s1", s1mode="live", include_lobster=False,
                          sf=sf, ss=ss, tf=tf, ts=ts, fibmax=fib)
        outs,_ = portfolio(SPLIT, TOTAL, regime="s1", s1mode="live", include_lobster=False,
                           sf=sf, ss=ss, tf=tf, ts=ts, fibmax=fib)
        rows.append(((sf,ss,tf,ts,fib), ins, outs))
    top = sorted(rows, key=lambda r: -r[1])[:5]
    for r in top:
        out("%-32s %+9.2f%% %+9.2f%%" % (str(r[0]), r[1]*100, r[2]*100))
    dflt = next(r for r in rows if r[0] == (20,60,144,169,0.62))
    out("%-32s %+9.2f%% %+9.2f%%  <-默认参数" % (str(dflt[0]), dflt[1]*100, dflt[2]*100))
    best_out = max(rows, key=lambda r: r[2])
    ins_v = [r[1] for r in rows]; outs_v = [r[2] for r in rows]
    mi, mo = sum(ins_v)/len(ins_v), sum(outs_v)/len(outs_v)
    cov = sum((a-mi)*(b-mo) for a,b in zip(ins_v,outs_v))
    vi = sum((a-mi)**2 for a in ins_v); vo = sum((b-mo)**2 for b in outs_v)
    corr = cov/math.sqrt(vi*vo) if vi>0 and vo>0 else 0.0
    out("-"*56)
    out("样本内最优: %s in=%+.2f%% out=%+.2f%%" %
        (top[0][0], top[0][1]*100, top[0][2]*100))
    out("样本外最优: %s in=%+.2f%% out=%+.2f%%" %
        (best_out[0], best_out[1]*100, best_out[2]*100))
    out("样本内/外收益相关系数: %.3f" % corr)
    out("样本外中位数: %+.2f%%  最好: %+.2f%%  最差: %+.2f%%" %
        (sorted(outs_v)[len(outs_v)//2]*100, max(outs_v)*100, min(outs_v)*100))

    os.makedirs(os.path.join(HERE, "results"), exist_ok=True)
    with open(os.path.join(HERE, "results", "output.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print("\nresults/output.txt 已写入", flush=True)


if __name__ == "__main__":
    main()
