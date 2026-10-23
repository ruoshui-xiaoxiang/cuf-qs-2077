"""
Experiment: Invert all weights of a 0.5B GGUF model.

GGUF format:
  [magic] [version] [n_tensors] [n_kv]
  [KV metadata...]
  [Tensor info...]
  [Padding to 32-byte alignment]
  [Tensor data...]

Strategy: Parse headers properly, then negate every byte in the tensor data region.
"""
import struct, pathlib, numpy as np

GGUF_PATH = pathlib.Path(r"e:\QODERCN\ai_agent_monitor\models\qwen2.5-0.5b-instruct-q4_k_m.gguf")
OUTPUT_PATH = GGUF_PATH.parent / "qwen2.5-0.5b-INVERTED.gguf"

# GGUF value types
GGUF_TYPES = {
    0: "UINT8", 1: "INT8", 2: "UINT16", 3: "INT16",
    4: "UINT32", 5: "INT32", 6: "FLOAT32", 7: "BOOL",
    8: "STRING", 9: "ARRAY", 10: "UINT64", 11: "INT64",
    12: "FLOAT64", 13: "FLOAT16",
}

# GGML tensor types and their element sizes
GGML_TYPE_SIZE = {
    0: 4,    # F32
    1: 2,    # F16
    2: 0,    # Q4_0 (special)
    3: 0,    # Q4_1 (special)
    6: 0,    # Q5_0 (special)
    7: 0,    # Q5_1 (special)
    8: 0,    # Q8_0 (special)
    9: 0,    # Q8_1 (special)
    10: 0,   # Q2_K (special)
    11: 0,   # Q3_K (special)
    12: 0,   # Q4_K (special)  <-- our model uses this
    13: 0,   # Q5_K (special)
    14: 0,   # Q6_K (special)
    15: 0,   # Q8_K (special)
    16: 2,   # IQ2_XXS
    17: 2,   # IQ2_XS
    18: 2,   # IQ3_XXS
    19: 2,   # IQ1_S
    20: 2,   # IQ4_NL
    21: 3,   # IQ3_S
    22: 2,   # IQ2_S
    23: 2,   # IQ4_XS
}

def read_string(data, offset):
    """Read a GGUF string (length-prefixed)."""
    length = struct.unpack_from("<Q", data, offset)[0]
    offset += 8
    s = data[offset:offset+length].decode("utf-8")
    return s, offset + length

def skip_value(data, offset, vtype):
    """Skip a GGUF metadata value, return new offset."""
    if vtype in (0, 1):  # UINT8, INT8
        return offset + 1
    elif vtype in (2, 3):  # UINT16, INT16
        return offset + 2
    elif vtype in (4, 5, 6):  # UINT32, INT32, FLOAT32
        return offset + 4
    elif vtype in (10, 11, 12):  # UINT64, INT64, FLOAT64
        return offset + 8
    elif vtype == 7:  # BOOL
        return offset + 1
    elif vtype == 8:  # STRING
        _, offset = read_string(data, offset)
        return offset
    elif vtype == 9:  # ARRAY
        atype = struct.unpack_from("<I", data, offset)[0]
        alen = struct.unpack_from("<Q", data, offset + 4)[0]
        offset += 12
        for _ in range(alen):
            offset = skip_value(data, offset, atype)
        return offset
    elif vtype == 13:  # FLOAT16
        return offset + 2
    else:
        raise ValueError(f"Unknown GGUF type: {vtype}")

