import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import db
db.reset()
print("Demo state reset (runtime.json recreated from seed).")
