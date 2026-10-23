"""S22 可视化与统计——消费 S21 formal_batch 产物，产出论文级对比数据。

六大交付物（render_all → stats_summary.json + SVG 图）：
1. 收敛曲线：实验组/对照组对齐分（同域 [-1,1]）vs 轮数，每公理一张。
2. C_H 成本曲线：struct/adapt/extend 三子维度逐轮——三子维度异质
   （锁定决策 #11），三条独立曲线，禁止合加 total。
3. 虚拟寿命统计：每公理条件 mean/std/min/max + overall。
4. 锚点验证结果表：P_W2_5 / P_M1 / P_W1 / P_C / H_L 机械验证，
   verdict ∈ {supported, falsified, not_triggered, not_tested,
   inconclusive}。表述纪律（锁定决策 #14）：操作化验证口径。
5. 量化指标：convergence_round / max_c_extend / warn_round /
   downgrade_round / virtual_lifespan / boundary_final_count。
6. 统计检验：检验单元 = 种子级运行均值（锁定决策 #9 独立性单元）。
   - 主判定：成对精确重排检验（纯 Python 全枚举 2^n，n≤20，
     确定性、无分布假设——小样本精确 p）。
   - 交叉核对：成对 t 检验（纯 Python 正则化不完全贝塔函数实现，
     与分布表等价；scipy 可用时测试交叉核对）。

反套壳对齐：全部判定硬编码纯函数；图形仅人类可读产物，
一切结论以 stats_summary.json 的结构化数值为准（铁则 #2/#4）。
SVG 为纯 Python 确定性渲染，零第三方绘图依赖。
"""

import json
import math
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from w3.log_recorder import LogRecorder

_PALETTE = ("#1f77b4", "#d62728", "#2ca02c", "#ff7f0e", "#9467bd",
            "#8c564b", "#17becf", "#e377c2", "#7f7f7f")


# ── 统计内核（纯函数，确定性）────────────────────────────────


def paired_permutation_test(exp_values, base_values, max_n=20,
                            mc_iters=10000, mc_seed=2077):
    """成对置换检验（双侧）。

    对成对差值做符号翻转枚举：n ≤ max_n 全枚举（精确、确定性）；
    n > max_n 降级为**确定性蒙特卡洛置换**（固定 mc_seed，可复现），
    返回值标注 approximate=True（S27 扩样后 pooled n=27 的支撑）。

    Raises:
        ValueError: 空输入或长度不等。
    """
    if len(exp_values) != len(base_values):
        raise ValueError("paired values must have equal length")
    diffs = [float(e) - float(b) for e, b in zip(exp_values, base_values)]
    n = len(diffs)
    if n == 0:
        raise ValueError("empty input")
    observed = sum(diffs) / n

    if n <= max_n:
        total = 1 << n
        extreme = 0
        for mask in range(total):
            s = 0.0
            for i, d in enumerate(diffs):
                s += d if (mask >> i) & 1 else -d
            if abs(s / n) >= abs(observed) - 1e-12:
                extreme += 1
        return {
            "test": "exact_paired_permutation_two_sided",
            "n": n,
            "observed_mean_diff": observed,
            "p_value": extreme / total,
            "enumerated": total,
            "approximate": False,
        }

    # 确定性蒙特卡洛置换（n > max_n）：固定 seed 保证可复现
    import random
    rng = random.Random(mc_seed)
    extreme = 0
    for _ in range(mc_iters):
        s = 0.0
        for d in diffs:
            s += d if rng.getrandbits(1) else -d
        if abs(s / n) >= abs(observed) - 1e-12:
            extreme += 1
    return {
        "test": "mc_paired_permutation_two_sided",
        "n": n,
        "observed_mean_diff": observed,
        "p_value": (extreme + 1) / (mc_iters + 1),  # +1 校正防 p=0
        "enumerated": mc_iters,
        "iterations": mc_iters,
        "seed": mc_seed,
        "approximate": True,
    }


def _betacf(a, b, x):
    """不完全贝塔连分式（Lentz 算法，数值标准实现）。"""
    max_it = 200
    eps = 3e-12
    fpmin = 1e-300
    qab = a + b
    qap = a + 1.0
    qam = a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < fpmin:
        d = fpmin
    d = 1.0 / d
    h = d
    for m in range(1, max_it + 1):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < fpmin:
            d = fpmin
        c = 1.0 + aa / c
        if abs(c) < fpmin:
            c = fpmin
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < fpmin:
            d = fpmin
        c = 1.0 + aa / c
        if abs(c) < fpmin:
            c = fpmin
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < eps:
            break
    return h


