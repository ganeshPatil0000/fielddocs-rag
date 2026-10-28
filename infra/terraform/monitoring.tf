resource "aws_sns_topic" "alarms" {
  name = "${local.name}-alarms"
}

resource "aws_sns_topic_subscription" "email" {
  count     = local.alarm_email == "" ? 0 : 1
  topic_arn = aws_sns_topic.alarms.arn
  protocol  = "email"
  endpoint  = local.alarm_email
}

locals {
  app_dims = { Service = "api", Environment = var.environment }

  alarms = {
    api-errors = {
      description = "API Lambda threw errors"
      namespace   = "AWS/Lambda"
      metric      = "Errors"
      dimensions  = tomap({ FunctionName = aws_lambda_function.api.function_name })
      statistic   = "Sum"
      period      = 300
      threshold   = 1
    }
    ingest-errors = {
      description = "Ingestion Lambda threw errors"
      namespace   = "AWS/Lambda"
      metric      = "Errors"
      dimensions  = tomap({ FunctionName = aws_lambda_function.ingest.function_name })
      statistic   = "Sum"
      period      = 300
      threshold   = 1
    }
    dlq-not-empty = {
      description = "Documents failed ingestion 3 times and are parked in the DLQ"
      namespace   = "AWS/SQS"
      metric      = "ApproximateNumberOfMessagesVisible"
      dimensions  = tomap({ QueueName = aws_sqs_queue.ingest_dlq.name })
      statistic   = "Maximum"
      period      = 300
      threshold   = 1
    }
    api-5xx = {
      description = "API Gateway returned 5xx responses"
      namespace   = "AWS/ApiGateway"
      metric      = "5xx"
      dimensions  = tomap({ ApiId = aws_apigatewayv2_api.http.id, Stage = "$default" })
      statistic   = "Sum"
      period      = 300
      threshold   = 5
    }
    keyword-search-errors = {
      description = "Keyword search is failing; answers fall back to semantic-only retrieval"
      namespace   = "FieldDocsRAG"
      metric      = "KeywordSearchErrors"
      dimensions  = tomap(local.app_dims)
      statistic   = "Sum"
      period      = 300
      threshold   = 5
    }
    llm-spend = {
      description = "Estimated Bedrock spend in the last hour is above budget"
      namespace   = "FieldDocsRAG"
      metric      = "EstimatedCostUSD"
      dimensions  = tomap(local.app_dims)
      statistic   = "Sum"
      period      = 3600
      threshold   = var.hourly_llm_cost_alarm_usd
    }
  }
}

resource "aws_cloudwatch_metric_alarm" "this" {
  for_each = local.alarms

  alarm_name          = "${local.name}-${each.key}"
  alarm_description   = each.value.description
  namespace           = each.value.namespace
  metric_name         = each.value.metric
  dimensions          = each.value.dimensions
  statistic           = each.value.statistic
  period              = each.value.period
  threshold           = each.value.threshold
  evaluation_periods  = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alarms.arn]
  ok_actions          = [aws_sns_topic.alarms.arn]
}

resource "aws_cloudwatch_metric_alarm" "api_latency_p95" {
  alarm_name          = "${local.name}-api-latency-p95"
  alarm_description   = "p95 API Lambda duration above 15s"
  namespace           = "AWS/Lambda"
  metric_name         = "Duration"
  dimensions          = { FunctionName = aws_lambda_function.api.function_name }
  extended_statistic  = "p95"
  period              = 300
  evaluation_periods  = 3
  threshold           = 15000
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alarms.arn]
}

resource "aws_cloudwatch_dashboard" "main" {
  dashboard_name = local.name
  dashboard_body = jsonencode({
    widgets = [
      {
        type = "metric", x = 0, y = 0, width = 12, height = 6
        properties = {
          title  = "Questions, refusals, cache hits"
          region = var.region
          stat   = "Sum"
          period = 300
          metrics = [
            ["FieldDocsRAG", "Questions", "Service", "api", "Environment", var.environment],
            [".", "Refusals", ".", ".", ".", "."],
            [".", "Ungrounded", ".", ".", ".", "."],
            [".", "CacheHits", ".", ".", ".", "."],
          ]
        }
      },
      {
        type = "metric", x = 12, y = 0, width = 12, height = 6
        properties = {
          title  = "Tokens and estimated LLM cost (USD)"
          region = var.region
          stat   = "Sum"
          period = 3600
          metrics = [
            ["FieldDocsRAG", "InputTokens", "Service", "api", "Environment", var.environment],
            [".", "OutputTokens", ".", ".", ".", "."],
            [".", "EstimatedCostUSD", ".", ".", ".", ".", { yAxis = "right" }],
          ]
        }
      },
      {
        type = "metric", x = 0, y = 6, width = 12, height = 6
        properties = {
          title  = "Answer latency (ms)"
          region = var.region
          period = 300
          metrics = [
            ["FieldDocsRAG", "LatencyMs", "Service", "api", "Environment", var.environment, { stat = "p50" }],
            ["...", { stat = "p95" }],
          ]
        }
      },
      {
        type = "metric", x = 12, y = 6, width = 12, height = 6
        properties = {
          title  = "Errors and ingestion"
          region = var.region
          stat   = "Sum"
          period = 300
          metrics = [
            ["AWS/Lambda", "Errors", "FunctionName", aws_lambda_function.api.function_name],
            ["...", aws_lambda_function.ingest.function_name],
            ["FieldDocsRAG", "DocumentsIngested", "Service", "ingestion", "Environment", var.environment],
            [".", "DocumentsFailed", ".", ".", ".", "."],
            ["AWS/SQS", "ApproximateNumberOfMessagesVisible", "QueueName", aws_sqs_queue.ingest_dlq.name, { stat = "Maximum" }],
          ]
        }
      },
    ]
  })
}

resource "aws_budgets_budget" "monthly" {
  count        = local.alarm_email == "" ? 0 : 1
  name         = "${local.name}-monthly"
  budget_type  = "COST"
  limit_amount = tostring(var.monthly_budget_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 80
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [local.alarm_email]
  }

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    notification_type          = "FORECASTED"
    subscriber_email_addresses = [local.alarm_email]
  }
}
