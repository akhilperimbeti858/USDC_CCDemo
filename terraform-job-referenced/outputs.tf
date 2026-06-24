output "s3_bucket" {
  description = "Existing S3 bucket holding the input manifest, UI template, and output."
  value       = data.aws_s3_bucket.gt.id
}

output "input_manifest_s3_uri" {
  value = local.manifest_s3_uri
}

output "ui_template_s3_uri" {
  value = local.ui_template_s3_uri
}

output "output_s3_uri" {
  value = local.output_s3_uri
}

output "ground_truth_role_arn" {
  description = "Execution role created here and assumed by the labeling job."
  value       = aws_iam_role.ground_truth.arn
}

output "pre_annotation_lambda_arn" {
  description = "Referenced pre-annotation Lambda wired into the job (not created here)."
  value       = var.pre_annotation_lambda_arn
}

output "post_annotation_lambda_arn" {
  description = "Referenced consolidation Lambda wired into the job (not created here)."
  value       = var.post_annotation_lambda_arn
}

output "labeling_job_name" {
  value = local.labeling_job_name
}

output "labeling_job_console_url" {
  description = "Ground Truth console URL for the launched labeling job."
  value       = "https://${var.aws_region}.console.aws.amazon.com/sagemaker/groundtruth?region=${var.aws_region}#/labeling-jobs/details/${local.labeling_job_name}"
}
