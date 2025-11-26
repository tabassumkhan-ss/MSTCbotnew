
import importlib
import sys

def find_app():
    try:
        mod = importlib.import_module("backend.app")
    except Exception as e:
        print("ERROR: failed to import backend.app:", e, file=sys.stderr)
        raise

    # 1) If module has attribute 'app' and it's a Flask instance
    if hasattr(mod, "app"):
        candidate = getattr(mod, "app")
        if hasattr(candidate, "url_map") and hasattr(candidate, "view_functions"):
            return candidate

    # 2) If backend package exports an 'app'
    try:
        pkg = importlib.import_module("backend")
        if hasattr(pkg, "app"):
            candidate = getattr(pkg, "app")
            if hasattr(candidate, "url_map") and hasattr(candidate, "view_functions"):
                return candidate
    except Exception:
        pass

    # 3) Duck-type scan of module globals for Flask-like object
    for name, val in vars(mod).items():
        if hasattr(val, "url_map") and hasattr(val, "view_functions") and hasattr(val, "run"):
            return val

    raise RuntimeError("Could not locate Flask app instance in backend.app")

if __name__ == "__main__":
    app = find_app()
    print("Routes (rule -> endpoint -> function.__name__):")
    rules = sorted(app.url_map.iter_rules(), key=lambda r: r.rule)
    for rule in rules:
        func = app.view_functions.get(rule.endpoint)
        fname = getattr(func, "__name__", repr(func))
        print(f"{rule.rule:30} -> endpoint={rule.endpoint:30} func={fname}")
