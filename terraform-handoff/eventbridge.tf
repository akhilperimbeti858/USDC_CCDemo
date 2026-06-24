# EventBridge rules turn S3 "Object Created" events into the two links of the
# pipeline. Requires EventBridge notifications to be enabled on the bucket (see
# manage_bucket_eventbridge / the README). These rules are NOT authoritative over
# the bucket -- they never touch its notification config.

locals {
  # Match the Comprehend tarball by suffix, optionally scoped to a key prefix
  # (prefix + suffix is expressed as an EventBridge wildcard).
  comprehend_key_match = var.comprehend_output_key_prefix != "" ? [
    { wildcard = "${var.comprehend_output_key_prefix}*${var.comprehend_output_suffix}" }
    ] : [
    { suffix = var.comprehend_output_suffix }
  ]
}

# --- Link 1: output.tar.gz lands -> run the converter ------------------------
resource "aws_cloudwatch_event_rule" "comprehend_output" {
  name        = "${var.project_name}-comprehend-output"
  description = "Comprehend output.tar.gz created -> build the GT input manifest."

  event_pattern = jsonencode({
    source        = ["aws.s3"]
    "detail-type" = ["Object Created"]
    detail = {
      bucket = { name = [var.s3_bucket_name] }
      object = { key = local.comprehend_key_match }
    }
  })
}

resource "aws_cloudwatch_event_target" "comprehend_output" {
  rule      = aws_cloudwatch_event_rule.comprehend_output.name
  target_id = "converter"
  arn       = aws_lambda_function.converter.arn
}

# --- Link 2: input manifest lands -> launch the labeling job -----------------
resource "aws_cloudwatch_event_rule" "manifest_created" {
  name        = "${var.project_name}-manifest-created"
  description = "Input manifest created -> launch the Ground Truth labeling job."

  event_pattern = jsonencode({
    source        = ["aws.s3"]
    "detail-type" = ["Object Created"]
    detail = {
      bucket = { name = [var.s3_bucket_name] }
      object = { key = [var.manifest_s3_key] }
    }
  })
}

resource "aws_cloudwatch_event_target" "manifest_created" {
  rule      = aws_cloudwatch_event_rule.manifest_created.name
  target_id = "launcher"
  arn       = aws_lambda_function.launcher.arn
}
