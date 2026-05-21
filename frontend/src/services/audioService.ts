/**
 * audioService.ts
 * ---------------
 * Standalone service for browser microphone capture and audio lifecycle management.
 * Responsible for:
 *  - Requesting mic permissions
 *  - Setting up AudioContext + ScriptProcessorNode pipeline
 *  - Downsampling audio from browser rate -> 16kHz
 *  - Converting Float32 PCM -> Int16 PCM ArrayBuffer chunks
 *  - Delivering chunked audio via an onChunk callback (ready for STT streaming)
 *  - Clean teardown on stop
 *
 * Does NOT implement Speech-to-Text.
 */

import { downsampleBuffer, floatTo16BitPCM } from '../utils/audio';

/** Configuration for the audio capture pipeline. */
export interface AudioServiceConfig {
  /** Target sample rate for STT (default: 16000 Hz). */
  targetSampleRate?: number;
  /** ScriptProcessor buffer size: 256 | 512 | 1024 | 2048 | 4096 (default: 4096). */
  bufferSize?: 256 | 512 | 1024 | 2048 | 4096;
  /** Called with each Int16 PCM chunk ready for streaming. */
  onChunk: (chunk: ArrayBuffer) => void;
  /** Called when an error occurs in the pipeline. */
  onError?: (err: Error) => void;
}

/** Internal state of the audio service. */
export type AudioServiceState = 'idle' | 'requesting' | 'recording' | 'stopping';

export class AudioService {
  private config: Required<Omit<AudioServiceConfig, 'onError'>> & { onError?: (err: Error) => void };
  private state: AudioServiceState = 'idle';

  // Pipeline nodes
  private audioContext: AudioContext | null = null;
  private mediaStream: MediaStream | null = null;
  private sourceNode: MediaStreamAudioSourceNode | null = null;
  private processorNode: ScriptProcessorNode | null = null;

  constructor(config: AudioServiceConfig) {
    this.config = {
      targetSampleRate: config.targetSampleRate ?? 16000,
      bufferSize: config.bufferSize ?? 4096,
      onChunk: config.onChunk,
      onError: config.onError,
    };
  }

  /** Current lifecycle state of the service. */
  getState(): AudioServiceState {
    return this.state;
  }

  /**
   * Requests microphone access and starts the audio capture pipeline.
   * Calls config.onChunk() with each Int16 PCM ArrayBuffer chunk.
   */
  async start(): Promise<void> {
    if (this.state !== 'idle') {
      console.warn('[AudioService] start() called while not idle, current state:', this.state);
      return;
    }

    this.state = 'requesting';

    try {
      // 1. Request mic permissions with optimal constraints for voice
      this.mediaStream = await navigator.mediaDevices.getUserMedia({
        audio: {
          channelCount: 1,
          sampleRate: { ideal: this.config.targetSampleRate },
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
        video: false,
      });

      // 2. Create AudioContext — browser may grant a different sample rate
      this.audioContext = new AudioContext();
      const browserSampleRate = this.audioContext.sampleRate;
      console.log(`[AudioService] AudioContext sample rate: ${browserSampleRate} Hz`);

      // 3. Build the processing graph: mic source → script processor → (silent) destination
      this.sourceNode = this.audioContext.createMediaStreamSource(this.mediaStream);
      this.processorNode = this.audioContext.createScriptProcessor(
        this.config.bufferSize,
        1, // mono input
        1  // mono output
      );

      const targetRate = this.config.targetSampleRate;
      const onChunk = this.config.onChunk;

      // 4. Process each audio buffer frame
      this.processorNode.onaudioprocess = (event: AudioProcessingEvent) => {
        if (this.state !== 'recording') return;

        const float32Data = event.inputBuffer.getChannelData(0);

        // Downsample if the browser opened the context at a higher rate
        const downsampled =
          browserSampleRate !== targetRate
            ? downsampleBuffer(float32Data, browserSampleRate, targetRate)
            : float32Data;

        // Convert to 16-bit PCM and deliver to consumer
        const pcmChunk = floatTo16BitPCM(downsampled);
        onChunk(pcmChunk);
      };

      // Connect graph (output to destination prevents chrome from garbage collecting)
      this.sourceNode.connect(this.processorNode);
      this.processorNode.connect(this.audioContext.destination);

      this.state = 'recording';
      console.log('[AudioService] Recording started.');
    } catch (err) {
      this.state = 'idle';
      const error = err instanceof Error ? err : new Error(String(err));
      console.error('[AudioService] Failed to start recording:', error.message);
      this.config.onError?.(error);
      this._cleanup();
    }
  }

  /**
   * Stops audio capture and tears down the pipeline.
   * Safe to call multiple times.
   */
  stop(): void {
    if (this.state === 'idle') return;

    this.state = 'stopping';
    console.log('[AudioService] Stopping recording and cleaning up pipeline...');
    this._cleanup();
    this.state = 'idle';
    console.log('[AudioService] Recording stopped.');
  }

  /** Releases all audio resources. */
  private _cleanup(): void {
    // Disconnect processor
    if (this.processorNode) {
      this.processorNode.onaudioprocess = null;
      try { this.processorNode.disconnect(); } catch { /* already disconnected */ }
      this.processorNode = null;
    }

    // Disconnect source
    if (this.sourceNode) {
      try { this.sourceNode.disconnect(); } catch { /* already disconnected */ }
      this.sourceNode = null;
    }

    // Stop all mic tracks
    if (this.mediaStream) {
      this.mediaStream.getTracks().forEach((track) => track.stop());
      this.mediaStream = null;
    }

    // Close AudioContext
    if (this.audioContext && this.audioContext.state !== 'closed') {
      this.audioContext.close().catch(() => {/* ignore */});
      this.audioContext = null;
    }
  }
}
