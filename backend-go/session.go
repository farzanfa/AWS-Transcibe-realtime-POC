package main

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"sync"
	"time"

	"github.com/aws/aws-sdk-go-v2/aws"
	"github.com/aws/aws-sdk-go-v2/service/transcribestreaming"
	"github.com/aws/aws-sdk-go-v2/service/transcribestreaming/types"
	"github.com/google/uuid"
	"github.com/gorilla/websocket"
)

// Message types
type MessageType string

const (
	MessageTypeSelectProtocol   MessageType = "select_protocol"
	MessageTypeProtocolSelected MessageType = "protocol_selected"
	MessageTypeSessionStarted   MessageType = "session_started"
	MessageTypeAudio            MessageType = "audio"
	MessageTypeTranscript       MessageType = "transcript"
	MessageTypeStop             MessageType = "stop"
	MessageTypeSessionEnded     MessageType = "session_ended"
	MessageTypeError            MessageType = "error"
)

// ClientMessage represents a message from the client
type ClientMessage struct {
	Type     MessageType     `json:"type"`
	Protocol string          `json:"protocol,omitempty"`
	Data     string          `json:"data,omitempty"`
}

// ServerMessage represents a message to the client
type ServerMessage struct {
	Type        MessageType     `json:"type"`
	SessionID   string          `json:"session_id,omitempty"`
	Protocol    string          `json:"protocol,omitempty"`
	Specialty   string          `json:"specialty,omitempty"`
	Timestamp   string          `json:"timestamp,omitempty"`
	Error       string          `json:"error,omitempty"`
	Transcript  json.RawMessage `json:"transcript,omitempty"`
	S3URL       string          `json:"s3_url,omitempty"`
}

// Session represents a transcription session
type Session struct {
	ID               string
	conn             *websocket.Conn
	protocol         string
	transcribeStream *transcribestreaming.StartMedicalStreamTranscriptionEventStream
	audioStream      chan []byte
	running          bool
	mu               sync.Mutex
	ctx              context.Context
	cancel           context.CancelFunc
	audioBuffer      []byte
	sampleRate       int32
}

// NewSession creates a new session
func NewSession(conn *websocket.Conn) *Session {
	ctx, cancel := context.WithCancel(context.Background())
	return &Session{
		ID:          uuid.New().String(),
		conn:        conn,
		audioStream: make(chan []byte, 100),
		sampleRate:  16000, // Default sample rate
		ctx:         ctx,
		cancel:      cancel,
	}
}

// Handle manages the WebSocket session
func (s *Session) Handle() error {
	defer s.cleanup()

	// Wait for protocol selection
	var msg ClientMessage
	if err := s.conn.ReadJSON(&msg); err != nil {
		return fmt.Errorf("failed to read initial message: %w", err)
	}

	if msg.Type != MessageTypeSelectProtocol {
		return s.sendError("Expected protocol selection message")
	}

	s.protocol = msg.Protocol
	if s.protocol != "websocket" && s.protocol != "http2" {
		return s.sendError("Invalid protocol. Must be 'websocket' or 'http2'")
	}

	// Send protocol selected confirmation
	if err := s.sendMessage(ServerMessage{
		Type:     MessageTypeProtocolSelected,
		Protocol: s.protocol,
	}); err != nil {
		return err
	}

	// Start the appropriate session
	if s.protocol == "websocket" {
		return s.handleWebSocketProtocol()
	} else {
		return s.handleHTTP2Protocol()
	}
}

// handleWebSocketProtocol handles WebSocket-based streaming
func (s *Session) handleWebSocketProtocol() error {
	// Start the transcription stream
	if err := s.startTranscriptionStream(); err != nil {
		return err
	}

	// Send session started message
	if err := s.sendMessage(ServerMessage{
		Type:      MessageTypeSessionStarted,
		SessionID: s.ID,
		Protocol:  s.protocol,
		Specialty: medicalSpecialty,
		Timestamp: time.Now().UTC().Format(time.RFC3339),
	}); err != nil {
		return err
	}

	s.running = true

	// Start goroutines for handling
	errChan := make(chan error, 2)
	
	// Handle incoming messages from client
	go func() {
		errChan <- s.handleClientMessages()
	}()

	// Handle transcription events
	go func() {
		errChan <- s.handleTranscriptionEvents()
	}()

	// Wait for either to finish
	err := <-errChan
	s.running = false
	
	return err
}

// handleHTTP2Protocol handles HTTP/2-based streaming
func (s *Session) handleHTTP2Protocol() error {
	// For HTTP/2, we'll collect audio data and send it in a single request
	// This is a simplified implementation
	
	// Send session started message
	if err := s.sendMessage(ServerMessage{
		Type:      MessageTypeSessionStarted,
		SessionID: s.ID,
		Protocol:  s.protocol,
		Specialty: medicalSpecialty,
		Timestamp: time.Now().UTC().Format(time.RFC3339),
	}); err != nil {
		return err
	}

	s.running = true

	// Collect audio data
	for s.running {
		var msg ClientMessage
		if err := s.conn.ReadJSON(&msg); err != nil {
			if websocket.IsUnexpectedCloseError(err, websocket.CloseGoingAway, websocket.CloseAbnormalClosure) {
				return err
			}
			break
		}

		switch msg.Type {
		case MessageTypeAudio:
			audioData, err := base64.StdEncoding.DecodeString(msg.Data)
			if err != nil {
				log.Printf("Failed to decode audio data: %v", err)
				continue
			}
			s.audioBuffer = append(s.audioBuffer, audioData...)

		case MessageTypeStop:
			s.running = false
			// Process the collected audio
			if len(s.audioBuffer) > 0 {
				if err := s.processHTTP2Audio(); err != nil {
					return err
				}
			}
		}
	}

	return nil
}