def _betainc(a, b, x):
    """正则化不完全贝塔函数 I_x(a, b)。"""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    bt = math.exp(math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
                  + a * math.log(x) + b * math.log(1.0 - x))
    if x < (a + 1.0) / (a + b + 2.0):
        return bt * _betacf(a, b, x) / a
    return 1.0 - bt * _betacf(b, a, 1.0 - x) / b


def _t_two_sided_p(t_stat, dof):
    """t 分布双侧 p 值：p = I_{dof/(dof+t²)}(dof/2, 1/2)。"""
    x = dof / (dof + t_stat * t_stat)
    return _betainc(dof / 2.0, 0.5, x)


def paired_t_test(exp_values, base_values):
    """成对 t 检验（纯 Python）。零方差分支显式处理（fail-safe）。"""
    if len(exp_values) != len(base_values):
        raise ValueError("paired values must have equal length")
    diffs = [float(e) - float(b) for e, b in zip(exp_values, base_values)]
    n = len(diffs)
    if n < 2:
        raise ValueError("paired t test requires n >= 2")
    mean_d = sum(diffs) / n
    var = sum((d - mean_d) ** 2 for d in diffs) / (n - 1)
    if var <= 1e-18:
        if abs(mean_d) <= 1e-12:
            return {"test": "paired_t", "n": n, "dof": n - 1,
                    "observed_mean_diff": mean_d, "t_stat": 0.0,
                    "p_value": 1.0, "cohen_d": 0.0,
                    "note": "no_difference"}
        return {"test": "paired_t", "n": n, "dof": n - 1,
                "observed_mean_diff": mean_d, "t_stat": None,
                "p_value": 0.0, "cohen_d": None,
                "note": "zero_variance_nonzero_effect"}
    sd = math.sqrt(var)
    t_stat = mean_d / (sd / math.sqrt(n))
    return {
        "test": "paired_t",
        "n": n,
        "dof": n - 1,
        "observed_mean_diff": mean_d,
        "t_stat": t_stat,
        "p_value": _t_two_sided_p(t_stat, n - 1),
        "cohen_d": mean_d / sd,
    }


# ── SVG 确定性渲染 ───────────────────────────────────────────


def _fmt(v):
    return f"{v:.3f}"


def line_chart_svg(series_list, width=760, height=400, title="",
                   xlabel="round", ylabel="score"):
    """纯 Python 折线图 SVG。series_list: [(label, values), ...]。"""
    ml, mr, mt, mb = 56, 16, 40, 44
    pw, ph = width - ml - mr, height - mt - mb
    all_vals = [v for _, vals in series_list for v in vals
                if isinstance(v, (int, float))]
    if not all_vals:
        all_vals = [0.0, 1.0]
    ymin, ymax = min(all_vals), max(all_vals)
    if ymax - ymin < 1e-12:
        ymin -= 0.5
        ymax += 0.5
    pad = (ymax - ymin) * 0.06
    ymin -= pad
    ymax += pad
    max_len = max(len(vals) for _, vals in series_list) if series_list else 1
    n_pts = max(max_len - 1, 1)

    def sx(i):
        return ml + pw * (i / n_pts)

    def sy(v):
        return mt + ph * (1.0 - (v - ymin) / (ymax - ymin))

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" '
        f'height="{height}" viewBox="0 0 {width} {height}">',
        f'<rect width="{width}" height="{height}" fill="white"/>',
        f'<text x="{width // 2}" y="20" text-anchor="middle" '
        f'font-size="14" font-family="sans-serif">{title}</text>',
        f'<line x1="{ml}" y1="{mt}" x2="{ml}" y2="{mt + ph}" '
        f'stroke="black" stroke-width="1"/>',
        f'<line x1="{ml}" y1="{mt + ph}" x2="{ml + pw}" y2="{mt + ph}" '
        f'stroke="black" stroke-width="1"/>',
        f'<text x="{ml + pw // 2}" y="{height - 8}" text-anchor="middle" '
        f'font-size="11" font-family="sans-serif">{xlabel}</text>',
        f'<text x="14" y="{mt + ph // 2}" text-anchor="middle" '
        f'font-size="11" font-family="sans-serif" '
        f'transform="rotate(-90 14 {mt + ph // 2})">{ylabel}</text>',
        f'<text x="{ml - 6}" y="{mt + 4}" text-anchor="end" font-size="10" '
        f'font-family="sans-serif">{_fmt(ymax)}</text>',
        f'<text x="{ml - 6}" y="{mt + ph + 4}" text-anchor="end" '
        f'font-size="10" font-family="sans-serif">{_fmt(ymin)}</text>',
    ]
    for idx, (label, vals) in enumerate(series_list):
        color = _PALETTE[idx % len(_PALETTE)]
        pts = " ".join(
            f"{_fmt(sx(i))},{_fmt(sy(v))}"
            for i, v in enumerate(vals)
            if isinstance(v, (int, float)))
        dash = "" if label.startswith("exp") else ' stroke-dasharray="6,3"'
        parts.append(
            f'<polyline points="{pts}" fill="none" stroke="{color}" '
            f'stroke-width="1.6"{dash}/>')
        ly = mt + 14 + idx * 14
        parts.append(
            f'<text x="{ml + 8}" y="{ly}" font-size="10" fill="{color}" '
            f'font-family="sans-serif">{label}</text>')
    parts.append("</svg>")
    return "\n".join(parts)


