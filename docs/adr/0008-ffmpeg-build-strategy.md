# Build do FFmpeg a partir do source com flags nativas por arquitetura

FFmpeg e os três encoders (`libx264`, `libx265`, `libsvtav1`) são compilados **from source**, mesmo commit/tag, com `-march=native` em cada arquitetura. Isso garante que cada arch exerce seus SIMD paths nativos (NEON no Graviton 3 — incompleto, ver a emenda abaixo —, AVX-512 no Sapphire Rapids e EPYC Genoa) — que é exatamente o que estamos medindo. Versões de FFmpeg e de cada encoder são pinadas por tag/commit no Dockerfile pra reprodutibilidade.

## Considered Options

- **Pacote de distro (`apt install ffmpeg`)** — rejeitado: flags de compilação podem divergir entre `arm64` e `amd64` de formas não controláveis; versão pode diferir entre repos; não há garantia de que SIMD paths estejam habilitados igualmente.
- **Binário estático pré-compilado (ffmpeg.org / BtbN / John Van Sickle)** — rejeitado: builds de terceiros com flags de compilação que não controlamos e que podem não ser simétricas entre archs. Caixa preta inaceitável pra experimento onde SIMD paths são a variável de interesse.

## Emenda: o binário medido exclui o filtro `ciescope`

O `./configure` do FFmpeg passa `--disable-filter=ciescope`. O binário que o experimento mede **não é o FFmpeg vanilla**, e o motivo é um bug de compilador, não uma escolha de desenho.

Compilando `libavfilter/vf_ciescope.c`, o GCC 13 aborta com um internal compiler error no passo de vetorização (`vectorizable_live_operation`, `tree-vect-loop.cc:9875`). O erro derruba o `docker build` inteiro, e com ele a preparação dos Masters e toda a medição em ARM.

**O que foi observado**, e é o que sustenta a decisão: o mesmo Dockerfile, com a mesma imagem base pinada — portanto o mesmo GCC —, compila sem erro num Apple M1 e falha duas vezes no Graviton 3 (Neoverse V1). x264, x265, SVT-AV1 e VMAF compilam inteiros sob as mesmas flags nas duas máquinas; o que quebra é um laço de um arquivo do FFmpeg.

**O que é inferência, e fica registrada como tal:** a diferença de ISA entre as duas máquinas é o SVE, que o `-march=native` habilita no Graviton 3 e não no M1, e essa é a explicação mais provável. Não foi isolada — um build no Graviton 3 com o SVE explicitamente desligado fecharia a questão e não foi feito. De passagem, isto qualifica o texto acima: o SIMD path nativo do Graviton 3 não é só NEON, é NEON **e SVE**.

**O que não foi testado:** o build em x86. Ele nunca rodou, nem em CI nem localmente, e o ICE é em `tree-vect-loop.cc` — o vetorizador *target-independent* do GCC, não o backend aarch64. A arquitetura decide se o compilador tenta a vetorização que dispara o bug, não se o bug existe; nada garante que o AVX-512 não abra a mesma porta. A primeira medição em `c7i`/`c7a` é que vai responder.

`ciescope` é um filtro de visualização de colorimetria. Nenhum Cenário o invoca: o que o experimento usa são os três encoders, o `scale` e o `libvmaf`. Desligá-lo não altera nenhum caminho de código medido.

Das saídas possíveis, esta é a única que preserva a premissa desta ADR. Baixar o `-march=native` no arm64 criaria flag de compilação assimétrica entre arquiteturas — exatamente o confundidor que a decisão acima existe para evitar. `-fno-tree-vectorize` mudaria o C genérico do FFmpeg nas três arquiteturas, ou seja, mudaria o que está sendo medido. Trocar o compilador por gcc-14 atacaria a classe inteira do bug, e fica como plano B se outros ICEs aparecerem, mas é decisão de maior alcance do que o problema observado exige.

A assimetria que sobra é aceita e é de outra natureza: o filtro some nas três arquiteturas igualmente, porque o Dockerfile é um só.

## Consequences

- O Dockerfile multi-arch (`docker buildx`) compila nativamente em cada arch — mesmo Dockerfile, build diferente por plataforma. Tempo de build é não-trivial (minutos) mas acontece uma vez.
- Reprodutibilidade total: qualquer pessoa com o Dockerfile e as tags pinadas reconstrói o mesmo ambiente.
- `-march=native` significa que o binário ARM não roda em x86 e vice-versa — esperado e desejado.
