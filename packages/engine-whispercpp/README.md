# resona-engine-whispercpp

whisper.cpp backend for [Resona](https://github.com/ecsplico/resona), via
[pywhispercpp](https://github.com/abdeladim-s/pywhispercpp).

Runs the GGML Whisper models with hardware acceleration — Metal on Apple
Silicon, Accelerate/BLAS elsewhere. Lower memory footprint than the PyTorch
engines and a strong speedup over CPU CTranslate2 on a Mac, at the same model
size.

Models are GGML names that are downloaded on demand (`large-v3`, `medium`,
`base.en`, …); configure with `DEFAULT_WHISPERCPP_MODEL`. Set
`WHISPERCPP_N_THREADS` to your performance-core count for best throughput.

See the [main repository](https://github.com/ecsplico/resona) for documentation.
