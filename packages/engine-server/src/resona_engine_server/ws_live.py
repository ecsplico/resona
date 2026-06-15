"""
WebSocket endpoint for live audio transcription with VAD and local agreement.

Protocol:
  Client -> Server:
    {"type": "audio", "data": "<base64 PCM int16>", "sample_rate": 16000}
    {"type": "stop"}
    {"type": "config", "language": "de"}

  Server -> Client:
    {"type": "partial", "text": "unstable text...", "confirmed": "stable text", "delta": "newly confirmed"}
    {"type": "final", "text": "confirmed stable text", "delta": "newly confirmed"}
    {"type": "stopped"}
    {"type": "error", "message": "..."}
    {"type": "keepalive"}

  ``delta`` carries only the words confirmed in this step (never the cumulative
  transcript) so proxies can forward incremental finals without re-deriving them.

Query params: ?language=de selects the transcription language.
"""
import asyncio
import base64
import json
import logging
import numpy as np
from fastapi import WebSocket, WebSocketDisconnect

from resona_asr_core.live_transcriber import LiveTranscriber, SAMPLE_RATE

logger = logging.getLogger(__name__)


def _resample_to_16k(audio: np.ndarray, src_rate: int) -> np.ndarray:
    """Linear-resample a mono float32 waveform to 16 kHz (fallback for non-16k clients)."""
    if src_rate == SAMPLE_RATE or len(audio) == 0:
        return audio
    n_target = int(round(len(audio) / src_rate * SAMPLE_RATE))
    if n_target <= 0:
        return np.array([], dtype=np.float32)
    src_idx = np.linspace(0, len(audio) - 1, n_target)
    return np.interp(src_idx, np.arange(len(audio)), audio).astype(np.float32)


async def live_transcribe_websocket(websocket: WebSocket):
    """Handle a WebSocket connection for live transcription."""
    logger.info("Live WebSocket connection attempt")

    try:
        await websocket.accept()
        logger.info("Live WebSocket accepted")
    except Exception as e:
        logger.error(f"Failed to accept live WebSocket: {e}", exc_info=True)
        raise

    language = websocket.query_params.get("language") or "de"
    transcriber = LiveTranscriber(language=language)
    processing = True

    async def process_loop():
        while processing:
            try:
                await asyncio.wait_for(
                    transcriber._audio_event.wait(),
                    timeout=1.0,
                )
            except asyncio.TimeoutError:
                continue
            transcriber._audio_event.clear()

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
                        "delta": result.confirmed_delta,
                    })
                elif result.confirmed:
                    await websocket.send_json({
                        "type": "final",
                        "text": result.confirmed,
                        "delta": result.confirmed_delta,
                    })
            except WebSocketDisconnect:
                break
            except Exception as e:
                logger.error(f"Process loop error: {e}", exc_info=True)

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

                    if sample_rate != SAMPLE_RATE:
                        audio_float32 = _resample_to_16k(audio_float32, sample_rate)

                    transcriber.add_audio(audio_float32)

                elif msg_type == "stop":
                    processing = False
                    process_task.cancel()
                    try:
                        await process_task
                    except asyncio.CancelledError:
                        pass

                    result = await transcriber.flush()
                    if result and result.confirmed:
                        await websocket.send_json({
                            "type": "final",
                            "text": result.confirmed,
                            "delta": result.confirmed_delta,
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
