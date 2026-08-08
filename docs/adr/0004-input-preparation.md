# Preparação dos vídeos de entrada (masters)

Três resoluções de **Master** são usadas como input: **4K (2160p)**, **1080p** e **720p**, gerando 9 pares input → output (sempre downscale ou no-scale; nunca upscale). Os masters 1080p e 720p são gerados localmente, **uma única vez como etapa de bootstrap do experimento**, por downscale Lanczos lossless (FFV1) do master 4K canônico. Cada execução depois consome o master que corresponde à `input_res` do seu cenário.

Pares input → output do experimento primário:

| Input (`input_res`) | Outputs (`output_res`) |
|---|---|
| 2160p | 2160p, 1080p, 720p, 480p |
| 1080p | 1080p, 720p, 480p |
| 720p | 720p, 480p |

O scaling em execução (master → output_res) também usa Lanczos: `-vf scale=W:H:flags=lanczos`.

Os tiers acima são **rótulos nominais**, não dimensões: os dois vídeos têm aspect ratios diferentes, e a geometria exata de cada tier por vídeo é decidida na ADR-0023.

## Considered Options

- **4K como único input** — rejeitado: viesa o estudo para "premium content workload"; produção real recebe masters em várias resoluções (4K para originais; 1080p para a maioria do conteúdo legado e UGC; 720p para conteúdo mais antigo/mobile). Variar `input_res` aumenta validade externa.
- **Multi-source nativo (encoding na mesma resolução, sem downscale)** — rejeitado: não é transcoding, é re-encoding. Sai do escopo da pergunta de pesquisa.
- **Baixar versões canônicas em cada resolução de peach.blender.org / mango.blender.org** — rejeitado: cada versão tem codec/qualidade de origem diferente, introduzindo variável de confusão na qualidade do master. Pre-downscale local garante que os 3 masters de um vídeo são equivalentes em conteúdo (só resolução muda).
- **Algoritmo `bicubic` (default FFmpeg) ou `bilinear`** — rejeitado: Lanczos é o que pipelines ABR de produção usam para downscale; mais ressonante com workload-alvo.
- **Manter clip original H.264 como master** — rejeitado para o pre-downscale: queremos eliminar variabilidade de codec de origem nos masters derivados. FFV1 lossless garante que decode dos masters é equivalente, e a única variável é a resolução.

## Consequences

- Master 4K vem da versão canônica encodada (provavelmente H.264 dos repos Blender); masters 1080p/720p são FFV1 lossless. **Decode dos três masters não é idêntico em CPU cost** — H.264 decode é diferente de FFV1 decode. Para os encoders pesados (`libx265 medium`, `libsvtav1 8`), decode é < 5% do tempo total, então o efeito é pequeno mas presente. Documentado aqui pra futura referência.
- Storage do experimento precisa caber masters FFV1 (potencialmente dezenas de GB por vídeo). Gerenciável, mas não-trivial.
