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

### Emenda: as fontes, pinadas

"Provavelmente H.264 dos repos Blender" (consequências, abaixo) vira arquivo nomeado, e o "provavelmente" fica confirmado. Os dois sources 4K são os que `download.blender.org` publica (empacotados em `.zip`), inspecionados com o `ffprobe` da imagem de medição (ADR-0018):

| Vídeo | Arquivo | URL (ver a emenda abaixo: o host mudou) | Tamanho | sha256 |
|---|---|---|---|---|
| Big Buck Bunny | `bbb_sunflower_2160p_30fps_normal.mp4` | `https://download.blender.org/demo/movies/BBB/bbb_sunflower_2160p_30fps_normal.mp4.zip` | 633 016 449 B | `37f0ff251a606c2dcfa26c19fe6bf843234b4e7a8889cfab50bc26f644e55520` |
| Tears of Steel | `tearsofsteel_4k.mov` | `https://download.blender.org/demo/movies/ToS/tearsofsteel_4k.mov.zip` | 6 737 592 810 B | `89b7fd21c7729b7d5071af993939997f847b5af06613a3388ba158dae9e52ab3` |

| Propriedade | Big Buck Bunny | Tears of Steel |
|---|---|---|
| Vídeo | H.264 High, `yuv420p` | H.264 High, `yuv420p` |
| Geometria | 3840x2160 (16:9) | 3840x1714 (~2.24:1) |
| Frame rate | 30 fps | 24 fps |
| Frames / duração do container | 19 036 / 634,6 s | 17 616 / 734,0 s |
| Bitrate | ~8,0 Mb/s | ~73,4 Mb/s |
| Áudio | MP3 + AC-3 (descartado, `-an`) | AAC (descartado, `-an`) |

O sha256 é o do arquivo **descomprimido**, que é o que o bootstrap valida depois do `unzip`. Os masters 4K entram em `masters/` reempacotados em Matroska com `-c copy` (sem re-encode), sob os nomes que o plano já fixa — `bbb_2160p.mkv`, `tos_2160p.mkv` —, e os derivados (`*_1080p.mkv`, `*_720p.mkv`) saem do downscale Lanczos FFV1 descrito acima nas geometrias da ADR-0023.

Duas escolhas que os arquivos impuseram, cada uma decidida na sua ADR: a versão 4K do Big Buck Bunny ("Sunflower") só existe a 30 e 60 fps, e o motivo de ficar com a de 30 está na ADR-0002; o Tears of Steel 4K encodado tem 3840 de largura, não 4096, e a geometria recomputada está na ADR-0023.

Os bitrates dos dois masters 4K são muito diferentes (~8 contra ~73 Mb/s): o Big Buck Bunny chega mais comprimido, e é uma propriedade do conteúdo que o Experimento recebe, igual ao aspecto e ao frame rate. O vídeo é fator controlado, não eixo de comparação — para um dado Cenário, as três arquiteturas recebem o mesmo master.

### Emenda: o download sai de um espelho, porque a origem recusa a AWS

O `config/experiment.toml` baixa as duas fontes de `https://ftp.nluug.nl/pub/graphics/blender/demo/movies/`, e não do `download.blender.org` da tabela acima.

O motivo não é preferência. O `download.blender.org` está atrás do Cloudflare, e ele **responde 403 a faixas de IP de datacenter**: da máquina do pesquisador o download funciona, da EC2 volta a página "Just a moment...". O `curl -fsSL` do `masters/prepare.sh` sai com 22 no primeiro vídeo, e a preparação morre sete segundos depois de começar. Nenhum header resolve — testados User-Agent de browser, `Accept`/`Accept-Language` e HTTP/1.1 forçado, todos 403 —, porque o bloqueio é por ASN. Derrotar o desafio não é opção: seria frágil e não é o que se faz.

**O que autoriza a troca é o pin, não a confiança no espelho.** `sha256` e `size` da tabela acima são a identidade do arquivo; a URL é endereço de entrega. O `prepare.sh` confere o sha256 do arquivo descomprimido antes de gerar qualquer Master e sai com `source_error` nomeando o vídeo se divergir — um espelho errado custa tempo de download, nunca medição contaminada.

A equivalência foi verificada por hash sobre o conteúdo, nas duas pontas, e não por `ETag` ou tamanho:

| | origem, do Mac | espelho, da EC2 | pin acima |
|---|---|---|---|
| Big Buck Bunny | `37f0ff25…55520` | `37f0ff25…55520` | `37f0ff25…55520` |
| Tears of Steel | `89b7fd21…52ab3` | `89b7fd21…52ab3` | `89b7fd21…52ab3` |

Nos dois, `unzip -l` confirma um único membro, com o tamanho interno igual ao da tabela. O `.zip` do Tears of Steel é **Zip64** (6,7 GB, acima do limite de 4 GB do ZIP clássico): o `unzip` do Info-ZIP que o `prepare.sh` usa o lê sem problema, mas ferramentas de streaming como o `funzip` não — truncam e produzem um hash de aparência legítima sobre dados parciais.

O NLUUG é um dos espelhos oficiais da Blender; Clarkson, dotsrc e RWTH Aachen servem os mesmos arquivos com `ETag` idêntico e são substitutos se ele cair. Trocar de espelho não precisa de emenda nova — trocar o **pin** precisaria, porque aí o arquivo medido teria mudado.

## Considered Options

- **4K como único input** — rejeitado: viesa o estudo para "premium content workload"; produção real recebe masters em várias resoluções (4K para originais; 1080p para a maioria do conteúdo legado e UGC; 720p para conteúdo mais antigo/mobile). Variar `input_res` aumenta validade externa.
- **Multi-source nativo (encoding na mesma resolução, sem downscale)** — rejeitado: não é transcoding, é re-encoding. Sai do escopo da pergunta de pesquisa.
- **Baixar versões canônicas em cada resolução de peach.blender.org / mango.blender.org** — rejeitado: cada versão tem codec/qualidade de origem diferente, introduzindo variável de confusão na qualidade do master. Pre-downscale local garante que os 3 masters de um vídeo são equivalentes em conteúdo (só resolução muda).
- **Algoritmo `bicubic` (default FFmpeg) ou `bilinear`** — rejeitado: Lanczos é o que pipelines ABR de produção usam para downscale; mais ressonante com workload-alvo.
- **Manter clip original H.264 como master** — rejeitado para o pre-downscale: queremos eliminar variabilidade de codec de origem nos masters derivados. FFV1 lossless garante que decode dos masters é equivalente, e a única variável é a resolução.

## Consequences

- Master 4K vem da versão canônica encodada (provavelmente H.264 dos repos Blender); masters 1080p/720p são FFV1 lossless. **Decode dos três masters não é idêntico em CPU cost** — H.264 decode é diferente de FFV1 decode. Para os encoders pesados (`libx265 medium`, `libsvtav1 8`), decode é < 5% do tempo total, então o efeito é pequeno mas presente. Documentado aqui pra futura referência.
- Storage do experimento precisa caber masters FFV1 (potencialmente dezenas de GB por vídeo). Gerenciável, mas não-trivial.
