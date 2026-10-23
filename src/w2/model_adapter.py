"""W2 模型适配器——7B 本地推理 + forward hook 隐状态提取。

锁定决策 #1：目标向量 g 用 7B 模型自身嵌入生成（弃用 CLIP），
保证 g 与隐状态 s_t 同空间。
锁定决策 #2：隐状态提取协议固化——层号、token 位置、池化方式、读取时机
全部从 config/experiment_config.json 的 model.hidden_state_protocol 读取，
实验全程不可变更。
反套壳铁则 #1：本模块只产出向量（g 与 s_t），不参与任何裁决；
对齐分、收敛判定、降级触发全部由 qs_kernel / downgrade_manager 硬编码完成。

接口：
- encode(text) -> list[float]：按协议提取文本的隐状态向量
- compute_target_g(goal_text) -> list[float]：生成目标向量 g 的嵌入（交给 QSKernel）
- extract_hidden(observation_text) -> list[float]：提取观测隐状态 s_t（交给 QSKernel）
"""

import json
import os
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from common.utils import ContextOverflowError

DEFAULT_CONFIG_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "config", "experiment_config.json"
)

# 协议允许的取值（锁定决策 #2，fail-loud：未知取值直接抛错）
ALLOWED_TOKEN_POSITIONS = ("last_non_pad", "last")
ALLOWED_POOLINGS = ("last",)
ALLOWED_READ_TIMINGS = ("post_forward",)


def resolve_token_index(input_ids, attention_mask, token_position, pad_token_id=None):
    """按协议计算目标 token 的下标（纯函数，可脱离模型单测）。

    Args:
        input_ids: token id 序列（list 或一维张量）。
        attention_mask: 0/1 序列，与 input_ids 等长。
        token_position: 协议取值，见 ALLOWED_TOKEN_POSITIONS。
        pad_token_id: 保留参数（last_non_pad 依据 attention_mask 判定）。

    Returns:
        目标 token 下标 int。

    Raises:
        ValueError: 协议取值未知，或序列中无有效 token。
    """
    if token_position not in ALLOWED_TOKEN_POSITIONS:
        raise ValueError(f"unsupported token_position: {token_position!r}")

    n = len(input_ids)
    if n == 0:
        raise ValueError("empty input sequence")
    if len(attention_mask) != n:
        raise ValueError(
            f"attention_mask length {len(attention_mask)} != input_ids length {n}")

    if token_position == "last":
        return n - 1

    # last_non_pad：取 attention_mask==1 的最后一个位置
    valid = [i for i in range(n) if int(attention_mask[i]) == 1]
    if not valid:
        raise ValueError("no non-pad token found (attention_mask all zero)")
    return valid[-1]


def pool_hidden_state(hidden_2d, index, pooling):
    """按协议对隐状态做池化（纯函数，可脱离模型单测）。

    Args:
        hidden_2d: (seq_len, dim) 的隐状态（张量或二维 list）。
        index: 目标 token 下标。
        pooling: 协议取值，见 ALLOWED_POOLINGS。

    Returns:
        (dim,) 向量（与输入同类型：张量输入返回张量行，list 输入返回 list）。

    Raises:
        ValueError: 协议取值未知或下标越界。
    """
    if pooling not in ALLOWED_POOLINGS:
        raise ValueError(f"unsupported pooling: {pooling!r}")

    seq_len = len(hidden_2d)
    if not (0 <= index < seq_len):
        raise ValueError(f"token index {index} out of range [0, {seq_len})")

    # 当前协议仅 "last"：直接取目标 token 位置的隐状态
    return hidden_2d[index]


