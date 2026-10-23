"""Fix escaped quotes in pet_dialogue.py."""
PATH = r"e:\QODERCN\ai_agent_monitor\src\ui\pet_dialogue.py"
with open(PATH, "r", encoding="utf-8") as f:
    code = f.read()

# Fix escaped triple quotes
code = code.replace('\\"\\"\\"', '"""')

with open(PATH, "w", encoding="utf-8") as f:
    f.write(code)
print("Fixed escaped quotes in pet_dialogue.py")
