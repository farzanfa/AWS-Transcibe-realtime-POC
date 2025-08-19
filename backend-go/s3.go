package main

import (
	"bytes"
	"context"
	"fmt"
	"time"

	"github.com/aws/aws-sdk-go-v2/aws"
	"github.com/aws/aws-sdk-go-v2/service/s3"
)

// uploadToS3 uploads data to S3 and returns the S3 URL
func uploadToS3(ctx context.Context, key string, data []byte) (string, error) {
	_, err := s3Client.PutObject(ctx, &s3.PutObjectInput{
		Bucket:      aws.String(s3Bucket),
		Key:         aws.String(key),
		Body:        bytes.NewReader(data),
		ContentType: aws.String("audio/pcm"),
	})
	
	if err != nil {
		return "", fmt.Errorf("failed to upload to S3: %w", err)
	}
	
	return fmt.Sprintf("s3://%s/%s", s3Bucket, key), nil
}

// generatePresignedURL generates a presigned URL for S3 object access
func generatePresignedURL(ctx context.Context, key string, expiration time.Duration) (string, error) {
	presignClient := s3.NewPresignClient(s3Client)
	
	request, err := presignClient.PresignGetObject(ctx, &s3.GetObjectInput{
		Bucket: aws.String(s3Bucket),
		Key:    aws.String(key),
	}, s3.WithPresignExpires(expiration))
	
	if err != nil {
		return "", fmt.Errorf("failed to generate presigned URL: %w", err)
	}
	
	return request.URL, nil
}