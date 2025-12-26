
import sys
import os
sys.path.append(os.getcwd())
try:
    print("Attempting to import backend.api.sc_api.sermon_manager...")
    from backend.api.sc_api import sermon_manager
    print("sermon_manager imported.")
    
    print("Attempting to import backend.api.sc_api.router...")
    from backend.api.sc_api import router
    print("router imported.")
    
    print("Backend modules imported successfully.")
except Exception as e:
    print(f"Error importing backend modules: {e}")
    import traceback
    traceback.print_exc()
