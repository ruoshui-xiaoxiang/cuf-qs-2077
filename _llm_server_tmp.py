"""Minimal OpenAI-compatible API server using llama-cpp-python.

Serves a local GGUF model and provides /v1/chat/completions endpoint
that the monitor proxy can intercept.
"""

import json
import time
import uuid
import argparse
from http.server import HTTPServer, BaseHTTPRequestHandler
from llama_cpp import Llama


def parse_args():
    p = argparse.ArgumentParser(description="Tiny OpenAI-compatible GGUF server")
    p.add_argument("--model", required=True, help="Path to .gguf model file")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=11434)
    p.add_argument("--n-ctx", type=int, default=2048)
    p.add_argument("--n-gpu-layers", type=int, default=0)
    return p.parse_args()


args = parse_args()
print(f"[LLM Server] Loading model: {args.model}")
t0 = time.time()
llm = Llama(
    model_path=args.model,
    n_ctx=args.n_ctx,
    n_gpu_layers=args.n_gpu_layers,
    verbose=False,
)
print(f"[LLM Server] Model loaded in {time.time()-t0:.1f}s")


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path == "/v1/chat/completions":
            content_len = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(content_len)) if content_len else {}

            messages = body.get("messages", [])
            model_name = body.get("model", "local")
            max_tokens = min(body.get("max_tokens", 256), 512)
            temperature = body.get("temperature", 0.7)

            # Build prompt from messages
            prompt_parts = []
            for msg in messages:
                role = msg.get("role", "user")
                content = msg.get("content", "")
                if role == "system":
                    prompt_parts.append(f"<|system|>\n{content}</s>")
                elif role == "user":
                    prompt_parts.append(f"<|user|>\n{content}</s>")
                elif role == "assistant":
                    prompt_parts.append(f"<|assistant|>\n{content}</s>")
            prompt_parts.append("<|assistant|>\n")
            prompt = "\n".join(prompt_parts)

            # Generate
            t0 = time.monotonic()
            result = llm(
                prompt,
                max_tokens=max_tokens,
                temperature=temperature,
                echo=False,
            )
            elapsed = time.monotonic() - t0

            text = result["choices"][0]["text"]
            usage = result.get("usage", {})
            prompt_tokens = usage.get("prompt_tokens", 0)
            completion_tokens = usage.get("completion_tokens", 0)
            total_tokens = prompt_tokens + completion_tokens

            response = {
                "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
                "object": "chat.completion",
                "created": int(time.time()),
                "model": model_name,
                "choices": [{
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": text.strip(),
                    },
                    "finish_reason": "stop",
                }],
                "usage": {
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "total_tokens": total_tokens,
                },
            }

            resp_body = json.dumps(response, ensure_ascii=False).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(resp_body)))
            self.end_headers()
            self.wfile.write(resp_body)
            print(f"  [LLM] {model_name} | {total_tokens}tok | {elapsed:.1f}s | {text[:60]}...")
        else:
            self.send_response(404)
            self.end_headers()

    def do_GET(self):
        # Handle /v1/models endpoint
        if self.path == "/v1/models":
            response = {
                "data": [{
                    "id": "local-model",
                    "object": "model",
                    "owned_by": "local",
                }]
            }
            resp_body = json.dumps(response).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(resp_body)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, fmt, *a):
        pass  # Suppress default logging


if __name__ == "__main__":
    server = HTTPServer((args.host, args.port), Handler)
    print(f"[LLM Server] Serving on http://{args.host}:{args.port}")
    print(f"[LLM Server] OpenAI-compatible endpoint: http://{args.host}:{args.port}/v1")
    server.serve_forever()
