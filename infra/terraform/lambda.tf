locals {
  common_env = {
    APP_ENV                = var.environment
    LOG_LEVEL              = "INFO"
    LLM_PROVIDER           = "bedrock"
    LLM_MODEL_ID           = var.llm_model_id
    UTILITY_MODEL_ID       = var.utility_model_id
    EMBEDDING_MODEL_ID     = var.embedding_model_id
    EMBEDDING_DIMENSIONS   = tostring(var.embedding_dimensions)
    VECTOR_STORE           = "pinecone"
    PINECONE_INDEX         = var.pinecone_index
    PINECONE_NAMESPACE     = var.pinecone_namespace
    ENABLE_HYBRID_SEARCH   = tostring(var.enable_hybrid_search)
    PINECONE_SPARSE_INDEX  = var.pinecone_sparse_index
    STORAGE_BACKEND        = "dynamodb"
    DYNAMODB_TABLE         = aws_dynamodb_table.app.name
    DOCUMENTS_BUCKET       = aws_s3_bucket.docs.bucket
    SSM_PARAMETER_PREFIX   = local.ssm_prefix
    AUTH_MODE              = "cognito"
    MIN_RELEVANCE_SCORE    = tostring(var.min_relevance_score)
    ENABLE_GROUNDING_CHECK = tostring(var.enable_grounding_check)
    CORS_ORIGINS           = jsonencode(var.cors_origins)
    LANGSMITH_TRACING      = tostring(var.langsmith_tracing)
    LANGSMITH_PROJECT      = "${var.langsmith_project}-${var.environment}"
  }
}

resource "aws_cloudwatch_log_group" "api" {
  name              = "/aws/lambda/${local.name}-api"
  retention_in_days = var.log_retention_days
}

resource "aws_cloudwatch_log_group" "ingest" {
  name              = "/aws/lambda/${local.name}-ingest"
  retention_in_days = var.log_retention_days
}

resource "aws_lambda_function" "api" {
  function_name = "${local.name}-api"
  role          = aws_iam_role.api.arn
  package_type  = "Image"
  image_uri     = local.image_uri
  architectures = [var.lambda_architecture]
  memory_size   = 1024
  timeout       = 29 # API Gateway HTTP APIs time out at 30s

  reserved_concurrent_executions = var.api_reserved_concurrency

  image_config {
    command = ["app.lambda_api.handler"]
  }

  environment {
    variables = local.common_env
  }

  tracing_config {
    mode = "Active"
  }

  depends_on = [aws_cloudwatch_log_group.api, aws_iam_role_policy_attachment.basic]
}

resource "aws_lambda_function" "ingest" {
  function_name = "${local.name}-ingest"
  role          = aws_iam_role.ingest.arn
  package_type  = "Image"
  image_uri     = local.image_uri
  architectures = [var.lambda_architecture]
  memory_size   = 2048 # PDF parsing is CPU-bound; more memory = more CPU
  timeout       = 300

  image_config {
    command = ["app.ingestion.handler.handler"]
  }

  environment {
    variables = local.common_env
  }

  tracing_config {
    mode = "Active"
  }

  depends_on = [aws_cloudwatch_log_group.ingest, aws_iam_role_policy_attachment.basic]
}

resource "aws_lambda_event_source_mapping" "ingest" {
  event_source_arn                   = aws_sqs_queue.ingest.arn
  function_name                      = aws_lambda_function.ingest.arn
  batch_size                         = 5
  maximum_batching_window_in_seconds = 10
  function_response_types            = ["ReportBatchItemFailures"]

  # Caps parallel ingestion so a bulk upload can't trip Bedrock/Pinecone rate limits.
  scaling_config {
    maximum_concurrency = 2
  }
}
