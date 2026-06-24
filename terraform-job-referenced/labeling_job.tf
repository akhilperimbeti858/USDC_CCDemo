locals {
  # A short, deterministic suffix that changes only when the job configuration
  # changes. A changed suffix => a new unique labeling job name on re-apply.
  # The referenced Lambda ARNs are part of the fingerprint, so pointing the job
  # at a different pre/post Lambda yields a fresh job. (Object content/etags
  # aren't visible -- the assets live in a bucket this stack doesn't manage.)
  config_hash = substr(sha256(join(":", [
    local.manifest_s3_uri,
    local.ui_template_s3_uri,
    var.pre_annotation_lambda_arn,
    var.post_annotation_lambda_arn,
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
    pre_lambda_arn                     = var.pre_annotation_lambda_arn
    post_lambda_arn                    = var.post_annotation_lambda_arn
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

  # Only the role + rendered request are owned here. The referenced Lambdas (and
  # their sagemaker invoke permission) are managed by the stack that created them.
  depends_on = [
    aws_iam_role_policy.gt_inline,
    aws_iam_role_policy_attachment.gt_managed,
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
