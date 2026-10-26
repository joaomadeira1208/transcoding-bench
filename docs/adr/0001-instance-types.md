# Tipos de instância EC2 para comparação arquitetural

Para isolar a arquitetura de CPU como variável independente, escolhemos três famílias EC2 de mesma geração `c7*` e mesmo tamanho `xlarge` (4 vCPU): **`c7g.xlarge`** (AWS Graviton 3 / ARM Neoverse-V1), **`c7i.xlarge`** (Intel Sapphire Rapids) e **`c7a.xlarge`** (AMD EPYC Genoa). Mesma janela geracional (2022–2023) em todas remove "geração de processo/microarquitetura cross-vendor" como variável de confusão.

## Considered Options

- **Apenas duas famílias (ARM + um x86)** — rejeitado: Intel e AMD têm microarquiteturas distintas (cache, SIMD, branch predictor); agrupá-los como "x86" esconde diferenças relevantes que [matha2021] já mostrou existir.
- **Graviton 2 (`c6g`) pra paridade direta com [matha2021]** — rejeitado: geração ultrapassada; produção atual está em Graviton 3/4. A comparação científica fica mais relevante na geração atual.
- **Tamanhos variados (xlarge + 2xlarge + 4xlarge)** — rejeitado para o experimento primário: multiplica matriz sem responder à pergunta principal. Fica como sub-experimento opcional de escalabilidade.

## Consequences

- A simetria do tamanho `xlarge` (4 vCPU) é estrutural, não comportamental: Graviton 3 entrega 4 cores físicos sem SMT, enquanto Sapphire Rapids e EPYC entregam 2 cores físicos × 2 SMT threads. Essa assimetria é parte do que está sendo medido, não um defeito a corrigir.
- Comparabilidade direta com [matha2021] (que usou Graviton 2 / Skylake / Naples) fica fragilizada — mas o ganho de relevância para gerações atuais compensa.

## Emenda: a `c7a` tem 4 núcleos físicos, e a assimetria é só da Intel

A primeira consequência acima está errada sobre a `c7a`. Pela API da EC2 (`aws ec2 describe-instance-types`, `VCpuInfo`), a `c7g.xlarge` e a `c7a.xlarge` entregam **4 núcleos × 1 thread**, e só a `c7i.xlarge` entrega **2 núcleos × 2 threads** (SMT). A AMD da família `c7a` não expõe SMT ao guest.

A simetria dos 4 vCPUs continua estrutural, mas a assimetria de topologia é só da Intel. A comparação com a `c7i` mistura o núcleo com o número de núcleos físicos: o IPC por thread dela ficou em ~0,66× o da `c7a` na campanha, o que é consistente com SMT e não estava isolado. A ADR-0026 isola o efeito com um experimento de topologia (uma `c7i.2xlarge` com `CpuOptions` 2 × 2 e outra 4 × 1). A escolha dos três tipos não muda.
