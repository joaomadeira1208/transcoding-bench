# Build do FFmpeg a partir do source com flags nativas por arquitetura

FFmpeg e os três encoders (`libx264`, `libx265`, `libsvtav1`) são compilados **from source**, mesmo commit/tag, com `-march=native` em cada arquitetura. Isso garante que cada arch exerce seus SIMD paths nativos (NEON no Graviton 3, AVX-512 no Sapphire Rapids e EPYC Genoa) — que é exatamente o que estamos medindo. Versões de FFmpeg e de cada encoder são pinadas por tag/commit no Dockerfile pra reprodutibilidade.

## Considered Options

- **Pacote de distro (`apt install ffmpeg`)** — rejeitado: flags de compilação podem divergir entre `arm64` e `amd64` de formas não controláveis; versão pode diferir entre repos; não há garantia de que SIMD paths estejam habilitados igualmente.
- **Binário estático pré-compilado (ffmpeg.org / BtbN / John Van Sickle)** — rejeitado: builds de terceiros com flags de compilação que não controlamos e que podem não ser simétricas entre archs. Caixa preta inaceitável pra experimento onde SIMD paths são a variável de interesse.

## Consequences

- O Dockerfile multi-arch (`docker buildx`) compila nativamente em cada arch — mesmo Dockerfile, build diferente por plataforma. Tempo de build é não-trivial (minutos) mas acontece uma vez.
- Reprodutibilidade total: qualquer pessoa com o Dockerfile e as tags pinadas reconstrói o mesmo ambiente.
- `-march=native` significa que o binário ARM não roda em x86 e vice-versa — esperado e desejado.
