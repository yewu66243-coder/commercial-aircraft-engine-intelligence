from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

if __name__ == '__main__':
    from dotenv import load_dotenv
    load_dotenv(ROOT / '.env')
    from gpt_researcher.document.upload_indexing import run_worker
    try:
        run_worker()
    except RuntimeError as exc:
        if 'index build is already running' not in str(exc):
            raise
