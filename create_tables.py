import importlib
import sys

def main():
    try:
        mod = importlib.import_module("backend.models")
    except Exception as e:
        print("ERROR: could not import backend.models:", e, file=sys.stderr)
        sys.exit(2)

    # 1) prefer an exposed Base + engine
    Base = getattr(mod, "Base", None)
    engine = getattr(mod, "engine", None)

    # 2) fallback: detect SessionLocal (sessionmaker) and try to get its bind/engine
    SessionLocal = getattr(mod, "SessionLocal", None)
    if engine is None and SessionLocal is not None:
        engine = getattr(SessionLocal, "bind", None)

    # 3) if we still don't have engine, try to infer from common names
    if engine is None:
        for name in ("ENGINE", "engine_", "sql_engine"):
            if hasattr(mod, name):
                engine = getattr(mod, name)
                break

    # 4) if we have Base + engine, create_all
    if Base is not None and engine is not None:
        print("Using Base and engine from backend.models. Creating tables...")
        Base.metadata.create_all(bind=engine)
        print("Done.")
        return

    # 5) fallback: create tables from any classes that have __table__
    tables = []
    for val in vars(mod).values():
        if hasattr(val, "__table__"):
            tables.append(val.__table__)
    if tables and engine is not None:
        print("Using discovered model tables and engine. Creating tables...")
        metadata = tables[0].metadata
        metadata.create_all(bind=engine)
        print("Done.")
        return

    # 6) last resort: user must edit this script to point at engine
    print("ERROR: could not locate Base/engine or model tables automatically.", file=sys.stderr)
    print("Open backend/models.py and confirm it exposes either 'Base' and 'engine',", file=sys.stderr)
    print("or adjust this script to construct an engine with the correct DB URL.", file=sys.stderr)
    sys.exit(2)

if __name__ == "__main__":
    main()
