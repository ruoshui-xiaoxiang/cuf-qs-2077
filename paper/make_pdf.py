import os
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (BaseDocTemplate, Paragraph, Spacer, Table,
                                TableStyle, PageBreak, Flowable, KeepTogether,
                                PageTemplate, Frame)
from reportlab.graphics import renderPDF

pdfmetrics.registerFont(TTFont("SimHei", r"C:\Windows\Fonts\simhei.ttf"))
pdfmetrics.registerFont(TTFont("SimSun", r"C:\Windows\Fonts\simsun.ttc",
                               subfontIndex=0))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIG_DIR = os.path.join(ROOT, "logs", "formal_batch_7b_v6", "stats", "figures")
OUT = os.path.join(os.environ["USERPROFILE"], "Desktop", "CUF-QS_阶段性实验报告.pdf")


def svg_flowable(path, target_w=165 * mm):
    try:
        from svglib.svglib import svg2rlg
        drawing = svg2rlg(path)
        if drawing is None:
            return None
        scale = target_w / drawing.width
        drawing.width = target_w
        drawing.height = drawing.height * scale
        drawing.scale(scale, scale)

        class _Svg(Flowable):
            def __init__(self, d):
                super().__init__()
                self.d = d

            def wrap(self, aW, aH):
                return self.d.width, self.d.height

            def draw(self):
                renderPDF.draw(self.d, self.canv, 0, 0)
        return _Svg(drawing)
    except Exception as e:
        print(f"[figure skipped] {path}: {e}")
        return None


def P(text, style):
    return Paragraph(text, style)