def parse_gguf(data):
    """Parse GGUF header, return (tensor_data_offset, tensor_info_list)."""
    magic = struct.unpack_from("<I", data, 0)[0]
    assert magic == 0x46554747, f"Not GGUF: {hex(magic)}"  # 'GGUF' in LE
    version = struct.unpack_from("<I", data, 4)[0]
    n_tensors = struct.unpack_from("<Q", data, 8)[0]
    n_kv = struct.unpack_from("<Q", data, 16)[0]
    print(f"  GGUF v{version}: {n_tensors} tensors, {n_kv} metadata entries")

    # Skip KV metadata
    offset = 24
    for i in range(n_kv):
        key, offset = read_string(data, offset)
        vtype = struct.unpack_from("<I", data, offset)[0]
        offset += 4
        offset = skip_value(data, offset, vtype)
    print(f"  KV metadata ends at offset {offset}")

    # Read tensor info
    tensors = []
    for i in range(n_tensors):
        name, offset = read_string(data, offset)
        n_dims = struct.unpack_from("<I", data, offset)[0]
        offset += 4
        dims = []
        for _ in range(n_dims):
            dims.append(struct.unpack_from("<Q", data, offset)[0])
            offset += 8
        ttype = struct.unpack_from("<I", data, offset)[0]
        offset += 4
        toffset = struct.unpack_from("<Q", data, offset)[0]
        offset += 8
        tensors.append((name, n_dims, dims, ttype, toffset))
        if i < 5 or i >= n_tensors - 3:
            print(f"    [{i}] {name}: dims={dims} type={ttype} offset={toffset}")
        elif i == 5:
            print(f"    ... ({n_tensors - 8} more tensors) ...")

    # Find tensor data start
    # Tensor data is aligned to 32 bytes
    tensor_data_start = max(t[4] for t in tensors) if tensors else offset
    # Actually, find the MINIMUM offset where tensor data begins
    # The data region starts after all tensor info, aligned to 32 bytes
    header_end = offset
    alignment = 32
    tensor_data_start = ((header_end + alignment - 1) // alignment) * alignment
    
    # Find actual tensor data region
    min_data_offset = min(t[4] for t in tensors)
    max_data_offset = max(t[4] for t in tensors)
    
    print(f"\n  Header ends at: {header_end}")
    print(f"  Aligned tensor data start: {tensor_data_start}")
    print(f"  First tensor data at: {min_data_offset}")
    print(f"  Last tensor data at: {max_data_offset}")
    print(f"  File size: {len(data)}")
    
    return tensor_data_start, tensors

def main():
    print("=" * 60)
    print("Experiment: Invert 0.5B Model Weights")
    print("=" * 60)
    print(f"\n  Source: {GGUF_PATH.name} ({GGUF_PATH.stat().st_size / 1024 / 1024:.1f} MB)")
    
    print("\n[1/4] Reading file...")
    data = bytearray(GGUF_PATH.read_bytes())
    print(f"  Read {len(data)} bytes")
    
    print("\n[2/4] Parsing GGUF structure...")
    tensor_data_start, tensors = parse_gguf(data)
    
    # Count tensor types
    type_counts = {}
    for name, ndims, dims, ttype, toffset in tensors:
        type_counts[ttype] = type_counts.get(ttype, 0) + 1
    print(f"\n  Tensor type distribution:")
    for ttype, count in sorted(type_counts.items()):
        print(f"    Type {ttype}: {count} tensors")
    
    print(f"\n[3/4] Inverting tensor data (XOR 0xFF)...")
    print(f"  Strategy: XOR every byte from offset {tensor_data_start} to end")
    print(f"  This flips all bits in quantized weights")
    print(f"  Metadata preserved intact")
    
    # Count bytes to invert
    n_bytes = len(data) - tensor_data_start
    print(f"  Bytes to invert: {n_bytes:,} ({n_bytes/1024/1024:.1f} MB)")
    
    # XOR invert all tensor data
    inverted = bytearray(data)
    for i in range(tensor_data_start, len(inverted)):
        inverted[i] ^= 0xFF
    
    print(f"  Done. {n_bytes:,} bytes inverted.")
    
    print(f"\n[4/4] Writing inverted model...")
    OUTPUT_PATH.write_bytes(inverted)
    print(f"  Written: {OUTPUT_PATH} ({OUTPUT_PATH.stat().st_size / 1024 / 1024:.1f} MB)")
    
    # Verify
    print(f"\n{'=' * 60}")
    print("Verification:")
    orig = pathlib.Path(GGUF_PATH).read_bytes()
    inv = OUTPUT_PATH.read_bytes()
    
    # Check header is identical
    header_match = orig[:tensor_data_start] == inv[:tensor_data_start]
    print(f"  Header preserved: {header_match} (first {tensor_data_start} bytes identical)")
    
    # Check tensor data is different
    diff_count = sum(1 for a, b in zip(orig[tensor_data_start:], inv[tensor_data_start:]) if a != b)
    print(f"  Tensor data changed: {diff_count:,} bytes differ out of {n_bytes:,}")
    print(f"  Change ratio: {diff_count/n_bytes*100:.1f}%")
    
    print(f"\n{'=' * 60}")
    print("Now test with llama-cpp:")
    print(f"  Original: {GGUF_PATH}")
    print(f"  Inverted: {OUTPUT_PATH}")

if __name__ == "__main__":
    main()
