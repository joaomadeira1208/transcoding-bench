# Configuração dos encoders FFmpeg

Três encoders CPU-only com configuração fixa e determinística em todas as execuções e arquiteturas:

| Codec | Encoder | Preset | Rate control |
|---|---|---|---|
| H.264 | `libx264` | `medium` | `-crf 23` |
| H.265 | `libx265` | `medium` | `-crf 28` |
| AV1   | `libsvtav1` | `preset 8` | `-crf 35` |

Configurações comuns a todos: `-threads 0` (auto-detect, deixando assimetria estrutural ARM/x86 emergir), `-g 48` com scene-change keyframes desabilitado (`sc_threshold=0` / `scenecut=0` / `scd=0`) para GOP determinístico, `-pix_fmt yuv420p` explícito, `-an` (áudio strippado), saída em `.mkv`.

### Emenda: o GOP é fixo em frames, e em segundos varia por vídeo

`-g 48` foi justificado como "2 s @ 24 fps" (a linha das opções consideradas abaixo descreve só o Tears of Steel). A versão 4K do Big Buck Bunny só existe a 30 e 60 fps (ADR-0004). Fica a de **30 fps**: é a mais próxima dos 24 fps assumidos, e a de 60 dobraria os frames e o tempo de cada encode, faria o GOP de 48 valer 0,8 s (fora da prática ABR) e tornaria o Big Buck Bunny um workload sistematicamente mais pesado que o Tears of Steel sem que o rótulo dissesse isso.

O invariante que esta ADR protege é o GOP **em frames**, idêntico entre codecs e arquiteturas para um dado Cenário — e ele continua 48. Em segundos, é 2,0 s no Tears of Steel e 1,6 s no Big Buck Bunny, ambos dentro da prática ABR (1–2 s). Um `-g` por vídeo foi rejeitado: adicionaria um parâmetro variável à configuração fixa por um ganho cosmético, e o vídeo já é fator controlado, com geometria (ADR-0023) e bitrate de master (ADR-0004) próprios.

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
