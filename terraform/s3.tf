locals {
  manifest_key    = "input/input.manifest"
  ui_template_key = "templates/ner-template.liquid.html"
  output_prefix   = "output/"

  manifest_s3_uri    = "s3://${aws_s3_bucket.gt.id}/${local.manifest_key}"
  ui_template_s3_uri = "s3://${aws_s3_bucket.gt.id}/${local.ui_template_key}"
  output_s3_uri      = "s3://${aws_s3_bucket.gt.id}/${local.output_prefix}"
}

resource "aws_s3_bucket" "gt" {
  bucket = var.s3_bucket_name
}

resource "aws_s3_bucket_public_access_block" "gt" {
  bucket                  = aws_s3_bucket.gt.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "gt" {
  bucket = aws_s3_bucket.gt.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_versioning" "gt" {
  bucket = aws_s3_bucket.gt.id
  versioning_configuration {
    status = "Enabled"
  }
}

# CORS lets the Ground Truth worker UI fetch the template asset from S3.
resource "aws_s3_bucket_cors_configuration" "gt" {
  bucket = aws_s3_bucket.gt.id

  cors_rule {
    allowed_headers = ["*"]
    allowed_methods = ["GET"]
    allowed_origins = ["*"]
    max_age_seconds = 3000
  }
}

resource "aws_s3_object" "ui_template" {
  bucket       = aws_s3_bucket.gt.id
  key          = local.ui_template_key
  source       = "${path.module}/../ui/ner-template.liquid.html"
  etag         = filemd5("${path.module}/../ui/ner-template.liquid.html")
  content_type = "text/html"
}

resource "aws_s3_object" "input_manifest" {
  bucket       = aws_s3_bucket.gt.id
  key          = local.manifest_key
  source       = "${path.module}/../manifests/input.manifest.example"
  etag         = filemd5("${path.module}/../manifests/input.manifest.example")
  content_type = "application/json"
}
