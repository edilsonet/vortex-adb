-- Camada editável do registro. Aplicada pelo servidor em `etl/app_server.py`,
-- e nunca pelo `etl/run.py`.
--
-- A separação é deliberada. `schema/schema.sql` é dado da ANAC: recarregável,
-- com `DROP TABLE` no topo e reconstruído do zero a cada carga. O que o
-- operador digita não é recarregável, e um `DROP` acidental apagaria trabalho
-- que não existe em nenhum arquivo. Por isso esta camada mora fora, é criada
-- com `IF NOT EXISTS` e não tem `DROP` em lugar nenhum.
--
-- Duas regras que o resto do sistema depende:
--
-- **1. Sobreposição, não mutação.** Os campos que a ANAC traz continuam em
-- `pessoa` e não são reescritos. O que o operador corrige vai em
-- `nome_alterado`/`uf_alterada`, e `NULL` significa "não corrigido". Assim o
-- dado original nunca se perde, e dá para responder "isso veio da ANAC ou foi
-- digitado?" sem ambiguidade.
--
-- **2. Todo campo tem procedência.** `origem_societario` e `origem_contato`
-- dizem `ANAC` quando a valor veio da fonte e `MANUAL` quando foi digitado. Só
-- 30 organizações da ANAC têm razão social e endereço; o resto é `MANUAL` por
-- preenchimento posterior, nunca por dedução. Telefone, e-mail e logo não
-- existem em nenhum arquivo da pasta, então começam `NULL` — a interface os
-- mostra como "não consta na fonte", e não como zero.

PRAGMA foreign_keys = ON;

-- Contato, razão social e correções de identidade, por pessoa.
CREATE TABLE IF NOT EXISTS pessoa_extra (
    pessoa_id      INTEGER PRIMARY KEY REFERENCES pessoa(id) ON DELETE CASCADE,
    -- `chave` é a identidade estável de `pessoa` (`PJ:<cnpj>` ou
    -- `NOME:<nome>|<uf>`). O vínculo é refeito por ela, e não pelo
    -- `pessoa_id`: um `etl/run.py` completo recria `pessoa` do zero e renumera
    -- os ids, e o curado do operador sobreviveria à recarga só se não
    -- dependesse do surrogate.
    chave          TEXT UNIQUE,

    -- Sobreposições manuais. NULL = mantém o que a ANAC traz.
    nome_alterado  TEXT,
    uf_alterada    TEXT,
    justificativa   TEXT,            -- por que a fonte foi corrigida

    -- Identificação societária.
    razao_social   TEXT,
    nome_fantasia  TEXT,

    -- Contato. Nenhum destes existe na pasta dados-anac; ficam para
    -- preenchimento, e a origem distingue o que veio do que foi digitado.
    telefone       TEXT,
    email          TEXT,
    site           TEXT,
    logo_url       TEXT,

    -- Endereço. A ANAC só o traz para as organizações de produção.
    end_logradouro TEXT,
    end_numero     TEXT,
    end_complemento TEXT,
    end_bairro     TEXT,
    end_cep        TEXT,
    end_municipio  TEXT,
    end_uf         TEXT,
    end_pais       TEXT,

    origem_societario TEXT NOT NULL DEFAULT 'MANUAL',  -- ANAC | MANUAL
    origem_contato    TEXT NOT NULL DEFAULT 'MANUAL',  -- ANAC | MANUAL
    fonte_ref         TEXT,   -- de onde veio: 'Organizacoes de Producao.json:RazaoSocial'
    editado_em        TEXT,
    editado_por       TEXT,

    -- Nenhum campo de contato é obrigatório: a fonte não os traz, e exigir
    -- preenchimento só serviria para fabricar dado.
    CHECK (origem_societario IN ('ANAC', 'MANUAL')),
    CHECK (origem_contato    IN ('ANAC', 'MANUAL'))
);

