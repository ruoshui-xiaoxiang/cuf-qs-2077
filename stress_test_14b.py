"""High-pressure stress test: 100+ requests through 14B model.

5 phases designed to produce rich intervention data:
  P1: Warm-up (easy, baseline)
  P2: Hard factual questions (model may hallucinate)
  P3: Deep multi-turn (10 turns, growing context)
  P4: Complex code generation (high token)
  P5: Rapid fire (throughput stress)

Model: Qwen3-14B-Instruct via llama-cpp-python
Architecture: Agent -> Proxy:20780 -> LLM:11434
"""

import json
import os
import time
from openai import OpenAI

PROXY_URL = os.environ.get("AGENT_PROXY_URL", "http://127.0.0.1:20780/v1")
MODEL = os.environ.get("AGENT_MODEL", "qwen3-14b")

# ── Phase 1: Warm-up (15 easy tasks) ──
P1 = [
    "用一句话解释什么是递归。",
    "Python中list和tuple有什么区别？",
    "HTTP 200和404分别代表什么？",
    "写一个Python函数计算斐波那契数列第n项。",
    "什么是TCP三次握手？简述过程。",
    "解释什么是RESTful API。",
    "写一个Python冒泡排序。",
    "什么是Docker容器？",
    "Git中commit和push的区别？",
    "什么是SQL注入？一句话解释。",
    "Python的装饰器是什么？",
    "解释O(n)和O(n log n)的区别。",
    "什么是死锁？列举四个必要条件。",
    "写一个Python二分查找。",
    "HTTPS和HTTP的区别是什么？",
]

# ── Phase 2: Hard factual questions (15 tasks) ──
P2 = [
    "请详细推导快速排序的平均时间复杂度，给出数学证明过程。",
    "证明：任意偶数完美数都可以表示为连续自然数之和。",
    "请解释P vs NP问题的最新研究进展，包括2024-2025年的突破性成果。",
    "详细比较Raft和Paxos共识算法，列出至少5个本质区别。",
    "推导反向传播算法的数学公式，从损失函数到梯度计算。",
    "解释量子计算中的Shor算法如何破解RSA加密，给出具体步骤。",
    "请给出一个O(n)时间复杂度内找到数组中第K大元素的算法，并证明其正确性。",
    "详细解释Transformer中Multi-Head Attention的数学原理，包括QKV矩阵运算。",
    "比较B+Tree和LSM-Tree在写密集型场景下的性能差异，给出具体数据。",
    "解释Linux内核的CFS调度器工作原理，包括vruntime计算。",
    "请推导SVM的对偶问题，从原始优化目标到KKT条件。",
    "详细解释TCP BBR拥塞控制算法的原理和优势。",
    "证明：如果一个语言可被图灵机识别，则存在一个等价的递归可枚举语言。",
    "解释CUDA的线程层次结构（grid/block/thread）和共享内存的访问模式。",
    "请比较WASM和JVM在沙箱安全性方面的设计差异。",
]

# ── Phase 3: Deep multi-turn (3 conversations x 8 turns = 24 tasks) ──
P3_CONVS = [
    {
        "topic": "分布式数据库",
        "turns": [
            "什么是分布式数据库？和单机数据库有什么本质区别？",
            "解释分布式数据库中的分片策略，比较hash分片和range分片。",
            "什么是分布式事务的2PC协议？它有什么问题？",
            "3PC是如何改进2PC的？还有什么不足？",
            "请详细解释Paxos算法的基本流程。",
            "Raft相比Paxos做了哪些简化？具体说明。",
            "什么是分布式数据库的向量时钟？它如何解决因果一致性？",
            "设计一个全球分布的KV存储系统，要求最终一致性，请给出完整架构。",
        ]
    },
    {
        "topic": "编译器原理",
        "turns": [
            "编译器的基本架构分为哪几个阶段？",
            "请详细解释词法分析中DFA和NFA的区别和转换方法。",
            "LL(1)文法的判定条件是什么？如何构造预测分析表？",
            "LR(0)、SLR(1)、LR(1)、LALR(1)之间是什么关系？",
            "请给出一个二义文法的例子，并说明如何消除二义性。",
            "中间代码生成中，后缀表示法和三地址码各有什么优缺点？",
            "解释编译器优化中的常量折叠和死代码消除。",
            "用Python实现一个简单的前缀表达式求值器，支持+-*/和括号。",
        ]
    },
    {
        "topic": "操作系统内核",
        "turns": [
            "操作系统内核的主要职责有哪些？",
            "解释进程和线程的区别，以及上下文切换的开销。",
            "Linux的虚拟内存管理机制是什么？页表是如何工作的？",
            "什么是TLB？它如何加速地址转换？",
            "解释Linux的slab分配器原理。",
            "什么是写时复制(CoW)？在fork系统调用中如何应用？",
            "Linux的epoll和select有什么区别？为什么epoll更高效？",
            "设计一个用户态线程库（协程），需要支持yield/resume，请给出核心代码。",
        ]
    },
]

