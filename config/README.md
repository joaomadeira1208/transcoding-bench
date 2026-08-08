# config/

SPEC declarativa do Experimento (ADR-0017). É a fonte de verdade auditável — a
matriz experimental se lê aqui, sem ler código, e este diretório é o que vira
anexo do artigo.

`experiment.toml` declara codecs, pares `input_res → output_res`, vídeos com
geometria por tier (ADR-0023), instâncias, parâmetros fixos de encode, seed e
contagem de replicações. Nada tem default: campo ausente, tipo divergente ou
chave desconhecida fazem a validação falhar alto nomeando o registro ofensor.

A *maquinaria* que age sobre esta spec mora em `orchestrator/` — a separação é
deliberada (ADR-0017): contrato de dado de um lado, código do outro.
