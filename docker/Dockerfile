# Digest e não só a tag: `24.04` é reescrita a cada point release do Ubuntu. O
# índice é multi-arch, então o mesmo pin serve as três arquiteturas.
ARG BASE_IMAGE=ubuntu:24.04@sha256:33ceb71981b602c1a7443a53469e4dba065f7503eab3078a2d7a57a2ab987517
FROM ${BASE_IMAGE}

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

ARG DEBIAN_FRONTEND=noninteractive

# DL3008 desligado deliberadamente: pinar versão de pacote `apt` aqui quebraria o
# build assim que o archive do Ubuntu rotacionasse, e as versões que a medição
# realmente toca (FFmpeg, encoders, libvmaf) são pinadas from source logo abaixo.
#
# O symlink do `perf` não é conveniência. O `/usr/bin/perf` do
# `linux-tools-common` é um wrapper que despacha para
# `/usr/lib/linux-tools-$(uname -r)/perf`; dentro do container `uname -r` é o
# kernel do host EC2, que nunca casa com o `linux-tools` da imagem, e o wrapper
# recusa em vez de rodar. `/usr/local/bin` vem antes no PATH, então apontar
# direto para o binário real é o que faz `perf stat` existir na Execução.
# hadolint ignore=DL3008
RUN apt-get update \
  && apt-get install --no-install-recommends -y \
    build-essential \
    ca-certificates \
    cmake \
    curl \
    git \
    jq \
    libnuma-dev \
    linux-tools-common \
    linux-tools-generic \
    meson \
    nasm \
    ninja-build \
    pkg-config \
    sysstat \
    time \
    unzip \
    zlib1g-dev \
  && rm -rf /var/lib/apt/lists/* \
  && perf_bin="$(find /usr/lib -maxdepth 3 -type f -name perf | sort | tail -n 1)" \
  && test -n "${perf_bin}" \
  && ln -s "${perf_bin}" /usr/local/bin/perf

ENV PKG_CONFIG_PATH=/usr/local/lib/pkgconfig

# A flag governa o C genérico. Em aarch64 os arquivos SIMD do x265 e do SVT-AV1
# são compilados a níveis fixos de ISA (`armv8.2-a+dotprod+i8mm`, SVE, SVE2) que
# sobrescrevem o `native`, e a escolha entre eles acontece por dispatch em
# runtime — é como esses projetos entregam NEON/SVE, não algo a corrigir aqui.
ARG NATIVE_FLAGS=-march=native

# A AWS CLI vem do instalador oficial (v2), não do `apt`: o pacote da distro não
# garante nem a major, e é de dentro do container que a Execução sobe os `runs/`
# (ADR-0018).
ARG AWSCLI_VERSION=2.36.38
WORKDIR /tmp/awscli
RUN curl -fsSL "https://awscli.amazonaws.com/awscli-exe-linux-$(uname -m)-${AWSCLI_VERSION}.zip" -o awscli.zip \
  && unzip -q awscli.zip \
  && ./aws/install \
  && rm -rf /tmp/awscli

# O x264 não publica tags — o pin possível é o commit do `stable`, resolvido uma
# vez e congelado aqui.
ARG X264_COMMIT=b35605ace3ddf7c1a5d67a2eb553f034aef41d55
WORKDIR /tmp/x264
RUN git clone https://code.videolan.org/videolan/x264.git . \
  && git checkout --quiet "${X264_COMMIT}" \
  && ./configure \
    --prefix=/usr/local \
    --enable-static \
    --enable-pic \
    --disable-cli \
    --extra-cflags="${NATIVE_FLAGS}" \
  && make -j"$(nproc)" \
  && make install \
  && rm -rf /tmp/x264

# `CMAKE_INSTALL_LIBDIR=lib` aqui e no SVT-AV1 (o `--libdir` do meson faz o mesmo
# pela libvmaf): em Debian/Ubuntu o GNUInstallDirs escolheria `lib/<triplet>`, e
# o caminho de instalação passaria a depender da arquitetura — a última coisa que
# se quer num Dockerfile que precisa se comportar igual nas três.
ARG X265_VERSION=4.2
WORKDIR /tmp/x265
RUN git clone --depth 1 --branch "${X265_VERSION}" https://bitbucket.org/multicoreware/x265_git.git . \
  && cmake -S /tmp/x265/source -B /tmp/x265/build \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_INSTALL_PREFIX=/usr/local \
    -DCMAKE_INSTALL_LIBDIR=lib \
    -DENABLE_SHARED=OFF \
    -DENABLE_CLI=OFF \
    -DCMAKE_C_FLAGS="${NATIVE_FLAGS}" \
    -DCMAKE_CXX_FLAGS="${NATIVE_FLAGS}" \
  && cmake --build /tmp/x265/build -j"$(nproc)" \
  && cmake --install /tmp/x265/build \
  && rm -rf /tmp/x265

ARG SVTAV1_VERSION=v4.1.0
WORKDIR /tmp/svtav1
RUN git clone --depth 1 --branch "${SVTAV1_VERSION}" https://gitlab.com/AOMediaCodec/SVT-AV1.git . \
  && cmake -S /tmp/svtav1 -B /tmp/svtav1/build \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_INSTALL_PREFIX=/usr/local \
    -DCMAKE_INSTALL_LIBDIR=lib \
    -DBUILD_SHARED_LIBS=OFF \
    -DBUILD_APPS=OFF \
    -DBUILD_TESTING=OFF \
    -DCMAKE_C_FLAGS="${NATIVE_FLAGS}" \
    -DCMAKE_CXX_FLAGS="${NATIVE_FLAGS}" \
  && cmake --build /tmp/svtav1/build -j"$(nproc)" \
  && cmake --install /tmp/svtav1/build \
  && rm -rf /tmp/svtav1

ARG VMAF_VERSION=v3.1.0
WORKDIR /tmp/vmaf
RUN git clone --depth 1 --branch "${VMAF_VERSION}" https://github.com/Netflix/vmaf.git . \
  && meson setup /tmp/vmaf/build /tmp/vmaf/libvmaf \
    --prefix=/usr/local \
    --libdir=lib \
    --buildtype=release \
    --default-library=static \
    -Dc_args="${NATIVE_FLAGS}" \
  && ninja -C /tmp/vmaf/build \
  && ninja -C /tmp/vmaf/build install \
  && rm -rf /tmp/vmaf

# `--pkg-config-flags=--static`: os quatro componentes acima são bibliotecas
# estáticas, e sem isso o linker não vê as dependências transitivas
# (`-lstdc++`, `-lnuma`) que o `Libs.private` de cada `.pc` declara. O `-lstdc++`
# ainda vai explícito porque o `libvmaf.pc` do upstream é o único que não a
# declara: o teste de link do configure puxa `svm.cpp.o` de dentro do
# `libvmaf.a` e falha dizendo "libvmaf not found", que aponta para o lugar
# errado.
ARG FFMPEG_VERSION=n8.1.2
WORKDIR /tmp/ffmpeg
RUN git clone --depth 1 --branch "${FFMPEG_VERSION}" https://github.com/FFmpeg/FFmpeg.git . \
  && ./configure \
    --prefix=/usr/local \
    --pkg-config-flags=--static \
    --extra-cflags="${NATIVE_FLAGS}" \
    --extra-cxxflags="${NATIVE_FLAGS}" \
    --extra-libs="-lstdc++" \
    --enable-gpl \
    --enable-libx264 \
    --enable-libx265 \
    --enable-libsvtav1 \
    --enable-libvmaf \
    --disable-shared \
    --enable-static \
    --disable-doc \
    --disable-debug \
  && make -j"$(nproc)" \
  && make install \
  && rm -rf /tmp/ffmpeg

# O `run_scenario.sh` copia este arquivo **verbatim** para o campo `versions` do
# `meta.json` (decisão D3): montá-lo dos mesmos ARGs que compilaram é o que
# impede alguém de derivar a informação parseando `ffmpeg -version`.
ENV VERSIONS_FILE=/opt/transcoding-bench/versions.json
ARG BASE_IMAGE
WORKDIR /opt/transcoding-bench
RUN jq -n \
  --arg base_image "${BASE_IMAGE}" \
  --arg ffmpeg "${FFMPEG_VERSION}" \
  --arg libx264 "${X264_COMMIT}" \
  --arg libx265 "${X265_VERSION}" \
  --arg libsvtav1 "${SVTAV1_VERSION}" \
  --arg libvmaf "${VMAF_VERSION}" \
  --arg aws_cli "${AWSCLI_VERSION}" \
  '$ARGS.named' > "${VERSIONS_FILE}"

# Sem esta checagem, um encoder que o configure aceitou mas não expôs só
# apareceria na hora 30 da campanha. A saída vai para arquivo antes do `grep`
# porque `grep -q` fecha o pipe cedo e o SIGPIPE, sob `pipefail`, derrubaria o
# build por um motivo que não é o testado.
RUN ffmpeg -hide_banner -encoders > /tmp/encoders.txt \
  && grep -q libx264 /tmp/encoders.txt \
  && grep -q libx265 /tmp/encoders.txt \
  && grep -q libsvtav1 /tmp/encoders.txt \
  && ffmpeg -hide_banner -filters > /tmp/filters.txt \
  && grep -q libvmaf /tmp/filters.txt \
  && rm -f /tmp/encoders.txt /tmp/filters.txt \
  && perf --version \
  && pidstat -V \
  && /usr/bin/time --version \
  && jq --version \
  && aws --version

WORKDIR /work
