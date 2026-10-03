-- Camada de atualizações. Aplicada pelo servidor em `etl/registro_http.py`,
-- nunca pelo `etl/run.py` — mesma regra de `registro.sql`: nada aqui tem
-- `DROP`, tudo é `IF NOT EXISTS`, e uma recarga completa do ETL não apaga o
-- histórico de quem atualizou o quê e quando.
--
-- Quatro blocos:
--
-- **1. Configuração e estado.** `atualizacao_config` guarda o intervalo em
-- minutos e a última verificação. A data da última verificação é o que permite
-- a contagem offline: ela não depende de o relógio da tela nem de o servidor
-- estar ligado o tempo todo, e sim da diferença entre o instante gravado e
-- agora.
--
-- **2. Execuções.** Uma linha por verificação, com a pasta `update/<yyyymmdd>`
-- analisada, quantos registros entraram e quantos campos mudaram de verdade.
--
-- **3. Fontes externas.** Link cadastrado, arquivo baixado, `.md` derivado e o
-- sha256 do conteúdo — que é o que permite re-baixar sem guardar cópia
-- duplicada quando nada mudou.
--
-- **4. Hidratação.** Resposta da REDEMET e da AISWEB por aeródromo, com o
-- hash do conteúdo. O dado oficial da ANAC **não** é sobrescrito pela
-- hidratação: as duas fontes ficam lado a lado e a divergência fica visível.

PRAGMA foreign_keys = ON;

-- ------------------------------------------------------------- configuração
CREATE TABLE IF NOT EXISTS atualizacao_config (
    chave TEXT PRIMARY KEY,
    valor TEXT
);

-- Um registro só de estado, com `id = 1`. `etapa` é o texto que o painel
-- mostra enquanto o amarelo está aceso: 'procurando pasta', 'baixando',
-- 'analisando', 'aplicando'.
CREATE TABLE IF NOT EXISTS atualizacao_status (
    id              INTEGER PRIMARY KEY CHECK (id = 1),
    cor             TEXT NOT NULL DEFAULT 'VERMELHO',  -- VERMELHO|AMARELO|VERDE
    etapa           TEXT,
    mensagem        TEXT,
    pasta           TEXT,
    iniciada_em     TEXT,
    concluida_em    TEXT,
    registros       INTEGER DEFAULT 0,
    campos          INTEGER DEFAULT 0,
    novos           INTEGER DEFAULT 0,
    duracao_s       REAL
);

-- --------------------------------------------------------------- execuções
CREATE TABLE IF NOT EXISTS atualizacao_execucao (
    id            INTEGER PRIMARY KEY,
    origem        TEXT NOT NULL,          -- 'manual' | 'automatica' | 'inicial'
    pasta         TEXT,                   -- 'update/20260930'
    iniciada_em   TEXT NOT NULL,
    concluida_em  TEXT,
    estado        TEXT NOT NULL,          -- ok | erro | ignorado
    mensagem      TEXT,
    arquivos      INTEGER DEFAULT 0,
    registros     INTEGER DEFAULT 0,
    campos        INTEGER DEFAULT 0,
    novos         INTEGER DEFAULT 0,
    ignorados     INTEGER DEFAULT 0,
    duracao_s     REAL
);
CREATE INDEX IF NOT EXISTS idx_exec_data ON atualizacao_execucao(iniciada_em DESC);

-- Diff campo a campo. Guarda o antes e o depois para que "só mudou o que era
-- diferente" seja auditável depois, e não apenas alegado.
CREATE TABLE IF NOT EXISTS atualizacao_diferenca (
    id            INTEGER PRIMARY KEY,
    execucao_id   INTEGER REFERENCES atualizacao_execucao(id) ON DELETE CASCADE,
    tabela        TEXT NOT NULL,
    chave         TEXT NOT NULL,
    rotulo        TEXT,
    campo         TEXT NOT NULL,
    antes         TEXT,
    depois        TEXT,
    origem        TEXT                    -- pasta update/<yyyymmdd>/<arquivo>
);
CREATE INDEX IF NOT EXISTS idx_dif_chave ON atualizacao_diferenca(tabela, chave);

-- ----------------------------------------------------------------- fontes
CREATE TABLE IF NOT EXISTS fonte_link (
    id              INTEGER PRIMARY KEY,
    url             TEXT NOT NULL UNIQUE,
    titulo          TEXT,
    formato_declarado TEXT,       -- json|csv|doc|docx|xls|xlsx|pdf|txt|md
    formato_arquivo   TEXT,       -- o que realmente é
    categoria       TEXT,         -- json|csv|documento|metadados
    descricao       TEXT,
    documento_md    TEXT,         -- conteúdo convertido (ou o texto do .md)
    documento_bruto TEXT,         -- texto original quando não dá para converter
    tamanho_bytes   INTEGER,
    sha256          TEXT,
    status          TEXT NOT NULL DEFAULT 'pendente',
    erro            TEXT,
    tentativas      INTEGER NOT NULL DEFAULT 0,
    criado_em       TEXT NOT NULL,
    coletado_em     TEXT
);
CREATE INDEX IF NOT EXISTS idx_fonte_status ON fonte_link(status);

-- -------------------------------------------------------------- hidratação
-- Uma linha por (aeródromo, fonte, endpoint). O `conteudo_sha256` é a chave do
-- "só atualiza o que mudou": reidratar o mesmo aeródromo reescreve a linha só
-- quando o texto devolvido pela API é de fato diferente do que já estava.
CREATE TABLE IF NOT EXISTS aerodromo_hidratacao (
    icao           TEXT NOT NULL,
    fonte          TEXT NOT NULL,       -- 'REDEMET' | 'AISWEB'
    endpoint       TEXT NOT NULL,
    parametros     TEXT,
    status         TEXT,                -- ok | erro | sem_chave
    resumo         TEXT,                -- contagens do que veio
    conteudo       TEXT,                -- JSON bruto, para a tela mostrar
    conteudo_sha256 TEXT,
    bytes          INTEGER,
    coletado_em    TEXT NOT NULL,
    mudou          INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (icao, fonte, endpoint)
);
CREATE INDEX IF NOT EXISTS idx_hidrat_aero ON aerodromo_hidratacao(icao);

-- Progresso da hidratação: quantos endpoints foram consultados de quantos, para
-- a tela mostrar progresso em vez de um spinner cego.
CREATE TABLE IF NOT EXISTS hidratacao_execucao (
    id            INTEGER PRIMARY KEY,
    icao          TEXT NOT NULL,
    iniciada_em   TEXT NOT NULL,
    concluida_em  TEXT,
    estado        TEXT NOT NULL,        -- ok | erro
    endpoints_ok  INTEGER DEFAULT 0,
    endpoints_erro INTEGER DEFAULT 0,
    registros_novos INTEGER DEFAULT 0,
    registros_iguais INTEGER DEFAULT 0,
    mensagem      TEXT
);
CREATE INDEX IF NOT EXISTS idx_hidrat_exec ON hidratacao_execucao(icao, iniciada_em DESC);