# ── 批量可视化与统计 ─────────────────────────────────────────


class BatchVisualizer:
    """消费 formal_batch 目录，产出六大交付物。"""

    def __init__(self, batch_dir, out_dir=None, tau=0.6, window_k=5):
        # τ 单一事实源：与 config qs.convergence_threshold 保持一致
        # （2026-08-26 S29.15 前曾滞留旧值 0.95，导致 7B 批次
        # convergence_round 恒为 None 的双轨口径）。
        self.batch_dir = Path(batch_dir)
        summary_path = self.batch_dir / "formal_batch_summary.json"
        self.summary = json.loads(summary_path.read_text(encoding="utf-8"))
        self.out_dir = Path(out_dir) if out_dir else self.batch_dir / "stats"
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.figures_dir = self.out_dir / "figures"
        self.figures_dir.mkdir(parents=True, exist_ok=True)
        self.tau = float(tau)
        self.window_k = int(window_k)
        self._archives = {}

    # ── 数据访问 ──────────────────────────────────────────

    def _runs(self):
        for cond in self.summary.get("conditions", []):
            for run in cond.get("runs", []):
                if run.get("interruption"):
                    continue
                yield cond["axiom"], run

    def _archive(self, run):
        key = run["log_path"]
        if key not in self._archives:
            self._archives[key] = LogRecorder(run["log_path"]).load_all()
        return self._archives[key]

    # ── 1/2. 曲线图 ───────────────────────────────────────

    def plot_convergence_curves(self):
        """收敛曲线：实验组实线 / 对照组虚线，每公理一张。"""
        figures = {}
        for cond in self.summary.get("conditions", []):
            axiom = cond["axiom"]
            series = []
            for run in cond.get("runs", []):
                if run.get("interruption"):
                    continue
                seed = run["seed"]
                series.append((f"exp seed={seed}",
                               run.get("convergence_curve") or []))
                cmp = run.get("comparison") or {}
                series.append((f"baseline seed={seed}",
                               cmp.get("baseline_series") or []))
            svg = line_chart_svg(
                series, title=f"Convergence curve — {axiom}",
                xlabel="round", ylabel="alignment score")
            path = self.figures_dir / f"convergence_{axiom}.svg"
            path.write_text(svg, encoding="utf-8")
            figures[f"convergence_{axiom}"] = str(path)
        return figures

    def plot_c_h_curves(self):
        """C_H 成本曲线：三子维度异质，独立曲线不合加（锁定决策 #11）。"""
        figures = {}
        for cond in self.summary.get("conditions", []):
            axiom = cond["axiom"]
            series = []
            for run in cond.get("runs", []):
                if run.get("interruption"):
                    continue
                seed = run["seed"]
                entries = self._archive(run)
                for dim in ("struct", "adapt", "extend"):
                    series.append(
                        (f"exp C_{dim} seed={seed}",
                         [e.get("c_h", {}).get(dim) for e in entries]))
            svg = line_chart_svg(
                series, title=f"C_H cost curve — {axiom} (heterogeneous)",
                xlabel="round", ylabel="C_H component")
            path = self.figures_dir / f"c_h_{axiom}.svg"
            path.write_text(svg, encoding="utf-8")
            figures[f"c_h_{axiom}"] = str(path)
        return figures

    # ── 3. 虚拟寿命统计 ───────────────────────────────────

    def lifespan_statistics(self):
        per_condition = {}
        all_vals = []
        for cond in self.summary.get("conditions", []):
            values = [r.get("virtual_lifespan") for r in cond.get("runs", [])
                      if not r.get("interruption")]
            values = [v for v in values if v is not None]
            all_vals.extend(values)
            per_condition[cond["axiom"]] = self._describe(values)
        return {"per_condition": per_condition,
                "overall": self._describe(all_vals)}

    @staticmethod
    def _describe(values):
        n = len(values)
        if n == 0:
            return {"values": [], "n": 0, "mean": None, "std": None,
                    "min": None, "max": None}
        mean = sum(values) / n
        var = (sum((v - mean) ** 2 for v in values) / (n - 1)) if n > 1 else 0.0
        return {"values": list(values), "n": n, "mean": mean,
                "std": math.sqrt(var), "min": min(values),
                "max": max(values)}

    # ── 5. 量化指标 ───────────────────────────────────────

    def _convergence_round(self, scores):
        """首个连续 window_k 轮全 ≥ τ 的窗口末轮（0 基），无则 None。"""
        k = self.window_k
        for i in range(k - 1, len(scores)):
            window = scores[i - k + 1:i + 1]
            if all(isinstance(s, (int, float)) and s >= self.tau
                   for s in window):
                return i
        return None

    def quantitative_metrics(self):
        per_condition = {}
        for cond in self.summary.get("conditions", []):
            runs_out = []
            for run in cond.get("runs", []):
                if run.get("interruption"):
                    continue
                entries = self._archive(run)
                extends = [e.get("c_h", {}).get("extend", 0)
                           for e in entries]
                it = run.get("iteration") or {}
                curve = run.get("convergence_curve") or []
                runs_out.append({
                    "seed": run.get("seed"),
                    "convergence_round": self._convergence_round(curve),
                    "max_c_extend": max(extends) if extends else 0,
                    "warn_round": it.get("warn_round"),
                    "downgrade_round": it.get("downgrade_round"),
                    "virtual_lifespan": run.get("virtual_lifespan"),
                    "converged_final": run.get("converged_final"),
                    "boundary_final_count": len(
                        it.get("boundary_conditions_final") or []),
                })
            per_condition[cond["axiom"]] = runs_out
        return {"per_condition": per_condition}

    # ── 6. 统计检验 ───────────────────────────────────────

    def _paired_block(self, exp_means, baseline_means):
        diffs = [e - b for e, b in zip(exp_means, baseline_means)]
        return {
            "n_runs": len(exp_means),
            "exp_means": exp_means,
            "baseline_means": baseline_means,
            "diffs": diffs,
            "permutation": paired_permutation_test(exp_means, baseline_means),
            "t_test": paired_t_test(exp_means, baseline_means),
        }

    def statistical_tests(self):
        per_condition = {}
        pooled_exp, pooled_base = [], []
        for cond in self.summary.get("conditions", []):
            exp_means, base_means = [], []
            for run in cond.get("runs", []):
                if run.get("interruption"):
                    continue
                cmp = run.get("comparison") or {}
                if cmp.get("exp_mean") is None:
                    continue
                exp_means.append(cmp["exp_mean"])
                base_means.append(cmp["baseline_mean"])
            per_condition[cond["axiom"]] = self._paired_block(
                exp_means, base_means)
            pooled_exp.extend(exp_means)
            pooled_base.extend(base_means)
        return {"per_condition": per_condition,
                "pooled": self._paired_block(pooled_exp, pooled_base),
                "unit": "seed-level run mean (锁定决策 #9 独立性单元)",
                "alpha": 0.05}

    # ── 4. 锚点验证结果表 ─────────────────────────────────

    def anchor_validation_table(self):
        st = self.statistical_tests()
        pooled = st["pooled"]
        perm = pooled["permutation"]
        all_nonneg = all(d >= -1e-12 for d in pooled["diffs"])
        # P_W2_5 判定（2026-08-25 用户批准修改）：supported = 重排 p≤0.05
        # 且观测差>0。重排检验本身已检验"方向+显著性"，不要求每运行都同向
        # （all_diffs_nonneg 在分数域差异下过度严格：对照组拟合正确轮映射
        # 1.0 满分、实验组 7B 余弦域 ~0.8 天然到不了 1.0，部分运行 diff 为负
        # 是分数域假象而非机制劣势）。all_diffs_nonneg 降为 evidence 展示项。
        if perm["observed_mean_diff"] > 0 and perm["p_value"] <= 0.05:
            p_w2_5_verdict = "supported"
        elif perm["observed_mean_diff"] < 0 and perm["p_value"] <= 0.05:
            p_w2_5_verdict = "falsified"
        else:
            p_w2_5_verdict = "inconclusive"

        # P_M1：边界条件数逐轮单调不减且净增
        all_monotonic = True
        net_increases = {}
        for axiom, run in self._runs():
            entries = self._archive(run)
            series = []
            for e in entries:
                cnt = 0
                for a in e.get("w2", {}).get("axioms", []) or []:
                    bc = a.get("boundary_conditions", []) or []
                    cnt += len(bc)
                series.append(cnt)
            monotonic = all(b >= a for a, b in zip(series, series[1:]))
            all_monotonic = all_monotonic and monotonic
            net = (series[-1] - series[0]) if series else 0
            net_increases.setdefault(axiom, []).append(net)
        all_net = all(v > 0 for nets in net_increases.values()
                      for v in nets)
        p_m1_verdict = ("supported" if all_monotonic and all_net
                        else ("falsified" if not all_monotonic
                              else "inconclusive"))

        # P_W1：脏数据（无效实体/事件）为零 → 无超能力有效观测
        dirty_total = 0
        for _, run in self._runs():
            for e in self._archive(run):
                stats = e.get("w0", {}).get("stats", {}) or {}
                dirty_total += (int(stats.get("invalid_entities", 0))
                                 + int(stats.get("failed_entities", 0))
                                 + int(stats.get("invalid_events", 0)))
        p_w1_verdict = "supported" if dirty_total == 0 else "falsified"

        # P_C：超预算轮必须伴随解体（本批默认预算预期无触发实例）
        over_rounds = 0
        for _, run in self._runs():
            for e in self._archive(run):
                if e.get("c_h", {}).get("within_budget") is False:
                    over_rounds += 1
        if over_rounds == 0:
            p_c_verdict, p_c_matched = "not_triggered", True
        else:
            p_c_matched = all(run.get("criterion") == "over_budget"
                              for _, run in self._runs()
                              if run.get("disintegrated"))
            p_c_verdict = "supported" if p_c_matched else "falsified"

        # H_L：仅当 ①/② 触发时有评估
        evaluated = 0
        violated = 0
        for _, run in self._runs():
            rep = json.loads(Path(run["report_path"]).read_text(
                encoding="utf-8"))
            h_l = rep.get("h_l") or {}
            if h_l.get("evaluated"):
                evaluated += 1
                if h_l.get("violated"):
                    violated += 1
        if evaluated == 0:
            h_l_verdict = "not_tested"
        else:
            h_l_verdict = "falsified" if violated > 0 else "supported"

        return {
            "P_W2_5": {
                "hypothesis": "降级机制比纯统计拟合更能维持稳态",
                "falsification_criterion": "实验组收敛性不优于对照组",
                "evidence": {
                    "permutation_p": perm["p_value"],
                    "observed_mean_diff": perm["observed_mean_diff"],
                    "all_diffs_nonneg": all_nonneg,
                    "n_runs": perm["n"],
                },
                "verdict": p_w2_5_verdict,
            },
            "P_M1": {
                "hypothesis": "规则边界单调细化、永不穷尽",
                "falsification_criterion": "公理集边界信息量出现净减少",
                "evidence": {"all_monotonic": all_monotonic,
                             "net_increases": net_increases},
                "verdict": p_m1_verdict,
            },
            "P_W1": {
                "hypothesis": "采样受传感器与规则处理能力双重限制",
                "falsification_criterion": "出现超出白名单能力的有效观测",
                "evidence": {"dirty_total": dirty_total},
                "verdict": p_w1_verdict,
            },
            "P_C": {
                "hypothesis": "超预算即解体",
                "falsification_criterion": (
                    "成本代理超预算（C_extend > B_extend）后系统仍维持稳态"),
                "evidence": {"over_budget_rounds": over_rounds,
                             "disintegration_matched": p_c_matched},
                "verdict": p_c_verdict,
            },
            "H_L": {
                "hypothesis": "递进动力学链单向成立（③→②→①）",
                "falsification_criterion": (
                    "①/②触发但此前连续 N 轮 C_extend 从未达到 B_extend"),
                "evidence": {"evaluated": evaluated, "violated": violated},
                "verdict": h_l_verdict,
            },
        }

    # ── 汇总 ─────────────────────────────────────────────

    def render_all(self):
        figures = {}
        figures.update(self.plot_convergence_curves())
        figures.update(self.plot_c_h_curves())
        result = {
            "mode": "s22_stats",
            "batch_dir": str(self.batch_dir),
            "rounds_target": self.summary.get("rounds_target"),
            "axiom_coverage": self.summary.get("axiom_coverage"),
            "figures": figures,
            "lifespan": self.lifespan_statistics(),
            "anchors": self.anchor_validation_table(),
            "metrics": self.quantitative_metrics(),
            "statistical_tests": self.statistical_tests(),
        }
        out_path = self.out_dir / "stats_summary.json"
        out_path.write_text(json.dumps(result, ensure_ascii=False,
                                       indent=2, default=str),
                            encoding="utf-8")
        return result
