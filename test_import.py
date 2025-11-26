import traceback, importlib

try:
    m = importlib.import_module('backend.app')
    print("import OK; app object:", getattr(m,'app',None))
except Exception:
    traceback.print_exc()
