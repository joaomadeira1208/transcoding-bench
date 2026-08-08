# Geometria por vídeo — os tiers de resolução são rótulos nominais

Os tiers `2160p/1080p/720p/480p` da ADR-0004 são **nomes**, não dimensões. Big Buck Bunny é 16:9 (3840x2160 nativo) e Tears of Steel é ~2.39:1 (4096x1714 nativo, DCI 4K), então "1080p" não pode significar 1920x1080 para os dois. Cada registro de vídeo no `config/experiment.toml` declara a **geometria exata (largura × altura) de cada tier**, e o `-vf scale=W:H:flags=lanczos` recebe valores literais vindos da spec.

A regra que gera a tabela é:

- o tier **2160p é o master 4K nativo** de cada vídeo, como a ADR-0004 já o define (versão canônica baixada);
- os tiers derivados seguem a **largura nominal do ladder** (1920, 1280, 854) **preservando o aspect ratio nativo**, com a altura arredondada para o par mais próximo — `yuv420p` (ADR-0002) exige as duas dimensões pares.

| Tier | Big Buck Bunny (16:9) | Tears of Steel (~2.39:1) |
|---|---|---|
| 2160p | 3840x2160 | 4096x1714 |
| 1080p | 1920x1080 | 1920x804 |
| 720p | 1280x720 | 1280x536 |
| 480p | 854x480 | 854x358 |

Casar a **largura**, e não a altura, é o que pipelines ABR de produção fazem com conteúdo scope: a rendition "1080p" de um filme 2.39:1 é 1920x804. É também a única regra que sobrevive ao tier de topo — casar a altura nominal daria 5162x2160 para Tears of Steel, que é upscale do master nativo.

Com a geometria explícita, o "nunca upscale" da ADR-0004 vira propriedade verificável sobre pixels (`output.width <= input.width` e idem para a altura, por vídeo), asserida na validação da spec antes de qualquer coisa ser gerada. Comparar os rótulos não diria nada: `1080p -> 720p` parece downscale sob qualquer geometria, inclusive uma errada.

## Considered Options

- **Deixar o FFmpeg calcular a dimensão livre (`scale=-2:H`)** — rejeitado: o argv deixaria de ser previsível a partir do TOML, o "nunca upscale" não teria números sobre os quais operar, e a asserção de argv do smoke (ADR-0022) perderia o elo que ela existe pra verificar — que os valores chegam ao FFmpeg iguais aos do TOML que alimentou o Cenário.
- **Geometria pela altura nominal** (Tears of Steel: 2582x1080 no tier 1080p) — rejeitado por duas razões. No tier 2160p exigiria 5162x2160, upscale do master nativo, quebrando a premissa da ADR-0004 justamente no topo do ladder. E infla o pixel budget do tier em ~35% só para o conteúdo scope, tornando "1080p" um workload sistematicamente mais pesado para um dos vídeos sem que o rótulo diga isso.
- **Padronizar a largura também no tier 2160p** (Tears of Steel: 3840x1606) — rejeitado: exigiria derivar também o master 4K de Tears of Steel por downscale do nativo, adicionando etapa de preparo e deixando o master 4K de um vídeo em FFV1 enquanto o do outro continua sendo a versão canônica H.264 (ADR-0004). O ganho seria cosmético: uniformidade de largura num tier que já não é iso-pixel entre os vídeos.
- **Crop ou pad para 16:9** — rejeitado: pillarboxing adiciona barras pretas, que são baratíssimas de codificar e falsificariam o workload medido; crop descartaria conteúdo e mudaria o vídeo que o Pass de qualidade compara.
- **Um único vídeo 16:9, eliminando o problema** — rejeitado: a variedade de conteúdo é validade externa do experimento, e conteúdo scope é comum em catálogo real. O custo de tratá-lo é uma tabela no TOML.

## Consequences

- **Os tiers não são iso-pixel entre vídeos.** No tier 1080p, Big Buck Bunny tem 2,07 MP e Tears of Steel 1,54 MP. Comparações **entre vídeos** não são comparações de mesmo trabalho e não devem ser lidas como tal; o vídeo é fator controlado, não eixo de comparação. A comparação que o experimento reporta — entre arquiteturas — não é afetada: para um dado Cenário, a geometria é idêntica nas três instâncias.
- **A ADR-0004 continua valendo como está**, e esta ADR a complementa: para Tears of Steel, os masters 1080p e 720p são gerados por downscale Lanczos FFV1 do master 4K nativo nas geometrias da tabela. O tier 480p é só saída, nunca master.
- Todas as oito geometrias têm largura e altura pares, requisito de `yuv420p`.
- A tabela é dado versionado, não constante em código: mudar um tier é editar o TOML, e a validação recusa a mudança se ela criar upscale em qualquer vídeo.
