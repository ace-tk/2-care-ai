/**
 * Audio conversion utilities for voice AI streaming.
 * Converts browser AudioContext output (Float32Array) to 16kHz Mono 16-bit PCM.
 */

/**
 * Downsamples Float32 audio chunk to 16kHz sample rate.
 */
export function downsampleBuffer(
  buffer: Float32Array,
  inputSampleRate: number,
  outputSampleRate = 16000
): Float32Array {
  if (inputSampleRate === outputSampleRate) {
    return buffer;
  }
  if (inputSampleRate < outputSampleRate) {
    throw new Error('Downsampling rate must be smaller than input sample rate.');
  }

  const sampleRateRatio = inputSampleRate / outputSampleRate;
  const newLength = Math.round(buffer.length / sampleRateRatio);
  const result = new Float32Array(newLength);
  
  let offsetResult = 0;
  let offsetBuffer = 0;

  while (offsetResult < result.length) {
    const nextOffsetBuffer = Math.round((offsetResult + 1) * sampleRateRatio);
    
    // Accumulate values in the window to find average
    let accum = 0;
    let count = 0;
    for (let i = offsetBuffer; i < nextOffsetBuffer && i < buffer.length; i++) {
      accum += buffer[i];
      count++;
    }
    
    result[offsetResult] = count > 0 ? accum / count : 0;
    offsetResult++;
    offsetBuffer = nextOffsetBuffer;
  }

  return result;
}

/**
 * Converts Float32Array audio buffer to 16-bit linear PCM (Int16Array / ArrayBuffer).
 */
export function floatTo16BitPCM(float32Buffer: Float32Array): ArrayBuffer {
  const buffer = new ArrayBuffer(float32Buffer.length * 2);
  const view = new DataView(buffer);
  
  for (let i = 0; i < float32Buffer.length; i++) {
    // Clamp to [-1.0, 1.0] range
    const s = Math.max(-1, Math.min(1, float32Buffer[i]));
    // Scale and convert to signed 16-bit integer
    view.setInt16(i * 2, s < 0 ? s * 0x8000 : s * 0x7fff, true); // true = little-endian
  }
  
  return buffer;
}
