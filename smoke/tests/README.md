# smoke/tests/

O seam do `smoke/`: um só, cobrindo os `run_*.sh`, a forma do `meta.json` e o
layout de `runs/{run_id}/` (ADR-0022).

A asserção central é a do argv do FFmpeg, e o porquê dela está no cabeçalho do
módulo que a faz.
