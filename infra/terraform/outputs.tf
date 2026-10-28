output "api_url" {
  value = aws_apigatewayv2_api.http.api_endpoint
}

output "cognito_user_pool_id" {
  value = aws_cognito_user_pool.main.id
}

output "cognito_client_id" {
  value = aws_cognito_user_pool_client.api.id
}

output "documents_bucket" {
  value = aws_s3_bucket.docs.bucket
}

output "ecr_repository_url" {
  value = aws_ecr_repository.app.repository_url
}

output "dlq_url" {
  value = aws_sqs_queue.ingest_dlq.url
}

output "ssm_prefix" {
  value = local.ssm_prefix
}

output "dashboard_url" {
  value = "https://${var.region}.console.aws.amazon.com/cloudwatch/home?region=${var.region}#dashboards:name=${aws_cloudwatch_dashboard.main.dashboard_name}"
}

output "github_deploy_role_arn" {
  value = try(aws_iam_role.github_deploy[0].arn, "")
}