def mean_diff_normalize(pos_vecs, neg_vecs):
    """对比方向提取的纯计算核（D1，可脱离模型单测）：
    mean(pos) - mean(neg) 后归一化为单位向量。

    Args:
        pos_vecs: 概念侧向量列表（等维）。
        neg_vecs: 中性对照侧向量列表（与 pos 同维）。

    Returns:
        (direction, meta)：direction 为单位长度 list；meta 含两侧均值范数、
        差分原始范数、样本量。

    Raises:
        ValueError: 任一侧为空、维度不一致、差分零向量。
    """
    if not pos_vecs or not neg_vecs:
        raise ValueError("both sides need at least one vector")
    dim = len(pos_vecs[0])
    for side in (pos_vecs, neg_vecs):
        for v in side:
            if len(v) != dim:
                raise ValueError("vector dimension mismatch")

    def mean(vecs):
        acc = [0.0] * dim
        for v in vecs:
            for i, x in enumerate(v):
                acc[i] += float(x)
        n = float(len(vecs))
        return [x / n for x in acc]

    mp, mn = mean(pos_vecs), mean(neg_vecs)
    diff = [a - b for a, b in zip(mp, mn)]
    raw_norm = sum(x * x for x in diff) ** 0.5
    if raw_norm == 0.0:
        raise ValueError("mean difference is a zero vector")
    direction = [x / raw_norm for x in diff]
    meta = {
        "n_pos": len(pos_vecs),
        "n_neg": len(neg_vecs),
        "pos_mean_norm": sum(x * x for x in mp) ** 0.5,
        "neg_mean_norm": sum(x * x for x in mn) ** 0.5,
        "raw_diff_norm": raw_norm,
    }
    return direction, meta


def make_steering_hook(alpha, vector):
    """构造生成侧转向钩子（D1：输出 + α·v，纯函数，可脱离模型单测）。

    钩子只注册在生成调用期间（调用方负责注册/移除）；度量通道
    （encode/extract_hidden）不注册此钩子——干预只加生成侧是铁则，
    动度量侧等于掰仪表。
    """
    def hook(module, inputs, output):
        hidden = output[0] if isinstance(output, tuple) else output
        try:
            import torch
            v = vector.to(device=hidden.device, dtype=hidden.dtype)
            steered = hidden + float(alpha) * v
        except ImportError:
            steered = [[h + float(alpha) * vi for h, vi in zip(row, vector)]
                       for row in hidden]
        if isinstance(output, tuple):
            return (steered,) + output[1:]
        return steered
    return hook


