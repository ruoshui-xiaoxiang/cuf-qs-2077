"""Fix shallow copy bug in pet_memory.py."""

PATH = r"e:\QODERCN\ai_agent_monitor\src\common\pet_memory.py"
with open(PATH, "r", encoding="utf-8") as f:
    code = f.read()

# 1. Add copy import
OLD_IMPORT = "import json\nimport logging\nimport os\nimport time"
NEW_IMPORT = "import copy\nimport json\nimport logging\nimport os\nimport time"
code = code.replace(OLD_IMPORT, NEW_IMPORT, 1)

# 2. Fix _load method: use deepcopy instead of dict() shallow copy
OLD_LOAD_OK = """            merged = dict(_DEFAULT_MEMORY)
                merged.update(data)"""
NEW_LOAD_OK = """            merged = copy.deepcopy(_DEFAULT_MEMORY)
                merged.update(data)"""
code = code.replace(OLD_LOAD_OK, NEW_LOAD_OK, 1)

OLD_LOAD_NEW = """            data = dict(_DEFAULT_MEMORY)
            data["created_at"] = time.time()
            return data
        except Exception as e:
            log.warning("Failed to load pet memory: %s", e)
            data = dict(_DEFAULT_MEMORY)"""
NEW_LOAD_NEW = """            data = copy.deepcopy(_DEFAULT_MEMORY)
            data["created_at"] = time.time()
            return data
        except Exception as e:
            log.warning("Failed to load pet memory: %s", e)
            data = copy.deepcopy(_DEFAULT_MEMORY)"""
code = code.replace(OLD_LOAD_NEW, NEW_LOAD_NEW, 1)

with open(PATH, "w", encoding="utf-8") as f:
    f.write(code)

# Verify
assert "import copy" in code
assert "copy.deepcopy(_DEFAULT_MEMORY)" in code
assert "dict(_DEFAULT_MEMORY)" not in code
print("pet_memory.py fixed: shallow copy → deepcopy")
