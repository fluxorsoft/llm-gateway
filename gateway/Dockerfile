# The official LiteLLM image, by digest (scripts/images.sh verifies its signature first), without its Enterprise parts,
# plus the filter and the settings.
#
# LiteLLM is MIT, except its enterprise folder, whose licence does not allow production use or distribution without a
# BerriAI agreement. The gateway does not use it, so it is left out: the files are removed, and the result is copied into
# an empty image, so no earlier layer still carries them.
ARG LITELLM_IMAGE
FROM ${LITELLM_IMAGE} AS open
USER root
RUN rm -rf /app/enterprise /app/.venv/lib/python3.*/site-packages/litellm_enterprise* /opt/liteadmin /app/docker \
    && test -z "$(find / -xdev -iname '*litellm_enterprise*' -not -path '/proc/*' 2>/dev/null)"
COPY config.yaml guard.py usage.py /etc/litellm/

FROM scratch
COPY --from=open / /
ENV PATH=/app/.venv/bin:/usr/local/sbin:/usr/local/bin:/usr/bin:/usr/sbin:/sbin:/bin \
    SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt \
    PRISMA_BINARY_CACHE_DIR=/opt/prisma/binaries \
    PRISMA_CLI_PATH=/opt/prisma/binaries/node_modules/.bin/prisma \
    PRISMA_CLI_QUERY_ENGINE_TYPE=binary \
    PRISMA_OFFLINE_MODE=true \
    LITELLM_LOCAL_MODEL_COST_MAP=True \
    DISABLE_SCHEMA_UPDATE=true \
    PYTHONDONTWRITEBYTECODE=1 \
    HOME=/tmp
LABEL org.opencontainers.image.title="LLM gateway (LiteLLM, open source parts)" \
      org.opencontainers.image.source="https://github.com/fluxorsoft/llm-gateway" \
      org.opencontainers.image.licenses="Apache-2.0 AND MIT"
WORKDIR /app
USER 65532:65532
EXPOSE 8080
ENTRYPOINT ["/app/.venv/bin/python", "/etc/litellm/guard.py"]
CMD ["--config", "/etc/litellm/config.yaml", "--port", "8080", "--num_workers", "1"]