class ModelAdapter:
    """7B 模型适配器——本地量化推理 + forward hook 隐状态提取。

    用法：
        adapter = ModelAdapter.from_config()
        adapter.load()
        g_emb = adapter.compute_target_g("维持公理稳态的目标描述")
        s_t = adapter.extract_hidden("本轮观测描述")
        # 交给 QSKernel：qs.compute_target_g(g_emb); qs.compute_alignment_score(s_t)
    """

    def __init__(self, model_id, device="cuda:0", load_in_4bit=True,
                 compute_dtype="bfloat16", max_length=512, seed=2077,
                 protocol=None, hf_home=None, generate_max_length=4096):
        self.model_id = model_id
        self.device = device
        self.load_in_4bit = bool(load_in_4bit)
        self.compute_dtype = compute_dtype
        self.max_length = int(max_length)
        # 长度预算分家：max_length 锁测量仪器（encode，决策 #2 锁定，
        # 不得随工具调用扩容以免破坏隐状态可比性）；
        # generate_max_length 单独承载生成侧（规划器/工具调用多轮上下文）。
        self.generate_max_length = int(generate_max_length)
        self.seed = int(seed)
        self.hf_home = hf_home
        self.protocol = dict(protocol or {})
        self._validate_protocol()

        self._model = None
        self._tokenizer = None
        self._hook_handle = None
        self._captured = None
        self._steering = None

        # HF 缓存目录须在导入 huggingface 组件前生效
        if self.hf_home:
            os.environ["HF_HOME"] = self.hf_home

    def _validate_protocol(self):
        """协议校验（锁定决策 #2）：缺字段或取值非法直接抛错，禁止默认回退。"""
        required = ("layer_index", "token_position", "pooling", "read_timing")
        for key in required:
            if key not in self.protocol:
                raise ValueError(f"hidden_state_protocol missing field: {key}")
        if not isinstance(self.protocol["layer_index"], int):
            raise ValueError("protocol.layer_index must be int")
        if self.protocol["token_position"] not in ALLOWED_TOKEN_POSITIONS:
            raise ValueError(
                f"unsupported token_position: {self.protocol['token_position']!r}")
        if self.protocol["pooling"] not in ALLOWED_POOLINGS:
            raise ValueError(f"unsupported pooling: {self.protocol['pooling']!r}")
        if self.protocol["read_timing"] not in ALLOWED_READ_TIMINGS:
            raise ValueError(
                f"unsupported read_timing: {self.protocol['read_timing']!r}")

    @classmethod
    def from_config(cls, config_path=None):
        """从配置文件构造（读取 model 段）。"""
        path = config_path or DEFAULT_CONFIG_PATH
        with open(path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        m = cfg.get("model")
        if not m:
            raise ValueError(f"config missing 'model' section: {path}")
        return cls(
            model_id=m["model_id"],
            device=m.get("device", "cuda:0"),
            load_in_4bit=m.get("load_in_4bit", True),
            compute_dtype=m.get("compute_dtype", "bfloat16"),
            max_length=m.get("max_length", 512),
            seed=m.get("seed", 2077),
            protocol=m.get("hidden_state_protocol"),
            hf_home=m.get("hf_home"),
            generate_max_length=m.get("generate_max_length", 4096),
        )

    # ── 加载 / 卸载 ──────────────────────────────────────

    @property
    def loaded(self):
        return self._model is not None

    def load(self):
        """加载 tokenizer + 量化模型，并在协议指定层注册 forward hook。

        缓存定位：hf_home 为 HF_HOME 根（如 E:\\HF_CACHE），实际权重在其
        hub/ 子目录下（models--org--name/snapshots/...）。
        local_files_only=True：只读本地缓存，缺失即抛错，禁止静默联网下载。
        """
        if self.loaded:
            return

        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer

        torch.manual_seed(self.seed)

        cache_dir = None
        if self.hf_home:
            hub_dir = os.path.join(self.hf_home, "hub")
            cache_dir = hub_dir if os.path.isdir(hub_dir) else self.hf_home

        self._tokenizer = AutoTokenizer.from_pretrained(
            self.model_id, cache_dir=cache_dir, local_files_only=True)

        kwargs = {}
        if self.load_in_4bit:
            from transformers import BitsAndBytesConfig
            dtype = getattr(torch, self.compute_dtype)
            kwargs["quantization_config"] = BitsAndBytesConfig(
                load_in_4bit=True,
                bnb_4bit_quant_type="nf4",
                bnb_4bit_compute_dtype=dtype,
            )
        else:
            kwargs["torch_dtype"] = getattr(torch, self.compute_dtype)

        self._model = AutoModelForCausalLM.from_pretrained(
            self.model_id,
            cache_dir=cache_dir,
            local_files_only=True,
            device_map={"": self.device},
            **kwargs,
        )
        self._model.eval()

        self._register_hook()

    def _register_hook(self):
        """在协议指定解码层注册 forward hook（read_timing=post_forward）。"""
        layers = self._model.model.layers
        layer_index = self.protocol["layer_index"]
        n_layers = len(layers)
        resolved = layer_index if layer_index >= 0 else n_layers + layer_index
        if not (0 <= resolved < n_layers):
            raise ValueError(
                f"protocol.layer_index {layer_index} out of range "
                f"(model has {n_layers} layers)")

        def hook(module, inputs, output):
            # 解码层输出为 tuple，首元素为 hidden_states (batch, seq, dim)
            hidden = output[0] if isinstance(output, tuple) else output
            self._captured = hidden.detach()

        self._hook_handle = layers[resolved].register_forward_hook(hook)
        self._resolved_layer = resolved

    def unload(self):
        """卸载模型释放显存。"""
        if self._hook_handle is not None:
            self._hook_handle.remove()
            self._hook_handle = None
        self._model = None
        self._tokenizer = None
        self._captured = None
        self._steering = None

    # ── 方向干预（D1：生成侧 activation steering）──────────

    def _resolve_layer(self, layer_index):
        layers = self._model.model.layers
        n_layers = len(layers)
        resolved = layer_index if layer_index >= 0 else n_layers + layer_index
        if not (0 <= resolved < n_layers):
            raise ValueError(
                f"layer_index {layer_index} out of range "
                f"(model has {n_layers} layers)")
        return resolved

    def set_steering(self, layer_index, alpha, vector):
        """配置生成侧转向（D1 纪律：只作用于生成调用，度量通道不注册）。

        Args:
            layer_index: 干预层（支持负索引）。
            alpha: 注入系数（可为负 = 反方向消融）。
            vector: 方向向量（单位向量，长度须等于模型 hidden_size）。
        """
        if not self.loaded:
            raise RuntimeError("model not loaded; call load() first")
        import torch
        resolved = self._resolve_layer(int(layer_index))
        hidden_size = self._model.config.hidden_size
        if len(vector) != hidden_size:
            raise ValueError(
                f"steering vector dim {len(vector)} != hidden_size {hidden_size}")
        self._steering = {
            "layer": resolved,
            "alpha": float(alpha),
            "vector": torch.tensor(vector, dtype=torch.float32,
                                   device=self.device),
        }

    def clear_steering(self):
        self._steering = None

    def steering_status(self):
        if self._steering is None:
            return None
        return {"layer": self._steering["layer"],
                "alpha": self._steering["alpha"],
                "dim": int(self._steering["vector"].shape[0])}

    def _steer_handle(self):
        """按当前转向配置注册临时钩子，返回 handle（未配置返回 None）。"""
        if self._steering is None:
            return None
        hook = make_steering_hook(self._steering["alpha"],
                                  self._steering["vector"])
        layers = self._model.model.layers
        return layers[self._steering["layer"]].register_forward_hook(hook)

    def extract_direction(self, layer_index, pos_texts, neg_texts):
        """对比方向提取（D1：概念侧 − 中性侧 均值差，归一化）。

        提取走度量通道语义（只读、无转向钩子）：每文本前向后在目标层
        捕获隐状态，按协议 token_position=last_non_pad 取目标位置。

        Returns:
            (direction: list[float], meta: dict)
        """
        if not self.loaded:
            raise RuntimeError("model not loaded; call load() first")
        if not pos_texts or not neg_texts:
            raise ValueError("both sides need at least one text")
        import torch
        resolved = self._resolve_layer(int(layer_index))
        layers = self._model.model.layers
        captured = {}

        def hook(module, inputs, output):
            hidden = output[0] if isinstance(output, tuple) else output
            captured["h"] = hidden.detach()

        handle = layers[resolved].register_forward_hook(hook)

        def collect(texts):
            vecs = []
            for text in texts:
                enc = self._tokenizer(text, return_tensors="pt")
                if enc["input_ids"].shape[1] > self.max_length:
                    raise ContextOverflowError(
                        f"context overflow: {enc['input_ids'].shape[1]} tokens "
                        f"> max_length {self.max_length}")
                enc = enc.to(self.device)
                captured.clear()
                with torch.no_grad():
                    self._model(**enc)
                if "h" not in captured:
                    raise RuntimeError("direction hook captured nothing")
                idx = resolve_token_index(
                    enc["input_ids"][0], enc["attention_mask"][0],
                    "last_non_pad",
                    pad_token_id=self._tokenizer.pad_token_id)
                vec = captured["h"][0][idx]
                vecs.append(vec.to(torch.float32).cpu().tolist())
            return vecs

        try:
            pos_vecs = collect(pos_texts)
            neg_vecs = collect(neg_texts)
        finally:
            handle.remove()
        direction, meta = mean_diff_normalize(pos_vecs, neg_vecs)
        meta["layer"] = resolved
        meta["dim"] = len(direction)
        return direction, meta

    # ── 向量提取 ─────────────────────────────────────────

    def encode(self, text):
        """按协议提取文本的隐状态向量。

        流程（锁定决策 #2 固化）：
        1. tokenize（截断至 max_length）
        2. torch.no_grad() forward，hook 在协议层捕获隐状态
        3. resolve_token_index 按 token_position 选目标 token
        4. pool_hidden_state 按 pooling 池化
        5. 转 float32 list 返回

        Returns:
            list[float]，维度 = 模型 hidden_size。

        Raises:
            RuntimeError: 模型未加载或 hook 未捕获到隐状态。
        """
        if not self.loaded:
            raise RuntimeError("model not loaded; call load() first")

        import torch

        # 上下文窗口 = 意识体采集窗口，只采客观条件且容量有限。
        # 溢出（观测超出 max_length）属系统前置条件②破坏 → 抛
        # ContextOverflowError（「系统不可信」），不再 truncation 静默截断。
        enc = self._tokenizer(text, return_tensors="pt")
        if enc["input_ids"].shape[1] > self.max_length:
            raise ContextOverflowError(
                f"context overflow: {enc['input_ids'].shape[1]} tokens "
                f"> max_length {self.max_length}")
        enc = enc.to(self.device)

        self._captured = None
        with torch.no_grad():
            self._model(**enc)

        if self._captured is None:
            raise RuntimeError("forward hook captured nothing; check layer registration")

        hidden = self._captured[0]  # (seq_len, dim)
        idx = resolve_token_index(
            enc["input_ids"][0], enc["attention_mask"][0],
            self.protocol["token_position"],
            pad_token_id=self._tokenizer.pad_token_id)
        vec = pool_hidden_state(hidden, idx, self.protocol["pooling"])
        return vec.to(torch.float32).cpu().tolist()

    def compute_target_g(self, goal_text):
        """生成目标向量 g 的嵌入（锁定决策 #1：模型自身嵌入）。

        返回值交给 QSKernel.compute_target_g 归一化后使用。
        """
        return self.encode(goal_text)

    def extract_hidden(self, observation_text):
        """提取观测隐状态 s_t，交给 QSKernel.compute_alignment_score。"""
        return self.encode(observation_text)

    def generate_text(self, prompt, max_new_tokens=64):
        """贪心文本生成（审计 A1：LLM 辅助 live 路径的模型端）。

        供 LLMAssistant 三接口的实机分支调用——LLM 只生成人类可读
        自然语言，裁决权仍归硬编码模块（llm_assistant 侧剥离裁决词
        并打 advisory 标记）。do_sample=False：确定性贪心，保证同
        prompt 同输出（可复现）。

        Returns:
            str: 生成的补全文本（不含 prompt 前缀）。

        Raises:
            RuntimeError: 模型未加载。
        """
        if not self.loaded:
            raise RuntimeError("model not loaded; call load() first")

        import torch

        # 长度预算走生成侧账本；超限按「系统前置条件②破坏」抛
        # ContextOverflowError（系统不可信），不得静默截断。
        enc = self._tokenizer(prompt, return_tensors="pt")
        if enc["input_ids"].shape[1] > self.generate_max_length:
            raise ContextOverflowError(
                f"generate context overflow: {enc['input_ids'].shape[1]} "
                f"tokens > generate_max_length {self.generate_max_length}")
        enc = enc.to(self.device)
        pad_id = self._tokenizer.pad_token_id or self._tokenizer.eos_token_id
        handle = self._steer_handle()
        try:
            with torch.no_grad():
                out = self._model.generate(
                    **enc, max_new_tokens=int(max_new_tokens),
                    do_sample=False, pad_token_id=pad_id)
        finally:
            if handle is not None:
                handle.remove()
        new_tokens = out[0][enc["input_ids"].shape[1]:]
        return self._tokenizer.decode(
            new_tokens, skip_special_tokens=True).strip()

    def generate_chat_text(self, messages, max_new_tokens=256,
                           forced_prefix=None, do_sample=False,
                           temperature=0.7, enable_thinking=False):
        """基于 chat template 的指令遵循生成（S29 动作规划等强指令任务）。

        与 generate_text（裸 prompt 续写）的关键区别：经
        tokenizer.apply_chat_template 包裹 system/user 消息后，
        Qwen2.5-Instruct 才进入指令遵循模式；裸续写会回显观测/续写
        规则（S29 shadow 实机观察到的失败模式）。do_sample=False
        贪心确定性，同输入同输出（与 generate_text 一致）。

        forced_prefix：guided decoding（constrained prefix）——在
        assistant 生成起点注入固定文本前缀（如 '{"action_type": "'），
        锁死输出骨架，模型只能从该前缀续写，杜绝回显观测导致的
        结构漂移。返回文本 = forced_prefix + 新生成部分。

        Args:
            messages: OpenAI 风格消息列表，如 [{"role": "system", ...},
                {"role": "user", "content": ...}]。
            max_new_tokens: 生成上限。
            forced_prefix: 非 None 时注入生成前缀（constrained prefix）。

        Returns:
            str: 生成的补全文本（含 forced_prefix，不含 chat template）。
        """
        if not self.loaded:
            raise RuntimeError("model not loaded; call load() first")

        import torch

        chat = self._tokenizer.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True,
            enable_thinking=bool(enable_thinking))
        if forced_prefix:
            chat += forced_prefix
        # 同 generate_text：生成侧账本 + fail-loud，禁止 truncation 静默截。
        enc = self._tokenizer(chat, return_tensors="pt")
        if enc["input_ids"].shape[1] > self.generate_max_length:
            raise ContextOverflowError(
                f"generate context overflow: {enc['input_ids'].shape[1]} "
                f"tokens > generate_max_length {self.generate_max_length}")
        enc = enc.to(self.device)
        pad_id = self._tokenizer.pad_token_id or self._tokenizer.eos_token_id
        gen_kwargs = {"max_new_tokens": int(max_new_tokens),
                      "do_sample": bool(do_sample), "pad_token_id": pad_id}
        if do_sample:
            # S29.10 后 7B 贪心回显观测（无动作词）→ 采样重试跳出回显
            # 分布；贪心模式不传 temperature（无效参数）。
            gen_kwargs["temperature"] = float(temperature)
        handle = self._steer_handle()
        try:
            with torch.no_grad():
                out = self._model.generate(**enc, **gen_kwargs)
        finally:
            if handle is not None:
                handle.remove()
        new_tokens = out[0][enc["input_ids"].shape[1]:]
        text = self._tokenizer.decode(
            new_tokens, skip_special_tokens=True).strip()
        if forced_prefix:
            text = forced_prefix + text
        return text


