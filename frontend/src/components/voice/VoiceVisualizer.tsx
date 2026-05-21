import React, { useEffect, useRef } from 'react';
import type { CallState } from '../../types';

interface VoiceVisualizerProps {
  callState: CallState;
}

export const VoiceVisualizer: React.FC<VoiceVisualizerProps> = ({ callState }) => {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const animationRef = useRef<number | null>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    canvas.width = canvas.offsetWidth * window.devicePixelRatio;
    canvas.height = canvas.offsetHeight * window.devicePixelRatio;
    ctx.scale(window.devicePixelRatio, window.devicePixelRatio);

    // Resize handler
    const handleResize = () => {
      if (!canvas) return;
      canvas.width = canvas.offsetWidth * window.devicePixelRatio;
      canvas.height = canvas.offsetHeight * window.devicePixelRatio;
      ctx.scale(window.devicePixelRatio, window.devicePixelRatio);
    };
    window.addEventListener('resize', handleResize);

    let phase = 0;

    const render = () => {
      ctx.clearRect(0, 0, canvas.offsetWidth, canvas.offsetHeight);

      const w = canvas.offsetWidth;
      const h = canvas.offsetHeight;
      const centerY = h / 2;

      if (callState === 'listening') {
        phase += 0.15;
        // Draw 3 layers of glowing waves
        for (let i = 0; i < 3; i++) {
          ctx.beginPath();
          const amplitude = (30 - i * 8) * (0.8 + Math.sin(phase * 0.5) * 0.2);
          const frequency = 0.02 + i * 0.01;
          const speed = (i + 1) * 0.05;

          ctx.strokeStyle =
            i === 0
              ? 'rgba(99, 102, 241, 0.8)' // Indigo
              : i === 1
              ? 'rgba(6, 182, 212, 0.6)'  // Cyan
              : 'rgba(236, 72, 153, 0.4)';  // Pink

          ctx.lineWidth = i === 0 ? 3 : i === 1 ? 2 : 1;

          for (let x = 0; x < w; x++) {
            const y = centerY + Math.sin(x * frequency + phase * speed) * amplitude;
            if (x === 0) {
              ctx.moveTo(x, y);
            } else {
              ctx.lineTo(x, y);
            }
          }
          ctx.stroke();
        }
      } else if (callState === 'processing') {
        // Pulse animation
        phase += 0.05;
        ctx.beginPath();
        ctx.strokeStyle = 'rgba(234, 179, 8, 0.8)'; // Amber
        ctx.lineWidth = 3;
        const amplitude = 5 + Math.sin(phase * 4) * 3;
        for (let x = 0; x < w; x++) {
          const y = centerY + Math.sin(x * 0.08) * amplitude;
          if (x === 0) {
            ctx.moveTo(x, y);
          } else {
            ctx.lineTo(x, y);
          }
        }
        ctx.stroke();
      } else {
        // Flat status line (idle/completed)
        ctx.beginPath();
        ctx.strokeStyle = 'rgba(156, 163, 175, 0.3)'; // Gray
        ctx.lineWidth = 2;
        ctx.moveTo(0, centerY);
        ctx.lineTo(w, centerY);
        ctx.stroke();
      }

      animationRef.current = requestAnimationFrame(render);
    };

    render();

    return () => {
      window.removeEventListener('resize', handleResize);
      if (animationRef.current) {
        cancelAnimationFrame(animationRef.current);
      }
    };
  }, [callState]);

  return (
    <div className="voice-visualizer-container">
      <canvas ref={canvasRef} className="voice-visualizer-canvas" />
    </div>
  );
};
export default VoiceVisualizer;
