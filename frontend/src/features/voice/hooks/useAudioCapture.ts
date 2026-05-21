/**
 * useAudioCapture.ts
 * -------------------
 * React hook wrapping the AudioService class.
 * Provides reactive state for recording status, chunk counts,
 * and clean start/stop controls with automatic cleanup on unmount.
 */

import { useState, useRef, useCallback, useEffect } from 'react';
import { AudioService, type AudioServiceState } from '../../../services/audioService';

export interface UseAudioCaptureResult {
  /** Current state of the audio pipeline (idle | requesting | recording | stopping). */
  audioState: AudioServiceState;
  /** Whether the microphone is actively recording. */
  isRecording: boolean;
  /** Total PCM chunks captured this session. */
  chunkCount: number;
  /** Start microphone capture. Provide an onChunk callback to receive audio data. */
  startRecording: (onChunk: (chunk: ArrayBuffer) => void) => Promise<void>;
  /** Stop microphone capture and tear down audio pipeline. */
  stopRecording: () => void;
  /** Last error message from the audio pipeline, if any. */
  audioError: string | null;
}

export function useAudioCapture(): UseAudioCaptureResult {
  const [audioState, setAudioState] = useState<AudioServiceState>('idle');
  const [chunkCount, setChunkCount] = useState(0);
  const [audioError, setAudioError] = useState<string | null>(null);

  const audioServiceRef = useRef<AudioService | null>(null);

  const startRecording = useCallback(async (onChunk: (chunk: ArrayBuffer) => void) => {
    // Prevent double-start
    if (audioServiceRef.current) {
      console.warn('[useAudioCapture] Already recording, ignoring start request.');
      return;
    }

    setAudioError(null);
    setChunkCount(0);
    setAudioState('requesting');

    const service = new AudioService({
      targetSampleRate: 16000,
      bufferSize: 4096,
      onChunk: (chunk: ArrayBuffer) => {
        setChunkCount((prev) => prev + 1);
        onChunk(chunk);
      },
      onError: (err: Error) => {
        setAudioError(err.message);
        setAudioState('idle');
        audioServiceRef.current = null;
      },
    });

    audioServiceRef.current = service;

    try {
      await service.start();
      setAudioState(service.getState()); // 'recording' on success
    } catch {
      // AudioService.start() handles its own errors via onError callback
      setAudioState('idle');
      audioServiceRef.current = null;
    }
  }, []);

  const stopRecording = useCallback(() => {
    if (audioServiceRef.current) {
      audioServiceRef.current.stop();
      audioServiceRef.current = null;
      setAudioState('idle');
    }
  }, []);

  // Auto-cleanup on component unmount
  useEffect(() => {
    return () => {
      if (audioServiceRef.current) {
        audioServiceRef.current.stop();
        audioServiceRef.current = null;
      }
    };
  }, []);

  return {
    audioState,
    isRecording: audioState === 'recording',
    chunkCount,
    startRecording,
    stopRecording,
    audioError,
  };
}
