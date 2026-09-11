# config/

SPEC declarativa do Experimento (ADR-0017). É a fonte de verdade auditável — a
matriz experimental se lê aqui, sem ler código, e este diretório é o que vira
anexo do artigo.

`experiment.toml` declara codecs — com o muxer do elementary stream usado na
extração que precede o `output.sha256` (ADR-0005/0007) —, pares
`input_res → output_res`, vídeos com geometria por tier (ADR-0023), instâncias,
parâmetros fixos de encode, os eventos de PMU que instrumentam cada Execução
(ADR-0006), seed e contagem de replicações. Nada tem default: campo ausente,
tipo divergente ou chave desconhecida fazem a validação falhar alto nomeando o
registro ofensor.

Cada `[[video]]` declara também o que a preparação e a validação dos Masters vão
copiar: `frame_rate`, `frames` e uma sub-tabela `source` com `url`, `file`,
`size` e `sha256`. Os valores são os da emenda da ADR-0004 — os dois arquivos 4K
da Blender, inspecionados com o `ffprobe` da imagem de medição (ADR-0018) —, e a
`url` aponta para um espelho, e não para o `download.blender.org`, que recusa a
faixa de IP da EC2: quem manda no que é baixado é o `sha256`, e a ADR-0004
registra a verificação que autorizou a troca de host. O `size` e o `sha256` são os do arquivo **descomprimido**,
não os do `.zip` que a URL entrega, porque é depois do `unzip` que o bootstrap
confere o master. O `frame_rate` é a racional que o `ffprobe` reporta (`30/1`,
`24/1`) e é declarado como string: um número não exprime `24000/1001` e chegaria
arredondado a quem o usasse. A ADR continua sendo a casa da justificativa; o que
está aqui é o dado versionado do qual a preparação lê.

`pilot.toml` é a definição do piloto (ADR-0022) — a campanha em escopo menor —,
da mesma forma e lida pelo mesmo validador e pelo mesmo gerador: nenhum campo,
flag ou modo diz "isto é um piloto". Todo registro que ele declara é **cópia
idêntica** do homônimo do `experiment.toml`, e o que faz dele um subconjunto é o
que ele omite — dos nove pares, só `1080p → 720p`. A regra é "idêntico ou
ausente": é ela que mantém o diff entre os dois arquivos legível, e é por isso
que os vídeos declaram a geometria dos quatro tiers embora o piloto use dois. A
seed também é a mesma, para que cada Execução do piloto seja a Execução homônima
da campanha. Editá-lo é copiar **valores**: os comentários que explicam cada um
moram no `experiment.toml`, e uma segunda cópia deles envelheceria em silêncio.

O subconjunto não é convenção: o CI o garante. A suíte do `orchestrator/`
confronta cada registro do piloto com o seu homônimo na campanha, campo a campo,
e confronta também os objetos de run dos dois planos canônicos, exigindo que cada
um do piloto exista idêntico no da campanha. Para quem edita: mudar um valor num
dos arquivos sem copiá-lo para o outro quebra o CI nomeando a família, o registro
e o campo que divergiram. A única liberdade do `pilot.toml` é **omitir** um
registro inteiro; dentro de um registro que ele declara nada pode faltar,
inclusive os tiers de geometria que ele não usa.

A *maquinaria* que age sobre esta spec mora em `orchestrator/` — a separação é
deliberada (ADR-0017): contrato de dado de um lado, código do outro.
