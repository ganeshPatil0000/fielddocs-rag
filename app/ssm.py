"""Load secrets from SSM Parameter Store into environment variables at cold start.

Parameters live under ``SSM_PARAMETER_PREFIX`` (e.g. ``/fielddocs/dev/``) as
SecureStrings named after the env var they populate::

    /fielddocs/dev/PINECONE_API_KEY
    /fielddocs/dev/LANGSMITH_API_KEY

Why SSM and not Secrets Manager: standard SecureString parameters are free and
these keys do not need automatic rotation. Values are never baked into the image
or written to Terraform state.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

_loaded = False


def load_ssm_secrets() -> None:
    global _loaded
    prefix = os.environ.get("SSM_PARAMETER_PREFIX")
    if _loaded or not prefix:
        return

    import boto3

    client = boto3.client("ssm")
    paginator = client.get_paginator("get_parameters_by_path")
    count = 0
    for page in paginator.paginate(Path=prefix, WithDecryption=True, Recursive=False):
        for param in page["Parameters"]:
            name = param["Name"].rsplit("/", 1)[-1].upper()
            value = param["Value"]
            # Placeholder values created by Terraform are ignored so a missing
            # secret fails loudly at use-time instead of sending "CHANGE_ME".
            if value and value != "CHANGE_ME" and name not in os.environ:
                os.environ[name] = value
                count += 1
    _loaded = True
    logger.info("loaded secrets from ssm", extra={"count": count, "prefix": prefix})
