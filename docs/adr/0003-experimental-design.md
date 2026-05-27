# Desenho experimental — cenário, execução, replicação

A unidade de análise é o **Cenário** (tupla `codec × input_res × output_res × vídeo × instância`). Cada cenário é executado 6 vezes consecutivas **na mesma instância EC2**; a primeira é descartada (warm-up para cache CPU e estabilização de frequência), as 5 seguintes são as replicações reportadas, permitindo cálculo de média + desvio padrão + intervalo de confiança 95% via t-Student.

Inputs são **vídeos inteiros** (Big Buck Bunny ~9,5 min, Tears of Steel ~12,2 min), não clips. A justificativa é metodológica: medição em regime estacionário (onde diferenças arquiteturais — cache, SIMD path, branch predictor — realmente se manifestam) exige que warm-up vire fração pequena do encode; em clips curtos, warm-up domina.

## Considered Options

- **Clips curtos (60s) como input primário** — rejeitado: encoder não atinge regime estacionário; cache warm-up e estado interno do rate control viram fração dominante da medição, contaminando a comparação arquitetural. Análise por complexidade de cena via clips fica como sub-experimento opcional.
- **3 replicações** — rejeitado: insuficiente para CI confiável (2 graus de liberdade).
- **10 replicações** — rejeitado: overkill para a variância baixa do workload em instância dedicada (CV típico < 5%). Custo extra não compra precisão proporcional.
- **Sem warm-up run (todas as 5 contam)** — rejeitado: primeira execução em instância fria é sistematicamente mais lenta; sem descarte, o viés inflaria a média.
- **Instâncias diferentes por replicação** — rejeitado para experimento primário: captura heterogeneidade entre hosts físicos (relevante), mas dobra ou quintuplica custo de provisioning. Fica como sub-experimento opcional.

Cada tipo de instância (`c7g`, `c7i`, `c7a`) é provisionado **uma vez**, e todos os ~54 cenários daquele tipo rodam na mesma instância EC2. Isso mantém o host físico constante (isolando a "loteria de hardware" como constante, não variável) e é coerente com o warm-up por cenário — trocar de host entre cenários resettaria o estado térmico/frequência que o warm-up existe pra estabilizar.

Dentro de cada instância, a **ordem entre cenários é randomizada** (com seed persistido em `meta.json`). O bloco de 6 execuções consecutivas por cenário permanece atômico — randomiza-se a ordem dos blocos, não das replicações internas. Isso evita que efeitos temporais do host (aquecimento térmico, migração de VM, throttling) se correlacionem sistematicamente com codec ou resolução.

## Consequences

- Variância intra-execução (cenas estáticas vs. ação dentro do mesmo vídeo) entra como parte do workload medido, não como ruído a eliminar — é o que pipelines de produção enfrentam.
- "Loteria de hardware" (qual host físico AWS você cai) não é capturada — todas as replicações de um cenário caem no mesmo host. Trade-off consciente.
