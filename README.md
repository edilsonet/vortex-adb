# anac-db · Registro Aeronáutico Brasileiro

Banco normalizado e painel analítico construídos a partir dos dados abertos da
ANAC baixados em `C:\Users\edils\dados-anac`.

## Rodar

```bash
.venv/Scripts/python.exe etl/run.py                # monta o banco do zero (~2,5min)
.venv/Scripts/python.exe etl/run.py --manter       # recarga incremental, pula meses já carregados (~27s)
.venv/Scripts/python.exe etl/run.py --redemet      # também coleta a API-REDEMET
.venv/Scripts/python.exe etl/validate.py           # confere contagens, FKs e série
.venv/Scripts/python.exe etl/build_dashboard.py
```

Saídas em `build/`: `anac.db` (SQLite) e `dashboard.html` (abre com duplo
clique, sem CDN, sem npm, funciona offline).

A coleta da REDEMET é opt-in: a API da DECEA impõe limite de uso, e o ETL
principal não toca na rede sem você pedir.

## Modelo

| Tabela | Papel |
|---|---|
| `pessoa` | raiz de identidade. `natureza` = FISICA (CPF) ou JURIDICA (CNPJ) |
| `v_usuario` / `v_empresa` | views que recortam `pessoa` por natureza |
| `fabricante` | empresa detentora de `pessoa` — fabricante é PJ por definição |
| `marca`, `modelo` | `modelo` liga a `marca` e ao `fabricante` |
| `aeronave` | uma linha por aeronave, estado do snapshot mais recente |
| `participacao` | **núcleo**: relação pessoa–aeronave com `percentual`, versionada por `snapshot_mes` |
| `aerodromo` | público, privado, heliponto e helideck |
| `produto_aeronautico`, `peca_aprovada`, `org_producao` | catálogo certificado |
| `registro_sisant` | drones e aeronaves de pequeno porte |
| `snapshot` | contagem por mês, com a era de esquema |
| `redemet_aerodromo_status` | cor por localidade, ligado a `aerodromo.icao` quando existe |
| `redemet_mensagem` | METAR, SPECI e TAF em texto bruto, versionados por validade |
| `redemet_coleta` | rastro das coletas: endpoint, volume e duração |
| `ingestao_log` | rastro de cada arquivo carregado |

A identidade de `pessoa` é a coluna `chave`: `PJ:<cnpj>` ou `PF:<cpf>` quando o
documento é íntegro, e `NOME:<nome normalizado>|<uf>` quando não é. Isso mantém
a mesma empresa como uma linha só mesmo aparecendo em arquivos diferentes.

## As três eras de esquema

A ANAC exportou o RAB em três formatos dentro da série de 12 meses. `etl/rab_eras.py`
detecta a era de cada arquivo e reduz todas ao mesmo tuplo, para que o resto do
ETL nunca dependa do formato do mês.

| Era | Meses | Formato de proprietário |
|---|---|---|
| A | 2025-09 → 2026-01 | `PROPRIETARIO` + `OUTROSPROPRIETARIOS` + `CPFCNPJ` + `SGUF` |
| B | 2026-02 → 2026-04 | `PROPRIETARIOSARRAY` (`NOME\|DOC\|PCT`) e, em 03/04, `OPERADORESARRAY` |
| C | 2026-05 → 2026-09 | `PROPRIETARIOSJSON` + `OPERADORESJSON` (arrays JSON) |

A era B não tem um formato único, e é onde mora a maior parte do risco:

- **Operadores.** 2026-02 traz `NMOPERADOR`/`CPFCGC`; 2026-03 e 2026-04 trazem
  `OPERADORESARRAY` (`NOME|DOC|UF`). Sem tratar isso, dois meses perdem todos os
  operadores.
- **Passo do proprietário.** 2026-02 empacota `NOME|DOCUMENTO|PERCENTUAL` (3
  campos); 2026-03 e 2026-04 empacotam `NOME|DOCUMENTO|PERCENTUAL|UF` (4).
  `detectar_passo_era_b` escolhe o passo modal do mês. Assumir 3 nos meses de
  passo 4 desalinha 33 mil registros e atribui a UF ao proprietário seguinte.
