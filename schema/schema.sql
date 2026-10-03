-- Banco do Registro Aeronáutico Brasileiro (RAB) e dados abertos ANAC.
-- Convenção: `chave` é a identidade de dedup de `pessoa`. Pessoa jurídica com CNPJ
-- íntegro vira `PJ:<cnpj>`; pessoa física e qualquer CNPJ indisponível caem em
-- `NOME:<nome normalizado>|<uf>`. Isso mantém a identidade estável entre execuções
-- sem inventar documento que a fonte não fornece.

PRAGMA foreign_keys = ON;

DROP TABLE IF EXISTS ingestao_log;
DROP TABLE IF EXISTS redemet_coleta;
DROP TABLE IF EXISTS redemet_mensagem;
DROP TABLE IF EXISTS redemet_aerodromo_status;
DROP TABLE IF EXISTS snapshot;
DROP TABLE IF EXISTS registro_sisant;
DROP TABLE IF EXISTS peca_aprovada;
DROP TABLE IF EXISTS produto_aeronautico;
DROP TABLE IF EXISTS org_producao;
DROP TABLE IF EXISTS aerodromo;
DROP TABLE IF EXISTS participacao;
DROP TABLE IF EXISTS aeronave;
DROP TABLE IF EXISTS modelo;
DROP TABLE IF EXISTS marca;
DROP TABLE IF EXISTS fabricante;
DROP TABLE IF EXISTS pessoa;