# S29.11 模型驻留（方案 A）：常驻 model_server 进程持有模型，测试脚本
# 通过 RemoteModelAdapter 远程调用，避免每次测试重复加载 7B（13-18 分钟）。
# server 只做模型推理（文本生成 / 隐状态提取），不参与任何裁决
# （反套壳不变）。协议见 model_server.py（JSON 行，端口 20772）。
# 2026-08-26 扩展：协议新增 encode op——7B 双路批次（embedding_fn/
# state_fn 需 3584 维隐状态）也可直接复用驻留模型，无需本地再加载。
DEFAULT_MODEL_SERVER_PORT = 20772


class RemoteModelAdapter:
    """远程模型服务 client——与 ModelAdapter 推理接口兼容。

    支持 generate_chat_text（文本生成）与 encode/compute_target_g/
    extract_hidden（隐状态提取，走协议 encode op）。ping() 做健康
    检查：server 在则用远程（秒级），否则调用方回退本地加载
    （向后兼容）。上下文溢出经 error_type=context_overflow 还原为
    ContextOverflowError（「系统不可信」契约，决策 #12）。
    """

    def __init__(self, host="127.0.0.1", port=DEFAULT_MODEL_SERVER_PORT,
                 timeout_s=180.0):
        self.host = host
        self.port = port
        self.timeout_s = timeout_s

    def _rpc(self, payload):
        import socket
        with socket.create_connection(
                (self.host, self.port), timeout=self.timeout_s) as s:
            s.sendall((json.dumps(payload, ensure_ascii=False) + "\n")
                      .encode("utf-8"))
            f = s.makefile("r", encoding="utf-8")
            line = f.readline()
            if not line:
                raise RuntimeError("model_server empty response")
            resp = json.loads(line)
        if not resp.get("ok"):
            if resp.get("error_type") == "context_overflow":
                # 前置条件②破坏须穿透（决策 #12）：还原为
                # ContextOverflowError，不得吞成普通运行时错误
                raise ContextOverflowError(
                    resp.get("error") or "remote context overflow")
            raise RuntimeError(f"model_server error: {resp.get('error')}")
        return resp

    def generate_chat_text(self, messages, max_new_tokens=256,
                           forced_prefix=None, do_sample=False,
                           temperature=0.7, enable_thinking=False):
        resp = self._rpc({
            "op": "generate_chat_text",
            "messages": messages,
            "max_new_tokens": int(max_new_tokens),
            "do_sample": bool(do_sample),
            "temperature": float(temperature),
            "forced_prefix": forced_prefix,
            "enable_thinking": bool(enable_thinking),
        })
        return resp["text"]

    def encode(self, text):
        """远程隐状态提取（协议 encode op）——与本地 ModelAdapter.encode
        语义一致：同权重同协议 → 同向量（确定性）；上下文溢出抛
        ContextOverflowError（「系统不可信」）。

        Returns:
            list[float]，维度 = 模型 hidden_size。
        """
        resp = self._rpc({"op": "encode", "text": text})
        return resp["vector"]

    def compute_target_g(self, goal_text):
        """目标向量 g 的远程嵌入（与 ModelAdapter.compute_target_g 对齐）。"""
        return self.encode(goal_text)

    def extract_hidden(self, observation_text):
        """观测隐状态 s_t 的远程提取（与 ModelAdapter.extract_hidden 对齐）。"""
        return self.encode(observation_text)

    # ── D1 方向干预（驻留协议扩展）─────────────────────────

    def set_steering(self, layer_index, alpha, vector):
        """配置驻留模型的生成侧转向（只影响 generate，不影响 encode）。"""
        resp = self._rpc({
            "op": "set_steering",
            "layer": int(layer_index),
            "alpha": float(alpha),
            "vector": [float(x) for x in vector],
        })
        return resp.get("status")

    def clear_steering(self):
        self._rpc({"op": "clear_steering"})

    def steering_status(self):
        resp = self._rpc({"op": "steering_status"})
        return resp.get("status")

    def extract_direction(self, layer_index, pos_texts, neg_texts):
        """远程对比方向提取（驻留模型上计算，避免重复加载）。"""
        resp = self._rpc({
            "op": "extract_direction",
            "layer": int(layer_index),
            "pos_texts": list(pos_texts),
            "neg_texts": list(neg_texts),
        })
        return resp["vector"], resp.get("meta") or {}

    @classmethod
    def ping(cls, host="127.0.0.1", port=DEFAULT_MODEL_SERVER_PORT,
             timeout_s=2.0):
        try:
            import socket
            with socket.create_connection(
                    (host, port), timeout=timeout_s) as s:
                s.sendall(b'{"op":"ping"}\n')
                f = s.makefile("r", encoding="utf-8")
                resp = json.loads(f.readline())
            return bool(resp.get("pong"))
        except Exception:
            return False
