locals {
  # A short, deterministic suffix that changes only when the job configuration
  # changes. A changed suffix => a new unique labeling job name on re-apply.
  # (Object etags aren't available since assets live in an existing bucket we
  # don't manage; the S3 URIs + pre-Lambda code hash + post ARN are used instead.)
  config_hash = substr(sha256(join(":", [
    local.manifest_s3_uri,
    local.ui_template_s3_uri,
    data.archive_file.pre.output_base64sha256,
    aws_lambda_function.post_single.arn,
    var.label_attribute_name,
  ])), 0, 8)

  labeling_job_name = "${var.project_name}-ner-${local.config_hash}"

  cli_input_json = templatefile("${path.module}/create-labeling-job.json.tftpl", {
    labeling_job_name                  = local.labeling_job_name
    label_attribute_name               = var.label_attribute_name
    manifest_s3_uri                    = local.manifest_s3_uri
    output_s3_uri                      = local.output_s3_uri
    role_arn                           = aws_iam_role.ground_truth.arn
    workteam_arn                       = var.private_workteam_arn
    ui_template_s3_uri                 = local.ui_template_s3_uri
    pre_lambda_arn                     = aws_lambda_function.pre_annotation.arn
    post_lambda_arn                    = aws_lambda_function.post_single.arn
    task_title                         = var.task_title
    task_description                   = var.task_description
    task_time_limit_seconds            = var.task_time_limit_seconds
    task_availability_lifetime_seconds = var.task_availability_lifetime_seconds
  })
}

# Render the create-labeling-job request to disk so it can be inspected and reused.
resource "local_file" "cli_input" {
  content  = local.cli_input_json
  filename = "${path.module}/build/create-labeling-job.${local.config_hash}.json"
}

# Launch the labeling job. Terraform has no native Ground Truth labeling-job
# resource, so we drive the AWS CLI. Requires AWS CLI v2 + credentials on the host.
resource "null_resource" "labeling_job" {
  triggers = {
    config_hash = local.config_hash
    region      = var.aws_region
    job_name    = local.labeling_job_name
  }

  depends_on = [
    aws_iam_role_policy.gt_inline,
    aws_iam_role_policy_attachment.gt_managed,
    aws_lambda_permission.gt_invoke_pre,
    aws_lambda_permission.gt_invoke_post_single,
    local_file.cli_input,
  ]

  provisioner "local-exec" {
    command = "aws sagemaker create-labeling-job --region ${var.aws_region} --cli-input-json file://${local_file.cli_input.filename}"
  }

  # Best-effort stop on destroy. The job may already be Completed/Stopped.
  provisioner "local-exec" {
    when       = destroy
    on_failure = continue
    command    = "aws sagemaker stop-labeling-job --region ${self.triggers.region} --labeling-job-name ${self.triggers.job_name}"
  }
}
