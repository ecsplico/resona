import logging
from typing import TextIO, Union
from whisper.utils import ResultWriter, WriteTXT, WriteSRT, WriteVTT, WriteTSV, WriteJSON

log = logging.getLogger(__name__)


def write_result(result: dict, file: TextIO, output: Union[str, None]):
    """Writes the transcription result to a file in the specified format."""
    options = {
        'max_line_width': 1000,
        'max_line_count': 10,
        'highlight_words': False
    }
    if output == "srt":
        WriteSRT(ResultWriter).write_result(result, file=file, options=options)
    elif output == "vtt":
        WriteVTT(ResultWriter).write_result(result, file=file, options=options)
    elif output == "tsv":
        WriteTSV(ResultWriter).write_result(result, file=file, options=options)
    elif output == "json":
        WriteJSON(ResultWriter).write_result(result, file=file, options=options)
    elif output == "txt":
        WriteTXT(ResultWriter).write_result(result, file=file, options=options)
    else:
        log.warning(f"Invalid output format specified: {output}. Defaulting to text.")
        WriteTXT(ResultWriter).write_result(result, file=file, options=options)
