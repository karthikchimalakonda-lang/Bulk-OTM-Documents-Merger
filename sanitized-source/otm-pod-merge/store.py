"""Thin Object Storage wrapper: resource principal inside Functions, ~/.oci/config locally."""
import json
import logging
import os

import oci

log = logging.getLogger("otm.store")


class PreconditionFailed(RuntimeError):
    pass


class Store:
    def __init__(self, bucket: str, prefix: str, namespace: str = "", profile: str = "DEFAULT"):
        # Retries throttling, 5xx and connection errors with backoff (conditional writes stay safe: a 412 is
        # never retried).
        retry = oci.retry.DEFAULT_RETRY_STRATEGY
        if os.getenv("OCI_RESOURCE_PRINCIPAL_VERSION"):
            signer = oci.auth.signers.get_resource_principals_signer()
            self.client = oci.object_storage.ObjectStorageClient(config={}, signer=signer, retry_strategy=retry)
        else:
            self.client = oci.object_storage.ObjectStorageClient(oci.config.from_file(profile_name=profile),
                                                                 retry_strategy=retry)
        self.ns = namespace or self.client.get_namespace().data
        self.bucket = bucket
        self.prefix = prefix

    def key(self, path: str) -> str:
        return f"{self.prefix}/{path.lstrip('/')}"

    def get(self, path: str) -> tuple[bytes, str] | None:
        try:
            resp = self.client.get_object(self.ns, self.bucket, self.key(path))
        except oci.exceptions.ServiceError as exc:
            if exc.status == 404:
                return None
            raise
        return resp.data.content, resp.headers.get("etag")

    def put(self, path: str, data: bytes, content_type: str = "application/octet-stream",
            if_match: str | None = None, if_none_match: str | None = None) -> str:
        kwargs = {"content_type": content_type}
        if if_match:
            kwargs["if_match"] = if_match
        if if_none_match:
            kwargs["if_none_match"] = if_none_match
        try:
            resp = self.client.put_object(self.ns, self.bucket, self.key(path), data, **kwargs)
        except oci.exceptions.ServiceError as exc:
            if exc.status in (409, 412):
                raise PreconditionFailed(f"{path}: {exc.message}") from exc
            raise
        return resp.headers.get("etag")

    def delete(self, path: str) -> None:
        try:
            self.client.delete_object(self.ns, self.bucket, self.key(path))
        except oci.exceptions.ServiceError as exc:
            if exc.status != 404:
                raise

    def list(self, path_prefix: str) -> list[str]:
        """Object names (relative to the prefix) under path_prefix."""
        names, start = [], None
        while True:
            resp = self.client.list_objects(self.ns, self.bucket, prefix=self.key(path_prefix), start=start,
                                            fields="name")
            names += [o.name[len(self.prefix) + 1:] for o in resp.data.objects]
            start = resp.data.next_start_with
            if not start:
                return names

    def get_json(self, path: str) -> tuple[dict, str] | tuple[None, None]:
        got = self.get(path)
        if got is None:
            return None, None
        return json.loads(got[0]), got[1]

    def put_json(self, path: str, obj: dict, **kw) -> str:
        return self.put(path, json.dumps(obj, indent=1, default=str).encode(), "application/json", **kw)
