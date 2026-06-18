output "s3_bucket" {
  description = "S3 bucket holding the input manifest, UI template, and output."
  value       = aws_s3_bucket.gt.id
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
  value = aws_iam_role.ground_truth.arn
}

output "pre_annotation_lambda_arn" {
  value = aws_lambda_function.pre_annotation.arn
}

output "post_single_lambda_arn" {
  value = aws_lambda_function.post_single.arn
}

output "post_merge_lambda_arn" {
  value = aws_lambda_function.post_merge.arn
}

output "active_consolidation_lambda_arn" {
  description = "Post-annotation Lambda actually wired into the launched job."
  value       = local.selected_post_lambda_arn
}

output "labeling_job_name" {
  value = local.labeling_job_name
}

output "labeling_job_console_url" {
  description = "Ground Truth console URL for the launched labeling job."
  value       = "https://${var.aws_region}.console.aws.amazon.com/sagemaker/groundtruth?region=${var.aws_region}#/labeling-jobs/details/${local.labeling_job_name}"
}
