# References to the EXISTING S3 bucket and the assets already stored in it.
#
# This stack does NOT create the bucket and does NOT upload anything from local
# disk. The input manifest and the UI template must already live in the bucket at
# the keys below (see the *_s3_key variables). Because the bucket is not managed
# here, its settings are the owner's responsibility -- in particular it must have a
# CORS rule allowing `GET` so the Ground Truth worker UI can fetch the template.

data "aws_s3_bucket" "gt" {
  bucket = var.s3_bucket_name
}

locals {
  manifest_s3_uri    = "s3://${data.aws_s3_bucket.gt.id}/${var.manifest_s3_key}"
  ui_template_s3_uri = "s3://${data.aws_s3_bucket.gt.id}/${var.ui_template_s3_key}"
  output_s3_uri      = "s3://${data.aws_s3_bucket.gt.id}/${var.output_s3_prefix}"
}
