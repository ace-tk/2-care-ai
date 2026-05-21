import React, { useState, useRef, useEffect } from 'react';
import { useVoiceWebSocket } from './features/voice/hooks/useVoiceWebSocket';
import { VoiceVisualizer } from './components/voice/VoiceVisualizer';
import type { Patient } from './types';

// Mock Clinician profile
const CLINICIAN_NAME = "Dr. Sarah Jenkins, MD";

// Mock Patient list for demonstrating clinical workflows
const MOCK_PATIENTS: Patient[] = [
  {
    id: 1,
    medical_record_number: "MRN-849-204",
    first_name: "Elena",
    last_name: "Gomez",
    date_of_birth: "1978-04-12",
    language_preference: "es", // Spanish speaker
  },
  {
    id: 2,
    medical_record_number: "MRN-102-493",
    first_name: "Jean-Pierre",
    last_name: "Dubois",
    date_of_birth: "1965-11-23",
    language_preference: "fr", // French speaker
  },
  {
    id: 3,
    medical_record_number: "MRN-552-872",
    first_name: "Zhihao",
    last_name: "Wang",
    date_of_birth: "1991-08-05",
    language_preference: "zh", // Mandarin speaker
  },
];

export const App: React.FC = () => {
  const [selectedPatient, setSelectedPatient] = useState<Patient>(MOCK_PATIENTS[0]);
  const [sessionLang, setSessionLang] = useState<string>('auto');

  // Load custom websocket and microphone recording state hook
  const {
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
  } = useVoiceWebSocket();

  const [chatInput, setChatInput] = useState('');
  const chatEndRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [chatHistory]);

  const handleSendChat = (e: React.FormEvent) => {
    e.preventDefault();
    if (chatInput.trim()) {
      sendChatMessage(chatInput);
      setChatInput('');
    }
  };

  const handleCallToggle = () => {
    if (callState === 'idle' || callState === 'completed') {
      const lang = sessionLang === 'auto' ? selectedPatient.language_preference : sessionLang;
      startCall(selectedPatient.id, lang);
    } else if (callState === 'listening') {
      stopCall();
    }
  };

  return (
    <div className="app-container">
      {/* Clinician Dashboard Header */}
      <header className="app-header">
        <div className="logo-container">
          <div className="logo-icon">2C</div>
          <span className="logo-text">2Care AI Console</span>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: '1rem' }}>
          <span style={{ fontSize: '0.9rem', color: 'var(--text-secondary)' }}>
            Logged in as: <strong>{CLINICIAN_NAME}</strong>
          </span>
        </div>
      </header>

      {/* Main Layout Grid */}
      <main className="app-main">
        
        {/* Left Column: Patients Sidebar */}
        <section className="sidebar-panel">
          <div className="glass-card">
            <h2 className="panel-title">Active Patient Registry</h2>
            <div className="patient-list">
              {MOCK_PATIENTS.map((patient) => (
                <div
                  key={patient.id}
                  className={`patient-card ${selectedPatient.id === patient.id ? 'active' : ''}`}
                  onClick={() => {
                    if (callState === 'idle' || callState === 'completed') {
                      setSelectedPatient(patient);
                    }
                  }}
                  style={{
                    opacity: callState === 'listening' || callState === 'processing' ? 0.6 : 1,
                    cursor: callState === 'listening' || callState === 'processing' ? 'not-allowed' : 'pointer'
                  }}
                >
                  <div className="patient-name">
                    {patient.first_name} {patient.last_name}
                  </div>
                  <div className="patient-meta">
                    <span>MRN: {patient.medical_record_number}</span>
                    <span>Lang: {patient.language_preference.toUpperCase()}</span>
                  </div>
                </div>
              ))}
            </div>
          </div>

          <div className="glass-card">
            <h3 className="panel-title" style={{ fontSize: '1rem' }}>Session Configurations</h3>
            <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
              <label style={{ fontSize: '0.8rem', color: 'var(--text-secondary)' }}>
                Target Translation:
              </label>
              <input
                type="text"
                value="English (Clinician Preference)"
                disabled
                style={{
                  padding: '0.5rem',
                  borderRadius: '6px',
                  background: 'rgba(255,255,255,0.05)',
                  border: '1px solid var(--border-color)',
                  color: 'var(--text-muted)',
                  fontSize: '0.85rem'
                }}
              />
              
              <label style={{ fontSize: '0.8rem', color: 'var(--text-secondary)', marginTop: '0.5rem' }}>
                Speech Detection:
              </label>
              <select
                value={sessionLang}
                onChange={(e) => setSessionLang(e.target.value)}
                disabled={callState === 'listening' || callState === 'processing'}
                style={{
                  padding: '0.5rem',
                  borderRadius: '6px',
                  background: 'var(--bg-dark)',
                  border: '1px solid var(--border-color)',
                  color: 'var(--text-primary)',
                  fontSize: '0.85rem',
                  outline: 'none'
                }}
              >
                <option value="auto">Auto-Detect Language</option>
                <option value="es">Spanish (Español)</option>
                <option value="fr">French (Français)</option>
                <option value="zh">Chinese (中文)</option>
                <option value="en">English</option>
              </select>
            </div>
          </div>
        </section>

        {/* Right Column: Active Voice Session Panel */}
        <section className="workspace-panel">
          
          {/* Main Voice Streaming Console */}
          <div className="glass-card voice-console">
            <div className="status-badge">
              <span className={`status-indicator ${connectionState} ${callState}`}></span>
              <span>
                {isRecording
                  ? `🔴 RECORDING — ${chunkCount} chunks captured`
                  : callState === 'listening'
                  ? `STREAMING AUDIO (${detectedLanguage.toUpperCase()})`
                  : callState === 'processing'
                  ? 'COMPILING SOAPS NOTES...'
                  : connectionState === 'connected'
                  ? 'DEVICE CONNECTED'
                  : 'READY FOR CONSULTATION'}
              </span>
            </div>

            {/* Audio Capture Indicator */}
            {isRecording && (
              <div style={{
                display: 'flex',
                alignItems: 'center',
                gap: '0.75rem',
                padding: '0.5rem 1rem',
                background: 'rgba(239, 68, 68, 0.1)',
                border: '1px solid rgba(239, 68, 68, 0.3)',
                borderRadius: '8px',
                fontSize: '0.8rem',
                color: 'var(--accent-rose)',
                marginTop: '0.5rem'
              }}>
                <span style={{ width: '8px', height: '8px', borderRadius: '50%', background: '#ef4444', animation: 'pulse 1.5s infinite' }}></span>
                <span>Microphone active — streaming {chunkCount} PCM chunks @ 16kHz mono</span>
              </div>
            )}

            <h2 style={{ fontFamily: 'Outfit, sans-serif', fontSize: '1.75rem', fontWeight: 600, marginBottom: '0.5rem' }}>
              Consultation with {selectedPatient.first_name} {selectedPatient.last_name}
            </h2>
            <p style={{ color: 'var(--text-secondary)', fontSize: '0.9rem' }}>
              MRN: {selectedPatient.medical_record_number} | DOB: {selectedPatient.date_of_birth}
            </p>

            {/* Glowing wave animation */}
            <VoiceVisualizer callState={callState} />

            {/* Toggle Mic Button */}
            <button
              onClick={handleCallToggle}
              className={`call-btn ${callState === 'listening' ? 'active' : ''}`}
              disabled={callState === 'processing'}
              title={callState === 'listening' ? 'End stream and compile documentation' : 'Start consultation stream'}
            >
              {callState === 'listening' ? (
                // Stop call square icon
                <svg viewBox="0 0 24 24"><path d="M6 6h12v12H6V6z"/></svg>
              ) : (
                // Start call microphone icon
                <svg viewBox="0 0 24 24"><path d="M12 14c1.66 0 3-1.34 3-3V5c0-1.66-1.34-3-3-3S9 3.34 9 5v6c0 1.66 1.34 3 3 3zm5.3-3c0 3-2.54 5.1-5.3 5.1S6.7 14 6.7 11H5c0 3.41 2.72 6.23 6 6.72V21h2v-3.28c3.28-.48 6-3.3 6-6.72h-1.7z"/></svg>
              )}
            </button>
            
            {error && (
              <div style={{ marginTop: '1rem', color: 'var(--accent-rose)', fontSize: '0.85rem', fontWeight: 500 }}>
                {error}
              </div>
            )}
            
            {/* Latency Log Section Placeholder */}
            <div className="latency-log" style={{ marginTop: '1.5rem', paddingTop: '1rem', borderTop: '1px solid rgba(255,255,255,0.1)', display: 'flex', justifyContent: 'center', gap: '1.5rem', fontSize: '0.8rem', color: 'var(--text-muted)' }}>
              <span>STT Latency: <strong style={{ color: 'var(--accent-emerald)' }}>-- ms</strong></span>
              <span>LLM Reasoning: <strong style={{ color: 'var(--accent-indigo)' }}>-- ms</strong></span>
              <span>TTS Latency: <strong style={{ color: 'var(--accent-rose)' }}>-- ms</strong></span>
            </div>
          </div>

          {/* Transcription Logs and AI SOAP Summary Panel */}
          <div className="workspace-grid">
            
            {/* Live translation block */}
            <div className="glass-card transcript-box">
              <h3 className="panel-title">Realtime Transcription Feed</h3>
              <div className="transcript-log">
                {(originalText || translatedText) ? (
                  <>
                    <div className="speech-block">
                      <span className="speech-label">Patient Dialogue</span>
                      <p>{originalText || "[Speaking...]"}</p>
                    </div>
                    {detectedLanguage !== 'en' && translatedText && (
                      <div className="speech-block">
                        <span className="speech-label" style={{ color: 'var(--secondary)' }}>English Translation</span>
                        <p className="speech-translation">{translatedText}</p>
                      </div>
                    )}
                  </>
                ) : (
                  <div className="placeholder-text">
                    Select a patient and click the microphone icon to stream live audio. Transcripts will auto-generate.
                  </div>
                )}
              </div>
            </div>

            {/* Generated Clinical SOAP Summary */}
            <div className="glass-card summary-box">
              <h3 className="panel-title">
                Structured Clinical Notes
                {callState === 'completed' && (
                  <span style={{ fontSize: '0.75rem', padding: '0.15rem 0.5rem', borderRadius: '4px', background: 'rgba(16,185,129,0.15)', color: 'var(--accent-emerald)', border: '1px solid rgba(16,185,129,0.3)' }}>
                    SOAP GENERATED
                  </span>
                )}
              </h3>
              <div className="summary-content">
                {clinicalSummary ? (
                  clinicalSummary
                ) : (
                  <div className="placeholder-text">
                    {callState === 'processing' 
                      ? 'AI is compiling clinical discussion notes...' 
                      : 'SOAP medical reports will display here automatically once the voice consultation finishes.'}
                  </div>
                )}
              </div>
            </div>

          </div>

          {/* Realtime Chat Panel */}
          <div className="glass-card chat-box" style={{ marginTop: '1.5rem' }}>
            <h3 className="panel-title">Realtime Consultation Chat</h3>
            <div 
              className="chat-history" 
              style={{ 
                height: '200px', 
                overflowY: 'auto', 
                padding: '1rem', 
                background: 'rgba(0,0,0,0.2)', 
                borderRadius: '8px',
                marginBottom: '1rem',
                display: 'flex',
                flexDirection: 'column',
                gap: '0.5rem'
              }}
            >
              {chatHistory.length === 0 ? (
                <div className="placeholder-text" style={{ textAlign: 'center', marginTop: '2rem' }}>
                  No messages yet. Send a message to the AI.
                </div>
              ) : (
                chatHistory.map((msg, idx) => (
                  <div 
                    key={idx} 
                    style={{ 
                      alignSelf: msg.sender === 'user' ? 'flex-end' : 'flex-start',
                      background: msg.sender === 'user' ? 'var(--accent-indigo)' : 'var(--bg-dark)',
                      color: msg.sender === 'user' ? '#fff' : 'var(--text-primary)',
                      padding: '0.5rem 1rem',
                      borderRadius: '12px',
                      maxWidth: '80%',
                      border: msg.sender === 'ai' ? '1px solid var(--border-color)' : 'none'
                    }}
                  >
                    <strong>{msg.sender === 'user' ? 'You' : 'AI'}:</strong> {msg.text}
                  </div>
                ))
              )}
              <div ref={chatEndRef} />
            </div>
            
            <form onSubmit={handleSendChat} style={{ display: 'flex', gap: '0.5rem' }}>
              <input 
                type="text" 
                value={chatInput}
                onChange={(e) => setChatInput(e.target.value)}
                placeholder="Type a message..."
                disabled={connectionState !== 'connected'}
                style={{
                  flex: 1,
                  padding: '0.75rem',
                  borderRadius: '8px',
                  background: 'var(--bg-dark)',
                  border: '1px solid var(--border-color)',
                  color: 'var(--text-primary)',
                  outline: 'none'
                }}
              />
              <button 
                type="submit" 
                disabled={connectionState !== 'connected' || !chatInput.trim()}
                style={{
                  padding: '0.75rem 1.5rem',
                  borderRadius: '8px',
                  background: 'var(--accent-indigo)',
                  color: '#fff',
                  border: 'none',
                  cursor: connectionState === 'connected' && chatInput.trim() ? 'pointer' : 'not-allowed',
                  opacity: connectionState === 'connected' && chatInput.trim() ? 1 : 0.6
                }}
              >
                Send
              </button>
            </form>
          </div>

          {/* AI Reasoning Trace Panel Placeholder */}
          <div className="glass-card reasoning-box" style={{ marginTop: '1.5rem' }}>
            <h3 className="panel-title" style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="10"></circle><path d="M12 16v-4"></path><path d="M12 8h.01"></path></svg>
              AI Clinical Reasoning Trace
            </h3>
            <div 
              style={{ 
                padding: '1rem', 
                background: 'rgba(0,0,0,0.2)', 
                borderRadius: '8px',
                fontFamily: 'monospace',
                fontSize: '0.85rem',
                color: 'var(--text-secondary)'
              }}
            >
              {callState === 'processing' ? (
                <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
                  <span style={{ color: 'var(--accent-indigo)' }}>&gt; Analyzing patient transcript for clinical entities...</span>
                  <span style={{ color: 'var(--accent-indigo)', opacity: 0.8 }}>&gt; Cross-referencing ICD-10 codes with reported symptoms...</span>
                  <span style={{ color: 'var(--accent-indigo)', opacity: 0.6 }}>&gt; Generating differential diagnosis hypotheses...</span>
                </div>
              ) : (
                <div className="placeholder-text">
                  AI internal reasoning steps, safety checks, and medical references will appear here during active processing...
                </div>
              )}
            </div>
          </div>

        </section>
      </main>
    </div>
  );
};
export default App;
