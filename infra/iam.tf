data "aws_iam_policy_document" "mtm_policy" {
  statement {
    sid = "S3Access"
    actions = ["s3:PutObject", "s3:PutObjectAcl", "s3:ListBucket"]
    resources = [
      aws_s3_bucket.transcripts.arn,
      "${aws_s3_bucket.transcripts.arn}/*"
    ]
  }
  statement {
    sid = "TranscribeMedical"
    actions = [
      "transcribe:StartMedicalStreamTranscription",
      "transcribe:StartMedicalTranscriptionJob",
      "transcribe:GetMedicalTranscriptionJob"
    ]
    resources = ["*"]
  }
}
resource "aws_iam_user" "backend" {
  name = var.iam_user_name
}
resource "aws_iam_user_policy" "backend_inline" {
  name   = "mtm-backend-policy"
  user   = aws_iam_user.backend.name
  policy = data.aws_iam_policy_document.mtm_policy.json
}
resource "aws_iam_access_key" "backend" {
  user = aws_iam_user.backend.name
}
output "backend_access_key_id" {
  value = aws_iam_access_key.backend.id
}
output "backend_secret_access_key" {
  value     = aws_iam_access_key.backend.secret
  sensitive = true
}