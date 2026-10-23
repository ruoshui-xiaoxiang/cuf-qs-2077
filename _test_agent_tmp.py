"""Real AI Agent for end-to-end monitor test.

This agent uses the OpenAI-compatible API through the monitor proxy,
which intercepts calls and extracts metrics for the HUD to display.

Architecture:
  Agent -> Monitor Proxy (port 20780) -> Ollama (port 11434)

The agent performs a multi-step "research task" that generates
multiple API calls for the monitor to observe.
"""

import json
import os
import time
from openai import OpenAI

# Point to the monitor proxy (NOT directly to Ollama)
PROXY_URL = os.environ.get("AGENT_PROXY_URL", "http://127.0.0.1:20780/v1")
MODEL = os.environ.get("AGENT_MODEL", "qwen2.5:0.5b")

# Simulated research tasks - each generates one API call
TASKS = [
    "用一句话解释什么是递归。",
    "Python 中 list 和 tuple 的区别是什么？",
    "写一个 Python 函数计算斐波那契数列的第 n 项。",
    "什么是时间复杂度？用 O(n log n) 举一个排序算法的例子。",
    "解释 HTTP 状态码 200、404、500 分别代表什么。",
    "用 Python 写一段代码，读取 CSV 文件并统计每列的平均值。",
    "什么是 REST API？它和 GraphQL 有什么区别？",
    "解释什么是死锁(deadlock)，以及预防死锁的四种方法。",
    "用 Python 实现一个简单的栈类，支持 push、pop、is_empty。",
    "什么是 Docker？它和虚拟机有什么区别？",
    "解释 TCP 三次握手的过程。",
    "用 Python 写一个二分查找算法。",
    "什么是设计模式？列举三种常见的设计模式。",
    "解释什么是哈希表以及它的时间复杂度。",
    "用 Python 写一段代码，实现冒泡排序。",
]


def run_agent():
    print(f"[Agent] Starting real agent test")
    print(f"[Agent] Proxy: {PROXY_URL}")
    print(f"[Agent] Model: {MODEL}")
    print(f"[Agent] Tasks: {len(TASKS)}")
    print()

    client = OpenAI(
        base_url=PROXY_URL,
        api_key="not-needed",  # Ollama doesn't need a real key
    )

    success = 0
    fail = 0

    for i, task in enumerate(TASKS, 1):
        print(f"[Agent] Task {i}/{len(TASKS)}: {task[:40]}...")
        t0 = time.time()

        try:
            response = client.chat.completions.create(
                model=MODEL,
                messages=[
                    {"role": "system", "content": "你是一个简洁的编程助手，回答控制在3句话以内。"},
                    {"role": "user", "content": task},
                ],
                temperature=0.7,
                max_tokens=200,
            )

            elapsed = time.time() - t0
            answer = response.choices[0].message.content
            tokens = response.usage.total_tokens if response.usage else "?"
            print(f"  -> {elapsed:.1f}s | {tokens} tokens")
            print(f"  -> {answer[:80]}...")
            success += 1

        except Exception as e:
            elapsed = time.time() - t0
            print(f"  -> ERROR ({elapsed:.1f}s): {e}")
            fail += 1

        # Small delay between tasks so the monitor can capture each one
        time.sleep(1.5)

    print()
    print(f"[Agent] Done! Success: {success}, Failed: {fail}, Total: {len(TASKS)}")


if __name__ == "__main__":
    run_agent()
