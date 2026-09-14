# Modelo de orquestração da pipeline

O experimento é coordenado pelo **Orquestrador** — um script Python rodando numa **instância EC2 dedicada (t3.micro)** na mesma VPC das instâncias de encode. Essa instância é provisionada pelo Terraform junto com a infra base e persiste durante todo o experimento.

O orquestrador **não controla cada execução individualmente**. Em vez disso, cada instância de encode **auto-dirige**: recebe um `scenarios.json` (lista de cenários em ordem randomizada, gerada pelo orquestrador) e executa o loop inteiro sozinha via `run_all.sh`. O orquestrador dispara o processo e depois monitora.

## Comunicação orquestrador → instâncias

**SSH (key pair gerenciado pelo Terraform)** como canal primário. O orquestrador faz `ssh -i key.pem ubuntu@<ip> "bash run_all.sh ..."` (usuário `ubuntu` — AMI Ubuntu 24.04, ADR-0015) pra disparar cada instância.

Security group permite porta 22 apenas do IP da instância do Orquestrador dentro da VPC.

**Emenda: o disparo é desacoplado da sessão SSH.** O `ssh ... "bash run_all.sh ..."` acima é o comando certo dentro da sessão errada. O que o Orquestrador dispara é o `launch_container.sh` sob `setsid`/`nohup`, com stdin de `/dev/null` e as duas saídas num log do work dir da instância; o comando ecoa o PID do processo desacoplado, que vai para um arquivo lá e para o arquivo de estado aqui, e o `ssh` volta em segundos. O SSH continua sendo o canal — o disparo, o `kill -0` de cada poll e o `cloud-init status` do bootstrap passam por ele —, mas nenhuma sessão dura mais que o comando que ela carrega.

## Detecção de término

**SSH bloqueante como primário + marcador S3 como fallback.** O `ssh` bloqueante retorna quando `run_all.sh` termina, dando feedback imediato e exit code. Em paralelo, `run_all.sh` sempre cria um marcador no S3 ao terminar (`s3://bucket/status/${INSTANCE_TYPE}_done`). Se a conexão SSH cair, o orquestrador faz fallback pra polling no S3 a cada 5 minutos.

**Emenda: o SSH bloqueante foi abandonado; a detecção é por polling, sempre.** O esquema acima não entrega a propriedade que esta ADR mais valoriza. Com o `run_all.sh` do outro lado de um `ssh` bloqueante, o stdout e o stderr dele são o canal SSH: a queda da conexão — a t3.micro reiniciando, o `tmux` fechado, o Wi-Fi do pesquisador — fecha o descritor remoto, e o laço morre por SIGPIPE na próxima linha de log. "Se o Orquestrador morrer, o experimento continua" era falso na letra do desenho; o fallback nunca chegava a ser exercido, porque não havia mais processo para vigiar.

O que fica no lugar é o polling a cada 5 minutos — o mesmo intervalo que esta ADR já fixava para o fallback — como caminho **único**, com três perguntas por instância a cada poll: `describe-instances` (a instância existe e está `running`), `kill -0` no PID gravado, por SSH (o processo do disparo vive) e a listagem de `status/` (o marcador e o objeto de progresso apareceram). O `kill -0` existe porque um encode de 4K no x265 leva uma hora, e nesse intervalo o progresso não muda: progresso parado com processo vivo é o caso normal, e progresso parado com processo ausente e sem marcador é morte no meio de uma Execução.

Dos dois motivos pelos quais "apenas polling no S3" foi rejeitado abaixo, o segundo deixou de existir: **o código de saída está no marcador.** Ele passou a ser JSON com `instance_id`, `finished_at`, `runs_total`, `runs_failed`, `capped` e `exit_status` (ADR-0011), de modo que o resultado do `run_all.sh` chega inteiro sem sessão nenhuma aberta. O primeiro — feedback imediato — é o custo aceito, e é o único: a latência de detecção sobe de segundos para no máximo 5 minutos, numa janela de 46 h por arquitetura.

## Randomização

As 3 instâncias usam a **mesma seed** de randomização — mesma sequência de cenários, ritmo diferente (cada arch tem performance distinta). Efeitos temporais (aquecimento, throttling) afetam os mesmos cenários nas 3 archs e cancelam na comparação cross-arch.

## Considered Options

- **Orquestrador roda na máquina local** — rejeitado: experimento leva ~46h por instância (~2 dias). Acoplar ao Mac é arriscado (sleep, queda de Wi-Fi, reboot). Instância do Orquestrador na mesma VPC é estável e custa centavos (~$0.01/h).
- **Orquestrador dirige cada passo via SSH** (324 chamadas por instância) — rejeitado: manter SSH ativo por 46h é frágil. Auto-direção desacopla a instância do orquestrador.
- **Apenas polling no S3 (sem SSH bloqueante)** — rejeitado: perde feedback imediato e exit code. Mix de SSH + S3 dá o melhor dos dois mundos. **Emenda: é a opção adotada**, pelo que a emenda da detecção de término registra — o exit code veio no marcador, e o feedback imediato não sobrevivia à queda da conexão que ele exigia manter aberta.
- **Bloquear e desacoplar ao mesmo tempo, com um `tail --pid` reconectável** — rejeitado com a emenda: é o mesmo polling com um mecanismo a mais, e a reconexão teria de tratar os mesmos estados que o poll já trata.
- **Seeds diferentes por instância** — rejeitado: introduz posição na sequência como variável extra sem ganho analítico. Mesma seed isola a arquitetura como única variável.
- **SSM em vez de SSH** — rejeitado: mais verboso; debug interativo menos natural pra um TCC. SSH é familiar e direto.

## Consequences

- A instância do Orquestrador é o único ponto de coordenação — se ela morrer, o experimento continua (instâncias auto-dirigem) mas não há monitoramento até reiniciar. **Emenda:** reiniciar o monitoramento é o `orchestrator.py watch`, que lê o arquivo de estado do work dir e volta ao mesmo laço de onde o Orquestrador caiu, pulando as arquiteturas que o arquivo já dá por terminadas. Sem esse arquivo, "reiniciar o monitoramento" seria caçar `instance_id` no console da AWS.
- **Emenda: cada arquitetura é terminada no poll em que o marcador dela aparece**, com falha ou sem. A ADR-0012 fala em terminar "como último passo de cada fase", e aqui a fase de cada instância acaba em horas diferentes: esperar a última custaria a diferença entre as 30 h de uma e as 46 h da outra. Uma arquitetura dada por morta é terminada pelo mesmo motivo, reportada e **não** relançada; as outras seguem.
- **Emenda: o Orquestrador tem prazo próprio** — o teto de cada Instância mais o do bootstrap mais uma margem fixa. Ao estourar, o que resta de pé é terminado e o `run` sai com erro: nem instância zumbi, nem Orquestrador zumbi.
- **Emenda: `Ctrl-C` não termina nada.** SIGINT no `run` ou no `watch` para só a vigilância, imprime como voltar (`watch`) e como abortar (`watch --abort`) e sai com status próprio. As instâncias são auto-dirigidas por desenho, e matá-las por um terminal fechado seria a alavanca errada no lugar errado.
- `scenarios.json` é gerado pelo orquestrador com seed persistida antes de disparar cada instância. A seed fica registrada no JSON e no `meta.json` de cada run pra reprodutibilidade.
- A instância do Orquestrador precisa de Python, AWS CLI, e a chave SSH. Não precisa de Docker nem FFmpeg.
