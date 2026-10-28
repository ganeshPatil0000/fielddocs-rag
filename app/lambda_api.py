"""Lambda entrypoint for the HTTP API (API Gateway HTTP API -> Mangum -> FastAPI)."""

from app.ssm import load_ssm_secrets

load_ssm_secrets()  # must run before settings are read

from mangum import Mangum  # noqa: E402

from app.api.main import app  # noqa: E402

# lifespan="off": Lambda has no shutdown hook and we don't need startup tasks.
handler = Mangum(app, lifespan="off")
