import logging
import time
import json
import base64
import websockets
import asyncio
from typing import AsyncGenerator

logger = logging.getLogger(__name__)


class TTSService:
    """Service interface for Text-to-Speech synthesis.
    
    This abstracts converting text responses back into audio (e.g., ElevenLabs, OpenAI TTS).
    """

    def __init__(self, api_key: str):
        self.api_key = api_key
        logger.info("Text-to-Speech service placeholder initialized.")

    async def synthesize_speech(
        self, text: str, voice_id: str = "default", language: str = "en"
    ) -> bytes:
        """Synthesizes text into a complete audio file.
        
        Returns:
            bytes: The raw audio data (e.g., MP3 or PCM)
        """
        logger.debug(f"Synthesizing text: '{text[:30]}...' in language: {language}")
        
        # MOCK return value - returns empty bytes as placeholder
        return b""

    async def stream_speech(
        self, text_stream: AsyncGenerator[str, None], voice_id: str = "cjVigY5qzO86HufA2TX8"  # Default Rachel voice
    ) -> AsyncGenerator[bytes, None]:
        """Converts an incoming stream of text into an outgoing stream of audio chunks.
        
        Allows for low-latency playback where voice begins before LLM completes response.
        """
        logger.info("Starting real-time text-to-speech audio stream...")
        
        # Use eleven_multilingual_v2 for English, Hindi, and Tamil support
        ws_url = f"wss://api.elevenlabs.io/v1/text-to-speech/{voice_id}/stream-input?model_id=eleven_multilingual_v2&output_format=mp3_44100_128"
        
        headers = {
            "xi-api-key": self.api_key
        }
        
        start_time = time.time()
        first_chunk_time = None
        
        try:
            async with websockets.connect(ws_url, additional_headers=headers) as ws:
                
                async def sender():
                    async for chunk in text_stream:
                        if chunk.strip():
                            payload = {"text": chunk + " ", "try_trigger_generation": True}
                            await ws.send(json.dumps(payload))
                    
                    # Send empty text to indicate end of stream
                    await ws.send(json.dumps({"text": ""}))
                
                sender_task = asyncio.create_task(sender())
                
                while True:
                    try:
                        message = await ws.recv()
                        data = json.loads(message)
                        
                        if data.get("audio"):
                            if not first_chunk_time:
                                first_chunk_time = time.time()
                                latency = (first_chunk_time - start_time) * 1000
                                logger.info(f"TTS Time-To-First-Byte (TTFB): {latency:.2f} ms")
                                
                            audio_bytes = base64.b64decode(data["audio"])
                            yield audio_bytes
                            
                        if data.get("isFinal"):
                            logger.info("ElevenLabs stream completed.")
                            break
                            
                    except websockets.exceptions.ConnectionClosed:
                        logger.warning("ElevenLabs connection closed early.")
                        break
                        
                await sender_task
                
        except Exception as e:
            logger.error(f"Error in ElevenLabs TTS stream: {e}", exc_info=True)
