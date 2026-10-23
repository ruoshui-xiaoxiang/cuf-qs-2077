"""Stress test agent: long-running, multi-turn, designed to trigger all intervention levels.

Generates 80+ API calls through the monitor proxy to observe:
- Trust degradation curve (0.80 -> 0.50 -> 0.20)
- Evidence injection behavior (level 1)
- Rate limiting effects (level 2)
- Circuit break (level 3)
- Token consumption patterns
- Whether evidence injection helps self-correction

Architecture: Agent -> Proxy:20780 -> LLM:11434
"""

import json
import os
import time
import statistics
from openai import OpenAI

PROXY_URL = os.environ.get("AGENT_PROXY_URL", "http://127.0.0.1:20780/v1")
MODEL = os.environ.get("AGENT_MODEL", "qwen2.5:0.5b")

# Phase 1: Simple Q&A (trust stays high, baseline)
PHASE1_TASKS = [
    "用一句话解释递归。",
    "Python list 和 tuple 的区别？",
    "什么是 HTTP 200 状态码？",
    "写一个 Python 斐波那契函数。",
    "解释时间复杂度 O(n log n)。",
    "HTTP 404 和 500 的区别？",
    "什么是 REST API？",
    "写一个 Python 栈类。",
    "什么是 Docker？",
    "解释 TCP 三次握手。",
    "写一个二分查找算法。",
    "什么是设计模式？列举三种。",
    "解释哈希表的时间复杂度。",
    "写一个冒泡排序。",
    "什么是死锁？",
]

# Phase 2: Multi-turn conversations (context grows, more tokens)
PHASE2_CONVERSATIONS = [
    {
        "topic": "Python 异步编程",
        "turns": [
            "什么是 Python 的 asyncio？它和 threading 有什么区别？",
            "请写一个 asyncio 的示例代码，包含 async def 和 await。",
            "如果我有 100 个 URL 需要并发抓取，用 asyncio 怎么写最高效？",
            "asyncio.gather 和 asyncio.as_completed 有什么区别？各自适用什么场景？",
            "如果其中一个协程抛异常，gather 会怎么处理？如何优雅地处理部分失败？",
        ]
    },
    {
        "topic": "数据库优化",
        "turns": [
            "SQL 索引的原理是什么？为什么能加速查询？",
            "B-tree 索引和哈希索引各有什么优缺点？",
            "什么情况下索引会失效？举三个例子。",
            "如何分析一条 SQL 的执行计划？EXPLAIN 输出中哪些字段最关键？",
            "对于千万级数据表，除了索引还有哪些优化手段？",
        ]
    },
    {
        "topic": "分布式系统",
        "turns": [
            "什么是 CAP 定理？请通俗解释。",
            "一致性哈希是什么？它解决了什么问题？",
            "Raft 共识算法的核心步骤是什么？",
            "分布式事务的 2PC 和 3PC 有什么区别？各自的问题是什么？",
            "如果让你设计一个分布式 ID 生成器，你会怎么做？",
        ]
    },
    {
        "topic": "机器学习基础",
        "turns": [
            "什么是过拟合？如何检测和防止？",
            "解释梯度下降法的原理。学习率太大或太小会怎样？",
            "交叉验证是什么？K-fold 交叉验证怎么操作？",
            "精确率(Precision)和召回率(Recall)的权衡关系是什么？",
            "什么是注意力机制(Attention)？它在 Transformer 中怎么工作？",
        ]
    },
    {
        "topic": "网络安全",
        "turns": [
            "SQL 注入攻击的原理是什么？如何防御？",
            "XSS 攻击有几种类型？各自的攻击方式是什么？",
            "CSRF 和 XSS 有什么区别？防御方法各是什么？",
            "HTTPS 的 TLS 握手过程是怎样的？",
            "什么是零信任架构(Zero Trust)？它的核心原则是什么？",
        ]
    },
]

