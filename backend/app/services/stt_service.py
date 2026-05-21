import logging
import asyncio
import time
from typing import Callable, Any
from deepgram import (
    DeepgramClient,
    DeepgramClientOptions,
    LiveTranscriptionEvents,
    LiveOptions,
)

logger = logging.getLogger(__name__)

class STTService:
    """Service interface for real-time Speech-to-Text transcription via Deepgram."""

    def __init__(self, api_key: str):
        self.api_key = api_key
        # Deepgram client configuration
        config = DeepgramClientOptions(options={"keepalive": "true"})
        self.client = DeepgramClient(api_key, config)
        logger.info("Deepgram STT service initialized.")

    async def create_streaming_session(
        self, 
        on_transcript: Callable[[dict], None], 
        sample_rate: int = 16000, 
        language: str = "en"
    ):
        """Creates a bidirectional streaming connection with Deepgram.
        
        Args:
            on_transcript: Callback fired when a transcript is received.
            sample_rate: Expected audio sample rate in Hz.
            language: Language code (e.g., 'en', 'es').
            
        Returns:
            The active Deepgram websocket connection capable of receiving audio via .send()
        """
        logger.info(f"Initializing Deepgram real-time stream (Language: {language})")
        
        dg_connection = self.client.listen.asyncwebsocket.v("1")
        
        async def on_message(self_dg, result, **kwargs):
            try:
                if not result or not result.channel or not result.channel.alternatives:
                    return
                
                sentence = result.channel.alternatives[0].transcript
                if not sentence:
                    return
                    
                is_final = result.is_final
                
                # We can also track transcription latency here if we wanted to extract timestamps
                logger.debug(f"[DEEPGRAM] Transcript received: {sentence} (Final: {is_final})")
                
                if is_final:
                    # Execute callback to pipe transcript back to orchestrator
                    await on_transcript({
                        "transcript": sentence,
                        "is_final": is_final,
                        "language": language
                    })
            except Exception as e:
                logger.error(f"Error processing Deepgram message: {e}", exc_info=True)

        async def on_error(self_dg, error, **kwargs):
            logger.error(f"Deepgram stream error: {error}")

        dg_connection.on(LiveTranscriptionEvents.Transcript, on_message)
        dg_connection.on(LiveTranscriptionEvents.Error, on_error)

        options = LiveOptions(
            model="nova-2",
            language=language,
            smart_format=True,
            encoding="linear16",
            sample_rate=sample_rate,
            channels=1
        )
        
        # Start connection
        if await dg_connection.start(options) is False:
            logger.error("Failed to start Deepgram connection")
            raise RuntimeError("Failed to connect to Deepgram streaming API")
            
        logger.info("Deepgram streaming connection established.")
        return dg_connection
