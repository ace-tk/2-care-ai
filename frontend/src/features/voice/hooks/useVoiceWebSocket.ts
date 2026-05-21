import { useState, useEffect, useRef } from 'react';
import { ConnectionState, CallState, VoiceServerEvent } from '../../../types';
import { voiceWebSocketClient } from '../../../services/websocketClient';
import { downsampleBuffer, floatTo16BitPCM } from '../../../utils/audio';

export interface UseVoiceWebSocketResult {
  connectionState: ConnectionState;
  callState: CallState;
  originalText: string;
  translatedText: string;
  detectedLanguage: string;
  clinicalSummary: string;
  startCall: (patientId: number, sourceLanguage?: string) => Promise<void>;
  stopCall: () => void;
  error: string | null;
}

export function useVoiceWebSocket(): UseVoiceWebSocketResult {
  const [connectionState, setConnectionState] = useState<ConnectionState>('disconnected');
  const [callState, setCallState] = useState<CallState>('idle');
  const [originalText, setOriginalText] = useState('');
  const [translatedText, setTranslatedText] = useState('');
  const [detectedLanguage, setDetectedLanguage] = useState('en');
  const [clinicalSummary, setClinicalSummary] = useState('');
  const [error, setError] = useState<string | null>(null);

  // Audio Context Ref variables
  const audioContextRef = useRef<AudioContext | null>(null);
  const mediaStreamRef = useRef<MediaStream | null>(null);
  const processorRef = useRef<ScriptProcessorNode | null>(null);

  // Set up WebSocket status sync
  useEffect(() => {
    const handleStatus = (status: any) => {
      setConnectionState(status);
      if (status === 'error') {
        setError('WebSocket Connection error.');
      }
    };

    voiceWebSocketClient.onStatusChange(handleStatus);
    return () => {
      voiceWebSocketClient.offStatusChange(handleStatus);
    };
  }, []);

  // Listen to WebSocket events from server
  useEffect(() => {
    const handleStarted = (data: any) => {
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

    voiceWebSocketClient.on('started', handleStarted);
    voiceWebSocketClient.on('transcript_diff', handleTranscriptDiff);
    voiceWebSocketClient.on('summary_completed', handleSummaryCompleted);
    voiceWebSocketClient.on('error', handleServerError);

    return () => {
      voiceWebSocketClient.off('started', handleStarted);
      voiceWebSocketClient.off('transcript_diff', handleTranscriptDiff);
      voiceWebSocketClient.off('summary_completed', handleSummaryCompleted);
      voiceWebSocketClient.off('error', handleServerError);
    };
  }, []);

  const startCall = async (patientId: number, sourceLanguage = 'auto') => {
    try {
      setError(null);
      setOriginalText('');
      setTranslatedText('');
      setClinicalSummary('');
      setCallState('idle');

      // Fetch JWT token from storage
      const token = localStorage.getItem('token') || 'dummy-token-placeholder';

      // 1. Establish WebSocket Connection
      await voiceWebSocketClient.connect(token);

      // 2. Request user microphone permissions
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          channelCount: 1,
          echoCancellation: true,
          noiseSuppression: true,
        },
      });
      mediaStreamRef.current = stream;

      // 3. Setup AudioContext and script processor to record audio
      const audioContext = new (window.AudioContext || (window as any).webkitAudioContext)({
        sampleRate: 16000, // Request standard 16kHz directly if browser supports it
      });
      audioContextRef.current = audioContext;

      const source = audioContext.createMediaStreamSource(stream);
      
      // Creating ScriptProcessorNode. Buffers size 4096 (standard for latency vs stability)
      const processor = audioContext.createScriptProcessor(4096, 1, 1);
      processorRef.current = processor;

      // Process sound buffers in real-time
      processor.onaudioprocess = (e) => {
        if (voiceWebSocketClient) {
          const float32Input = e.inputBuffer.getChannelData(0);
          
          // Downsample block to 16kHz if browser context opened at another rate
          const downsampled = downsampleBuffer(float32Input, audioContext.sampleRate, 16000);
          // Convert Float32Array to 16-bit PCM bytes (ArrayBuffer)
          const pcmBytes = floatTo16BitPCM(downsampled);
          
          // Stream raw audio bytes to server via WebSocket binary frame
          voiceWebSocketClient.send(pcmBytes);
        }
      };

      // Connect nodes
      source.connect(processor);
      processor.connect(audioContext.destination);

      // 4. Send "start" control message over WebSocket
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
      cleanupAudio();
      voiceWebSocketClient.disconnect();
    }
  };

  const stopCall = () => {
    // 1. Send "stop" control command to generate summary
    setCallState('processing');
    voiceWebSocketClient.send(
      JSON.stringify({
        type: 'stop',
      })
    );

    // 2. Tear down micro recording node immediately
    cleanupAudio();
  };

  const cleanupAudio = () => {
    if (processorRef.current) {
      processorRef.current.disconnect();
      processorRef.current = null;
    }
    if (mediaStreamRef.current) {
      mediaStreamRef.current.getTracks().forEach((track) => track.stop());
      mediaStreamRef.current = null;
    }
    if (audioContextRef.current) {
      if (audioContextRef.current.state !== 'closed') {
        audioContextRef.current.close();
      }
      audioContextRef.current = null;
    }
  };

  // Auto clean-up on unmount
  useEffect(() => {
    return () => {
      cleanupAudio();
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
    error,
  };
}
