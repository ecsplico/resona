"""
WebSocket endpoint for live audio transcription.

This module provides real-time transcription capabilities via WebSocket,
allowing clients to stream audio and receive transcription results in real-time.
"""
import asyncio
import base64
import io
import json
import logging
import numpy as np
from typing import Optional
from fastapi import WebSocket, WebSocketDisconnect
import whisper

from core.paths import FILE_PATH
from ..processing.transcriber_factory import getTranscriber

logger = logging.getLogger(__name__)

# Audio configuration
SAMPLE_RATE = 16000
CHUNK_DURATION = 2.0  # Process 2 seconds of audio at a time
BUFFER_SIZE = int(SAMPLE_RATE * CHUNK_DURATION)


class AudioBuffer:
    """Manages audio buffering for streaming transcription."""
    
    def __init__(self, sample_rate: int = SAMPLE_RATE):
        self.sample_rate = sample_rate
        self.buffer = np.array([], dtype=np.float32)
        self.min_chunk_size = int(sample_rate * 1.0)  # Minimum 1 second
        
    def add_audio(self, audio_data: np.ndarray):
        """Add audio data to the buffer."""
        self.buffer = np.concatenate([self.buffer, audio_data])
        
    def get_chunk(self) -> Optional[np.ndarray]:
        """Get a chunk of audio if enough data is available."""
        if len(self.buffer) >= self.min_chunk_size:
            chunk = self.buffer[:BUFFER_SIZE]
            # Keep overlap for context
            overlap = int(SAMPLE_RATE * 0.5)  # 0.5 second overlap
            self.buffer = self.buffer[BUFFER_SIZE - overlap:]
            return chunk
        return None
    
    def clear(self):
        """Clear the buffer."""
        self.buffer = np.array([], dtype=np.float32)
    
    def has_data(self) -> bool:
        """Check if buffer has any data."""
        return len(self.buffer) > 0


async def transcribe_websocket(websocket: WebSocket):
    """
    Handle WebSocket connection for live transcription.
    
    Args:
        websocket: The WebSocket connection.
    """
    logger.info("🔌 transcribe_websocket function ENTERED")
    logger.info(f"WebSocket object: {websocket}")
    
    try:
        await websocket.accept()
        logger.info("✅ WebSocket connection ACCEPTED")
    except Exception as e:
        logger.error(f"❌ Failed to accept WebSocket: {e}", exc_info=True)
        raise
    
    audio_buffer = AudioBuffer()
    transcriber = getTranscriber()
    
    try:
        while True:
            # Receive message from client
            try:
                message = await asyncio.wait_for(
                    websocket.receive_text(),
                    timeout=10.0
                )
            except asyncio.TimeoutError:
                # Send keepalive
                await websocket.send_json({"type": "keepalive"})
                continue
            
            try:
                data = json.loads(message)
                msg_type = data.get("type")
                
                if msg_type == "audio":
                    # Decode audio data
                    audio_b64 = data.get("data", "")
                    sample_rate = data.get("sample_rate", SAMPLE_RATE)
                    
                    # Decode base64 audio
                    audio_bytes = base64.b64decode(audio_b64)
                    
                    # Convert to float32 numpy array
                    # Assuming 16-bit PCM audio
                    audio_int16 = np.frombuffer(audio_bytes, dtype=np.int16)
                    audio_float32 = audio_int16.astype(np.float32) / 32768.0
                    
                    # Resample if needed (simplified - in production use proper resampling)
                    if sample_rate != SAMPLE_RATE:
                        logger.warning(f"Sample rate mismatch: {sample_rate} != {SAMPLE_RATE}")
                    
                    # Add to buffer
                    audio_buffer.add_audio(audio_float32)
                    
                    # Process chunk if enough data
                    chunk = audio_buffer.get_chunk()
                    if chunk is not None:
                        # Transcribe the chunk
                        try:
                            result = transcriber.transcribe(
                                chunk,
                                task="transcribe",
                                language="de"
                            )
                            
                            text = result.get("text", "").strip()
                            
                            if text:
                                # Send transcription result
                                await websocket.send_json({
                                    "type": "transcript",
                                    "text": text,
                                    "is_final": False
                                })
                                logger.debug(f"Sent transcript: {text}")
                        
                        except Exception as e:
                            logger.error(f"Transcription error: {e}", exc_info=True)
                            await websocket.send_json({
                                "type": "error",
                                "message": f"Transcription error: {str(e)}"
                            })
                
                elif msg_type == "stop":
                    # Process any remaining audio
                    if audio_buffer.has_data():
                        try:
                            final_chunk = audio_buffer.buffer
                            result = transcriber.transcribe(
                                final_chunk,
                                task="transcribe",
                                language="de"
                            )
                            
                            text = result.get("text", "").strip()
                            if text:
                                await websocket.send_json({
                                    "type": "transcript",
                                    "text": text,
                                    "is_final": True
                                })
                        except Exception as e:
                            logger.error(f"Final transcription error: {e}")
                    
                    audio_buffer.clear()
                    await websocket.send_json({"type": "stopped"})
                    logger.info("Transcription stopped")
                
                else:
                    logger.warning(f"Unknown message type: {msg_type}")
            
            except json.JSONDecodeError:
                logger.error("Invalid JSON received")
                await websocket.send_json({
                    "type": "error",
                    "message": "Invalid JSON format"
                })
            except Exception as e:
                logger.error(f"Error processing message: {e}", exc_info=True)
                await websocket.send_json({
                    "type": "error",
                    "message": str(e)
                })
    
    except WebSocketDisconnect:
        logger.info("WebSocket disconnected")
    except Exception as e:
        logger.error(f"WebSocket error: {e}", exc_info=True)
    finally:
        logger.info("WebSocket connection closed")
