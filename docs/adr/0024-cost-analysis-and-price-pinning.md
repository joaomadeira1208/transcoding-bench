# Análise de custo e o pin do preço

Custo é **métrica derivada, calculada na camada de análise**, e o preço por hora é **fato externo pinado por consulta** em `analysis/prices.toml` — nunca coletado durante a Execução, nunca coluna do `meta.json` nem do Parquet.

O critério é se o dado é destruído quando não capturado no momento do run. Os contadores de PMU, o tempo decorrido e o `ffmpeg_fps` são: a janela passou, e o que não foi lido ali não se recupera. O preço não é: é consulta, respondida igual hoje e em 2027 desde que se saiba **quando** perguntar. Por isso ele mora do lado da análise, junto das outras derivações (`ipc`, `branch_mispredict_rate`), e não do lado da coleta.

São **dois** custos, e confundi-los produz número errado:

| | o que mede | como sai |
|---|---|---|
| **Custo por Cenário** — variável dependente (ADR-0006) | só o encode daquele run | `time_elapsed_s × usd_per_hour / 3600`, sobre o Parquet |
| **Custo operacional** — relatório e orçamento (ADR-0012) | a fatura da AWS | horas faturadas × preço, mais EBS e IPv4 |

O primeiro é a comparação entre arquiteturas e exclui bootstrap, ociosidade entre runs e a janela até o poll que termina a instância. O segundo é sempre maior — no piloto, cada Instância faturou ~25 min além do tempo de encode — e é o que diz quanto a campanha custa.

## O pin, e quando refazê-lo

Cada consulta entra como uma seção `[[quote]]` nova, com `consulted_at` e `covers`. **A consulta do piloto não é sobrescrita pela da campanha**: o relatório do piloto cita um custo, e esse número tem de continuar reproduzível depois de a AWS mudar a tabela. A análise escolhe a seção pelo `covers` do lançamento que ela está analisando.

**Antes de disparar a campanha, refaça a consulta e acrescente a seção.** Se os preços não tiverem mudado, a seção nova é idêntica na substância e diferente no `consulted_at` — e é isso que se quer registrar, não economizar linhas. Os filtros da consulta são `operatingSystem=Linux`, `preInstalledSw=NA`, `tenancy=Shared`, `capacitystatus=Used` e `location=US East (N. Virginia)`; qualquer um deles omitido devolve a primeira de várias tabelas, silenciosamente.

Como o preço por hora é praticamente proporcional entre as três arquiteturas, o custo por Cenário é o tempo reescalado, e **não é um quarto resultado independente**. O que o artigo reporta junto do número é o **break-even**: a razão de preço em que o ranking inverte. "A c7g é mais barata que a c7a enquanto custar menos de 0,72× o preço-hora dela" sobrevive a qualquer revisão de tabela da AWS; "a c7g custa 2% menos" não.

## O custo operacional por lançamento

As tags `role` e `commit` foram ativadas como cost allocation tags em 2026-09-15. Elas **não são retroativas**: o custo operacional do piloto é calculado à mão, das horas de lançamento e término, e a Cost Explorer entra só como conferência por tipo de instância na janela de datas. Da campanha em diante, `--group-by Type=TAG,Key=commit` isola o lançamento de tudo o mais na conta sem depender de janela — o `commit` serve como discriminador porque o Orquestrador já o aplica a cada Instância de encode (ADR-0021), e piloto e campanha rodam SHAs diferentes. Ativar a tag é ajuste do lado do faturamento: nenhum código muda, porque as Instâncias já nascem etiquetadas.

**A consulta pede `RECORD_TYPE = Usage`, e sem ele devolve zero.** A conta tem crédito promocional, e a Cost Explorer abate o crédito do `UnblendedCost` por default — a consulta sem o filtro devolveu `US$ 0,0000` em toda linha do piloto, sobre 22 h de instância que existiram. É a mesma armadilha que a camada 3 da ADR-0012 descreve para o `aws_budgets_budget`, do outro lado: lá o teto mediria líquido, aqui a conferência mede líquido. Nos dois casos o que se quer é **consumo**, porque é ele que o orçamento calibrou e é ele que o crédito, sendo finito, apenas adia.

    aws ce get-cost-and-usage --granularity DAILY \
      --metrics UnblendedCost UsageQuantity \
      --time-period Start=<início> End=<fim> \
      --group-by Type=TAG,Key=commit \
      --filter '{"Dimensions":{"Key":"RECORD_TYPE","Values":["Usage"]}}'

O sintoma de esquecê-lo não é erro: é zero, que se lê como "não custou nada".

## Considered Options

- **Preço como campo do `meta.json`** — rejeitado por três motivos somados: seria constante repetida em 810 linhas; o `run_scenario.sh` teria de conhecê-lo, o que significa `pricing:GetProducts` no papel `encode` por razão que não é medição (ADR-0016); e o modelo pydantic é estrito, então o campo novo é `schema_version` novo, fixture-âncora invalidada e definição divergente da que o piloto aprovou (ADR-0022).
- **Preço como coluna do Parquet, injetada na consolidação** — rejeitado: o `consolidate.py` projeta `runs/` e nada mais (ADR-0007), e dar a ele uma tabela de preços o faria falhar por rede ou por arquivo ausente numa etapa que hoje só depende do disco.
- **Uma única seção, sobrescrita a cada consulta** — rejeitado: apaga o preço sob o qual o piloto foi reportado, e o relatório commitado passa a citar um número que o repo não sustenta mais.
- **Cost Explorer como fonte primária do custo por Cenário** — rejeitado: ela fatura instância-hora, não Execução, e não separa encode de bootstrap nem de ociosidade. Serve ao custo operacional, e mesmo lá com ~24 h de atraso.
- **Preço de spot** — rejeitado para o experimento: preço variável no tempo tornaria o custo por Cenário função de quando o run caiu na fila, introduzindo na variável dependente uma fonte de variação que não é arquitetural.

## Consequences

- A análise de custo roda inteiramente pós-hoc, sobre o Parquet mais um arquivo de três linhas. Nenhuma alteração no código da campanha, e nada a recoletar se o preço for corrigido depois.
- O artigo reporta tempo e custo separadamente e diz qual é qual: o tempo mede a arquitetura; o custo mede a arquitetura **mais** a política de preços da AWS naquela data.
- `analysis/prices.toml` cresce um bloco por lançamento. É o registro da tabela sob a qual cada conjunto de números foi publicado.
- O custo operacional do piloto fica menos preciso que o da campanha, porque as tags não alcançam o passado. É perda de uma medida de relatório, não de variável dependente.
