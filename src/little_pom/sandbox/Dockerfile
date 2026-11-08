# Build only by explicit operator action, after building the upstream environment stage.
# Never use the task root or tests/ as the upstream build context.
ARG AGENT_IMAGE
FROM ${AGENT_IMAGE}
ARG TASK_ID
ARG SNAPSHOT_HASH
ARG DOCKERFILE_SHA256
USER root
# Some upstream Lean images do not install Python; network is build-time only.
RUN if ! command -v python3 >/dev/null 2>&1; then \
        apt-get update && apt-get install -y --no-install-recommends python3 && \
        rm -rf /var/lib/apt/lists/*; \
    fi
# Relocate the complete Lean toolchain, including elan's relative executable links.
# chmod /root alone would unnecessarily expose the rest of that directory.
RUN mkdir -p /opt /work && \
    if [ "$TASK_ID" = finite-free-stam ]; then \
        test -d /root/.elan && mv /root/.elan /opt/elan && chmod -R a+rX /opt/elan && \
        mv /app /opt/little-inputs && chmod -R a+rX /opt/little-inputs && ln -s /work /app; \
    elif [ "$TASK_ID" = dna-storage-codec ]; then \
        rmdir /app && ln -s /work /app; \
    elif [ "$TASK_ID" = nanoindentation-property-extraction ]; then \
        test -d /workspace/data && rmdir /workspace/output && ln -s /work/out /workspace/output; \
    else exit 1; fi
LABEL little_pom.task_id="${TASK_ID}" \
      little_pom.snapshot_hash="${SNAPSHOT_HASH}" \
      little_pom.dockerfile_sha256="${DOCKERFILE_SHA256}" \
      little_pom.isolation_version="1"
ENV PATH="/opt/elan/bin:/usr/local/bin:/usr/bin:/bin" HOME="/work" ELAN_HOME="/opt/elan"
USER 65532:65532
WORKDIR /work
