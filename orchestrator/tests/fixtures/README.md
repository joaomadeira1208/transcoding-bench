# orchestrator/tests/fixtures/

As saídas **cruas** que a AWS CLI v2 emitiu, capturadas na conta do experimento.
Elas são a âncora dos parsers de `command_output.py`: uma fixture escrita à mão
seria o autor do parser adivinhando o que a CLI devolve, e valida o Python
contra o Python (ADR-0022).

As factories do `orchestrator/conftest.py` continuam onde estão e não são
substituídas por estes arquivos. A divisão é a da ADR-0022 — factory por dentro,
âncora real por fora: a API nunca devolve um lançamento sem `InstanceId` nem uma
listagem truncada, e é exatamente isso que os parsers recusam.

## O que cada arquivo prova

| arquivo | o que só ele diz |
|---|---|
| `run-instances.json` | a forma de `Instances[]` numa resposta de lançamento |
| `describe-instances-running.json` | o aninhamento `Reservations[] → Instances[]`, com os dois endereços |
| `describe-instances-terminated.json` | instância terminada **não traz** `PrivateIpAddress` — a chave não vem, não vem `null` |
| `list-objects-v2.json` | os sete objetos de `masters/`, com `Key` e `Size` |
| `list-objects-v2-empty.json` | prefixo sem objeto **não traz** `Contents` |
| `get-parameter.json` | a forma de `Parameter`, com o `Value` substituído |
| `get-caller-identity.json` | o ARN de `assumed-role` que o perfil do Orquestrador resolve |

As duas linhas em negrito eram suposição do `command_output.py` (`default=[]` e
`_optional_field`) até esta captura.

## O que a captura corrigiu

Nenhuma das duas listagens traz `IsTruncated`, `KeyCount`, `MaxKeys` ou `Name`:
a CLI v2 agrega as páginas sozinha e remove os campos de paginação da saída. É a
premissa que o `_reject_truncation` assere, e ela só passa a valer ao contrário
se alguém acrescentar `--page-size`/`--max-items` ao argv do adaptador.

## Como regenerar

Da instância do Orquestrador, onde a identidade é a `assumed-role` que a campanha
usa — do Mac sairia o ARN de um usuário, que não é o que o parser vai ler:

    aws sts get-caller-identity --output json
    aws s3api list-objects-v2 --output json --bucket <pilot> --prefix masters/
    aws s3api list-objects-v2 --output json --bucket <pilot> --prefix nao-existe/
    aws ssm get-parameter --output json --name <parametro>
    aws ec2 run-instances --output json ...      # t3.micro, tag role=encode
    aws ec2 describe-instances --output json --instance-ids i-…
    aws ec2 terminate-instances --output json --instance-ids i-…
    aws ec2 describe-instances --output json --instance-ids i-…   # terminated

A hora de fazer isso é quando um payload da CLI mudar de forma — não de valor.

Duas regras da captura:

O `get-parameter` entra **sem** `--with-decryption`, e o `Value` é substituído
por `PLACEHOLDER` antes do commit. Aquele campo é a chave privada de SSH
(ADR-0016), o repositório é público (ADR-0021), e rodar sem o flag faz a chave em
claro não existir em disco, terminal nem scrollback — em vez de depender de
alguém lembrar de apagá-la.

A tag `role` da instância descartável é `encode`, não um nome próprio: a policy
escopa `TerminateInstances` a `role ∈ {encode, judge, masters}` (ADR-0016), e uma
tag fora dessa lista deixa a instância interminável por quem a lançou.

## O scrub

Trocado de forma consistente entre todos os arquivos, com os mesmos placeholders
que as factories do `conftest.py` usam, para que os ARNs continuem casando entre
um payload e outro:

    <conta>       → 123456789012       subnet-…     → subnet-0a1b2c3d4e5f60718
    AROA…         → AROAEXAMPLEID      vpc-…        → vpc-0192837465abcdef0
    AIPA…         → AIPAEXAMPLEID      sg-…         → sg-0fedcba9876543210
    <ip privado>  → 10.0.1.42          eni-…        → eni-0123456789abcdef0
    <ip publico>  → 203.0.113.2        eni-attach-… → eni-attach-0123456789abcdef0
    <MAC>         → 02:ab:cd:ef:01:23  vol-…        → vol-0123456789abcdef0
    <ETag>        → hex repetido       r-…          → r-0a1b2c3d4e5f60718
    <timestamp>   → 2026-09-07T12:00:00+00:00
    ClientToken   → o UUID nulo

O endereço público sai para `203.0.113.0/24`, que a RFC 5737 reserva para
documentação. O que a CLI devolveu está no espaço roteável da EC2 e hoje
pertence a outro cliente: publicá-lo como "placeholder" aponta para a máquina de
um terceiro, e nenhum leitor consegue distingui-lo de um vazamento.

O `AvailabilityZoneId` é inventado. O nome da AZ é embaralhado por conta e o id
é físico, então publicar o par real é impressão digital da conta; um id que não
existe quebra o mapeamento sem mexer na forma do campo.

Os `ami-…` ficam reais: são identificadores públicos da Canonical, e a ADR-0015
os pina por id de propósito.

**Confira com um padrão genérico, nunca com a lista de prefixos acima.** Três
valores reais passaram por uma varredura que enumerava `subnet|vpc|sg|eni|i|r`:
`vol-`, `eni-attach-` e o número da conta escrito na própria tabela de scrub — e
este arquivo não é `.json`, então quem varrer só os payloads não o vê.

    grep -rnE '\b[a-z][a-z0-9]{0,12}-[0-9a-f]{8,}\b' .
    grep -rnE '\b[0-9]{12}\b|([0-9]{1,3}\.){3}[0-9]{1,3}' .

A allowlist do `.gitignore` admite `.json` sob um diretório `fixtures/`
(ADR-0017) — é a exceção que deixa estes arquivos entrarem, e é por isso que o
scrub é a defesa que resta.
