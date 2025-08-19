package main

import (
	"context"
	"log"
	"net/http"
	"os"

	"github.com/aws/aws-sdk-go-v2/aws"
	"github.com/aws/aws-sdk-go-v2/config"
	"github.com/aws/aws-sdk-go-v2/credentials"
	"github.com/aws/aws-sdk-go-v2/service/s3"
	"github.com/aws/aws-sdk-go-v2/service/transcribestreaming"
	"github.com/gin-gonic/gin"
	"github.com/gorilla/websocket"
	"github.com/joho/godotenv"
)

var (
	// Environment configuration
	awsRegion                  string
	s3Bucket                   string
	medicalSpecialty           string
	medicalType                string
	medicalVocabularyName      string
	contentIdentificationType  string
	languageCode               string
	logLevel                   string

	// AWS clients
	s3Client                   *s3.Client
	transcribeClient           *transcribestreaming.Client
	awsConfig                  aws.Config

	// WebSocket upgrader
	upgrader = websocket.Upgrader{
		CheckOrigin: func(r *http.Request) bool {
			return true // Allow all origins for now
		},
	}
)

func init() {
	// Load .env file if it exists
	godotenv.Load()

	// Load environment variables
	awsRegion = getEnv("AWS_REGION", "us-east-1")
	s3Bucket = getEnv("S3_BUCKET", "")
	medicalSpecialty = getEnv("MEDICAL_SPECIALTY", "PRIMARYCARE")
	medicalType = getEnv("MEDICAL_TYPE", "CONVERSATION")
	medicalVocabularyName = getEnv("MEDICAL_VOCABULARY_NAME", "")
	contentIdentificationType = getEnv("CONTENT_IDENTIFICATION_TYPE", "PHI")
	languageCode = getEnv("LANGUAGE_CODE", "en-US")
	logLevel = getEnv("LOG_LEVEL", "INFO")

	if s3Bucket == "" {
		log.Fatal("S3_BUCKET environment variable is required")
	}

	// Initialize AWS configuration
	ctx := context.Background()
	var err error

	// Check if we have explicit credentials in environment
	accessKeyID := os.Getenv("AWS_ACCESS_KEY_ID")
	secretAccessKey := os.Getenv("AWS_SECRET_ACCESS_KEY")

	if accessKeyID != "" && secretAccessKey != "" {
		// Use static credentials from environment
		awsConfig, err = config.LoadDefaultConfig(ctx,
			config.WithRegion(awsRegion),
			config.WithCredentialsProvider(credentials.NewStaticCredentialsProvider(
				accessKeyID,
				secretAccessKey,
				os.Getenv("AWS_SESSION_TOKEN"),
			)),
		)
	} else {
		// Use default credential chain
		awsConfig, err = config.LoadDefaultConfig(ctx,
			config.WithRegion(awsRegion),
		)
	}

	if err != nil {
		log.Fatalf("Failed to load AWS configuration: %v", err)
	}

	// Initialize AWS clients
	s3Client = s3.NewFromConfig(awsConfig)
	transcribeClient = transcribestreaming.NewFromConfig(awsConfig)
}

func getEnv(key, defaultValue string) string {
	if value := os.Getenv(key); value != "" {
		return value
	}
	return defaultValue
}

func main() {
	// Initialize Gin router
	router := gin.Default()

	// Configure CORS
	router.Use(corsMiddleware())

	// Routes
	router.GET("/health", healthCheck)
	router.GET("/config", getConfig)
	router.GET("/ws", handleWebSocket)

	// Start server
	port := getEnv("PORT", "8000")
	log.Printf("Starting server on port %s", port)
	if err := router.Run(":" + port); err != nil {
		log.Fatalf("Failed to start server: %v", err)
	}
}

func corsMiddleware() gin.HandlerFunc {
	return func(c *gin.Context) {
		c.Writer.Header().Set("Access-Control-Allow-Origin", "*")
		c.Writer.Header().Set("Access-Control-Allow-Credentials", "true")
		c.Writer.Header().Set("Access-Control-Allow-Headers", "Content-Type, Content-Length, Accept-Encoding, X-CSRF-Token, Authorization, accept, origin, Cache-Control, X-Requested-With")
		c.Writer.Header().Set("Access-Control-Allow-Methods", "POST, OPTIONS, GET, PUT, DELETE")

		if c.Request.Method == "OPTIONS" {
			c.AbortWithStatus(204)
			return
		}

		c.Next()
	}
}

func healthCheck(c *gin.Context) {
	c.JSON(http.StatusOK, gin.H{
		"status":  "healthy",
		"service": "medical-transcription-backend-go",
		"region":  awsRegion,
		"features": gin.H{
			"websocket":             true,
			"http2":                 true,
			"medical_entities":      true,
			"content_identification": contentIdentificationType,
		},
	})
}

func getConfig(c *gin.Context) {
	vocabulary := medicalVocabularyName
	if vocabulary == "" {
		vocabulary = "none"
	}

	c.JSON(http.StatusOK, gin.H{
		"region":                 awsRegion,
		"specialty":              medicalSpecialty,
		"type":                   medicalType,
		"language":               languageCode,
		"content_identification": contentIdentificationType,
		"vocabulary":             vocabulary,
	})
}

func handleWebSocket(c *gin.Context) {
	// Upgrade HTTP connection to WebSocket
	conn, err := upgrader.Upgrade(c.Writer, c.Request, nil)
	if err != nil {
		log.Printf("Failed to upgrade connection: %v", err)
		return
	}
	defer conn.Close()

	// Create a new session
	session := NewSession(conn)
	
	// Handle the WebSocket session
	if err := session.Handle(); err != nil {
		log.Printf("Session error: %v", err)
	}
}