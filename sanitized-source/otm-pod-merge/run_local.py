"""Run the pipeline on this machine (uses ~/.oci/config and the project .env).

  python run_local.py                       # incremental run; continues batch after batch like the Function
  python run_local.py '{"max_docs": 5}'     # same JSON body the Function accepts

Prints the chain summary tables at the end (also saved under reports/runs/<chain_id>/summary.md).
"""
import json
import logging
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")
sys.stdout.reconfigure(encoding="utf-8")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
logging.getLogger("otm.reports").setLevel(logging.WARNING)  # tables are printed below instead

from pipeline import Pipeline  # noqa: E402
from reports import to_markdown  # noqa: E402

body = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
queue = [body]
result = {}
while queue:
    options = queue.pop(0)
    pipeline = Pipeline()
    result = pipeline.run(options, continue_fn=queue.append)

chain_id = result.get("chain_id")
summary, _ = pipeline.store.get_json(f"reports/runs/{chain_id}/summary.json") if chain_id else (None, None)
print(to_markdown(summary) if summary else json.dumps(result, indent=1, default=str))
