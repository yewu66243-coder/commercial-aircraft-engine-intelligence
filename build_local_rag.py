"""Run with the bundled Python: python.exe build_local_rag.py --build."""
import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))


def main():
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    from gpt_researcher.document.library_rag import build_library_index, inventory
    from gpt_researcher.document.local_rag import get_local_rag_dir, _atomic_text

    parser = argparse.ArgumentParser(description="Full-library body vector index builder")
    actions = parser.add_mutually_exclusive_group(required=True)
    actions.add_argument("--build", action="store_true")
    actions.add_argument("--background", action="store_true")
    actions.add_argument("--inventory", action="store_true")
    actions.add_argument("--status", action="store_true")
    actions.add_argument("--stop", action="store_true")
    actions.add_argument("--pending", action="store_true")
    parser.add_argument("--limit", type=int, help="Build only the first N files for a smoke test")
    parser.add_argument("--repair", action="store_true", help="Retry non-indexed files with local OCR and Word extraction")
    args = parser.parse_args()
    directory = get_local_rag_dir()
    if args.background:
        import subprocess
        from gpt_researcher.document.library_rag import build_lock
        with build_lock(directory):
            pass
        with (directory / "library-build.stdout.log").open("ab") as stdout, \
             (directory / "library-build.stderr.log").open("ab") as stderr:
            process = subprocess.Popen(
                [sys.executable, "-u", str(Path(__file__).resolve()), "--build"]
                + (["--repair"] if args.repair else [])
                + (["--limit", str(args.limit)] if args.limit is not None else []),
                cwd=ROOT, stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr,
                close_fds=True,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                start_new_session=os.name != "nt",
            )
        result = {"status": "started", "pid": process.pid}
    elif args.pending:
        import csv
        import sqlite3
        from gpt_researcher.document.library_rag import database_path
        with sqlite3.connect(database_path().as_uri() + "?mode=ro", uri=True) as database:
            rows = database.execute("SELECT scope,status,error,path FROM documents WHERE status != 'indexed' ORDER BY scope,path").fetchall()
        target = directory / "pending-documents.csv"
        with target.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["scope", "status", "reason", "source_path"])
            writer.writerows(rows)
        result = {"pending_files": len(rows), "list_path": str(target)}
    elif args.inventory:
        from collections import Counter
        sources = inventory()
        result = {"total": len(sources), "by_scope": dict(Counter(s["scope"] for s in sources)),
                  "by_extension": dict(Counter(Path(s["source_path"]).suffix.lower() for s in sources))}
    elif args.status:
        path = directory / "library-build-status.json"
        result = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"status": "not_started"}
    elif args.stop:
        _atomic_text(directory / "library-build.stop", "stop\n")
        result = {"status": "stop_requested"}
    else:
        result = build_library_index(
            repair=args.repair,
            limit=args.limit,
            chunk_size=int(os.getenv("LOCAL_RAG_CHUNK_SIZE", "1000")),
            overlap=int(os.getenv("LOCAL_RAG_CHUNK_OVERLAP", "120")),
            batch_size=int(os.getenv("LOCAL_RAG_BATCH_SIZE", "16")),
        )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.build and result["status"] == "interrupted":
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
