# Applies gateways/litellm/config from inside the VPC. The providers are in the image, so it needs no internet.
FROM hashicorp/terraform:1.16.5 AS terraform
FROM python:3.13-alpine
COPY --from=terraform /bin/terraform /bin/terraform
COPY core/settings /repo/core/settings
COPY gateways/litellm/config /repo/gateways/litellm/config
ARG SETTINGS_DIR=settings/example
COPY ${SETTINGS_DIR} /repo/settings/current
COPY gateways/litellm/runner/run.sh /repo/runner/
COPY src/onedoorai /opt/onedoorai/onedoorai
WORKDIR /repo/gateways/litellm/config
RUN terraform init -backend=false -input=false && terraform providers mirror /providers && rm -rf .terraform \
    && chmod 755 /repo/runner/run.sh && adduser -D -u 65532 runner && chown -R runner /repo
USER 65532
ENV PYTHONPATH=/opt/onedoorai CHECKPOINT_DISABLE=1 TF_IN_AUTOMATION=1 TF_VAR_settings_dir=/repo/settings/current
ENTRYPOINT ["/repo/runner/run.sh"]