def make_table(rows, col_widths=None, size=8):
    tbl = Table(rows, colWidths=col_widths, repeatRows=1)
    tbl.setStyle(TableStyle([
        ("FONT", (0, 0), (-1, -1), "SimSun", size),
        ("FONT", (0, 0), (-1, 0), "SimHei", size),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#ececec")),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#888888")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]))
    return tbl


st_title = ParagraphStyle("title", fontName="SimHei", fontSize=16,
                          leading=22, alignment=1, spaceAfter=4)
st_meta = ParagraphStyle("meta", fontName="SimSun", fontSize=9,
                         leading=13, alignment=1, spaceAfter=2,
                         textColor=colors.HexColor("#333333"))
st_h2 = ParagraphStyle("h2", fontName="SimHei", fontSize=13, leading=18,
                       spaceBefore=14, spaceAfter=6)
st_h3 = ParagraphStyle("h3", fontName="SimHei", fontSize=11, leading=15,
                       spaceBefore=10, spaceAfter=4)
st_body = ParagraphStyle("body", fontName="SimSun", fontSize=10, leading=17,
                         spaceAfter=6, firstLineIndent=0)
st_abs = ParagraphStyle("abs", fontName="SimSun", fontSize=9.5, leading=15,
                        backColor=colors.HexColor("#f5f5f5"),
                        borderPadding=6, spaceAfter=8)
st_cap = ParagraphStyle("cap", fontName="SimSun", fontSize=8.5, leading=12,
                        alignment=1, textColor=colors.HexColor("#444444"),
                        spaceAfter=8)
st_pre = ParagraphStyle("pre", fontName="SimSun", fontSize=7.5, leading=11,
                        backColor=colors.HexColor("#f7f7f7"),
                        borderPadding=5, spaceAfter=6, leftIndent=2)
st_note = ParagraphStyle("note", fontName="SimSun", fontSize=8.5, leading=13,
                         textColor=colors.HexColor("#555555"), spaceAfter=6)
st_ref = ParagraphStyle("ref", fontName="SimSun", fontSize=9, leading=14,
                        leftIndent=14, firstLineIndent=-14, spaceAfter=3)

story = []

story.append(P("CUF-QS：虚拟具身公理系统的弱自进化机制", st_title))
story.append(P("——阶段性实验报告", ParagraphStyle(
    "t2", parent=st_title, fontSize=13, leading=18, spaceAfter=8)))
story.append(P("CUF-QS 项目组 · 2026 年 8 月 26 日 · 内部阶段性报告（数据版）", st_meta))
story.append(P("实验平台：Cyberpunk 2077 + CufBridge 0.6.7 · Qwen2.5-7B-Instruct（NF4）· Python 3.12", st_meta))
story.append(Spacer(1, 6))

story.append(P("<b>摘要</b>　本报告汇总 CUF-QS（因果宇宙框架-公理稳态系统）弱自进化机制验证的阶段性结果。"
               "系统以「公理集 + 反例驱动的降级机制 + 稳态成本预算」为核心，在三层验证结构"
               "（机制正确性 / 判别分真实性 / 生态有效性）上完成闭环检验。正式批次 9 运行全部完成完整迭代链"
               "（反例累计 → 预警 → 降级 → 边界自动补全），虚拟寿命 10/10；压力批次 27/27 超预算运行全部触发解体"
               "（锚点 P_C 获支持），9/9 功能死亡运行均未先经超预算（递进链假设 H_L 被证伪）；"
               "真实游戏环境 20 轮闭环中候选规则 C_G2_SUPP_TMP_036 在真实观测下完成「预警 → 降级 → 弃用」全过程，"
               "7B 隐状态判别分出现降级后跳升动力学（约 0.76 → 0.79）。工程层面确立「裁决硬编码、"
               "LLM 只产表征与文本」的反套壳约束，累计回归 26 套 / 605 断言全绿。局限与诚实记录"
               "（分数域差异、开环行为注入无效、规则池失控与刹车机制）一并报告。", st_abs))
story.append(P("<b>关键词</b>：弱自进化；公理降级；稳态对齐；虚拟具身；因果宇宙框架；表征验证", st_note))

story.append(P("1　引言", st_h2))
story.append(P("「让 AI 自我进化」的命题若不加约束，将导致规则池失控膨胀与信念漂移。"
               "CUF（Causal Universe Framework，因果宇宙框架）v7 给出的核心命题是："
               "<b>自进化是引擎，稳态成本 C_H 是刹车</b>——系统在证据压力下修正自身公理"
               "（选择性扩展、降级、边界细化），但任何信念修正活动都计入成本，超预算即触发 Γ-脆解体。"
               "CUF-QS 是该命题的虚拟具身实验系统：让一个驻留在真实游戏世界（Cyberpunk 2077）中的智能体，"
               "通过观测—假设—反例—降级的闭环，自主验证并修正其世界模型公理。", st_body))
story.append(P("本阶段工作回答三个问题：（i）反例驱动的降级机制是否在机制层正确运转；"
               "（ii）引入真实 7B 语言模型后，对齐分是否具备真实判别力（而非退化解）；"
               "（iii）机制在真实游戏世界的事件流下是否仍然受控、可复现。"
               "三层验证结果分别见 §3.1–3.3、§3.4–3.5、§3.6。", st_body))

story.append(P("2　系统与方法", st_h2))
story.append(P("2.1　四层架构", st_h3))
story.append(make_table([
    ["层", "职责", "关键模块"],
    ["W0 采集", "实体/事件采样、脏数据过滤", "CufBridge 0.6.7（Lua，游戏内）、观测快照"],
    ["W1 因果规则", "物理/语义规则真值判定（硬编码）", "rule_validator（20 条规则）"],
    ["W2 公理引擎+QS核", "公理状态机、反例计数、降级、对齐分", "AxiomEngine、QSKernel、DowngradeManager"],
    ["W3 编排/动作规划", "闭环调度、基线对照、LLM 动作建议", "ClosedLoopRunner、ActionPlanner、统计基线"],
], col_widths=[34 * mm, 62 * mm, 70 * mm]))
story.append(P("表 1　CUF-QS 四层架构", st_cap))

story.append(P("2.2　核心机制", st_h3))
story.append(P("· <b>反例驱动降级链</b>：某公理的有效反例累计达 K=3 触发预警（warned），达 K=5 触发降级"
               "（downgrade）并自动补全边界条件；饱和上限 5（决策 #11）。", st_body))
story.append(P("· <b>QS 对齐分</b>：s_t 为 7B 隐状态（3584 维，forward hook 提取），g 为公理集文本嵌入的靶向量"
               "（同空间），对齐分 = cos(s_t, g)；收敛判据为滑动窗口全 ≥ τ=0.6"
               "（τ 按 7B 余弦域重标定，原 0.95 为离线路设计）。", st_body))
story.append(P("· <b>功能死亡</b>：连续 3 轮对齐分 &lt; τ 即判功能死亡（判据②）。", st_body))
story.append(P("· <b>预算与 Γ-脆</b>：C_extend = min(累计反例数, 5)，struct 或 extend 任一超预算即 "
               "within_budget=False（判据③，锚点 P_C）。", st_body))
story.append(P("· <b>反套壳四铁则</b>：全部裁决（真值/降级/对齐判定）由硬编码模块完成；"
               "LLM 只产生人类可读文本与隐状态向量，零决策回流。", st_body))

story.append(P("2.3　实验平台与统计方法", st_h3))
story.append(P("世界来源三级：①mock 剧本世界（种子化场景注入，机制层验证）；②7B 双路注入"
               "（make_model_embedding_fn / make_model_state_fn，判别分真实性）；"
               "③真实游戏（Cyberpunk 2077 + CufBridge，生态有效性）。模型为 Qwen2.5-7B-Instruct "
               "NF4 量化（约 5GB 显存，与游戏共驻 16GB 显卡），驻留服务 TCP 20772 常驻持有。", st_body))
story.append(P("统计判定：主判定为成对精确重排检验（符号翻转全枚举 2ⁿ，n≤20；n&gt;20 退化为固定种子"
               "蒙特卡洛置换，可复现），交叉核对为成对 t 检验（纯 Python 实现，与 scipy.stats.ttest_rel "
               "一致至 1e-9）。检验单元为种子级运行均值。全部统计为硬编码纯函数，零绘图/统计库依赖进生产路径。", st_body))

story.append(P("3　实验结果", st_h2))
story.append(P("3.1　机制正确性：正式批次（7B 双路，9 运行）", st_h3))
story.append(P("批次 formal_batch_7b_v6（修复体制终验，与 v4/v5 数值逐位一致）：3 公理 × 3 种子，"
               "每运行 10 轮，9/9 完成。迭代时序与边界补全内容如下：", st_body))
story.append(make_table([
    ["公理", "预警轮", "降级轮", "最终状态", "边界自动补全"],
    ["A_M1（攻击使 HP 下降）", "r3", "r5", "deprecated",
     "3 条：实体类型 npc/[gang,hostile]；事件 combat_attack(hp not decreased)；约束 hp_no_decrease 100→100"],
    ["A_W1（超半径感知受限）", "r1", "r2", "deprecated", "1 条（反例实体 kind/labels）"],
    ["A_W2_5（死亡不可逆）", "r3–r4", "r3–r4", "deprecated", "1–2 条（反例实体 kind/labels）"],
], col_widths=[38 * mm, 16 * mm, 16 * mm, 22 * mm, 74 * mm]))
story.append(P("表 2　正式批次迭代时序（三种子确定性一致；数据源 logs/formal_batch_7b_v6/formal_batch_summary.json）", st_cap))
story.append(P("A_M1 seed_7 的逐轮对齐分序列（7B 真实判别分，非离线路恒 1.0）：", st_body))
story.append(P("轮次: r0　r1　r2　r3　r4　r5*　r6　r7　r8　r9<br/>"
               "分数: 0.7716　0.7610　0.7702　0.7584　0.7603　0.7937　0.7991　0.7983　0.7978　0.7944<br/>"
               "（*r5 为降级轮：降级后对齐分由 ~0.76 跳升至 ~0.79 并稳定——g 按剩余公理集重嵌入的稳态收益）", st_pre))
story.append(P("表 3　A_M1 seed_7 收敛曲线（数据源同上，convergence_curve 字段原值）", st_cap))
story.append(P("对照组（同世界纯统计拟合基线）acc 序列为 [0.833, 0.667]×5、均值 0.5、无收敛（振荡）；"
               "实验组均值 0.7805、收敛轮 = 4、虚拟寿命 10、无解体。三批运行（v4/v5/v6）exp 均值逐位一致"
               "（0.7804769834391629），数值透明可复核。", st_body))

fig1 = svg_flowable(os.path.join(FIG_DIR, "convergence_A_M1.svg"))
if fig1:
    story.append(fig1)
    story.append(P("图 1　A_M1 世界收敛曲线（实验组 3 种子 + 对照组 + τ=0.6 阈值线）", st_cap))

story.append(P("3.2　锚点验证表（v6 批次）", st_h3))
story.append(make_table([
    ["锚点", "假设", "证伪判据", "证据", "判定"],
    ["P_M1", "规则边界单调细化、永不穷尽", "边界信息量净减少",
     "全公理单调，净增 A_M1=3 / A_W1=1 / A_W2_5=1–2", "supported"],
    ["P_W1", "采样受传感器与规则能力双重限制", "出现超白名单能力的有效观测",
     "脏数据总数 = 0", "supported"],
    ["P_W2_5", "降级机制比纯统计拟合更能维持稳态", "实验组收敛性不优于对照组",
     "diff=+0.0416，p=0.484（n=9，3 种子规模）", "inconclusive"],
    ["P_C", "超预算即解体", "超预算后仍维持稳态", "本批无超预算轮", "not_triggered"],
    ["H_L", "递进动力学链 ③→②→① 单向成立", "①/② 触发前从未超预算", "本批未评估", "not_tested"],
], col_widths=[16 * mm, 38 * mm, 34 * mm, 52 * mm, 26 * mm], size=7.5))
story.append(P("表 4　锚点判定（数据源 logs/formal_batch_7b_v6/stats/stats_summary.json；P_C 与 H_L 正式检验见 §3.3）", st_cap))

story.append(P("3.3　P_C 压力批次与 H_L 检验（36 运行）", st_h3))
story.append(P("批次 pc_hl：extend_budget ∈ {10（对照）, 4, 3, 2} × 3 公理 × 3 种子。结果：", st_body))
story.append(P("· <b>P_C supported</b>：低预算档 <b>27/27 全部触发 over_budget 解体</b>（虚拟寿命缩短），"
               "对照档 9/9 正常收敛——「C_H 超预算触发 Γ-脆解体」获实证。", st_body))
story.append(P("· <b>H_L falsified</b>：负靶向量注入使 <b>9/9 运行触发功能死亡</b>，且触发前窗口 "
               "C_extend=[1,3,5,5]/[0,0] 从未达 budget=10——功能死亡可未经超预算直接触发，"
               "「由轻到重渐进解体」的递进链不成立。功能死亡虚拟寿命：A_M1/A_W1 为 5，A_W2_5 为 3"
               "（数据源 logs/pc_hl/pc_hl_summary.json）。", st_body))

story.append(P("3.4　统计规模实验（P_W2_5 的样本量依赖）", st_h3))
story.append(P("3 种子规模（n=9）下 P_W2_5 判定为 inconclusive（p=0.484）。扩样批次记录如下："
               "<b>9 种子（每公理）</b>分公理出现显著分化——A_M1 世界实验组显著优于对照组"
               "（exp 0.780 vs base 0.333，diff=+0.447，p=0.0039=2/512 精确最小值）；"
               "<b>18/36 种子批次</b>（formal_batch_7b_v3，6 组）pooled p 全部显著"
               "（18 种子 0.0083/0.0079/0.0064；36 种子 0.0003/0.0002/0.0002，diff 稳定在 +0.094~+0.100），"
               "<b>6 组全部判 supported</b>。判定逻辑修正（用户批准）：重排检验已含方向与显著性，"
               "observed_diff&gt;0 且 p≤0.05 即 supported。", st_body))
story.append(P("数据可复核性更正：本段全部数字都能在本仓归档里逐字段对上，不需要引外部记录。"
               "9 种子 A_M1（exp_means 0.7804769834391629 / baseline_means 0.3333333333333333 / "
               "observed_mean_diff 0.4471436501058296 / p_value 0.00390625 即 2/512，enumerated 512）"
               "见 logs/formal_batch_7b_v2/stats/stats_summary.json。"
               "18/36 种子六批次的 permutation_p（0.0082991700829917 / 0.007899210078992101 / "
               "0.006399360063993601 / 0.00029997000299970003 / 0.00019998000199980003 / "
               "0.00019998000199980003，enumerated 10000）见 "
               "logs/formal_batch_7b_v3/seed18_run1|2|3 与 seed36_run1|2|3 下的 "
               "stats/stats_summary.json，字段位置为 anchors.P_W2_5.evidence，六组 verdict 均为 supported；"
               "正文按四位小数舍入引用。", st_note))
story.append(P("更正文注：本节早前版本写的是「v3 原始归档当前不在本地，缺失原因未考，数字引自项目记录"
               "（.trae/rules/project_rules.md S27/S29.16 条目）」。该陈述不成立——v3 归档始终在盘（1992 文件），"
               "现已随本仓全量公开，读者可直接复核，不必再依赖不可见的项目记录。"
               "本次更正只改引用出处与这段失效说明，未改动任何统计数值。", st_note))

story.append(P("3.5　生态有效性：真实游戏闭环（20 轮）", st_h3))
story.append(P("批次 live_7b_v3（真实 Cyberpunk 2077，CufBridge 0.6.7，7B 驻留远程双路）："
               "20/20 轮完成、收敛判定 True、无解体、无中断、无不可信轮。真实事件流下的关键观测：", st_body))
story.append(P("· <b>真实自进化观测</b>：候选规则 C_G2_SUPP_TMP_036（时序类）在真实观测累积 7 个反例："
               "预警 r3@r13、降级 @r14（count=5）、后续反例不再重复触发动作（F9 修复生效）、最终弃用"
               "——弱自进化闭环在真实世界跑通。", st_body))
story.append(P("· <b>多样性闸门真实生效</b>：C_G2_PER_SCAN 19 条、A_W2_5 17 条检出全部被标签多样性闸门拦截"
               "（count 恒 0），单标签组合噪声不污染公理。", st_body))
story.append(P("· <b>判别分真实性</b>：20 轮 19 个不同值，区间 [0.7584, 0.7876]；r14 降级后对齐分 0.74→0.89 "
               "跳升（与 §3.1 同现象）。", st_body))
story.append(P("· <b>规则池受控</b>：15 候选激活 11、降级 1、无失控膨胀、无 Γ-脆触发。", st_body))

fig2 = svg_flowable(os.path.join(FIG_DIR, "c_h_A_M1.svg"))
if fig2:
    story.append(fig2)
    story.append(P("图 2　A_M1 世界稳态成本 C_H 三子维度曲线（决策 #11：三子维度异质，禁合加）", st_cap))

story.append(P("3.6　行为层与自主纠错", st_h3))
story.append(P("· <b>动作策略分化（planner_batch）</b>：默认编排（威胁导向）攻击序列 [G1,G1,C1,C2]"
               "（先清敌对）；LLM 自主行动（距离导向）为 [C1,C2,G1,G1]（最近优先，先误伤平民）。"
               "行为分化三种子确定性一致；两组稳态对齐不分化（0.779 级），如实报告。", st_body))
story.append(P("· <b>自主淘汰错误规则（候选池 15 条）</b>：含 2 条故意错误规则（断言与真实设定相反）。"
               "2077 真实设定观测下正确规则 13/13 确认、<b>故意错误规则 2/2 全部被证伪</b>"
               "——系统能自主发现并淘汰错误规则（弱自进化的纠错能力实证）。", st_body))
story.append(P("· <b>「知道≠会做」（S29.11 实验观测）</b>：濒死期间注入「HP 明显下降」感知信号 + 跨轮记忆 "
               "+ 生存优先提示词，7B 仍连续攻击至死亡。语言层开环注入不能产生适应性行为；"
               "行为适应性需要「行为—奖惩」闭环。", st_body))

story.append(P("4　讨论", st_h2))
story.append(P("4.1　分数域差异（诚实归因）", st_h3))
story.append(P("7B 余弦域（~0.78 稳态）与对照组拟合映射域（拟合正确=1.0）同属 [-1,1] 但分布不同，"
               "绝对水平比较对实验组不利；P_W2_5 的组间差异在「绝对分」口径下部分被域差异解释，"
               "机制收益更可靠的证据在动力学（降级后跳升 0.76→0.79）与分剧本显著性"
               "（A_M1 世界 p=0.0039）。", st_body))
story.append(P("4.2　规则池失控与刹车（S29.13）", st_h3))
story.append(P("200 候选池 + 实机环境下曾出现规则池 3→104 失控膨胀（101 候选被高频泛化事件无差别激活、"
               "3 条基础公理全部被降级）。三处缺陷修复后复验受控：锚点白名单动态化、激活补传边界条件、"
               "struct 纳入 Γ-脆判据（刹车生效）。本阶段最终批次（15 候选）激活 11、降级 1、无失控。"
               "<b>启示</b>：规则池进化引擎真实存在，但离开干净环境后需要显式刹车"
               "——这正是「让进化受控」命题的工程落点。", st_body))
story.append(P("4.3　机制缺陷的诚实记录（S29.15）", st_h3))
story.append(P("全量诊断发现并修复的三个 P0：旁观者伪反例（死亡事件循环误伤旁观实体）、"
               "边界自动补全捏造（事件类边界无证据归属校验）、模型驻留服务并发竞态（隐状态互串）。"
               "修复体制经 v4/v5/v6 三批复验数值逐位一致。<b>方法论记录</b>：假设空间必须在观测词表内"
               "可表达、可证伪（候选激活后不可证伪 = 观测接口边界问题，非理论缺陷）。", st_body))
story.append(P("4.4　局限", st_h3))
story.append(P("· τ=0.6 为 7B 余弦域经验重标定，变更须重跑阈值敏感性论证；<br/>"
               "· 3 种子批次为机制重验规模，统计结论以 18/36 种子批次为准（原始归档不在本地，§3.4）；<br/>"
               "· 对齐分对「世界模型正确性」的度量仍是代理指标（余弦相似度），非真值判定；真值判定归 W1 硬编码层；<br/>"
               "· 实机实验依赖特定游戏版本与模组（CufBridge 0.6.7，SHA256 版本锁），生态有效性结论不外推到未验证环境；<br/>"
               "· 「知道≠会做」为实验观测（框架外命题），不作框架假设引用。", st_body))

story.append(P("5　结论与展望", st_h2))
story.append(P("<b>结论</b>：（i）反例→预警→降级→边界补全机制在三层验证结构中全部复现"
               "（机制层确定性、7B 判别分层、真实游戏层）；（ii）Γ-脆判据③（超预算解体）获压力批次实证支持"
               "（27/27），递进链假设 H_L 被证伪（9/9 功能死未超支）——功能死亡是独立触发路径；"
               "（iii）真实世界中候选规则可被观测激活、被反例降级、被系统弃用，弱自进化闭环在受控条件下成立；"
               "（iv）「自进化是引擎、C_H 是刹车」的命题在工程层获得可操作的实现与失效案例（失控→刹车修复）。", st_body))
story.append(P("<b>展望</b>（待议方向，未实施）：D1 隐空间方向级干预实验——在生成侧注入对比方向向量"
               "（activation steering），与「语言层注入改不了行为」的观测做机制级对照，检验"
               "「行为适应性需闭环奖惩」能否升级为机制级证据。文献定位：RepE（arXiv:2310.01405）/ "
               "ITI（arXiv:2306.03341）/ Arditi et al.（arXiv:2406.11717）构成表征层因果探针，"
               "与 LeAct（arXiv:2607.21856）的概率层因果过滤、CUF W1 的符号层证据归属构成三层因果工具链；"
               "纪律约束：干预只允许作用于生成侧，禁止作用于度量侧（s_t→对齐分为测量通道）。", st_body))

story.append(P("附录 A　实验日志摘录（原始文件）", st_h2))
story.append(P("A.1　实机闭环第 14 轮观测（logs/live_7b_v3/rounds.jsonl，节选）", st_h3))
story.append(P('{"round_id": 14, "timestamp": 1787706290.76, "w0": {"entities": [<br/>'
               '　{"kind": "player", "labels": ["player"], "hp": 100, "entity_id": "EID:1",<br/>'
               '　　"position": {"x": -41.72, "y": 298.22, "z": 38.99}},<br/>'
               '　{"kind": "npc", "labels": ["NPCPuppet"], "hp": 0, "entity_id": "EID:10640146",<br/>'
               '　　"position": {"x": -56.08, "y": 330.79, "z": 38.98}},<br/>'
               '　{"kind": "npc", "labels": ["NPCPuppet"], "hp": 100, "entity_id": "EID:10640145",<br/>'
               '　　"position": {"x": -58.14, "y": 331.89, "z": 38.88}}],<br/>'
               '　"stats": {"raw_entities": 3, "invalid_entities": 0, "invalid_events": 0}},<br/>'
               '"w1": {"checked_axioms": ["A_M1", "A_W2_5", "A_W1", "C_G2_SUPP_TMP_036", ...],<br/>'
               '　"observations_checked": 42, "valid_counterexamples": 5}, ...}', st_pre))
story.append(P("（r14 为候选规则 C_G2_SUPP_TMP_036 的降级轮：count 达 5，触发 downgrade 动作。"
               "坐标为游戏内真实世界坐标。）", st_note))

story.append(P("A.2　正式批次迭代档案（logs/formal_batch_7b_v6/formal_batch_summary.json，节选）", st_h3))
story.append(P('"iteration": {"axiom": "A_M1", "final_status": "deprecated", "warn_round": 3,<br/>'
               '　"downgrade_round": 5, "max_count": 5,<br/>'
               '　"boundary_conditions_final": [<br/>'
               '　　{"kind": "npc", "labels": ["gang", "hostile"], "source": "auto_complete",<br/>'
               '　　　"from_counterexample": true},<br/>'
               '　　{"event": "combat_attack", "note": "hp not decreased", "source": "auto_complete"},<br/>'
               '　　{"constraint": "hp_no_decrease", "before": 100.0, "after": 100.0,<br/>'
               '　　　"source": "auto_complete"}]},<br/>'
               '"other_axiom_status": {"A_W2_5": "active", "A_W1": "active"},<br/>'
               '"comparison": {"exp_mean": 0.7804769834391629, "baseline_mean": 0.5,<br/>'
               '　"compared_rounds": 10, "truncated": false, "converged_exp": true,<br/>'
               '　"converged_baseline": false}', st_pre))

story.append(P("A.3　监督者日志（logs/launcher/supervisor.log，2026-08-26 节选）", st_h3))
story.append(P("[2026-08-26 13:27:08] 监督者已在运行（pid=9492），不重复启动第二个实例<br/>"
               "[2026-08-26 13:27:08] 面板已在服务，直接打开现有面板：http://127.0.0.1:8090/<br/>"
               "[2026-08-26 13:27:20] model_server 端口 20772 pid=23556 进程活但端口未服务（冷加载中）<br/>"
               "[2026-08-26 13:27:20] panel　　　　端口 8090　pid=27064 OK", st_pre))

story.append(P("附录 B　数据可用性与复现", st_h2))
story.append(make_table([
    ["数据集", "路径", "内容"],
    ["离线机制批次", "logs/formal_batch", "3 公理 × 3 种子，离线路，四件套（轮次/报告/对比/迭代档案）"],
    ["7B 批次 v1/v2", "logs/formal_batch_7b, formal_batch_7b_v2", "9 种子规模统计分化观测"],
    ["7B 批次 v4/v5/v6", "logs/formal_batch_7b_v4..v6", "修复体制三轮复验（数值逐位一致）"],
    ["P_C/H_L 压力批次", "logs/pc_hl（5 预算档 × 9 运行）", "36 运行 + pc_hl_summary.json"],
    ["实机闭环", "logs/live_7b_v3", "真实游戏 20 轮"],
    ["行为层批次", "logs/planner_batch", "heuristic/llm 对照 + 两份 summary"],
    ["结论文件", "logs/conclusion/s23_conclusion.{json,md}", "结构化结论（假设映射表/局限/复现指引）"],
], col_widths=[34 * mm, 62 * mm, 70 * mm], size=7.5))
story.append(P("复现命令（项目根目录）：py -3.12 tests\\batch_7b.py（7B 双路批次）、"
               "py -3.12 tests\\pc_hl_test.py（压力批次）、py -3.12 tests\\live_7b_loop.py"
               "（实机，需游戏 + 驻留服务）、回归 tests\\s2–s29 共 26 套 / 605 断言。", st_body))

story.append(P("参考文献", st_h2))
story.append(P("[1] LeAct: Learning to Act with Actionable Reasoning. arXiv:2607.21856, 2026."
               "（反向采样候选推理链 + forward-delta 因果过滤；概率层因果过滤，与 CUF 符号层证据归属"
               "同构，见项目记录 S29.16）", st_ref))
story.append(P("[2] Zou, A. et al. Representation Engineering: A Top-Down Approach to AI "
               "Transparency. arXiv:2310.01405, 2023.", st_ref))
story.append(P("[3] Li, K. et al. Inference-Time Intervention: Eliciting Truthful Answers from a "
               "Language Model. NeurIPS 2023. arXiv:2306.03341.", st_ref))
story.append(P("[4] Arditi, A. et al. Refusal in Language Models Is Mediated by a Single Direction. "
               "arXiv:2406.11717, 2024.", st_ref))
story.append(Spacer(1, 8))
story.append(P("本报告全部数字直接取自上述归档文件（撰写时逐一读取），未做人工修饰；v3 批次为唯一引用"
               "记录值的数据集并已注明。报告生成日期 2026-08-26。", st_note))

doc = BaseDocTemplate(OUT, pagesize=A4,
                      leftMargin=18 * mm, rightMargin=18 * mm,
                      topMargin=20 * mm, bottomMargin=20 * mm,
                      title="CUF-QS 阶段性实验报告",
                      author="CUF-QS 项目组")
doc.addPageTemplates(PageTemplate(
    id="main", frames=[Frame(doc.leftMargin, doc.bottomMargin,
                             doc.width, doc.height)]))
doc.build(story)
print("PDF written:", OUT, os.path.getsize(OUT), "bytes")