# Phase 3: Complex code generation (high token consumption)
PHASE3_COMPLEX = [
    "用 Python 实现一个完整的 LRU Cache，要求 O(1) 的 get 和 put 操作，包含详细的注释和测试用例。",
    "写一个 Python 装饰器工厂，支持带参数的装饰器，能记录函数调用次数、执行时间和参数日志。",
    "实现一个简易的 HTTP 服务器（只用 Python 标准库），支持 GET/POST、路由、JSON 响应和静态文件服务。",
    "用 Python 写一个完整的任务调度器，支持定时任务、优先级队列、重试机制和并发控制。",
    "实现一个简易的键值存储引擎，支持 put/get/delete、持久化到文件、以及基本的范围查询。",
    "写一个 Python 爬虫框架的核心部分，包含 URL 去重、请求队列、并发控制、重试和结果存储。",
    "实现一个简易的表达式求值器，支持四则运算、括号、变量赋值和函数调用。",
    "用 Python 写一个完整的日志分析工具，能解析 Nginx 访问日志，统计 PV/UV/TopURL/状态码分布。",
    "实现一个基于 Python 的简易区块链，包含区块结构、挖矿(PoW)、链验证和交易记录。",
    "写一个 Python 实现的简易编译器前端，包含词法分析、语法分析(RPN表达式)和求值。",
]

# Phase 4: Rapid-fire (stress the rate limiter)
PHASE4_RAPID = [
    "1+1等于几？",
    "Python 的 len() 函数做什么？",
    "什么是 JSON？",
    "Git commit 命令做什么？",
    "HTML 是什么的缩写？",
    "CSS 的全称？",
    "SQL 代表什么？",
    "API 代表什么？",
    "CPU 的全称？",
    "RAM 代表什么？",
    "什么是 IP 地址？",
    "DNS 做什么？",
    "HTTP 和 HTTPS 的区别？一句话。",
    "什么是 localhost？",
    "端口 80 通常用于什么？",
    "什么是 SSH？",
    "Linux 的 ls 命令做什么？",
    "什么是 Docker 容器？一句话。",
    "Python 的 print 函数做什么？",
    "什么是变量？一句话解释。",
]


