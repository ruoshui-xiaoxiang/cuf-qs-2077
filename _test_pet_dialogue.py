"""Quick test for PetDialogue module."""
import sys
sys.path.insert(0, r"e:\QODERCN\ai_agent_monitor")

from src.ui.pet_dialogue import PetDialogue

pd = PetDialogue()
print("=== Human mode ===")
events = ["startup", "trust_drop", "repetition", "low_diversity",
          "circuit_break", "idle", "trust_recover", "healthy_streak", "rate_limit"]
for e in events:
    line = pd.on_event(e, mode="human", trust=0.6)
    print(f"  {e:16s} -> {line}")

print()
print("=== Cyber mode ===")
pd2 = PetDialogue()
for e in events:
    line = pd2.on_event(e, mode="cyber", trust=0.6)
    print(f"  {e:16s} -> {line}")

print()
print("=== Cooldown test (trust_drop x3 rapid) ===")
pd3 = PetDialogue()
for i in range(3):
    line = pd3.on_event("trust_drop", mode="human", trust=0.5)
    print(f"  call {i+1}: {line}")

print()
print("=== Context: trust escalation ===")
pd4 = PetDialogue()
print(f"  trust=0.9 -> {pd4.on_event('trust_drop', mode='human', trust=0.9)}")
print(f"  trust=0.3 -> {pd4.on_event('repetition', mode='human', trust=0.3)}")
print(f"  trust=0.1 -> {pd4.on_event('circuit_break', mode='human', trust=0.1)}")

print()
print("Stats:", pd.stats)
print("\nAll OK")
