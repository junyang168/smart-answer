import sys
import os
import asyncio
from pathlib import Path
from unittest.mock import MagicMock

# Add project root to sys.path
project_root = Path(__file__).resolve().parent.parent
sys.path.append(str(project_root))

# Mock git module to avoid ImportError in backend.api.service
sys.modules["git"] = MagicMock()
# Mock backend.api.router to avoid its dependencies if needed
sys.modules["backend.api.router"] = MagicMock()

# Now import the necessary modules
# We need to bypass backend.api.__init__ if possible, or ensure it doesn't fail
# But backend.api.__init__ likely imports router.
# Let's verify if mocking git is enough.

from backend.api.multi_agent.types import AgentState
from backend.api.multi_agent.agents import run_homiletician_beat

def test_drafter():
    project_id = "義的精意"
    # Locate the state file
    state_file = Path(f"/opt/homebrew/var/www/church/web/data/notes_to_surmon/{project_id}/agent_state.json")
    
    if not state_file.exists():
        print(f"Error: State file not found at {state_file}")
        return

    print(f"Loading state from {state_file}...")
    with open(state_file, "r", encoding="utf-8") as f:
        state_data = f.read()
        
    state = AgentState.parse_raw(state_data)
    
    if not state.beats:
        print("Error: No beats found in state.")
        return

    print("\n=== DEBUG: Exegetical Notes (Before Patch) ===")
    # print(state.exegetical_notes[:200] + "...") # Optional: print preview
    
    # PATCH: Simulate the new Exegete prompt by replacing English transliteration with Greek
    # This proves that IF the Exegete works (which we updated), the Drafter will handle it correctly.
    state.exegetical_notes = state.exegetical_notes.replace("Epithumia", "ἐπιθυμέω")
    state.exegetical_notes = state.exegetical_notes.replace("Raca", "Ῥακά")
    state.exegetical_notes = state.exegetical_notes.replace("More", "Μωρέ")
    
    print("\n=== DEBUG: Exegetical Notes (Patched with Greek) ===")
    print("Patched 'Epithumia' -> 'ἐπιθυμέω'")
    print("=== END DEBUG ===\n")
    
    first_beat = state.beats[0]
    print("\n=== Testing Drafter on First Beat ===")
    print(f"Beat Content Preview: {first_beat[:100]}...")
    
    print("\n--- Generating Draft... ---")
    try:
        draft = run_homiletician_beat(state, first_beat, previous_text="")
        print("\n=== GENERATED DRAFT START ===")
        print(draft)
        print("=== GENERATED DRAFT END ===")
    except Exception as e:
        print(f"Error calling Drafter: {e}")

if __name__ == "__main__":
    test_drafter()
