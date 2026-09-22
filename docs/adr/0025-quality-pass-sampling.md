# A amostragem do Pass de qualidade

A ADR-0005 desenhou o Pass sobre grupos `(codec × par × vídeo × Replicação)` — 270 na campanha, de 3 outputs cada — mais uma amostra metodológica fixa de 6 a 10 outputs estratificados. O piloto mediu o que aquele desenho supunha, e o que ele mediu muda a unidade de amostragem.

**O eixo de Replicação colapsa.** As 5 Replicações de uma mesma Instância deram bitstreams **bit-idênticos** nos 18 pares Cenário × arquitetura do piloto, sem exceção. Julgar por Replicação computaria cinco vezes o mesmo número, ao custo de cinco VMAF sobre 4K.

**A unidade passa a ser o Cenário**, `(codec, input_res, output_res, video)`: 54 grupos na campanha, 6 no piloto. Dentro do grupo, **cada bitstream distinto é julgado uma vez**, seja qual for a arquitetura que o produziu e quantas Replicações o compartilhem. O VMAF de um bitstream é o VMAF de toda Execução que o produziu — por definição, não por aproximação —, e a atribuição na análise é a junção por `output_sha256`, que o Parquet principal já carrega (ADR-0007).

A campanha fica entre 54 e 162 julgamentos; o piloto extrapola para ~120.

## Por que não "ARM à parte, x86 juntos"

O piloto sugere a regra e a contradiz no mesmo dado. Em 25 dos 30 grupos por Replicação, Intel e AMD produzem o mesmo bitstream e o ARM fica à parte — dois julgamentos bastariam. Nos 5 do `libx265` sobre Tears of Steel as **três** arquiteturas divergem. Uma regra escrita sobre a ISA acertaria 25 casos e erraria 5 em silêncio, reportando como equivalência o que é a média de dois bitstreams diferentes.

Contar bitstreams distintos não tem esse ramo: onde os três diferem, os três são julgados; onde dois coincidem, o segundo não custa nada. A regra não sabe o que é ARM.

## A amostra fixa é absorvida

A ADR-0005 previu a amostra estratificada porque, no caso esperado — "a maioria dos grupos passa no hash" —, o Pass terminaria sem nenhum valor absoluto de VMAF a reportar no artigo. O caso esperado não se materializou: **nenhum** dos 30 grupos do piloto passou no hash. E, mais importante, o desenho novo torna a pergunta vazia: **todo** grupo é julgado, inclusive um em que os três bitstreams coincidissem — ali o Pass custa um VMAF só e produz o valor absoluto do mesmo jeito.

Todo Cenário tem VMAF e SSIM a reportar como contexto, sem estratificação à parte e sem um segundo caminho de seleção para manter.

## O representante é determinístico

Entre as Replicações vencedoras que compartilham um bitstream, o julgado é o de menor índice de Replicação da **primeira arquitetura na ordem de declaração de `[[instance]]`** que o produziu. Não por `run_id`, que é UUID; não por `started_at`, que ordena por quem terminou primeiro.

Dois triages sobre o mesmo bucket escrevem o mesmo `plan.json` byte a byte — e o plano não é só o que o Juiz lê: é o que a retenção (ADR-0007) mantém quando apaga as cópias bit-idênticas. Um representante escolhido por sorte tornaria a limpeza irreproduzível.

## A célula divergente é achado, não erro

Se as 5 Replicações de uma Instância **não** forem bit-idênticas num Cenário, a regra acima já julga cada bitstream. O triage **nomeia** a célula no relatório e o `plan.json` a marca. Recusar o Pass esconderia não-determinismo do encoder entre Execuções da mesma máquina, que é exatamente o que a ADR-0005 manda documentar e investigar. A premissa "Replicações são bit-idênticas" passa a ser verificada a cada Pass em vez de assumida a partir do piloto.

## O Juiz é um `c7i.4xlarge`

A ADR-0014 deixou o tipo em aberto e estimou 2–4 h para 10–20 outputs. Com ~120 julgamentos, dezoito deles em 4K sobre 19 036 e 17 616 frames, um Juiz de 4 vCPU passa de um dia. O `libvmaf` escala com threads, e o preço da EC2 é proporcional ao tamanho: **16 vCPU fazem o Pass em horas pelo mesmo custo** (~US$ 5–8), porque quatro vezes o preço-hora sobre um quarto do tempo é a mesma fatura.

x86 porque os caminhos SIMD do `libvmaf` são os mais exercitados ali. É decisão **operacional** e não experimental — o Juiz não entra na comparação entre arquiteturas —, e o requisito metodológico da ADR-0005 continua sendo o mesmo: a mesma instância para todos os outputs julgados. O `judge.json` registra tipo e `instance_id` para reprodutibilidade.

## O que a definição declara

