data "aws_caller_identity" "current" {}

# ---------------------------------------------------------------------------
# Ground Truth labeling job execution role
#
# This stack creates ONLY the job execution role. It does NOT create the
# pre/post Lambda execution roles -- those belong to the separate stack that
# deploys the (shared) Lambdas.
# ---------------------------------------------------------------------------
data "aws_iam_policy_document" "gt_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["sagemaker.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "ground_truth" {
  name               = "${var.project_name}-gt-execution-role"
  assume_role_policy = data.aws_iam_policy_document.gt_assume.json
}

# Managed policy grants invoke on Lambdas whose names contain a SageMaker token
# (the referenced pre/post functions must be named accordingly) plus the GT
# runtime permissions.
resource "aws_iam_role_policy_attachment" "gt_managed" {
  role       = aws_iam_role.ground_truth.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSageMakerGroundTruthExecution"
}

data "aws_iam_policy_document" "gt_inline" {
  statement {
    sid     = "S3Access"
    actions = ["s3:GetObject", "s3:PutObject", "s3:ListBucket", "s3:GetBucketLocation"]
    resources = [
      data.aws_s3_bucket.gt.arn,
      "${data.aws_s3_bucket.gt.arn}/*",
    ]
  }

  # Invoke exactly the two REFERENCED Lambdas (identity-policy half of the invoke
  # equation). The resource-based half -- letting sagemaker.amazonaws.com invoke
  # them -- is owned by the stack that created the Lambdas.
  statement {
    sid     = "InvokeLabelingLambdas"
    actions = ["lambda:InvokeFunction"]
    resources = [
      var.pre_annotation_lambda_arn,
      var.post_annotation_lambda_arn,
    ]
  }
}

resource "aws_iam_role_policy" "gt_inline" {
  name   = "${var.project_name}-gt-inline"
  role   = aws_iam_role.ground_truth.id
  policy = data.aws_iam_policy_document.gt_inline.json
}
