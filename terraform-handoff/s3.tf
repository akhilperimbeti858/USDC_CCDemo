# The EXISTING bucket. This stack does NOT create it. It must already exist and
# must have EventBridge notifications enabled (see manage_bucket_eventbridge and
# the README) so the Object-Created events that drive the pipeline are emitted.
data "aws_s3_bucket" "gt" {
  bucket = var.s3_bucket_name
}

locals {
  manifest_s3_uri    = "s3://${data.aws_s3_bucket.gt.id}/${var.manifest_s3_key}"
  ui_template_s3_uri = "s3://${data.aws_s3_bucket.gt.id}/${var.ui_template_s3_key}"
  output_s3_uri      = "s3://${data.aws_s3_bucket.gt.id}/${var.output_s3_prefix}"

  comprehend_output_bucket = var.comprehend_output_bucket != "" ? var.comprehend_output_bucket : var.s3_bucket_name
  job_name_prefix          = var.job_name_prefix != "" ? var.job_name_prefix : var.project_name
}

# Upload the Crowd-HTML UI template into the existing bucket (a PutObject; does not
# create or reconfigure the bucket). Disable with upload_ui_template = false.
resource "aws_s3_object" "ui_template" {
  count        = var.upload_ui_template ? 1 : 0
  bucket       = data.aws_s3_bucket.gt.id
  key          = var.ui_template_s3_key
  source       = "${path.module}/${var.ui_template_local_path}"
  etag         = filemd5("${path.module}/${var.ui_template_local_path}")
  content_type = "text/html"
}

# OPTIONAL and AUTHORITATIVE: enable EventBridge on the bucket. This OVERWRITES any
# other notification configuration on the bucket. Prefer leaving this false and
# enabling EventBridge on the bucket out-of-band (one-time setting). See README.
resource "aws_s3_bucket_notification" "eventbridge" {
  count       = var.manage_bucket_eventbridge ? 1 : 0
  bucket      = data.aws_s3_bucket.gt.id
  eventbridge = true
}