- **Grupo final curto.** Em 2026-02, 5.639 registros trazem só 2 campos
  (`NOME|PCT` ou `NOME|DOC`) e 99 trazem 1. O parseamento puramente posicional
  descartava o grupo inteiro, e 5.738 aeronaves ficavam sem dono. O grupo
  remanescente é realocado pelo formato do valor, não pela posição.
- **Ausência dentro do grupo completo** não desalinha nada: a ANAC preenche a
  lacuna com `Documento Indisponível`, `Percentual Indisponível` e
  `UF Indisponível`, e `classifica_documento` trata os três como ausentes.

## Concentração de propriedade

`etl/concentracao.py` responde quem detém a frota e o tanto que a titularidade
está concentrada. Quatro decisões definem os números e estão no cabeçalho do
módulo:

1. **Identidade em dois níveis.** O nome normalizado não funde grafias, e a
   fonte grafia mal: `AIR TRACTOR CAPITAL, LLC.` aparece em cinco variantes que
   somam 215 aeronaves, `WELLS FARGO BANK NORTHWEST` em cinco também. O HHI sai
   nas duas bases — nome normalizado e conjunto de radicais — para que a
   fragmentação seja visível em vez de escolhida em silêncio. A chave de
   radicais só funde conjuntos **iguais**: `AIR TRACTOR` está contido em
   `AIR TRACTOR CAPITAL`, mas são a fabricante e o braço de financiamento dela.
2. **HHI por frota, não por percentual.** A era A não tem campo de percentual e
   a era B de 2026-02 preenche quase todos. Somar uma base que existe em uns
   meses e não em outros produz uma série que sobe de 12,6 para 13,9 sem que
   nada tenha acontecido na frota — era a troca de formato sendo lida como
   concentração. O HHI de referência é o da fatia de frota, que existe nos doze
   meses; o de percentual declarado sai à parte, sempre com a cobertura.
3. **HHI de crédito.** O maior proprietário do país é a EMBRAER, com 881
   aeronaves, mas são aeronaves de fábrica que ela vende. Somá-las ao índice
   mede integração vertical, não concorrência. O HHI de crédito exclui governo
   e fabricante.
4. **Titular é por aeronave.** Um veículo de securitização é um dono que não
   opera aquela aeronave. O teste é por aeronave e não por empresa: em 2026-03
   a ANAC lista `BRADESCO LEASING S.A ARREND.MERCANTIL` como operador de três
   aeronaves, e a leitura por empresa reclassificava as 383 que a empresa detém.

Resultado de 2026-09 (34.867 aeronaves com dono): HHI de frota **12,14**, HHI de
crédito **6,55**, Gini **0,396**, CR4 **5,56%**, maior dono **2,53%**. Metade da
frota está em 3.593 dos 21.161 donos (17%). **30,6%** das aeronaves estão
registradas em nome de alguém que não as opera; a cauda disso são 2.156
veículos, que colapsam em 2.016 pools quando as séries de um mesmo pool
(`... CORPORATION I`, `... 2017 III`) são somadas.

A concentração **cai** no período: de 12,59 para 12,14 na frota e de 7,10 para
6,55 no crédito. A leitura antiga mostrava o contrário, por causa do item 2.

A agregação roda em Python sobre 865 mil vínculos e leva cerca de dois minutos.
`build_dashboard.py` guarda o resultado em `build/concentracao.json`, invalidado
quando o banco é mais novo.

## Reparo dos JSONs

Os 13 snapshots saem da ANAC com escape corrompido (`/""` onde deveria ser
`\"`). `common.repair_json_anac` desfaz por substituição determinística e o
resultado é validado com `json.loads`. Sem isso, sobraria um mês de série em vez
de doze.

## REDEMET (DECEA)

Cliente em `etl/redemet_client.py`, coleta em `etl/ingest_redemet.py`. Três
endpoints: `/aerodromos/status`, `/mensagens/metar` e `/mensagens/taf`.

A chave fica em `secrets/redemet.key`, no `.gitignore`, e vai por header
`X-Api-Key` — não por query string, para não vazar em log de URL.

