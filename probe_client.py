import socket, json, sys
def query(sock, cmd):
    sock.sendall((cmd + "\n").encode())
    buf = b""
    while b"\n" not in buf:
        chunk = sock.recv(262144)
        if not chunk: break
        buf += chunk
    text = buf.split(b"\n")[0].decode("utf-8", errors="replace")
    return json.loads(text)
s = socket.create_connection(("127.0.0.1", 20771), timeout=10)
print("Connected!")
for c in sys.argv[1:]:
    print(f"\n>>> {c}")
    print(json.dumps(query(s, c), indent=2, ensure_ascii=False))
if len(sys.argv) <= 1:
    print("Interactive. Commands: ping, engine_info, list_types, inspect_class, find_func, find_global, get_systems, get_player, list_enums, shutdown")
    while True:
        try: line = input(">>> ").strip()
        except: break
        if not line or line == "quit": break
        try: print(json.dumps(query(s, line), indent=2, ensure_ascii=False))
        except Exception as e: print(f"Error: {e}")
s.close()
