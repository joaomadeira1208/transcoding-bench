# config/

SPEC declarativa do Experimento (ADR-0017). É a fonte de verdade auditável — a
matriz experimental se lê aqui, sem ler código, e este diretório é o que vira
anexo do artigo.

Conteúdo previsto: `experiment.toml` (codecs, pares `input_res → output_res`,
vídeos com geometria por tier, instâncias, parâmetros fixos de encode, seed e
contagem de replicações).

A *maquinaria* que age sobre esta spec mora em `orchestrator/` — a separação é
deliberada (ADR-0017): contrato de dado de um lado, código do outro.
