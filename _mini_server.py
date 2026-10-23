"""Minimal OpenAI-compatible API server for the 0.5B model.
Uses llama_cpp.Llama + stdlib http.server. Zero extra deps.
Port 11434 = Ollama-compatible endpoint.
"""
import json, time, sys
from http.server import HTTPServer, BaseHTTPRequestHandler
from llama_cpp import Llama

MODEL = r"e:\QODERCN\ai_agent_monitor\models\qwen2.5-0.5b-instruct-q4_k_m.gguf"
HOST, PORT = "127.0.0.1", 11434

print("Loading model...", flush=True)
llm = Llama(model_path=MODEL, n_ctx=2048, n_gpu_layers=0, verbose=False)
print("Ready on http://{}:{}".format(HOST, PORT), flush=True)


def chat(messages, max_tokens=200, temperature=0.7):
    prompt = ""
    for m in messages:
        role, content = m.get("role", "user"), m.get("content", "")
        tag = {"system": "system", "user": "user",
               "assistant": "assistant"}.get(role, "user")
        prompt += "