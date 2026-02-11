"""
WebSocket endpoint for live audio transcription with VAD and local agreement.

This module provides the /ws/live endpoint that streams audio from clients
and returns partial/final transcription results in real-time.

Protocol:
  Client -> Server:
    {"type": "audio", "data": "<base64 PCM int16>", "sample_rate": 16000}
    {"type": "stop"}
    {"type": "config", "language": "de"}

  Server -> Client:
    {"type": "partial", "text": "unstable text..."}
    {"type": "final", "text": "confirmed stable text"}
    {"type": "stopped"}
    {"type": "error", "message": "..."}
    {"type": "keepalive"}
"""
import asyncio
import base64
import json
import logging
import numpy as np
from fastapi import WebSocket, WebSocketDisconnect

from ..processing.live_transcriber import LiveTranscriber, SAMPLE_RATE

logger = logging.getLogger(__name__)

# How often to attempt transcription (seconds)
PROCESS_INTERVAL = 0.8


async def live_transcribe_websocket(websocket: WebSocket):
    """Handle a WebSocket connection for live transcription."""
    logger.info("🎙️ Live WebSocket connection attempt")

    try:
        await websocket.accept()
        logger.info("✅ Live WebSocket accepted")
    except Exception as e:
        logger.error(f"❌ Failed to accept live WebSocket: {e}", exc_info=True)
        raise

    transcriber = LiveTranscriber(language="de")
    processing = True

    async def process_loop():
        """Periodically process buffered audio and send results."""
        while processing:
            await asyncio.sleep(PROCESS_INTERVAL)
            if not transcriber.has_enough_audio():
                continue
            try:
                result = await transcriber.process()
                if result is None:
                    continue

                if result.partial:
                    await websocket.send_json({
                        "type": "partial",
                        "text": result.partial,
                        "confirmed": result.confirmed,
                    })
                elif result.confirmed:
                    await websocket.send_json({
                        "type": "final",
                        "text": result.confirmed,
                    })
            except WebSocketDisconnect:
                break
            except Exception as e:
                logger.error(f"Process loop error: {e}", exc_info=True)

    # Start the background processing loop
    process_task = asyncio.create_task(process_loop())

    try:
        while True:
            try:
                message = await asyncio.wait_for(
                    websocket.receive_text(),
                    timeout=30.0,
                )
            except asyncio.TimeoutError:
                await websocket.send_json({"type": "keepalive"})
                continue

            try:
                data = json.loads(message)
                msg_type = data.get("type")

                if msg_type == "audio":
                    audio_b64 = data.get("data", "")
                    sample_rate = data.get("sample_rate", SAMPLE_RATE)

                    audio_bytes = base64.b64decode(audio_b64)
                    audio_int16 = np.frombuffer(audio_bytes, dtype=np.int16)
                    audio_float32 = audio_int16.astype(np.float32) / 32768.0

                    # Basic resampling warning
                    if sample_rate != SAMPLE_RATE:
                        logger.warning(
                            f"Sample rate mismatch: {sample_rate} vs {SAMPLE_RATE}"
                        )

                    transcriber.add_audio(audio_float32)

                elif msg_type == "stop":
                    # Stop the process loop and flush
                    processing = False
                    process_task.cancel()
                    try:
                        await process_task
                    except asyncio.CancelledError:
                        pass

                    # Flush remaining audio
                    result = await transcriber.flush()
                    if result and result.confirmed:
                        await websocket.send_json({
                            "type": "final",
                            "text": result.confirmed,
                        })

                    await websocket.send_json({"type": "stopped"})
                    logger.info("Live transcription stopped, buffer flushed")
                    break

                elif msg_type == "config":
                    language = data.get("language")
                    if language:
                        transcriber.language = language
                        logger.info(f"Language changed to: {language}")

                else:
                    logger.warning(f"Unknown live message type: {msg_type}")

            except json.JSONDecodeError:
                logger.error("Invalid JSON in live WebSocket")
                await websocket.send_json({
                    "type": "error",
                    "message": "Invalid JSON format",
                })
            except Exception as e:
                logger.error(f"Error processing live message: {e}", exc_info=True)
                await websocket.send_json({
                    "type": "error",
                    "message": str(e),
                })

    except WebSocketDisconnect:
        logger.info("Live WebSocket disconnected")
    except Exception as e:
        logger.error(f"Live WebSocket error: {e}", exc_info=True)
    finally:
        processing = False
        process_task.cancel()
        try:
            await process_task
        except asyncio.CancelledError:
            pass
        logger.info("Live WebSocket connection closed")
