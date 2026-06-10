# Configuração dos encoders FFmpeg

Três encoders CPU-only com configuração fixa e determinística em todas as execuções e arquiteturas:

| Codec | Encoder | Preset | Rate control |
|---|---|---|---|
| H.264 | `libx264` | `medium` | `-crf 23` |
| H.265 | `libx265` | `medium` | `-crf 28` |
| AV1   | `libsvtav1` | `preset 8` | `-crf 35` |

Configurações comuns a todos: `-threads 0` (auto-detect, deixando assimetria estrutural ARM/x86 emergir), `-g 48` com scene-change keyframes desabilitado (`sc_threshold=0` / `scenecut=0` / `scd=0`) para GOP determinístico, `-pix_fmt yuv420p` explícito, `-an` (áudio strippado), saída em `.mkv`.

## Considered Options

- **Encoders por hardware (NVENC, QuickSync, VideoToolbox, AMF)** — rejeitado: não comparáveis entre arquiteturas (Graviton não tem equivalente). Tirariam CPU da mesa, que é o objeto de estudo.
- **`libaom-av1` em vez de `libsvtav1`** — rejeitado: ordens de magnitude mais lento, não é o encoder de produção da indústria. SVT-AV1 (Intel/Netflix) é o padrão de fato.
- **Fixar `-threads` explicitamente (ex.: `-threads 4`)** — rejeitado: esconderia a vantagem estrutural de Graviton (4 cores físicos sem SMT) que é exatamente o que se deseja medir.
- **CBR ou two-pass VBR** — rejeitado: two-pass dobra o tempo sem responder à pergunta; CBR esconde a qualidade resultante; CRF fixa um alvo de qualidade por codec, coerente com a premissa de iso-qualidade cross-arch validada pelo Pass de qualidade (ADR-0005).
- **CRF calibrado para iso-qualidade (mesmo VMAF-alvo entre codecs)** — rejeitado: adiciona fase de pré-calibração e responde à pergunta "qual codec é mais eficiente", não "qual arquitetura é mais eficiente".
- **Defaults de GOP por encoder (libx264: 250, libsvtav1: 60)** — rejeitado: workloads sutilmente diferentes entre codecs. `-g 48` (2s @ 24fps) alinha com prática ABR de produção e é idêntico em todos.
- **Variar preset entre execuções** — rejeitado para experimento primário: multiplica matriz e desfoca a pergunta principal.

## Consequences

- Qualidades resultantes vão diferir entre codecs (CRF não é intercambiável). Isso é esperado e não afeta a comparação arquitetural; a faixa absoluta de VMAF/SSIM por codec é reportada como contexto via amostra metodológica fixa do Pass de qualidade (ADR-0005) — qualidade não é variável dependente.
- Configuração é "produção realista", não "encoder agressivo": presets `medium`/`8` exercitam SIMD/cache/branch predictor de forma significativa, expondo diferenças arquiteturais.
