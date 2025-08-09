output "s3_bucket_name" {
  description = "Name of the S3 bucket for storing transcripts"
  value       = aws_s3_bucket.transcripts.id
}

output "s3_bucket_arn" {
  description = "ARN of the S3 bucket"
  value       = aws_s3_bucket.transcripts.arn
}

output "iam_user_name" {
  description = "Name of the IAM user"
  value       = aws_iam_user.transcription_user.name
}

output "iam_user_arn" {
  description = "ARN of the IAM user"
  value       = aws_iam_user.transcription_user.arn
}

output "aws_access_key_id" {
  description = "AWS Access Key ID for the service user"
  value       = aws_iam_access_key.transcription_user.id
  sensitive   = true
}

output "aws_secret_access_key" {
  description = "AWS Secret Access Key for the service user"
  value       = aws_iam_access_key.transcription_user.secret
  sensitive   = true
}

output "aws_region" {
  description = "AWS region where resources are created"
  value       = data.aws_region.current.name
}