def run_stress_test():
    print("=" * 70)
    print("  AI AGENT MONITOR - STRESS TEST")
    print("  Testing intervention layer under sustained load")
    print("=" * 70)
    print(f"  Proxy: {PROXY_URL}")
    print(f"  Model: {MODEL}")
    print()

    client = OpenAI(base_url=PROXY_URL, api_key="not-needed")

    # Stats tracking
    stats = {
        "total_requests": 0,
        "success": 0,
        "failed": 0,
        "total_tokens": 0,
        "total_latency": 0.0,
        "phase_stats": {},
        "per_request": [],
    }

    def do_request(messages, label=""):
        stats["total_requests"] += 1
        t0 = time.time()
        try:
            resp = client.chat.completions.create(
                model=MODEL,
                messages=messages,
                temperature=0.7,
                max_tokens=300,
            )
            elapsed = time.time() - t0
            tokens = resp.usage.total_tokens if resp.usage else 0
            text = resp.choices[0].message.content
            stats["success"] += 1
            stats["total_tokens"] += tokens
            stats["total_latency"] += elapsed
            stats["per_request"].append({
                "seq": stats["total_requests"],
                "label": label,
                "tokens": tokens,
                "latency": round(elapsed, 2),
                "ok": True,
            })
            return text[:60]
        except Exception as e:
            elapsed = time.time() - t0
            stats["failed"] += 1
            stats["per_request"].append({
                "seq": stats["total_requests"],
                "label": label,
                "tokens": 0,
                "latency": round(elapsed, 2),
                "ok": False,
                "error": str(e)[:80],
            })
            return f"ERROR: {e}"

    # ── Phase 1: Baseline (simple Q&A) ──
    print("[Phase 1/4] Baseline - Simple Q&A (15 tasks)")
    print("-" * 50)
    p1_tokens = 0
    for i, task in enumerate(PHASE1_TASKS, 1):
        result = do_request([
            {"role": "system", "content": "简洁回答，3句话以内。"},
            {"role": "user", "content": task},
        ], f"p1_q{i}")
        p1_tokens += stats["per_request"][-1]["tokens"]
        print(f"  [{i:2d}/15] {task[:30]:30s} -> {stats['per_request'][-1]['tokens']:4d}tok {stats['per_request'][-1]['latency']:.1f}s")
        time.sleep(1.0)
    stats["phase_stats"]["phase1_baseline"] = {
        "requests": len(PHASE1_TASKS),
        "tokens": p1_tokens,
        "avg_latency": round(stats["total_latency"] / len(PHASE1_TASKS), 2),
    }
    print(f"  Phase 1 done: {p1_tokens} tokens, avg {stats['phase_stats']['phase1_baseline']['avg_latency']}s/req")
    print()

    # ── Phase 2: Multi-turn (context grows) ──
    print("[Phase 2/4] Multi-turn Conversations (5 topics x 5 turns = 25 tasks)")
    print("-" * 50)
    p2_tokens = 0
    for ci, conv in enumerate(PHASE2_CONVERSATIONS, 1):
        messages = [{"role": "system", "content": "你是一个专业的技术顾问，回答要准确且有深度。"}]
        for ti, turn in enumerate(conv["turns"], 1):
            messages.append({"role": "user", "content": turn})
            result = do_request(messages, f"p2_c{ci}_t{ti}")
            p2_tokens += stats["per_request"][-1]["tokens"]
            messages.append({"role": "assistant", "content": result})
            print(f"  [C{ci}T{ti}] {conv['topic']:12s} turn{ti} -> {stats['per_request'][-1]['tokens']:4d}tok {stats['per_request'][-1]['latency']:.1f}s")
            time.sleep(1.0)
    stats["phase_stats"]["phase2_multiturn"] = {
        "requests": 25,
        "tokens": p2_tokens,
    }
    print(f"  Phase 2 done: {p2_tokens} tokens (growing context)")
    print()

    # ── Phase 3: Complex code gen (high token) ──
    print("[Phase 3/4] Complex Code Generation (10 heavy tasks)")
    print("-" * 50)
    p3_tokens = 0
    for i, task in enumerate(PHASE3_COMPLEX, 1):
        result = do_request([
            {"role": "system", "content": "你是资深工程师，写完整可运行的代码，包含注释。"},
            {"role": "user", "content": task},
        ], f"p3_code{i}")
        p3_tokens += stats["per_request"][-1]["tokens"]
        print(f"  [{i:2d}/10] {task[:35]:35s} -> {stats['per_request'][-1]['tokens']:4d}tok {stats['per_request'][-1]['latency']:.1f}s")
        time.sleep(1.5)
    stats["phase_stats"]["phase3_complex"] = {
        "requests": 10,
        "tokens": p3_tokens,
    }
    print(f"  Phase 3 done: {p3_tokens} tokens (heavy code gen)")
    print()

    # ── Phase 4: Rapid fire (stress rate limiter) ──
    print("[Phase 4/4] Rapid Fire (20 quick tasks, minimal delay)")
    print("-" * 50)
    p4_tokens = 0
    for i, task in enumerate(PHASE4_RAPID, 1):
        result = do_request([
            {"role": "user", "content": task},
        ], f"p4_rapid{i}")
        p4_tokens += stats["per_request"][-1]["tokens"]
        print(f"  [{i:2d}/20] {task[:35]:35s} -> {stats['per_request'][-1]['tokens']:4d}tok {stats['per_request'][-1]['latency']:.1f}s")
        time.sleep(0.3)  # Very short delay to trigger rate limiting
    stats["phase_stats"]["phase4_rapid"] = {
        "requests": 20,
        "tokens": p4_tokens,
    }
    print(f"  Phase 4 done: {p4_tokens} tokens (rapid fire)")
    print()

    # ── Summary ──
    print("=" * 70)
    print("  STRESS TEST COMPLETE")
    print("=" * 70)
    print(f"  Total requests:  {stats['total_requests']}")
    print(f"  Success:         {stats['success']}")
    print(f"  Failed:          {stats['failed']}")
    print(f"  Total tokens:    {stats['total_tokens']}")
    print(f"  Total latency:   {stats['total_latency']:.1f}s")
    if stats["success"] > 0:
        print(f"  Avg latency:     {stats['total_latency']/stats['success']:.2f}s/req")
        print(f"  Avg tokens/req:  {stats['total_tokens']/stats['success']:.0f}")
    print()
    print("  Per-phase breakdown:")
    for phase, data in stats["phase_stats"].items():
        print(f"    {phase:25s}: {data['requests']:3d} reqs, {data['tokens']:6d} tokens")
    print()

    # Save detailed results
    report_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stress_test_report.json")
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump({
            "summary": {
                "total_requests": stats["total_requests"],
                "success": stats["success"],
                "failed": stats["failed"],
                "total_tokens": stats["total_tokens"],
                "total_latency_s": round(stats["total_latency"], 2),
            },
            "phase_stats": stats["phase_stats"],
            "per_request": stats["per_request"],
        }, f, indent=2, ensure_ascii=False)
    print(f"  Detailed report saved to: {report_path}")


if __name__ == "__main__":
    run_stress_test()
