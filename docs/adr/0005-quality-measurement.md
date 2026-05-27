# Medição de qualidade como validação amostral

Sob params fixos de encoder (preset, CRF, threading, GOP, pix_fmt) e mesmo thread count auto-detectado em todas as instâncias da matriz (todas com 4 vCPU), a expectativa teórica é que as três arquiteturas produzam outputs **bit-identical** ou quase. Por isso, **qualidade não é variável dependente do experimento; é validação amostral da premissa de iso-qualidade**. Operacionalizada como **Pass de qualidade** no **Juiz** (instância separada, mesma para todos os outputs amostrados — isola variância arquitetural do cálculo da métrica).

Pipeline de validação:

1. **Hash-first triage (sha256, todos os 810 outputs, custo desprezível).** Pra cada grupo `(codec × pair × video × replication)` com 3 outputs (um por arch), se os hashes batem → equivalência total, dispensa VMAF.
2. **SSIM + VMAF nos grupos com hash divergente.** Referência = master de input do cenário em FFV1, Lanczos-downscaled para `output_res`; comparação em `output_res` (não upsampled VMAF). Métricas por output: mean SSIM, mean VMAF, std VMAF, percentil 5 de VMAF por frame. Série por frame de VMAF e SSIM persistida (kilobytes, abre análise post-hoc sem re-rodar).
3. **Amostra metodológica fixa (~6–10 outputs estratificados por codec × output_res) sempre roda SSIM + VMAF**, independente de hash, pra reportar faixa absoluta de VMAF no paper como contexto.

Critério de equivalência por grupo: **VMAF Δ ≤ 0.5 + SSIM Δ ≤ 0.001** (max menos min entre as 3 arch). Resultado reportado como distribuição (`X/162 grupos passaram`), não como gate binário do experimento. Grupos que falham viram subseção de "casos divergentes investigados" no paper, não invalidam o experimento.

## Considered Options

- **Tirar qualidade do escopo inteiramente.** Rejeitado: ainda que confirmatório, ter evidência de iso-qualidade fortalece o claim principal (economia de tempo/custo *em iso-qualidade* > economia de tempo/custo sem qualificar). Custo da validação amostral é baixo.
- **Medição exaustiva (VMAF em todos os 810 outputs).** Rejeitado: redundante. Replicações da mesma instância são bit-identical (ou quase); cross-arch sob params fixos também. Custo de compute alto, valor marginal desprezível vs. amostra.
- **VMAF na mesma instância que transcodificou** (sem Juiz separado). Rejeitado: VMAF tem paths SIMD próprios por arquitetura; rodar em ARM pra outputs ARM e x86 pra outputs x86 introduz variância arquitetural na própria medição da métrica. Juiz único fixa isso e mantém timing de encode limpo.
- **Referência = master 4K canônico sempre** (em vez do master de input do cenário). Rejeitado: penaliza o encoder por informação que ele nunca viu (todo o pre-downscale acumulado). Mede a cadeia inteira, não o encoder isolado. (a1) responde à pergunta certa.
- **Upsampled VMAF (output upscaled para `input_res`).** Rejeitado: faz sentido pra ABR ladder selection (Netflix), não pra validação de fidelidade de encoder.
- **Só VMAF, sem SSIM.** Rejeitado: SSIM é praticamente grátis quando VMAF já tá rodando; dois sinais independentes corroboram a equivalência.
- **Threshold loose (VMAF Δ ≤ 1.0 ou ≤ 2.0).** Rejeitado: thresholds permissivos são pra contextos perceptuais (JND, ABR steps). Pra validação de invariância sob params fixos, expectativa teórica é bit-identity, então threshold tight (≤ 0.5) é o apropriado.
- **Hash-first sem amostra metodológica fixa.** Rejeitado: no caso esperado (a maioria dos grupos passa no hash) ficaríamos sem nenhum valor absoluto de VMAF pra reportar no paper como contexto. Amostra fixa garante.

O Pass de qualidade roda **em batch, após todos os encodes terminarem** nas 3 instâncias. Outputs relevantes são coletados num storage central e o Juiz processa tudo de uma vez. Não há necessidade de orquestração em tempo real entre instâncias de encode e o Juiz — o Pass inteiro (hash check em 810 outputs + VMAF/SSIM nos divergentes e na amostra fixa) cabe em minutos a poucas horas.

## Consequences

- **Qualidade muda de status no artigo.** Deixa de ser variável dependente listada ao lado de tempo/CPU/custo (objetivos específicos linha 103) e vira *validação metodológica*. Os objetivos do `.tex` precisam ser ajustados — esta é a primeira consequência redacional a propagar (consistente com a hierarquia documentada no `CONTEXT.md`: ADR prevalece, artigo se ajusta).
- **Outputs precisam ser retidos** até o Juiz consumir cada grupo. Storage transiente é não-trivial (centenas de arquivos `.mkv`, dezenas a baixas centenas de GB total dependendo de codec/bitrate). Decisão de transporte/storage é da próxima sessão (arquitetura).
- **Juiz é uma instância adicional além das 3 do experimento primário.** Tipo/tamanho/arquitetura do Juiz ficam abertos pra ADR de arquitetura. Premissa: mesma instância pra todos os outputs amostrados.
- **Bit-identity ≠ idêntica em todos os campos.** Container `.mkv` pode ter metadados de mux com timestamps de criação, etc. Hash deve cobrir apenas o bitstream codificado (extraído via `ffmpeg -i ... -c copy -f rawvideo` ou similar), não o container inteiro.
- **Se a validação falhar inesperadamente em vários grupos,** isso é achado relevante por si só sobre não-determinismo do encoder no FFmpeg moderno entre arquiteturas — vale documentar e investigar, não esconder.