-- Sócios e administradores. Não existe QSA em nenhum arquivo da pasta
-- dados-anac, e o CPF vem sempre mascarado, então esta tabela nasce vazia e é
-- preenchida a partir de ficha societária. `socio_pessoa_id` fica NULL enquanto
-- o sócio não tiver cadastro próprio, para o vínculo existir mesmo assim.
CREATE TABLE IF NOT EXISTS pessoa_socio (
    id                INTEGER PRIMARY KEY,
    empresa_pessoa_id INTEGER NOT NULL REFERENCES pessoa(id) ON DELETE CASCADE,
    socio_pessoa_id   INTEGER REFERENCES pessoa(id) ON DELETE SET NULL,
    socio_nome        TEXT NOT NULL,
    socio_cpf         TEXT,          -- como vier; mascarado se a fonte mascarar
    qual_cargo        TEXT,          -- sócio, administrador, principeiro, etc.
    participacao_pct  REAL,          -- NULL quando a ficha não informa
    origem            TEXT NOT NULL DEFAULT 'MANUAL',
    editado_em        TEXT,
    UNIQUE (empresa_pessoa_id, socio_nome, socio_cpf)
);
CREATE INDEX IF NOT EXISTS idx_socio_empresa ON pessoa_socio(empresa_pessoa_id);
CREATE INDEX IF NOT EXISTS idx_socio_pessoa  ON pessoa_socio(socio_pessoa_id);

-- Trilha de auditoria. Append-only: grava o antes e o depois de cada campo
-- alterado, para dar para desfazer e para saber quem mudou o quê.
CREATE TABLE IF NOT EXISTS registro_auditoria (
    id      INTEGER PRIMARY KEY,
    em      TEXT NOT NULL,
    por     TEXT,
    tabela  TEXT NOT NULL,
    chave   TEXT NOT NULL,
    rotulo  TEXT,          -- nome legível, para o histórico não ser só id
    campo   TEXT NOT NULL,
    antes   TEXT,
    depois  TEXT
);
CREATE INDEX IF NOT EXISTS idx_auditoria_chave ON registro_auditoria(tabela, chave, id DESC);

-- Registro de uma pessoa, já com a sobreposição aplicada e a procedência de
-- cada bloco. `nome` vem de `pessoa` ou de `nome_alterado`; a interface usa
-- `origem_societario` para marcar o que é da ANAC.
CREATE VIEW IF NOT EXISTS v_pessoa_registro AS
SELECT
    p.id,
    p.chave,
    COALESCE(e.nome_alterado, p.nome)      AS nome,
    p.nome                                AS nome_fonte,
    p.natureza,
    p.documento,
    p.documento_bruto,
    p.documento_mascarado,
    p.documento_invalido,
    COALESCE(e.uf_alterada, p.uf)         AS uf,
    p.uf                                  AS uf_fonte,
    e.justificativa,
    e.razao_social, e.nome_fantasia,
    e.telefone, e.email, e.site, e.logo_url,
    e.end_logradouro, e.end_numero, e.end_complemento, e.end_bairro,
    e.end_cep, e.end_municipio, e.end_uf, e.end_pais,
    e.origem_societario, e.origem_contato, e.fonte_ref,
    e.editado_em, e.editado_por,
    f.id IS NOT NULL                      AS eh_fabricante,
    f.org_codigo, f.org_nabrev
FROM pessoa p
LEFT JOIN pessoa_extra e ON e.pessoa_id = p.id
LEFT JOIN fabricante    f ON f.pessoa_id = p.id;

