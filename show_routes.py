# show_routes.py — robustly find the Flask app instance and list routes
import importlib
import sys

def find_app():
    """
    Try a few sensible places for the Flask app instance:
      - backend.app.app
      - backend.app
      - backend.app:app (if package exports it)
    """
    try:
        mod = importlib.import_module("backend.app")
    except Exception as e:
        print("ERROR: failed to import backend.app:", e, file=sys.stderr)
        raise

    # try attribute 'app' on the module (common)
    if hasattr(mod, "app"):
        candidate = getattr(mod, "app")
        # if someone named their module 'app' but it's the Flask instance,
        # check if it has url_map
        if hasattr(candidate, "url_map"):
            return candidate

    # if backend package exports an 'app' symbol (rare), try importing backend and checking
    try:
        pkg = importlib.import_module("backend")
        if hasattr(pkg, "app"):
            candidate = getattr(pkg, "app")
            if hasattr(candidate, "url_map"):
                return candidate
    except Exception:
        pass

    # fallback: scan module globals for a Flask-like object (duck typing)
    for name, val in vars(mod).items():
        if hasattr(val, "url_map") and hasattr(val, "run"):
            return val

    raise RuntimeError("Could not locate Flask app instance in backend.app")

if __name__ == "__main__":
    try:
        app = find_app()
    except Exception as e:
        print("FATAL:", e, file=sys.stderr)
        sys.exit(2)

    print("Registered routes:")
    rules = sorted(app.url_map.iter_rules(), key=lambda r: r.rule)
    for rule in rules:
        methods = ",".join(sorted(rule.methods))
        print(f"{rule.rule:30} -> {methods}")
