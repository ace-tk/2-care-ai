/**
 * useVoiceWebSocket.ts
 * ---------------------
 * Orchestrator hook that composes:
 *   - useAudioCapture  → microphone recording & PCM chunk delivery
 *   - websocketClient  → bidirectional server communication
 *
 * This hook owns the high-level call lifecycle (idle → listening → processing → completed)
 * and delegates audio capture entirely to the modular AudioService layer.
 */

import { useState, useEffect } from 'react';
import type { ConnectionState, CallState } from '../../../types';
import { voiceWebSocketClient } from '../../../services/websocketClient';
import { useAudioCapture } from './useAudioCapture';

export interface UseVoiceWebSocketResult {
  connectionState: ConnectionState;
  callState: CallState;
  originalText: string;
  translatedText: string;
  detectedLanguage: string;
  clinicalSummary: string;
  startCall: (patientId: number, sourceLanguage?: string) => Promise<void>;
  stopCall: () => void;
  sendChatMessage: (text: string) => void;
  chatHistory: { sender: 'user' | 'ai'; text: string }[];
  /** Audio capture state from the modular AudioService. */
  isRecording: boolean;
  /** Total PCM chunks captured in the current session. */
  chunkCount: number;
  error: string | null;
}

export function useVoiceWebSocket(): UseVoiceWebSocketResult {
  const [connectionState, setConnectionState] = useState<ConnectionState>('disconnected');
  const [callState, setCallState] = useState<CallState>('idle');
  const [originalText, setOriginalText] = useState('');
  const [translatedText, setTranslatedText] = useState('');
  const [detectedLanguage, setDetectedLanguage] = useState('en');
  const [clinicalSummary, setClinicalSummary] = useState('');
  const [chatHistory, setChatHistory] = useState<{ sender: 'user' | 'ai'; text: string }[]>([]);
  const [error, setError] = useState<string | null>(null);

  // Delegate all audio capture to the modular AudioService hook
  const {
    isRecording,
    chunkCount,
    startRecording,
    stopRecording,
    audioError,
  } = useAudioCapture();

  // Surface audio errors in the main error state
  useEffect(() => {
    if (audioError) {
      setError(audioError);
    }
  }, [audioError]);

  // ---- WebSocket status sync ----
  useEffect(() => {
    const handleStatus = (status: any) => {
      setConnectionState(status);
      if (status === 'error') {
        setError('WebSocket connection error.');
      }
    };

    voiceWebSocketClient.onStatusChange(handleStatus);
    return () => {
      voiceWebSocketClient.offStatusChange(handleStatus);
    };
  }, []);

  // ---- WebSocket event listeners ----
  useEffect(() => {
    const handleStarted = (_data: any) => {
      setCallState('listening');
      setError(null);
    };

    const handleTranscriptDiff = (data: any) => {
      const payload = data.payload;
      setOriginalText((prev) => prev + ' ' + payload.original_text);
      setTranslatedText((prev) => prev + ' ' + payload.translated_text);
      setDetectedLanguage(payload.language);
    };

    const handleSummaryCompleted = (data: any) => {
      setCallState('completed');
      setClinicalSummary(data.payload.clinical_summary);
    };

    const handleServerError = (data: any) => {
      setError(data.payload.message || 'Server error occurred');
    };

    const handleChatResponse = (data: any) => {
      setChatHistory((prev) => [...prev, { sender: 'ai', text: data.payload.text }]);
    };

    voiceWebSocketClient.on('started', handleStarted);
    voiceWebSocketClient.on('transcript_diff', handleTranscriptDiff);
    voiceWebSocketClient.on('summary_completed', handleSummaryCompleted);
    voiceWebSocketClient.on('error', handleServerError);
    voiceWebSocketClient.on('chat_response', handleChatResponse);

    return () => {
      voiceWebSocketClient.off('started', handleStarted);
      voiceWebSocketClient.off('transcript_diff', handleTranscriptDiff);
      voiceWebSocketClient.off('summary_completed', handleSummaryCompleted);
      voiceWebSocketClient.off('error', handleServerError);
      voiceWebSocketClient.off('chat_response', handleChatResponse);
    };
  }, []);

  // ---- High-level call lifecycle ----

  const startCall = async (patientId: number, sourceLanguage = 'auto') => {
    try {
      setError(null);
      setOriginalText('');
      setTranslatedText('');
      setClinicalSummary('');
      setChatHistory([]);
      setCallState('idle');

      // 1. Establish WebSocket connection
      const token = localStorage.getItem('token') || 'dummy-token-placeholder';
      await voiceWebSocketClient.connect(token);

      // 2. Start microphone capture via AudioService
      //    Each PCM chunk is streamed as a binary WebSocket frame
      await startRecording((pcmChunk: ArrayBuffer) => {
        voiceWebSocketClient.send(pcmChunk);
      });

      // 3. Send "start" control message to the server
      voiceWebSocketClient.send(
        JSON.stringify({
          type: 'start',
          payload: {
            patient_id: patientId,
            source_language: sourceLanguage,
          },
        })
      );
    } catch (err: any) {
      console.error('Failed to start real-time call:', err);
      setError(err.message || 'Failed to initialize microphone or socket connection.');
      stopRecording();
      voiceWebSocketClient.disconnect();
    }
  };

  const stopCall = () => {
    // 1. Send "stop" command to trigger server-side summary generation
    setCallState('processing');
    voiceWebSocketClient.send(
      JSON.stringify({
        type: 'stop',
      })
    );

    // 2. Stop microphone capture via AudioService
    stopRecording();
  };

  const sendChatMessage = (text: string) => {
    if (!text.trim()) return;

    // Add user message to local history
    setChatHistory((prev) => [...prev, { sender: 'user', text }]);

    // Send over WebSocket
    voiceWebSocketClient.send(
      JSON.stringify({
        type: 'text',
        payload: { text },
      })
    );
  };

  // Auto clean-up on unmount
  useEffect(() => {
    return () => {
      stopRecording();
      voiceWebSocketClient.disconnect();
    };
  }, []);

  return {
    connectionState,
    callState,
    originalText,
    translatedText,
    detectedLanguage,
    clinicalSummary,
    startCall,
    stopCall,
    sendChatMessage,
    chatHistory,
    isRecording,
    chunkCount,
    error,
  };
}
