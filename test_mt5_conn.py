import sys
sys.path.insert(0, ".")
from core.mt5 import mt5_connect_test
ok, msg = mt5_connect_test()
print(msg if ok else f"FAILED: {msg}")