// startTranscriptionStream starts the AWS Transcribe Medical stream
func (s *Session) startTranscriptionStream() error {
	input := &transcribestreaming.StartMedicalStreamTranscriptionInput{
		LanguageCode:         types.LanguageCode(languageCode),
		MediaSampleRateHertz: aws.Int32(s.sampleRate),
		MediaEncoding:        types.MediaEncodingPcm,
		Specialty:            types.Specialty(medicalSpecialty),
		Type:                 types.Type(medicalType),
		EnableChannelIdentification: false,
		NumberOfChannels:     aws.Int32(1),
		ContentIdentificationType: types.MedicalContentIdentificationType(contentIdentificationType),
	}

	// Add vocabulary if specified
	if medicalVocabularyName != "" {
		input.VocabularyName = aws.String(medicalVocabularyName)
	}

	// Start the stream
	output, err := transcribeClient.StartMedicalStreamTranscription(s.ctx, input)
	if err != nil {
		return fmt.Errorf("failed to start transcription stream: %w", err)
	}

	s.transcribeStream = output.GetStream()

	// Start sending audio
	go s.sendAudioToTranscribe()

	return nil
}

// sendAudioToTranscribe sends audio data to AWS Transcribe
func (s *Session) sendAudioToTranscribe() {
	writer := s.transcribeStream.Writer
	defer writer.Close()

	for audio := range s.audioStream {
		if !s.running {
			break
		}

		event := &types.AudioStreamMemberAudioEvent{
			Value: types.AudioEvent{
				AudioChunk: audio,
			},
		}

		if err := writer.Send(s.ctx, event); err != nil {
			if err != io.EOF {
				log.Printf("Failed to send audio to transcribe: %v", err)
			}
			break
		}
	}
}

// handleClientMessages handles incoming messages from the client
func (s *Session) handleClientMessages() error {
	for s.running {
		var msg ClientMessage
		if err := s.conn.ReadJSON(&msg); err != nil {
			if websocket.IsUnexpectedCloseError(err, websocket.CloseGoingAway, websocket.CloseAbnormalClosure) {
				return err
			}
			break
		}

		switch msg.Type {
		case MessageTypeAudio:
			audioData, err := base64.StdEncoding.DecodeString(msg.Data)
			if err != nil {
				log.Printf("Failed to decode audio data: %v", err)
				continue
			}
			
			select {
			case s.audioStream <- audioData:
			case <-s.ctx.Done():
				return nil
			}

		case MessageTypeStop:
			s.running = false
			close(s.audioStream)
			return nil
		}
	}
	return nil
}

// handleTranscriptionEvents handles events from AWS Transcribe
func (s *Session) handleTranscriptionEvents() error {
	reader := s.transcribeStream.Reader
	defer reader.Close()

	for event := range reader.Events() {
		if reader.Err() != nil {
			return fmt.Errorf("stream error: %w", reader.Err())
		}

		switch v := event.(type) {
		case *types.MedicalTranscriptResultStreamMemberTranscriptEvent:
			// Process transcript event
			if err := s.processTranscriptEvent(v.Value); err != nil {
				log.Printf("Failed to process transcript event: %v", err)
			}

		default:
			log.Printf("Unknown event type: %T", v)
		}
	}

	return reader.Err()
}

// processTranscriptEvent processes a transcript event from AWS
func (s *Session) processTranscriptEvent(event types.MedicalTranscriptEvent) error {
	// Convert the transcript event to JSON for the client
	transcriptData, err := json.Marshal(event.Transcript)
	if err != nil {
		return err
	}

	return s.sendMessage(ServerMessage{
		Type:       MessageTypeTranscript,
		Transcript: json.RawMessage(transcriptData),
		Timestamp:  time.Now().UTC().Format(time.RFC3339),
	})
}

// processHTTP2Audio processes audio data for HTTP/2 protocol
func (s *Session) processHTTP2Audio() error {
	// For HTTP/2, we would typically send the entire audio in one request
	// This is a simplified implementation
	// In a real implementation, you might want to use the non-streaming API
	
	// Upload to S3
	s3Key := fmt.Sprintf("audio/%s.pcm", s.ID)
	s3URL, err := uploadToS3(s.ctx, s3Key, s.audioBuffer)
	if err != nil {
		return err
	}
	
	// TODO: Here you would typically call the non-streaming Transcribe Medical API
	// For now, we'll just return the S3 URL
	
	// Send completion message
	return s.sendMessage(ServerMessage{
		Type:      MessageTypeSessionEnded,
		SessionID: s.ID,
		S3URL:     s3URL,
		Timestamp: time.Now().UTC().Format(time.RFC3339),
	})
}

// sendMessage sends a message to the client
func (s *Session) sendMessage(msg ServerMessage) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.conn.WriteJSON(msg)
}

// sendError sends an error message to the client
func (s *Session) sendError(errMsg string) error {
	return s.sendMessage(ServerMessage{
		Type:  MessageTypeError,
		Error: errMsg,
	})
}

// cleanup cleans up the session resources
func (s *Session) cleanup() {
	s.cancel()
	s.running = false
	close(s.audioStream)
	
	// Send session ended message
	s.sendMessage(ServerMessage{
		Type:      MessageTypeSessionEnded,
		SessionID: s.ID,
		Timestamp: time.Now().UTC().Format(time.RFC3339),
	})
}