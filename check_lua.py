"""check_lua.py —— 用 lupa 的 Lua 运行时做语法检查（不执行）

为什么不执行：桥里引用了 UE4SS 的全局（FindFirstOf / LoopAsync），
在纯 Lua 里跑会直接报 nil；我们只要"能不能编译过"这一个答案。
```
py -3.12 check_lua.py <file.lua>
```
"""
import sys
import lupa

def main(path):
    src = open(path, encoding="utf-8").read()
    rt = lupa.LuaRuntime()
    try:
        try:
            rt.compile(src, name=path)
        except TypeError:
            rt.compile(src)
    except Exception as e:
        print("SYNTAX ERROR:", e)
        return 1
    print("OK 编译通过（%s，%d 行，Lua %s）"
          % (path.split("/")[-1], src.count("\n") + 1, rt.lua_implementation))
    return 0

if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
