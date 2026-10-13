"""
otm-pod-merge (OCI Functions entry point).

Invoked on a schedule or manually; body is optional JSON:
  {}                                   -> incremental run from the stored watermark
  {"max_docs": 50}                     -> cap documents for this run
  {"start_from": "2026-10-07T00:00:00Z"} -> only used when no state exists yet (first run)
  {"drive_only": true}                 -> just retry pending Google Drive uploads
  {"time_budget_s": 900}               -> override the run's time budget
  {"chain_id": "...", "batch": 2}      -> set by the function itself when it continues a backlog

Invoke detached (--fn-invoke-type detached) for scheduled runs: the caller doesn't wait, and the run
gets DETACHED_TIME_BUDGET_SECONDS (default 1500s, function detached timeout is 1800s) instead of 230s.
When a batch stops at its cap with more waiting, it invokes this function again (detached) to continue.
"""
import io
import json
import logging
import os

import oci
from fdk import response

from pipeline import Pipeline

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
for noisy in ("urllib3", "oci", "PIL"):
    logging.getLogger(noisy).setLevel(logging.WARNING)
logging.getLogger("otm").setLevel(logging.INFO)
log = logging.getLogger("otm.fn")


def _reply(ctx, body: dict, status: int = 200):
    return response.Response(ctx, response_data=json.dumps(body, default=str),
                             headers={"Content-Type": "application/json"}, status_code=status)


def _is_detached(ctx) -> bool:
    try:
        headers = {k.lower(): v for k, v in (ctx.Headers() or {}).items()}
    except Exception:  # noqa: BLE001 - older fdk without Headers()
        return False
    value = headers.get("fn-invoke-type") or headers.get("oci-fn-invoke-type") or ""
    if isinstance(value, (list, tuple)):
        value = value[0] if value else ""
    return str(value).lower() == "detached"


def _function_id(ctx) -> str | None:
    try:
        return ctx.FnID() or os.getenv("FN_FN_ID")
    except Exception:  # noqa: BLE001
        return os.getenv("FN_FN_ID")


def _self_invoker(fn_id: str | None):
    """Returns continue_fn(body) that invokes this function again, detached."""
    def invoke(body: dict) -> None:
        if not fn_id:
            raise RuntimeError("function OCID unknown (FN_FN_ID not set)")
        signer = oci.auth.signers.get_resource_principals_signer()
        endpoint = os.getenv("FN_INVOKE_ENDPOINT")
        if not endpoint:
            mgmt = oci.functions.FunctionsManagementClient(config={}, signer=signer)
            endpoint = mgmt.get_function(fn_id).data.invoke_endpoint
        client = oci.functions.FunctionsInvokeClient(config={}, signer=signer, service_endpoint=endpoint,
                                                     retry_strategy=oci.retry.DEFAULT_RETRY_STRATEGY)
        client.invoke_function(fn_id, invoke_function_body=json.dumps(body), fn_invoke_type="detached")
        log.info("Started next batch %s", body)
    return invoke


def handler(ctx, data: io.BytesIO = None):
    try:
        raw = data.getvalue() if data else b""
        body = json.loads(raw) if raw.strip() else {}
    except (ValueError, AttributeError):
        return _reply(ctx, {"status": "error", "reason": "Body must be JSON"}, 400)

    try:
        result = Pipeline().run(body, detached=_is_detached(ctx), continue_fn=_self_invoker(_function_id(ctx)))
        log.info("Result: %s", json.dumps({k: v for k, v in result.items() if k not in ("skipped", "merged_files")},
                                          default=str)[:4000])
        return _reply(ctx, result)
    except Exception as exc:
        log.exception("POD merge failed")
        return _reply(ctx, {"status": "error", "reason": f"{type(exc).__name__}: {exc}"}, 500)