A tabela `[quality]` do `config/experiment.toml`, copiada idêntica no `pilot.toml`:

| campo | valor | por quê está na definição |
|---|---|---|
| `vmaf_model` | `vmaf_v0.6.1` | o modelo decide a escala do número que o artigo reporta; o Juiz nunca o escolhe |
| `vmaf_delta_max` | `0.5` | o limiar de equivalência da ADR-0005, sobre o máximo menos o mínimo das médias do grupo |
| `ssim_delta_max` | `0.001` | idem, no segundo sinal |
| `judge.instance_type` | `c7i.4xlarge` | o tipo que o lançamento pede e o `judge.json` registra |
| `judge.arch` | `x86_64` | a AMI sai da arquitetura, e nenhum dos dois campos se deriva do outro |

O `scale_flags` da referência **não** entra: é o do `[encode]`, e medir o encoder exige que a referência desça pelo mesmo filtro que produziu o output (ADR-0005). Uma segunda declaração dele divergiria em silêncio do filtro real.

O validador exige a tabela e cada campo, sem default, e a guarda de subconjunto do CI a compara como compara `[encode]`. O objeto de run, o `meta.json`, o argv do encode e os planos de Cenários não mudam: a emenda é **classe 2 da ADR-0021**, e o piloto não se repete.

## Considered Options

- **Manter os 270 grupos por Replicação** — rejeitado: computa cinco vezes o mesmo número. O piloto mediu bit-identity intra-célula em 18 de 18 casos, e os cinco VMAF idênticos custariam horas de 4K por nenhuma informação.
- **Amostrar uma Replicação por célula em vez de colapsar o eixo** — rejeitado por ser a mesma coisa dita de forma que esconde a verificação: colapsar por hash julga uma Replicação *e* detecta a célula em que a premissa falha. Amostrar assumiria a premissa e nunca a testaria.
- **Julgar por arquitetura (3 por Cenário, sempre)** — rejeitado: 162 julgamentos fixos, dos quais ~50 seriam o mesmo bitstream computado duas vezes. A contagem por hash dá o mesmo resultado e pára onde não há o que medir.
- **A regra "ARM à parte, x86 juntos"** — rejeitado pelo dado do piloto: erra os 5 grupos do `libx265` sobre Tears of Steel, e erra em silêncio.
- **Manter a amostra metodológica fixa ao lado** — rejeitado: um segundo caminho de seleção, com estratificação própria a manter, para responder a uma pergunta que o desenho novo já responde em **todos** os 54 grupos em vez de em 6 a 10.
- **Recusar o Pass quando uma célula tem dois bitstreams** — rejeitado: é achado, e esconder não-determinismo do encoder contraria a ADR-0005.
- **Representante escolhido pelo menor `run_id`** — rejeitado: determinístico e arbitrário. A ordem de `[[instance]]` é dado da definição, e é a mesma que ordena o plano canônico (ADR-0019).
- **Um Juiz `c7i.xlarge`, como as instâncias de encode** — rejeitado: ~120 julgamentos a 4 vCPU passam de um dia de relógio pela mesma fatura de um `4xlarge` que os faz em horas. O tamanho do Juiz não é variável do experimento.
- **Um Juiz ARM** — rejeitado: os caminhos SIMD do `libvmaf` são menos exercitados em `aarch64`, e o Pass não tem nada a ganhar em provar isso. Escolher x86 não enviesa a comparação, porque a métrica é calculada pela mesma máquina para todo output.
- **Modelo e limiares como constantes do `run_quality.sh`** — rejeitado pelo motivo de sempre (ADR-0019): o modelo com que o artigo reporta VMAF é anexo da definição, não linha de bash.

## Consequences

- O `quality_triage.py` agrupa por Cenário e conta bitstreams distintos; o histograma (1, 2, 3 bitstreams por grupo) é o relatório que diz quanto o Pass vai custar antes de o Juiz subir.
- A retenção da ADR-0007 ganha o predicado que este desenho impõe: mantém-se **um `output.mkv` por bitstream distinto julgado com sucesso**, o representante do plano, e apagam-se as cópias bit-idênticas.
- A tabela por Cenário do `analysis/` reporta `X/54 grupos equivalentes` como distribuição, não como gate — a ADR-0005 não muda nisto.
- O `c7i.4xlarge` entra na allowlist de `ec2:InstanceType` da ADR-0016. Nenhuma outra linha da matriz IAM muda.
- O item 5 da checklist do piloto (ADR-0022) passa a ser enunciado em grupos por Cenário e bitstreams distintos, e não em "3 outputs por grupo e a amostra".
- A premissa de bit-identity intra-célula deixa de ser pressuposto do desenho e passa a ser saída do Pass: cada execução do triage a reafirma ou nomeia onde ela falhou.
