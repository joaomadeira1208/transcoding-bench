# Modelo de orquestração da pipeline

O experimento é coordenado por um **script Python rodando numa instância de controle EC2 (t3.micro)** na mesma VPC das instâncias de encode. A instância de controle é provisionada pelo Terraform junto com a infra base e persiste durante todo o experimento.

O orquestrador **não controla cada execução individualmente**. Em vez disso, cada instância de encode **auto-dirige**: recebe um `scenarios.json` (lista de cenários em ordem randomizada, gerada pelo orquestrador) e executa o loop inteiro sozinha via `run_all.sh`. O orquestrador dispara o processo e depois monitora.

## Comunicação orquestrador → instâncias

**SSH (key pair gerenciado pelo Terraform)** como canal primário. O orquestrador faz `ssh -i key.pem ec2-user@<ip> "bash run_all.sh ..."` pra disparar cada instância.

Security group permite porta 22 apenas do IP da instância de controle dentro da VPC.

## Detecção de término

**SSH bloqueante como primário + marcador S3 como fallback.** O `ssh` bloqueante retorna quando `run_all.sh` termina, dando feedback imediato e exit code. Em paralelo, `run_all.sh` sempre cria um marcador no S3 ao terminar (`s3://bucket/status/${INSTANCE_TYPE}_done`). Se a conexão SSH cair, o orquestrador faz fallback pra polling no S3 a cada 5 minutos.

## Randomização

As 3 instâncias usam a **mesma seed** de randomização — mesma sequência de cenários, ritmo diferente (cada arch tem performance distinta). Efeitos temporais (aquecimento, throttling) afetam os mesmos cenários nas 3 archs e cancelam na comparação cross-arch.

## Considered Options

- **Orquestrador roda na máquina local** — rejeitado: experimento leva ~46h por instância (~2 dias). Acoplar ao Mac é arriscado (sleep, queda de Wi-Fi, reboot). Instância de controle na mesma VPC é estável e custa centavos (~$0.01/h).
- **Orquestrador dirige cada passo via SSH** (324 chamadas por instância) — rejeitado: manter SSH ativo por 46h é frágil. Auto-direção desacopla a instância do orquestrador.
- **Apenas polling no S3 (sem SSH bloqueante)** — rejeitado: perde feedback imediato e exit code. Mix de SSH + S3 dá o melhor dos dois mundos.
- **Seeds diferentes por instância** — rejeitado: introduz posição na sequência como variável extra sem ganho analítico. Mesma seed isola a arquitetura como única variável.
- **SSM em vez de SSH** — rejeitado: mais verboso; debug interativo menos natural pra um TCC. SSH é familiar e direto.

## Consequences

- A instância de controle é o único ponto de coordenação — se ela morrer, o experimento continua (instâncias auto-dirigem) mas não há monitoramento até reiniciar.
- `scenarios.json` é gerado pelo orquestrador com seed persistida antes de disparar cada instância. A seed fica registrada no JSON e no `meta.json` de cada run pra reprodutibilidade.
- A instância de controle precisa de Python, AWS CLI, e a chave SSH. Não precisa de Docker nem FFmpeg.