-- Sobreposição genérica para as outras entidades. `pessoa_extra` é detalhada
-- porque tem muita coisa; `aeronave` e `aerodromo` editáveis cabem nesta
-- chave-valor, com a mesma garantia: a tabela original da ANAC não é reescrita
-- e o valor curado sobrevive a um `etl/run.py` completo.
CREATE TABLE IF NOT EXISTS registro_override (
    tabela  TEXT NOT NULL,          -- 'aeronave' | 'aerodromo'
    chave   TEXT NOT NULL,          -- id da entidade
    campo   TEXT NOT NULL,
    valor   TEXT,
    editado_em TEXT,
    editado_por TEXT,
    PRIMARY KEY (tabela, chave, campo)
);
CREATE VIEW IF NOT EXISTS v_aeronave_registro AS
-- `registro_override` é chave-valor: uma linha por campo editado. Um
-- `LEFT JOIN` direto multiplicaria a aeronave por quantos campos o operador
-- tivesse alterado, e a lista repetiria a mesma aeronave N vezes. Por isso a
-- sobreposição entra como subconsulta agregada, que achata os N campos em uma
-- linha só.
--
-- Os nomes daqui (`matricula`, `modelo`, ...) são os que a interface edita; as
-- colunas reais da ANAC têm prefixo (`nr_cert_matricula`, `ds_modelo`). O
-- COALESCE é a ponte: fonte primeiro, sobreposição depois.
SELECT a.*,
    COALESCE(a.nr_ano_fabricacao, '') AS ano_fabricacao_txt,
    COALESCE(o.matricula,        a.nr_cert_matricula)      AS matricula_ed,
    COALESCE(o.modelo,           a.ds_modelo)              AS modelo_ed,
    COALESCE(o.fabricante,       a.nm_fabricante)          AS fabricante_ed,
    COALESCE(o.numero_serie,     a.nr_serie)               AS numero_serie_ed,
    COALESCE(o.ano_fabricacao,   a.nr_ano_fabricacao)      AS ano_fabricacao_ed,
    COALESCE(o.classe,           a.cd_classe)              AS classe_ed,
    COALESCE(o.tipo_icao,        a.cd_tipo_icao)           AS tipo_icao_ed,
    COALESCE(o.motivo_cancelamento, a.ds_motivo_cancelamento) AS motivo_cancelamento_ed
FROM aeronave a
LEFT JOIN (
    SELECT chave,
           MAX(CASE WHEN campo = 'matricula'           THEN valor END) AS matricula,
           MAX(CASE WHEN campo = 'modelo'              THEN valor END) AS modelo,
           MAX(CASE WHEN campo = 'fabricante'          THEN valor END) AS fabricante,
           MAX(CASE WHEN campo = 'numero_serie'        THEN valor END) AS numero_serie,
           MAX(CASE WHEN campo = 'ano_fabricacao'      THEN valor END) AS ano_fabricacao,
           MAX(CASE WHEN campo = 'classe'              THEN valor END) AS classe,
           MAX(CASE WHEN campo = 'tipo_icao'           THEN valor END) AS tipo_icao,
           MAX(CASE WHEN campo = 'motivo_cancelamento' THEN valor END) AS motivo_cancelamento
    FROM registro_override WHERE tabela = 'aeronave' GROUP BY chave
) o ON o.chave = CAST(a.id AS TEXT);

-- A chave de `aerodromo` é o código ICAO (texto), não um id numérico. Por isso
-- a interface navega por um `id` que é alias de `icao`.
--
-- `operador` e `observacao` não existem em `aerodromo`: são campos que a ANAC
-- não publica, então não há coluna da fonte para o COALESCE usar. Vêm só da
-- sobreposição — que é a diferença entre "campo vazio" e "campo que a fonte não
-- tem", e a interface marca os dois de formas distintas.
CREATE VIEW IF NOT EXISTS v_aerodromo_registro AS
SELECT d.*,
    d.icao AS id,
    COALESCE(o.nome,       d.nome)       AS nome_ed,
    COALESCE(o.municipio,  d.municipio)  AS municipio_ed,
    COALESCE(o.uf,         d.uf)         AS uf_ed,
    COALESCE(o.situacao,   d.situacao)   AS situacao_ed,
    COALESCE(o.tipo,       d.tipo)       AS tipo_ed,
    o.operador                          AS operador_ed,
    o.observacao                        AS observacao_ed
FROM aerodromo d
LEFT JOIN (
    SELECT chave,
           MAX(CASE WHEN campo = 'nome'       THEN valor END) AS nome,
           MAX(CASE WHEN campo = 'municipio'  THEN valor END) AS municipio,
           MAX(CASE WHEN campo = 'uf'         THEN valor END) AS uf,
           MAX(CASE WHEN campo = 'situacao'   THEN valor END) AS situacao,
           MAX(CASE WHEN campo = 'tipo'       THEN valor END) AS tipo,
           MAX(CASE WHEN campo = 'operador'   THEN valor END) AS operador,
           MAX(CASE WHEN campo = 'observacao' THEN valor END) AS observacao
    FROM registro_override WHERE tabela = 'aerodromo' GROUP BY chave
) o ON o.chave = d.icao;