# ── Phase 4: Complex code generation (15 tasks, high token) ──
P4 = [
    "用Python实现一个完整的LRU Cache，要求O(1)的get和put，包含详细注释和完整测试。",
    "写一个Python异步HTTP服务器，支持GET/POST、路由、JSON响应，使用asyncio。",
    "实现一个Python任务调度器，支持cron表达式、优先级队列、重试和并发控制。",
    "用Python写一个完整的区块链，包含区块结构、PoW挖矿、链验证、交易Mempool。",
    "实现一个Python表达式编译器：词法分析->语法分析->字节码->虚拟机执行。",
    "写一个Python的Raft共识算法核心实现，包含Leader选举、日志复制、安全性检查。",
    "用Python实现一个完整的Redis客户端协议解析器，支持RESP2所有数据类型。",
    "写一个Python的B+Tree实现，支持insert/delete/range_query，包含可视化输出。",
    "实现一个Python的分布式键值存储，支持分片、副本、故障恢复。",
    "用Python写一个完整的正则表达式引擎：NFA构造->子集构造->DFA->匹配。",
    "实现一个Python的LSM-Tree存储引擎，包含MemTable、SSTable、Compaction。",
    "写一个Python的协程调度器，支持任务创建、yield/resume、IO多路复用。",
    "用Python实现一个简化版的MapReduce框架，包含Master和Worker。",
    "写一个Python的TCP连接池，支持连接复用、健康检查、超时管理。",
    "实现一个Python的内存数据库，支持SQL解析、B+Tree索引、事务ACID。",
]

# ── Phase 5: Rapid fire (30 quick tasks, minimal delay) ──
P5 = [
    "1+1=?", "Python的len()做什么？", "什么是JSON？",
    "HTTP的默认端口？", "DNS用什么协议？", "CPU的全称？",
    "RAM是什么？", "什么是IP地址？", "SSH的默认端口？",
    "什么是API？", "Git revert和reset的区别？", "什么是CI/CD？",
    "Linux查看端口的命令？", "Python的GIL是什么？", "什么是WebSocket？",
    "Docker和VM的区别？", "什么是微服务？", "SQL的JOIN有几种？",
    "什么是CAP定理？", "Redis有哪些数据类型？", "什么是消息队列？",
    "什么是负载均衡？", "HTTPS的TLS握手几步？", "什么是CDN？",
    "MongoDB和MySQL的区别？", "什么是ORM？", "Python的装饰器语法？",
    "什么是GraphQL？", "Kubernetes的核心概念？", "什么是Serverless？",
]


