output "s3_bucket" {
  description = "Existing bucket the pipeline reads/writes."
  value       = data.aws_s3_bucket.gt.id
}

output "manifest_s3_uri" {
  value = local.manifest_s3_uri
}

output "ui_template_s3_uri" {
  value = local.ui_template_s3_uri
}

output "output_s3_uri" {
  value = local.output_s3_uri
}

output "ground_truth_role_arn" {
  value = aws_iam_role.ground_truth.arn
}

output "pre_annotation_lambda_arn" {
  value = aws_lambda_function.pre_annotation.arn
}

output "post_single_lambda_arn" {
  value = aws_lambda_function.post_single.arn
}

output "converter_lambda_arn" {
  description = "Comprehend output.tar.gz -> manifest converter (EventBridge target)."
  value       = aws_lambda_function.converter.arn
}

output "launcher_lambda_arn" {
  description = "Labeling-job launcher (EventBridge target on the manifest)."
  value       = aws_lambda_function.launcher.arn
}

output "comprehend_output_rule_arn" {
  value = aws_cloudwatch_event_rule.comprehend_output.arn
}

output "manifest_created_rule_arn" {
  value = aws_cloudwatch_event_rule.manifest_created.arn
}

output "job_name_prefix" {
  description = "Prefix of the auto-generated labeling job names (suffixed with a UTC timestamp at launch)."
  value       = local.job_name_prefix
}

output "labeling_jobs_console_url" {
  description = "Ground Truth labeling-jobs list in the console."
  value       = "https://${var.aws_region}.console.aws.amazon.com/sagemaker/groundtruth?region=${var.aws_region}#/labeling-jobs"
}
