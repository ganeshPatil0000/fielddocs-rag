# One image, two Lambda functions (API + ingestion worker); Terraform picks the
# handler with image_config.command.
#
# Build for Lambda (--provenance=false: Lambda rejects multi-manifest image indexes):
#   docker build --platform linux/amd64 --provenance=false -t fielddocs-rag .
FROM public.ecr.aws/lambda/python:3.12

# Dependencies first, so a code change doesn't invalidate the pip layer.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ${LAMBDA_TASK_ROOT}/app/

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

CMD ["app.lambda_api.handler"]
