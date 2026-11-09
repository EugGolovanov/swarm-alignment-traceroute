# Explicit SCI build overlay. The upstream build context is environment/ only;
# neither a task root nor benchmark tests/solution/rubric/golden is copied here.
ARG AGENT_IMAGE
FROM ${AGENT_IMAGE}
ARG TASK_ID
ARG SNAPSHOT_HASH
ARG DOCKERFILE_SHA256
ARG OVERLAY_SHA256
USER root
# Public physics instructions require these CLIs, absent from the upstream base.
RUN if [ "$TASK_ID" = physics-simulation-tool-orchestration-01 ]; then \
        apt-get update && apt-get install -y --no-install-recommends sqlite3 hdf5-tools && \
        rm -rf /var/lib/apt/lists/*; \
    fi
# Preserve public data in the read-only image; bootstrap creates checked aliases
# and a writable copy only of the bioinformatics repair project in bounded tmpfs.
RUN test -d /home/agent/data && \
    test -z "$(find /home/agent -mindepth 1 -maxdepth 1 ! -name data ! -name results -print -quit)" && \
    mkdir -p /opt /work && mv /home/agent/data /opt/little-sci-inputs && \
    chmod -R a+rX /opt/little-sci-inputs && \
    rmdir /home/agent/results /home/agent && ln -s /work /home/agent
LABEL little_pom.task_id="${TASK_ID}" \
      little_pom.snapshot_hash="${SNAPSHOT_HASH}" \
      little_pom.dockerfile_sha256="${DOCKERFILE_SHA256}" \
      little_pom.overlay_sha256="${OVERLAY_SHA256}" \
      little_pom.isolation_version="sci-1"
ENV PATH="/home/agent/.local/bin:/usr/local/bin:/usr/bin:/bin" HOME="/home/agent"
USER 65532:65532
WORKDIR /home/agent