-- ---------------------------------------------------------------- identidade
CREATE TABLE pessoa (
    id                  INTEGER PRIMARY KEY,
    natureza            TEXT    NOT NULL CHECK (natureza IN ('FISICA', 'JURIDICA')),
    chave               TEXT    NOT NULL UNIQUE,
    nome                TEXT    NOT NULL,
    documento           TEXT,                       -- só dígitos; NULL se a fonte não fornece
    documento_bruto     TEXT,                       -- como veio na fonte
    documento_mascarado INTEGER NOT NULL DEFAULT 0, -- 1 = CPF parcial (SISANT)
    documento_invalido  INTEGER NOT NULL DEFAULT 0, -- 1 = "Indisponível" / vazio
    uf                  TEXT,
    uf_indisponivel     INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX idx_pessoa_doc    ON pessoa(documento);
CREATE INDEX idx_pessoa_natureza ON pessoa(natureza);
CREATE INDEX idx_pessoa_nome   ON pessoa(nome);

-- Fabricante é pessoa jurídica por definição (plano: "cnpj e fabricantes se
-- tornam empresas"). Aponta para `pessoa`; quando a fonte não traz CNPJ, o
-- fabricante é uma empresa sem documento, com `org_codigo` como identidade.
CREATE TABLE fabricante (
    id           INTEGER PRIMARY KEY,
    pessoa_id    INTEGER NOT NULL REFERENCES pessoa(id),
    org_codigo   TEXT,
    org_nabrev   TEXT,
    UNIQUE (pessoa_id)
);
CREATE INDEX idx_fabricante_org ON fabricante(org_codigo);

CREATE TABLE marca (
    id    INTEGER PRIMARY KEY,
    nome  TEXT NOT NULL UNIQUE
);

-- `ds_modelo` e anulável porque a fonte tem registros com CD_TIPO e sem nome de
-- modelo. Preencher com um placeholder inventaria um modelo que não existe.
CREATE TABLE modelo (
    id            INTEGER PRIMARY KEY,
    marca_id      INTEGER REFERENCES marca(id),
    cd_tipo       TEXT,
    ds_modelo     TEXT,
    fabricante_id INTEGER REFERENCES fabricante(id),
    UNIQUE (cd_tipo, ds_modelo)
);
CREATE INDEX idx_modelo_marca ON modelo(marca_id);
-- Contar modelos por fabricante é o que a lista de organizações mostra em
-- "Modelos". Sem este índice o SQLite varre os 4.506 modelos para cada linha da
-- lista, e a consulta passava de dois minutos para menos de um segundo.
CREATE INDEX idx_modelo_fabricante ON modelo(fabricante_id);

-- ------------------------------------------------------------------- aeronave
-- A chave natural do RAB seria (marca, nr_cert_matricula, nr_serie), mas ela
-- colide: 25 das 34.903 linhas se repetem exatamente na fonte. Descartar as
-- duplicatas perderia aeronave, então a PK é surrogateira e a chave natural
-- fica em coluna indexada para conferência. Uma linha por aeronave; o estado
-- aqui é o do snapshot mais recente carregado.
CREATE TABLE aeronave (
    id                       INTEGER PRIMARY KEY,
    chave_natural            TEXT NOT NULL,
    nr_cert_matricula        TEXT,
    nr_serie                 TEXT,
    marca                    TEXT,
    cd_tipo                  TEXT,
    ds_modelo                TEXT,
    nm_fabricante            TEXT,
    cd_classe                TEXT,
    nr_pmd                   TEXT,
    cd_tipo_icao             TEXT,
    nr_tripulacao_min        TEXT,
    nr_passageiros_max       TEXT,
    nr_assentos              TEXT,
    nr_ano_fabricacao        TEXT,
    dt_validade_cva          TEXT,
    dt_validade_ca           TEXT,
    dt_cancelamento          TEXT,
    ds_motivo_cancelamento   TEXT,
    cd_interdicao            TEXT,
    ds_gravame               TEXT,
    dt_matricula             TEXT,
    tp_motor                 TEXT,
    qt_motor                 TEXT,
    tp_pouso                 TEXT,
    tp_ca                    TEXT,
    cd_proposito_cave        TEXT,
    cf_operacional           TEXT,
    ds_categoria_homologacao TEXT,
    tp_operacao              TEXT,
    dt_venda                 TEXT,
    ds_moeda                 TEXT,
    nr_preco_venda           TEXT,
    modelo_id                INTEGER REFERENCES modelo(id),
    fabricante_id            INTEGER REFERENCES fabricante(id),
    snapshot_mes             TEXT
);
CREATE INDEX idx_aeronave_natural    ON aeronave(chave_natural);
CREATE INDEX idx_aeronave_modelo     ON aeronave(modelo_id);
CREATE INDEX idx_aeronave_fabricante ON aeronave(fabricante_id);
CREATE INDEX idx_aeronave_marca      ON aeronave(marca);
CREATE INDEX idx_aeronave_serie      ON aeronave(nr_serie);
CREATE INDEX idx_aeronave_ano        ON aeronave(nr_ano_fabricacao);
CREATE INDEX idx_aeronave_cert       ON aeronave(nr_cert_matricula);

-- ---------------------------------------------------------------- participacao
-- Many-to-many real: uma aeronave pode ter vários proprietários (e vários
-- operadores), com participação fracionária. `snapshot_mes` versiona a relação
-- ao longo dos 13 meses. A chave composta torna o ETL idempotente.
CREATE TABLE participacao (
    id               INTEGER PRIMARY KEY,
    aeronave_id      INTEGER NOT NULL REFERENCES aeronave(id),
    pessoa_id        INTEGER NOT NULL REFERENCES pessoa(id),
    papel            TEXT    NOT NULL CHECK (papel IN ('PROPRIETARIO', 'OPERADOR')),
    percentual       REAL,
    uf               TEXT,
    snapshot_mes     TEXT    NOT NULL,
    operacao_121     TEXT,
    operacao_135     TEXT,
    transp_reg_121   TEXT,
    transp_reg_135   TEXT,
    aut_pmac_121     TEXT,
    aut_pmac_135     TEXT,
    sae              TEXT,
    authistrut       TEXT,
    UNIQUE (aeronave_id, pessoa_id, papel, snapshot_mes)
);
CREATE INDEX idx_part_pessoa   ON participacao(pessoa_id);
CREATE INDEX idx_part_snap     ON participacao(snapshot_mes);
CREATE INDEX idx_part_papel    ON participacao(papel);
CREATE INDEX idx_part_121      ON participacao(operacao_121);
CREATE INDEX idx_part_135      ON participacao(operacao_135);

-- ------------------------------------------------------------------ aerodromo
CREATE TABLE aerodromo (
    icao             TEXT PRIMARY KEY,
    ciad             TEXT,
    nome             TEXT,
    tipo             TEXT NOT NULL,   -- PUBLICO | PRIVADO | HELIPONTO | HELIDECK
    lat              REAL,
    lon              REAL,
    latitude         TEXT,
    longitude        TEXT,
    altitude         TEXT,
    municipio        TEXT,
    uf               TEXT,
    municipio_servido TEXT,
    uf_servido       TEXT,
    operacao_diurna  TEXT,
    operacao_noturna TEXT,
    situacao         TEXT,
    validade_registro TEXT
);
CREATE INDEX idx_aero_uf   ON aerodromo(uf);
CREATE INDEX idx_aero_tipo ON aerodromo(tipo);

-- ------------------------------------------------------- produtos e produção
-- `cnpj`/`razao_social` e `papp_cod`/`org_codi` são as chaves naturais. Sem
-- elas, cada recarga de `--manter` duplicaria a linha, porque essas tabelas não
-- têm id vindo da fonte.
CREATE TABLE org_producao (
    id            INTEGER PRIMARY KEY,
    cnpj          TEXT,
    razao_social  TEXT NOT NULL,
    nome_fantasia TEXT,
    certificado   TEXT,
    endereco      TEXT,
    complemento   TEXT,
    cidade        TEXT,
    uf            TEXT,
    cep           TEXT,
    pais          TEXT,
    homepage      TEXT,
    tipo          TEXT,
    UNIQUE (cnpj, razao_social)
);
CREATE INDEX idx_orgpro_cnpj ON org_producao(cnpj);

CREATE TABLE produto_aeronautico (
    id            INTEGER PRIMARY KEY,
    prod_codi     TEXT NOT NULL,
    prod_nome     TEXT,
    prod_descr    TEXT,
    org_codi      TEXT,
    org_nome      TEXT,
    org_nabrev    TEXT,
    fabricante_id INTEGER REFERENCES fabricante(id),
    UNIQUE (prod_codi)
);
CREATE INDEX idx_produto_fab ON produto_aeronautico(fabricante_id);

CREATE TABLE peca_aprovada (
    id                  INTEGER PRIMARY KEY,
    papp_cod            TEXT,
    papp_nome           TEXT,
    papp_pn             TEXT,
    papp_modelo         TEXT,
    papp_modeloaer      TEXT,
    papp_tipo           TEXT,
    papp_otp            TEXT,
    papp_repoe          TEXT,
    papp_maprov         TEXT,
    papp_autoridade     TEXT,
    apaa_codi           TEXT,
    apaa_data           TEXT,
    apaa_status         TEXT,
    org_codi            TEXT,
    org_nome            TEXT,
    org_nabrev_fab_prod TEXT,
    fabricante_id       INTEGER REFERENCES fabricante(id),
    UNIQUE (papp_cod, org_codi)
);
CREATE INDEX idx_peca_org ON peca_aprovada(org_codi);

-- ------------------------------------------------------------------- SISANT
CREATE TABLE registro_sisant (
    codigo_aeronave TEXT PRIMARY KEY,
    data_validade   TEXT,
    pessoa_id       INTEGER REFERENCES pessoa(id),
    tipo_uso        TEXT,
    fabricante_nome TEXT,
    modelo_nome     TEXT,
    num_serie       TEXT,
    peso_max_kg     REAL,
    ramo_atividade  TEXT
);
CREATE INDEX idx_sisant_pessoa ON registro_sisant(pessoa_id);
CREATE INDEX idx_sisant_ramo   ON registro_sisant(ramo_atividade);
-- A lista de aeronaves conta, por linha, quantos drones têm o mesmo número de
-- série. Sem índice em `num_serie`, o SQLite varre os 187.195 registros do
-- SISANT para cada uma das 60 aeronaves da página: 2,6 segundos para abrir a
-- lista. Com ele, some da conta.
CREATE INDEX idx_sisant_serie  ON registro_sisant(num_serie);

-- --------------------------------------------------------------- série temporal
CREATE TABLE snapshot (
    mes            TEXT PRIMARY KEY,
    contagem       INTEGER NOT NULL,
    esquema_era    TEXT,
    arquivo_origem TEXT
);

CREATE TABLE ingestao_log (
    id           INTEGER PRIMARY KEY,
    fonte        TEXT NOT NULL,
    arquivo      TEXT NOT NULL,
    linhas       INTEGER NOT NULL DEFAULT 0,
    status       TEXT NOT NULL,
    mensagem     TEXT,
    executado_em TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_log_fonte ON ingestao_log(fonte);

-- ------------------------------------------------------------- REDEMET
-- API da DECEA. `aeronave_id` não existe aqui: o REDEMET é por localidade
-- meteorológica, não por aeronave.
--
-- `status_aerodromo` liga em `aerodromo.icao` quando a localidade também é
-- aeródromo no cadastro da ANAC. A REDEMET cobre 179 localidades e o cadastro
-- da ANAC tem 6.139 aeródromos, então a FK fica anulável de propósito: nem
-- todo status corresponde a um aeródromo cadastrado, e forçar a association
-- perderia as duas pontas.
CREATE TABLE redemet_aerodromo_status (
    icao       TEXT PRIMARY KEY,
    aerodromo_icao TEXT REFERENCES aerodromo(icao),
    nome       TEXT,
    lat        REAL,
    lon        REAL,
    cor        TEXT,
    mensagem   TEXT,
    colhido_em TEXT NOT NULL
);
CREATE INDEX idx_redemet_status_cor ON redemet_aerodromo_status(cor);

CREATE TABLE redemet_mensagem (
    id              INTEGER PRIMARY KEY,
    tipo            TEXT NOT NULL CHECK (tipo IN ('METAR', 'TAF', 'SPECI')),
    icao            TEXT NOT NULL,
    aerodromo_icao  TEXT REFERENCES aerodromo(icao),
    validade_inicial TEXT,
    validade_final   TEXT,
    mensagem         TEXT,
    recebimento     TEXT,
    colhido_em       TEXT NOT NULL,
    UNIQUE (tipo, icao, validade_inicial, mensagem)
);
CREATE INDEX idx_redemet_msg_icao ON redemet_mensagem(icao);
CREATE INDEX idx_redemet_msg_tipo ON redemet_mensagem(tipo);
CREATE INDEX idx_redemet_msg_val  ON redemet_mensagem(validade_inicial);

-- Rastro das coletas, para auditar o volume gasto contra o limite da API.
CREATE TABLE redemet_coleta (
    id           INTEGER PRIMARY KEY,
    endpoint     TEXT NOT NULL,
    parametros   TEXT,
    status       TEXT NOT NULL,
    mensagem     TEXT,
    registros    INTEGER NOT NULL DEFAULT 0,
    requisicoes  INTEGER NOT NULL DEFAULT 0,
    duracao_s    REAL,
    executado_em TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ------------------------------------------------------------------- views
-- Requisito do plano: CPF -> usuário, CNPJ/fabricante -> empresa.
CREATE VIEW v_usuario AS
    SELECT id, nome, documento, documento_bruto, documento_mascarado, uf
    FROM pessoa WHERE natureza = 'FISICA';

CREATE VIEW v_empresa AS
    SELECT p.id, p.nome, p.documento, p.documento_bruto, p.uf, f.org_codigo, f.org_nabrev
    FROM pessoa p LEFT JOIN fabricante f ON f.pessoa_id = p.id
    WHERE p.natureza = 'JURIDICA';
