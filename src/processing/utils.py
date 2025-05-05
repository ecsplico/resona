from threading import Lock
from typing import BinaryIO, Union, TextIO
import re 

import ffmpeg
import numpy as np
import torch
import logging
import whisper
from whisper.utils import ResultWriter, WriteTXT, WriteSRT, WriteVTT, WriteTSV, WriteJSON
from decouple import config
from faster_whisper import WhisperModel
from timeit import default_timer as timer
from transformers import pipeline, AutoTokenizer, AutoModelForSequenceClassification
from datetime import datetime

from sqlmodel import Field, Session, create_engine, select
from ..model import Job, engine, Replacement
from ..paths import DATA_PATH, MD_PATH

SAMPLE_RATE=16000
MODEL_NAME: str = config("ASR_MODEL", cast=str)  # type: ignore
MODE:str = config("ASR_MODE") # type: ignore

model_lock = Lock()

log=logging.getLogger('uvicorn.test')


class WhisperTranscriber:
    def __init__(self, device: str = "cpu"):
        self.model = whisper.load_model(MODEL_NAME, device=device)

    def get_model(self):
        return self.model

    def transcribe(self, audio: np.ndarray, **options_dict):
        self.model.transcribe(audio, **options_dict)

class TransformerTranscriber:
    def __init__(self, device: str = "cpu"):
        self.model = pipeline("automatic-speech-recognition", model=MODEL_NAME, device=device, chunk_length_s=30)
    
    def get_model(self):
        return self.model.model

    def transcribe(self, audio: np.ndarray, **options_dict):
        output = self.model(audio, batch_size=1, return_timestamps=True)
        log.info(f"Output: {output}")
        result = {
            "language": "de",
            "segments": output["chunks"],
            "text": output["text"],
        }
        return result

class FastWhisperTranscriber:
    def __init__(self, device: str = "cpu"):
        compute_type = "int8_float16" if device == "cuda" else "int8"
        self.model = WhisperModel(MODEL_NAME, device=device, compute_type=compute_type)

    def get_model(self):
        return self.model

    def transcribe(self, audio: np.ndarray, **kwargs) -> dict:
        segments = []
        text = ""
        segment_generator, info = self.model.transcribe(audio, beam_size=5, **kwargs)
        for segment in segment_generator:
            segments.append(segment)
            text = text + segment.text
        result = {
            "language": info.language,
            "segments": segments,
            "text": text,
        }
        return result

def getTranscriber():
    device = "cuda" if torch.cuda.is_available() and False else "cpu"
    log.info(f'Loading model {MODEL_NAME} into {device} using {MODE}')
    if MODE == "faster-whisper":
        t = FastWhisperTranscriber(device=device)
    elif MODE == "whisper-tf":
        t = TransformerTranscriber(device=device)
    else:
        t = WhisperTranscriber(device=device)
    return t

def run_asr(file: Union[str, BinaryIO], markdown: bool = True, task: str = "transcribe", language: str = "de" ) -> dict:
    # test if file is a string or a file object
    if isinstance(file, str):
        audio = whisper.load_audio(file)
    else:
        audio = load_audio(file)

    options_dict = {"task" : task, "language": language}

    with model_lock:
        T = getTranscriber()
        start=timer()
        result: dict = T.transcribe(audio, **options_dict)            
        duration = timer()-start
        log.info(f"ASR took {duration} seconds")

        if markdown is True:
            log.info("Converting result to markdown")
            result["md"] = toMarkdown(result["text"])

    return result

def load_audio(file: BinaryIO, encode=True, sr: int = SAMPLE_RATE):
    """
    Open an audio file object and read as mono waveform, resampling as necessary.
    Modified from https://github.com/openai/whisper/blob/main/whisper/audio.py to accept a file object
    Parameters
    ----------
    file: BinaryIO
        The audio file like object
    encode: Boolean
        If true, encode audio stream to WAV before sending to whisper
    sr: int
        The sample rate to resample the audio if necessary
    Returns
    -------
    A NumPy array containing the audio waveform, in float32 dtype.
    """
    if encode:
        try:
            # This launches a subprocess to decode audio while down-mixing and resampling as necessary.
            # Requires the ffmpeg CLI and `ffmpeg-python` package to be installed.
            out, _ = (
                ffmpeg.input("pipe:", threads=0)
                .output("-", format="s16le", acodec="pcm_s16le", ac=1, ar=sr)
                .run(cmd="ffmpeg", capture_stdout=True, capture_stderr=True, input=file.read())
            )
        except ffmpeg.Error as e:
            raise RuntimeError(f"Failed to load audio: {e.stderr.decode()}") from e
    else:
        out = file.read()

    return np.frombuffer(out, np.int16).flatten().astype(np.float32) / 32768.0


def write_result(
        result: dict, file: TextIO, output: Union[str, None]
):
    options = {
        'max_line_width': 1000,
        'max_line_count': 10,
        'highlight_words': False
    }
    if output == "srt":
        WriteSRT(ResultWriter).write_result(result, file=file)
    elif output == "vtt":
        WriteVTT(ResultWriter).write_result(result, file=file)
    elif output == "tsv":
        WriteTSV(ResultWriter).write_result(result, file=file)
    elif output == "json":
        WriteJSON(ResultWriter).write_result(result, file=file)
    elif output == "txt":
        WriteTXT(ResultWriter).write_result(result, file=file)
    else:
        return 'Please select an output method!'

def write_md_file(id: int, filename:str, md:str, keepfile:bool ):
    # Write a markdown file

    # Try to find name of patient
    # regex für "Verlaufsdokumentation von $1"
    p_match = re.compile(r"[Dd]okumentation von ([^\s]*)").search(md)
    if p_match is not None:
        patient = p_match.group(1)
    else:
        patient = ""
    filepart = id if patient == "" else patient
    date = datetime.now().strftime("%Y-%m-%d")
    date_full = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    with open(f"{MD_PATH}/{date} {filepart} ({id}).md", "w") as file:
        file.write(f"---\ncreated: {date_full}\npatient: {patient}\ndiktiert: true\nready: false\nnexus: false\n---\n\n")
        if keepfile:
            file.write(f"Audio: \n![[{filename}]]\n\n")
        file.write(f"{md}")

def toMarkdown(text):
    # Get a list of replacements from the database
    with Session(engine) as session:
        statement = select(Replacement).where(Replacement.active == True).order_by(Replacement.id=='asc')
        replacements = session.exec(statement).all()
        for r in replacements:
            text = re.compile(r.name, re.IGNORECASE).sub(r.replacement, text)
            # log.info(f"Replacing {r.name} with {r.replacement}")
        return text 


def register_job(filename: str, upload_name:str, keep: bool = True, translate: bool = False):
    """
        Registar a file for processing 
    """
    with Session(engine) as session:
        job = Job(filename=filename, keepfile=keep, translate=translate)
        session.add(job)
        session.commit()
        res = {
            "id": job.id,
            "file": f"/files/{job.filename}",
            "result": f"/asr-async/{job.id}",
        }
        return res

