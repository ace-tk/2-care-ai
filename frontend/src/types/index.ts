export interface User {
  id: number;
  email: string;
  full_name?: string;
  role: string;
  is_active: boolean;
}

export interface Patient {
  id: number;
  medical_record_number: string;
  first_name: string;
  last_name: string;
  date_of_birth: string;
  language_preference: string;
}

export interface Transcript {
  id: number;
  patient_id: number;
  creator_id: number;
  session_id: string;
  audio_url?: string;
  detected_language: string;
  original_text?: string;
  translated_text?: string;
  clinical_summary?: string;
  created_at: string;
}

export type ConnectionState = 'disconnected' | 'connecting' | 'connected' | 'error';

export type CallState = 'idle' | 'listening' | 'processing' | 'completed';

// WebSocket Server Events matching the Backend Pydantic schema
export type VoiceServerEvent =
  | {
      event: 'connected';
      session_id: string;
      payload: { message: string };
    }
  | {
      event: 'started';
      session_id: string;
      payload: { patient_id: number };
    }
  | {
      event: 'transcript_diff';
      session_id: string;
      payload: {
        original_text: string;
        translated_text: string;
        language: string;
        is_final: boolean;
      };
    }
  | {
      event: 'audio_response';
      session_id: string;
      payload: { audio_url?: string; text?: string };
    }
  | {
      event: 'summary_completed';
      session_id: string;
      payload: {
        transcript_id: number;
        clinical_summary: string;
      };
    }
  | {
      event: 'error';
      session_id: string;
      payload: { message: string };
    };