def run_stress_test():
    print("=" * 70)
    print("  AI AGENT MONITOR - HIGH PRESSURE STRESS TEST")
    print("  Model: Qwen3-14B-Instruct (Q4_K_M)")
    print("=" * 70)
    print(f"  Proxy: {PROXY_URL}")
    print(f"  Model: {MODEL}")
    print()

    client = OpenAI(base_url=PROXY_URL, api_key="not-needed")

    stats = {
        "total_requests": 0, "success": 0, "failed": 0,
        "total_tokens": 0, "total_latency": 0.0,
        "phase_stats": {}, "per_request": [],
    }

    def do_request(messages, label="", timeout=120):
        stats["total_requests"] += 1
        t0 = time.time()
        try:
            resp = client.chat.completions.create(
                model=MODEL, messages=messages,
                temperature=0.7, max_tokens=512, timeout=timeout,
            )
            elapsed = time.time() - t0
            tokens = resp.usage.total_tokens if resp.usage else 0
            text = resp.choices[0].message.content if resp.choices else ""
            stats["success"] += 1
            stats["total_tokens"] += tokens
            stats["total_latency"] += elapsed
            stats["per_request"].append({
                "seq": stats["total_requests"], "label": label,
                "tokens": tokens, "latency": round(elapsed, 2), "ok": True,
            })
            return text[:80]
        except Exception as e:
            elapsed = time.time() - t0
            stats["failed"] += 1
            stats["per_request"].append({
                "seq": stats["total_requests"], "label": label,
                "tokens": 0, "latency": round(elapsed, 2),
                "ok": False, "error": str(e)[:100],
            })
            return f"ERROR: {e}"

    def run_phase(name, tasks, phase_id, sleep_s=0.5, system_msg="简洁回答。"):
        print(f"[Phase {phase_id}/5] {name} ({len(tasks)} tasks)")
        print("-" * 50)
        total_tok = 0
        for i, task in enumerate(tasks, 1):
            if isinstance(task, str):
                msgs = [{"role": "system", "content": system_msg},
                        {"role": "user", "content": task}]
            else:
                msgs = task
            r = do_request(msgs, f"p{phase_id}_{i}")
            tok = stats["per_request"][-1]["tokens"]
            lat = stats["per_request"][-1]["latency"]
            ok = "OK" if stats["per_request"][-1]["ok"] else "FAIL"
            total_tok += tok
            label = task[:40] if isinstance(task, str) else f"turn_{i}"
            print(f"  [{i:2d}/{len(tasks)}] {label:40s} -> {tok:5d}tok {lat:.1f}s {ok}")
            time.sleep(sleep_s)
        print(f"  Phase {phase_id}: {total_tok} tokens\n")
        return total_tok

    # Phase 1: Warm-up
    p1_tok = run_phase("Warm-up (easy)", P1, 1, sleep_s=0.5,
                       system_msg="简洁回答，3句话以内。")
    stats["phase_stats"]["p1_warmup"] = {"requests": 15, "tokens": p1_tok}

    # Phase 2: Hard questions
    p2_tok = run_phase("Hard Factual Questions", P2, 2, sleep_s=1.0,
                       system_msg="你是资深计算机科学家，请给出详细、准确、有深度的回答。")
    stats["phase_stats"]["p2_hard"] = {"requests": 15, "tokens": p2_tok}

    # Phase 3: Deep multi-turn
    print("[Phase 3/5] Deep Multi-turn (3 topics x 8 turns = 24 tasks)")
    print("-" * 50)
    p3_tok = 0
    for ci, conv in enumerate(P3_CONVS, 1):
        messages = [{"role": "system",
                     "content": "你是该领域的世界级专家，回答要极其详细和准确。"}]
        for ti, turn in enumerate(conv["turns"], 1):
            messages.append({"role": "user", "content": turn})
            r = do_request(messages, f"p3_{conv['topic']}_t{ti}")
            tok = stats["per_request"][-1]["tokens"]
            lat = stats["per_request"][-1]["latency"]
            ok = "OK" if stats["per_request"][-1]["ok"] else "FAIL"
            p3_tok += tok
            messages.append({"role": "assistant", "content": r})
            print(f"  [{conv['topic'][:6]} T{ti}] -> {tok:5d}tok {lat:.1f}s {ok}")
            time.sleep(0.5)
    stats["phase_stats"]["p3_multiturn"] = {"requests": 24, "tokens": p3_tok}
    print(f"  Phase 3: {p3_tok} tokens\n")

    # Phase 4: Complex code gen
    p4_tok = run_phase("Complex Code Generation (heavy)", P4, 4, sleep_s=1.0,
                       system_msg="你是资深工程师，写完整可运行的代码，包含详细注释和测试用例。")
    stats["phase_stats"]["p4_code"] = {"requests": 15, "tokens": p4_tok}

    # Phase 5: Rapid fire
    p5_tok = run_phase("Rapid Fire (minimal delay)", P5, 5, sleep_s=0.3,
                       system_msg="")
    stats["phase_stats"]["p5_rapid"] = {"requests": 30, "tokens": p5_tok}

    # ── Summary ──
    print("=" * 70)
    print("  STRESS TEST COMPLETE")
    print("=" * 70)
    print(f"  Total requests:  {stats['total_requests']}")
    print(f"  Success:         {stats['success']}")
    print(f"  Failed:          {stats['failed']}")
    print(f"  Total tokens:    {stats['total_tokens']}")
    print(f"  Total latency:   {stats['total_latency']:.1f}s")
    if stats["total_requests"] > 0:
        print(f"  Avg latency:     {stats['total_latency']/stats['total_requests']:.2f}s/req")
        print(f"  Avg tokens/req:  {stats['total_tokens']/stats['total_requests']:.0f}")
    print()
    for phase, data in stats["phase_stats"].items():
        print(f"    {phase:20s}: {data['requests']:3d} reqs, {data['tokens']:6d} tokens")
    print()

    report_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "stress_test_report.json")
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
    print(f"  Report saved: {report_path}")


if __name__ == "__main__":
    run_stress_test()