Limites, todos por variável de ambiente:

| Variável | Padrão | O que faz |
|---|---|---|
| `REDEMET_MAX_AERODROMOS` | 25 | quantas localidades consultar |
| `REDEMET_DELAY_S` | 1.0 | intervalo entre requisições |
| `REDEMET_HORAS` | 6 | janela de histórico de METAR/TAF |
| `REDEMET_ICAOS` | — | lista explícita de OACI, tem precedência |

A escolha de localidades usa a **própria lista da REDEMET** (179 localidades com
serviço meteorológico ativo), filtrada pelas que também estão no cadastro da
ANAC. Consultar as 6.139 do cadastro às cegas geraria requisições vazias para
quem não emite METAR. A API rejeita `page_tam` acima de 150, e a documentação
pede intervalo entre chamadas — o cliente aplica os dois.

A FK para `aerodromo.icao` é anulável de propósito: 21 das 179 localidades da
REDEMET não constam no cadastro da ANAC, e forçar a associação perderia as duas
pontas.

## Ressalvas

- **CPF nunca vem completo.** A ANAC publica `062.XXX.XXX-89` no RAB e
  `****525.372***` no SISANT. Dá para separar pessoa física de jurídica pelo
  formato, mas a identidade da pessoa física é nome + UF. 39,6% dos vínculos
  têm CPF mascarado. É dado de análise, não cadastro de pessoas.
- **52,4% dos vínculos não têm documento.** A era A lista proprietários
  secundários só como nome, e o campo `DOCUMENTO` às vezes vem `Indisponível`.
- **2026-08 não entrou na série.** Só existe como `.xls`; a série usa os JSONs.
- **Chave natural de aeronave tem 25 colisões** na fonte (linhas repetidas
  literalmente). A PK é surrogateira e a chave natural fica indexada, para não
  descartar aeronave.
- **`MARCAS` do RAB é prefixo de matrícula** (PPAAA), não marca comercial. Não
  use a tabela `marca` para falar de fabricante — use `fabricante`.
- **Timestamps já vêm sujos.** `DT_MATRICULA` vem como `2013-01-08 00:00:00` e
  `DTVALIDADECVA` como `14012026`. As colunas guardam o valor bruto; converter
  exige uma coluna derivada.
- **O SISANT traz 64 códigos de aeronave duplicados** na origem (187.262 linhas,
  187.195 distintas). O `INSERT OR REPLACE` colapsa a repetição — comportamento
  correto — mas quando a linha duplicada é a única referência de alguém, essa
  pessoa fica sem vínculo. São 2 pessoas de 176.565. `validate.py` reporta como
  aviso, não como falha, porque é condição da fonte e não invariante quebrada.
- **O recorte por segmento não é comparável entre meses.** Ele depende de
  `pessoa.natureza`, e `classifica_documento` devolve `JURIDICA` quando o
  documento não vem. Na era A os proprietários secundários aparecem só como
  nome, então pessoa física sem documento é arquivada como empresa: a fatia de
  PF cai de 45,6% (2026-09) para 40,9% (2026-01) por causa do formato, não da
  frota. Por isso a série mensal do painel traz só HHI, Gini, CR4 e
  titularização, que não dependem de natureza.
- **Identidade de pessoa física é nome + UF.** Com CPF sempre mascarado, dois
  homônimos no mesmo estado são a mesma linha. A contagem de 21.161 donos em
  2026-09 é contagem de nomes distintos, não de pessoas.
- **A chave REDEMET foi exposta em texto puro no chat.** Movê-la para o arquivo
  ignorado protege o disco, não desfaz a exposição. Rotacione no DECEA e grave a
  nova em `secrets/redemet.key`.

## Base normativa

O painel usa **RBAC 45.12-I(a)** para o cruzamento 121/135: *"Uma pessoa somente
pode operar uma aeronave em operações segundo o RBAC nº 135 se na aeronave
estiver colocada a inscrição TRANSPORTE PÚBLICO"*. Conferida no cérebro ANAC em
`C:\Users\edils\cerebro-anac`.
