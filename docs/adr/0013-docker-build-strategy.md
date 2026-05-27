# Bootstrap e build do Docker na instância

Todas as instâncias (encode, Juiz, controle) fazem **`git clone`** do repositório ao subir. Isso entrega o Dockerfile, os shell scripts e o código do orquestrador de forma uniforme. Cada instância usa apenas o que precisa — o resto é ignorado.

Nas instâncias de encode, após o clone, `docker build` compila FFmpeg localmente. Como o Dockerfile usa `-march=native` (ADR-0008), o build nativo garante que os SIMD paths são exatamente os da CPU que vai rodar o experimento.

## Fluxo (instância de encode)

1. Instância sobe (via `aws ec2 run-instances`)
2. Orquestrador faz SSH e dispara: `git clone` do repositório
3. `docker build -t transcoding-bench .`
4. Build leva ~10–20 min — custo: ~$0.03–0.06 por instância ($0.18 total pras 3)
5. Orquestrador envia `scenarios.json` via SCP
6. Instância começa a rodar cenários via `run_all.sh`

## Considered Options

- **Orquestrador copia só os arquivos necessários via SCP** — rejeitado: funciona, mas mantém dois mecanismos de entrega (git clone pro controle, SCP pros encodes). Git clone é uniforme e simples.
- **Arquivo .tar no S3** — rejeitado: passo extra de empacotamento; versão depende de quando foi empacotado.
- **User-data do EC2** — rejeitado: limite de 16 KB; frágil pra scripts maiores.
- **Imagem pré-buildada no ECR** — rejeitado: `-march=native` no build precisa corresponder exatamente à CPU da instância. Build remoto (na máquina local ou em CI) pode gerar binário com SIMD paths de outra CPU. Além disso, ECR adiciona custo e complexidade (registry, autenticação, pull policy).
- **AMI customizada com imagem Docker pré-buildada** — rejeitado: precisa manter AMIs por arquitetura; complexidade de build de AMIs; AMI é snapshot de um momento — se o Dockerfile mudar, AMI precisa ser recriada.

## Consequences

- Tempo de build (~10–20 min) é desprezível frente ao experimento (~46h).
- Custo de build (~$0.18 total) é 0.25% do custo total de compute.
- Se o build falhar, o orquestrador detecta via exit code do SSH e pode alertar antes de desperdiçar horas de compute.
- Todas as instâncias buildam em paralelo — tempo de build não acumula.
