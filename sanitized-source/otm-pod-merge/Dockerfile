# Build context is this folder:
#   docker build -t <region-key>.ocir.io/<namespace>/otm-pod-merge/otm-pod-merge:<TAG> .
# Base images come from Oracle Container Registry (not Docker Hub).
FROM container-registry.oracle.com/oci_functions/python:3.12-dev AS build-stage
WORKDIR /function
COPY requirements.txt /function/
RUN pip3 install --target /python/ --no-cache --no-cache-dir -r requirements.txt \
    && rm -rf ~/.cache/pip /function/requirements.txt \
    && chmod -R o+r /python
COPY *.py /function/

FROM container-registry.oracle.com/oci_functions/python:3.12
WORKDIR /function
COPY --from=build-stage /python /python
COPY --from=build-stage /function /function
RUN chmod -R o+r /function
ENV PYTHONPATH=/function:/python
ENTRYPOINT ["/python/bin/fdk", "/function/func.py", "handler"]
